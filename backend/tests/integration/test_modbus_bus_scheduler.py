"""End-to-end bus scheduling: real pymodbus client against a fake TCP gateway.

The fake gateway answers slaves 1 and 2, answers some slaves too late, and stays
SILENT for any other id, which is exactly how a missing RS485 slave looks behind
a Modbus-TCP->RTU gateway. Requirements pinned here:

* a silent slave never blocks the healthy ones (they keep their poll rate);
* each transaction to the silent slave is bounded by its own deadline;
* the silent slave is quarantined and probed with backoff, not every poll;
* values are never attributed to the wrong slave;
* exclusive access (writes) waits at most about one round;
* late replies are discarded, never attributed to another slave;
* bus-level outages do not count against sensor health.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.drivers import modbus_bus
from app.drivers.modbus import ModbusDriver
from app.drivers.modbus_bus import BusSettings, SensorHealth

TX_TIMEOUT_MS = 100
POLL_MS = 200
SETTINGS = BusSettings(round_budget_s=0.5, default_transaction_timeout_s=0.1, quarantine_max_s=1.0)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class FakeGateway:
    """Minimal Modbus-TCP gateway: FC3 only.

    * slaves in ``values`` answer immediately with that constant in every register;
    * slaves in ``slow`` answer the same way but only after ``slow_delay_s``
      (a late reply, arriving after the client deadline);
    * every other slave id gets NO reply at all (a dead RS485 slave).

    pymodbus' own simulator answers unknown ids with an exception in 3.15, so it
    cannot reproduce a silent slave.
    """

    def __init__(self, values: dict[int, int], slow: dict[int, int] | None = None,
                 slow_delay_s: float = 0.3) -> None:
        self.values = values
        self.slow = slow or {}
        self.slow_delay_s = slow_delay_s
        self.requests: dict[int, int] = {}
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._late: set[asyncio.Task[None]] = set()

    async def start(self, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for task in list(self._late):
                task.cancel()
            for writer in list(self._writers):
                writer.close()
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    async def _reply(self, writer: asyncio.StreamWriter, header: bytes, unit: int,
                     count: int, value: int, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        payload = bytes([unit, 3, 2 * count]) + value.to_bytes(2, "big") * count
        writer.write(header[:4] + len(payload).to_bytes(2, "big") + payload)
        with contextlib.suppress(ConnectionError):
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                length = int.from_bytes(header[4:6], "big")
                body = await reader.readexactly(length - 1)
                unit, fc = header[6], body[0]
                self.requests[unit] = self.requests.get(unit, 0) + 1
                if fc != 3:
                    continue
                count = int.from_bytes(body[3:5], "big")
                if unit in self.values:
                    await self._reply(writer, header, unit, count, self.values[unit], 0.0)
                elif unit in self.slow:
                    task = asyncio.create_task(
                        self._reply(writer, header, unit, count, self.slow[unit], self.slow_delay_s)
                    )
                    self._late.add(task)
                    task.add_done_callback(self._late.discard)
                # else: silent, like a dead RS485 slave behind a gateway
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()


async def _start_server(port: int, slow: dict[int, int] | None = None) -> FakeGateway:
    gateway = FakeGateway({1: 11, 2: 22}, slow=slow)
    await gateway.start(port)
    return gateway


@pytest.fixture
def bus_settings() -> Iterator[None]:
    previous = modbus_bus.current_settings()
    modbus_bus.configure(SETTINGS)
    yield
    modbus_bus.configure(previous)


class Sink:
    def __init__(self) -> None:
        self.values: dict[int, list[float]] = {}
        self.statuses: dict[int, list[str]] = {}

    async def on_batch(self, sensor_id: int, readings: list[dict[str, Any]], _ts: Any) -> None:
        self.values.setdefault(sensor_id, []).extend(r["raw_value"] for r in readings)

    async def on_status(self, sensor_id: int, status: str) -> None:
        self.statuses.setdefault(sensor_id, []).append(status)


def _driver(sensor_id: int, slave: int, port: int, sink: Sink) -> ModbusDriver:
    driver = ModbusDriver(
        sensor_id=sensor_id,
        sensor_name=f"slave{slave}",
        config={
            "mode": "tcp",
            "host": "127.0.0.1",
            "port": port,
            "slave_id": slave,
            "transaction_timeout_ms": TX_TIMEOUT_MS,
        },
        registers=[
            {"id": sensor_id * 10, "name": "r0", "address": 0, "count": 1, "register_type": "holding"},
            {"id": sensor_id * 10 + 1, "name": "r1", "address": 1, "count": 1, "register_type": "holding"},
        ],
        poll_interval_ms=POLL_MS,
        retry_count=2,
    )
    driver.on_value_batch(sink.on_batch)
    driver.on_status_change(sink.on_status)
    return driver


@pytest.mark.usefixtures("bus_settings")
async def test_silent_slave_does_not_block_healthy_ones() -> None:
    port = _free_port()
    server = await _start_server(port)
    sink = Sink()
    drivers = [_driver(1, 1, port, sink), _driver(2, 2, port, sink), _driver(9, 9, port, sink)]
    try:
        for d in drivers:
            await d.start()
        run_s = 3.0
        await asyncio.sleep(run_s)

        bus = modbus_bus.get_bus(f"tcp://127.0.0.1:{port}")
        assert bus is not None
        dead_stats = bus.stats(9)
        dead_health = bus.health(9)
        assert dead_stats is not None and dead_health is not None

        expected_polls = run_s / (POLL_MS / 1000)  # 15
        for sid, value in ((1, 11.0), (2, 22.0)):
            got = sink.values.get(sid, [])
            # 2 registers per poll; healthy sensors keep >= 80 % of their rate.
            assert len(got) / 2 >= 0.8 * expected_polls, (sid, len(got))
            # Never a value from another slave.
            assert set(got) == {value}, (sid, set(got))
            assert bus.health(sid) == modbus_bus.HealthState(
                SensorHealth.ONLINE, 0, bus.health(sid).next_due  # type: ignore[union-attr]
            )

        # Silent slave: quarantined, probed with backoff (not every 200 ms) ...
        assert dead_health.health is SensorHealth.QUARANTINED
        assert dead_stats.successes == 0
        assert dead_stats.attempts <= 8, dead_stats
        # ... and each attempt is bounded by its own transaction deadline.
        assert dead_stats.max_latency_s <= TX_TIMEOUT_MS / 1000 + 0.08, dead_stats
        assert "OFFLINE" in sink.statuses.get(9, [])
        assert sink.values.get(9) is None
    finally:
        for d in drivers:
            await d.stop()
        await server.stop()
    assert modbus_bus.get_bus(f"tcp://127.0.0.1:{port}") is None  # closed with last sensor


@pytest.mark.usefixtures("bus_settings")
async def test_exclusive_access_waits_at_most_about_one_round() -> None:
    port = _free_port()
    server = await _start_server(port)
    sink = Sink()
    # Several silent slaves keep rounds as long as possible.
    drivers = [_driver(1, 1, port, sink)] + [_driver(100 + i, 50 + i, port, sink) for i in range(4)]
    try:
        for d in drivers:
            await d.start()
        await asyncio.sleep(0.3)
        bus = modbus_bus.get_bus(f"tcp://127.0.0.1:{port}")
        assert bus is not None
        started = time.monotonic()
        async with bus.exclusive(TX_TIMEOUT_MS / 1000):  # same path as write()
            elapsed = time.monotonic() - started
        assert elapsed <= SETTINGS.round_budget_s + TX_TIMEOUT_MS / 1000 + 0.15, elapsed
    finally:
        for d in drivers:
            await d.stop()
        await server.stop()


@pytest.mark.usefixtures("bus_settings")
async def test_bus_outage_does_not_quarantine_sensors() -> None:
    port = _free_port()  # nothing listening yet
    sink = Sink()
    driver = _driver(1, 1, port, sink)
    server: FakeGateway | None = None
    try:
        await driver.start()
        await asyncio.sleep(1.5)
        bus = modbus_bus.get_bus(f"tcp://127.0.0.1:{port}")
        assert bus is not None
        health = bus.health(1)
        assert health is not None
        assert health.consecutive_failures == 0  # outage is not the sensor's fault
        assert health.health is SensorHealth.ONLINE
        assert "OFFLINE" in sink.statuses.get(1, [])
        assert not sink.values.get(1)

        server = await _start_server(port)
        await asyncio.sleep(3.0)  # bus reconnect backoff is 1 s, 2 s, ...
        assert sink.values.get(1), "no data after the gateway came back"
        assert sink.statuses[1][-1] == "ONLINE"
    finally:
        await driver.stop()
        if server is not None:
            await server.stop()


@pytest.mark.parametrize("bad", [0, -5])
def test_invalid_transaction_timeout_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        ModbusDriver(
            sensor_id=1,
            sensor_name="x",
            config={"mode": "tcp", "slave_id": 1, "transaction_timeout_ms": bad},
        )


@pytest.mark.usefixtures("bus_settings")
async def test_late_reply_is_discarded_and_not_misattributed() -> None:
    """Slave 7 answers after the deadline; its late frame must not reach anyone."""
    port = _free_port()
    server = await _start_server(port, slow={7: 77})
    sink = Sink()
    drivers = [_driver(1, 1, port, sink), _driver(7, 7, port, sink), _driver(2, 2, port, sink)]
    try:
        for d in drivers:
            await d.start()
        await asyncio.sleep(2.0)
        assert set(sink.values.get(1, [])) == {11.0}
        assert set(sink.values.get(2, [])) == {22.0}
        assert 77.0 not in sink.values.get(1, []) + sink.values.get(2, [])
        assert sink.values.get(7) is None
        # Healthy neighbours keep their rate despite the late frames.
        assert len(sink.values.get(1, [])) / 2 >= 0.8 * (2.0 / (POLL_MS / 1000))
        assert len(sink.values.get(2, [])) / 2 >= 0.8 * (2.0 / (POLL_MS / 1000))
    finally:
        for d in drivers:
            await d.stop()
        await server.stop()
