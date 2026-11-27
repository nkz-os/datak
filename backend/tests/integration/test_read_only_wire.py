"""Read-only guarantee on the wire: a recording Modbus-TCP gateway.

The gateway logs the function code of every request it receives. For a locked
bus it must see reads only, whatever the caller tries, including driving the
pymodbus client directly through the bus (the "driver bug" scenario).
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
import pytest_asyncio
from pymodbus.pdu.register_message import WriteSingleRegisterRequest

from app.drivers import modbus_bus
from app.drivers.base import WriteError
from app.drivers.modbus import ModbusDriver
from app.drivers.modbus_bus import BusSettings

SETTINGS = BusSettings(round_budget_s=0.5, default_transaction_timeout_s=0.1, quarantine_max_s=1.0)
WRITE_CODES = {5, 6, 15, 16, 21, 22, 23}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class RecordingGateway:
    """Answers FC3 with zeros and echoes write requests; records every function code."""

    def __init__(self) -> None:
        self.function_codes: list[int] = []
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    @property
    def writes(self) -> list[int]:
        return [c for c in self.function_codes if c != 3]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                length = int.from_bytes(header[4:6], "big")
                body = await reader.readexactly(length - 1)
                unit, fc = header[6], body[0]
                self.function_codes.append(fc)
                if fc == 3:
                    count = int.from_bytes(body[3:5], "big")
                    payload = bytes([unit, 3, 2 * count]) + b"\x00\x00" * count
                else:
                    payload = bytes([unit]) + body[:5]  # echo: enough for a write ack
                writer.write(header[:4] + len(payload).to_bytes(2, "big") + payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()


@pytest_asyncio.fixture
async def gateway() -> AsyncGenerator[tuple[RecordingGateway, int], None]:
    previous = modbus_bus.current_settings()
    modbus_bus.configure(SETTINGS)
    modbus_bus._reset_read_only_latch()
    gw = RecordingGateway()
    port = _free_port()
    await gw.start(port)
    yield gw, port
    await gw.stop()
    modbus_bus._reset_read_only_latch()
    modbus_bus.configure(previous)


def _driver(sensor_id: int, port: int, **extra: Any) -> ModbusDriver:
    config: dict[str, Any] = {
        "mode": "tcp", "host": "127.0.0.1", "port": port, "slave_id": 1,
        "register_type": "holding", "address": 5, "transaction_timeout_ms": 100, **extra,
    }
    driver = ModbusDriver(
        sensor_id=sensor_id, sensor_name=f"s{sensor_id}", config=config, poll_interval_ms=200,
        registers=[{"id": sensor_id, "name": "r", "address": 0, "count": 1, "register_type": "holding"}],
    )

    async def sink(*_a: Any) -> None:
        return None

    driver.on_value_batch(sink)
    return driver


async def test_baseline_a_writable_sensor_does_put_a_write_on_the_wire(
    gateway: tuple[RecordingGateway, int],
) -> None:
    """Proves the harness can see a write at all (otherwise the rest is vacuous)."""
    gw, port = gateway
    driver = _driver(1, port)
    await driver.start()
    try:
        assert await driver.write(7.0) is True
    finally:
        await driver.stop()
    assert 6 in gw.function_codes


@pytest.mark.parametrize("how", ["sensor_flag", "endpoint_setting"])
async def test_read_only_sensor_is_polled_but_never_written(
    gateway: tuple[RecordingGateway, int], how: str
) -> None:
    gw, port = gateway
    if how == "endpoint_setting":
        modbus_bus.configure(
            BusSettings(
                round_budget_s=0.5, default_transaction_timeout_s=0.1, quarantine_max_s=1.0,
                read_only_endpoints=(f"127.0.0.1:{port}",),
            )
        )
        driver = _driver(1, port)
    else:
        driver = _driver(1, port, read_only=True)
    await driver.start()
    try:
        await asyncio.sleep(0.5)  # a couple of polls
        with pytest.raises(WriteError):
            await driver.write(7.0)
    finally:
        await driver.stop()
    assert 3 in gw.function_codes
    assert gw.writes == []


async def test_writable_neighbour_on_a_locked_bus_cannot_write(
    gateway: tuple[RecordingGateway, int],
) -> None:
    gw, port = gateway
    plain = _driver(1, port)
    await plain.start()  # bus created unlocked, client cached by the driver
    ro = _driver(2, port, read_only=True)
    await ro.start()
    try:
        await asyncio.sleep(0.4)
        with pytest.raises(WriteError):
            await plain.write(7.0)
        with pytest.raises(WriteError):
            await ro.write(7.0)
    finally:
        await ro.stop()
        await plain.stop()
    assert gw.writes == []


def _every_write_call() -> list[tuple[str, Callable[[Any], Any]]]:
    return [
        ("write_register", lambda c: c.write_register(address=0, value=1, device_id=1)),
        ("write_registers", lambda c: c.write_registers(address=0, values=[1], device_id=1)),
        ("write_coil", lambda c: c.write_coil(address=0, value=True, device_id=1)),
        ("write_coils", lambda c: c.write_coils(address=0, values=[True], device_id=1)),
        ("mask_write_register", lambda c: c.mask_write_register(address=0, and_mask=0, or_mask=1, device_id=1)),
        ("readwrite_registers", lambda c: c.readwrite_registers(read_address=0, read_count=1, address=0, values=[1], device_id=1)),
        ("write_file_record", lambda c: c.write_file_record(records=[], device_id=1)),
        ("execute", lambda c: c.execute(False, WriteSingleRegisterRequest(address=0, registers=[1], dev_id=1))),
        ("ctx.execute", lambda c: c.ctx.execute(False, WriteSingleRegisterRequest(address=0, registers=[1], dev_id=1))),
    ]


@pytest.mark.parametrize("name,call", _every_write_call(), ids=[n for n, _ in _every_write_call()])
async def test_no_pymodbus_write_path_reaches_the_wire_of_a_locked_bus(
    gateway: tuple[RecordingGateway, int], name: str, call: Callable[[Any], Any]
) -> None:
    """Drive the bus client directly, as a buggy driver would."""
    gw, port = gateway
    driver = _driver(1, port, read_only=True)
    await driver.start()
    bus = modbus_bus.get_bus(f"tcp://127.0.0.1:{port}")
    assert bus is not None
    try:
        async with bus.exclusive(0.2) as client:
            with pytest.raises(WriteError):
                result = call(client)
                if asyncio.iscoroutine(result):
                    await result
        # the connection is live (a read still works) yet nothing but reads was sent
        async with bus.exclusive(0.2) as client:
            await client.read_holding_registers(address=0, count=1, device_id=1)
    finally:
        await driver.stop()
    assert gw.writes == [], name
    assert 3 in gw.function_codes


async def test_sanity_unlocked_bus_client_does_write(
    gateway: tuple[RecordingGateway, int],
) -> None:
    gw, port = gateway
    driver = _driver(1, port)
    await driver.start()
    bus = modbus_bus.get_bus(f"tcp://127.0.0.1:{port}")
    assert bus is not None
    try:
        async with bus.exclusive(0.2) as client:
            await client.write_register(address=0, value=1, device_id=1)
    finally:
        await driver.stop()
    assert 6 in gw.function_codes


@pytest.mark.parametrize(
    "alias", ["localhost", "LOCALHOST", "127.1", "2130706433", "0x7f000001", "::ffff:127.0.0.1"]
)
async def test_alias_of_a_configured_endpoint_never_puts_a_write_on_the_wire(
    gateway: tuple[RecordingGateway, int], alias: str
) -> None:
    gw, port = gateway
    modbus_bus.configure(
        BusSettings(
            round_budget_s=0.5, default_transaction_timeout_s=0.1, quarantine_max_s=1.0,
            read_only_endpoints=(f"127.0.0.1:{port}",),
        )
    )
    driver = _driver(1, port, host=alias)
    await driver.start()
    bus = modbus_bus.get_bus(f"tcp://{alias}:{port}")
    assert bus is not None
    try:
        await asyncio.sleep(0.4)
        with pytest.raises(WriteError):
            await driver.write(7.0)
        async with bus.exclusive(0.2) as client:
            with pytest.raises(WriteError):
                await client.write_register(address=0, value=1, device_id=1)
    finally:
        await driver.stop()
    assert 3 in gw.function_codes
    assert gw.writes == []


async def test_alias_of_a_flagged_sensor_endpoint_is_locked_on_its_own_bus(
    gateway: tuple[RecordingGateway, int],
) -> None:
    """Flagged via 127.0.0.1; a plain sensor reaching the same device as 'localhost' cannot write."""
    gw, port = gateway
    ro = _driver(1, port, read_only=True)
    await ro.start()
    alias = _driver(2, port, host="localhost")
    await alias.start()
    try:
        await asyncio.sleep(0.4)
        with pytest.raises(WriteError):
            await alias.write(7.0)
    finally:
        await alias.stop()
        await ro.stop()
    assert gw.writes == []
