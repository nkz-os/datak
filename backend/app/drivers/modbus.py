"""Modbus TCP/RTU async driver using pymodbus.

Polling is NOT done by this driver's own loop: every Modbus sensor registers on
the scheduler of its physical bus (``app.drivers.modbus_bus``), which serialises
all traffic on that bus, enforces a per-transaction deadline, bounds each round
by a time budget and quarantines unresponsive slaves. See that module for the
timing contract.

Per-sensor ``connection_params``:
    host / port (TCP) or port / baudrate / parity / stopbits / bytesize (RTU)
    slave_id
    transaction_timeout_ms   optional, default from ``modbus.transaction_timeout_ms``

``poll_interval_ms`` and ``retry_count`` (consecutive failed polls before
quarantine) come from the sensor row. ``timeout_ms`` is not used by Modbus
sensors: the per-transaction deadline replaces it.
"""

import inspect
from datetime import datetime
from typing import Any

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from app.drivers import modbus_bus
from app.drivers.base import BaseDriver, ConnectionError, ReadError, WriteError
from app.drivers.modbus_bus import BusConnectionError, BusScheduler, SensorHealth

# Sentinel to group contiguous registers for batch reading
_MAX_GAP = 16  # max gap between registers to batch them together

# Serial line settings each open RTU bus was created with (port -> settings).
_SERIAL_LINE_SETTINGS: dict[str, tuple[int, str, int, int]] = {}


def _unit_kwarg(method: Any, slave_id: int) -> dict[str, int]:
    """pymodbus >= 3.10 names the unit ``device_id``; older versions ``slave``."""
    try:
        sig = inspect.signature(method)
        if "device_id" in sig.parameters and "slave" not in sig.parameters:
            return {"device_id": slave_id}
    except (TypeError, ValueError):
        pass
    return {"slave": slave_id}


class ModbusDriver(BaseDriver):
    """
    Async Modbus driver supporting both TCP and RTU modes.
    """

    def __init__(
        self,
        sensor_id: int,
        sensor_name: str,
        config: dict[str, Any],
        registers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        super().__init__(sensor_id, sensor_name, config, **kwargs)

        self.mode = config.get("mode", "tcp")
        self.slave_id = config.get("slave_id", 1)
        self.address = config.get("address", 0)
        self.count = config.get("count", 1)
        self.register_type = config.get("register_type", "holding")

        tx_ms = config.get("transaction_timeout_ms")
        self._tx_timeout_override_s: float | None = None
        if tx_ms is not None:
            if float(tx_ms) <= 0:
                raise ValueError(f"transaction_timeout_ms must be > 0, got {tx_ms}")
            self._tx_timeout_override_s = float(tx_ms) / 1000.0

        # Multi-register support
        self._registers: list[dict[str, Any]] = registers or []

        self._client: AsyncModbusTcpClient | AsyncModbusSerialClient | None = None
        self._bus: BusScheduler | None = None
        self._bus_key: str | None = None
        self._health: SensorHealth | None = None

    # ─────────────────────────────────────────────────────────────
    # Bus job interface (used by BusScheduler)
    # ─────────────────────────────────────────────────────────────

    @property
    def transaction_timeout_s(self) -> float:
        if self._tx_timeout_override_s is not None:
            return self._tx_timeout_override_s
        return modbus_bus.current_settings().default_transaction_timeout_s

    @property
    def interval_s(self) -> float:
        return self.poll_interval_ms / 1000.0

    @property
    def worst_case_s(self) -> float:
        return self._transaction_count() * self.transaction_timeout_s

    def _uses_batch(self) -> bool:
        return bool(self._registers) and self._on_value_batch is not None

    def _transaction_count(self) -> int:
        if self._uses_batch():
            return max(1, sum(len(blocks) for _, blocks in self._plan_blocks()))
        return 1

    async def transact(self) -> tuple[str, Any]:
        """Bus I/O only (called by the scheduler while it holds the bus)."""
        if self._uses_batch():
            return "batch", await self.read_registers()
        return "single", await self.read()

    async def deliver(self, result: tuple[str, Any], timestamp: datetime) -> None:
        """Hand a successful poll to consumers (called with the bus released)."""
        kind, payload = result
        # Keep the historical naive-UTC timestamps consumers already expect.
        ts = timestamp.replace(tzinfo=None)
        self._last_read_time = ts
        if kind == "batch":
            self._last_value = payload[0]["raw_value"] if payload else None
            if self._on_value_batch:
                await self._on_value_batch(self.sensor_id, payload, ts)
        else:
            self._last_value = payload
            if self._on_value:
                await self._on_value(self.sensor_id, payload, payload, ts)

    async def report(
        self, health: SensorHealth, error: str | None, *, bus_ok: bool
    ) -> None:
        """Map scheduler health to the gateway's sensor status vocabulary."""
        if not bus_ok:
            self._connected = False
            if self._on_error and error:
                await self._on_error(self.sensor_id, error)
            await self._set_status(None, "OFFLINE")
            return

        if error is None:
            self._error_count = 0
            self._connected = True
            await self._set_status(health, "ONLINE")
            return

        self._error_count += 1
        if self._on_error:
            await self._on_error(self.sensor_id, error)
        if health is SensorHealth.QUARANTINED:
            self._connected = False
            await self._set_status(health, "OFFLINE")
        else:
            self._health = health

    async def _set_status(self, health: SensorHealth | None, status: str) -> None:
        """Notify only on change (status callbacks fan out to WebSocket clients)."""
        if health is not None and health is self._health:
            return
        self._health = health
        await self._notify_status(status)

    # ─────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────

    def _bus_spec(self) -> tuple[str, Any, tuple[int, str, int, int] | None]:
        default_timeout = modbus_bus.current_settings().default_transaction_timeout_s
        if self.mode == "tcp":
            host = self.config.get("host", "localhost")
            port = int(self.config.get("port", 502))

            def tcp_factory() -> AsyncModbusTcpClient:
                return AsyncModbusTcpClient(
                    host=host, port=port, timeout=default_timeout, retries=0
                )

            return f"tcp://{host}:{port}", tcp_factory, None

        if self.mode == "rtu":
            serial_port = self.config.get("port", "/dev/ttyUSB0")
            line = (
                int(self.config.get("baudrate", 9600)),
                str(self.config.get("parity", "N")),
                int(self.config.get("stopbits", 1)),
                int(self.config.get("bytesize", 8)),
            )

            def rtu_factory() -> AsyncModbusSerialClient:
                return AsyncModbusSerialClient(
                    port=serial_port,
                    baudrate=line[0],
                    parity=line[1],
                    stopbits=line[2],
                    bytesize=line[3],
                    timeout=default_timeout,
                    retries=0,
                )

            return f"rtu://{serial_port}", rtu_factory, line

        raise ConnectionError(f"Unknown Modbus mode: {self.mode}")

    async def connect(self) -> bool:
        """Attach to the scheduler of this sensor's bus (no network I/O here).

        The scheduler connects lazily and reconnects with backoff; bus problems
        are reported through status callbacks, not by this call.
        """
        try:
            key, factory, line = self._bus_spec()
            if line is not None:
                existing = _SERIAL_LINE_SETTINGS.get(key)
                if existing is not None and existing != line:
                    raise ConnectionError(
                        f"{key} already open with line settings {existing}; "
                        f"sensor requests {line}. All slaves on one RS485 bus must share them."
                    )
            bus = modbus_bus.get_or_create_bus(key, factory)
            if line is not None:
                _SERIAL_LINE_SETTINGS[key] = line
        except ConnectionError:
            raise
        except Exception as e:
            self._log.error("Modbus bus setup failed", error=str(e))
            raise ConnectionError(f"Failed to set up Modbus bus: {e}") from e

        self._bus = bus
        self._bus_key = key
        self._client = bus.client
        return True

    async def disconnect(self) -> None:
        """Detach from the bus; the bus closes when its last sensor leaves."""
        key = self._bus_key
        self._bus = None
        self._bus_key = None
        self._client = None
        if key is not None:
            await modbus_bus.release_bus(key, self.sensor_id)
            if modbus_bus.get_bus(key) is None:
                _SERIAL_LINE_SETTINGS.pop(key, None)

    async def start(self) -> None:
        """Register on the bus scheduler instead of running an own poll loop."""
        if self._running:
            self._log.warning("Driver already running")
            return

        self._log.info("Starting driver")
        self._error_count = 0
        self._health = None
        try:
            await self.connect()
        except Exception as e:
            # Fail-safe: a misconfigured sensor stays stopped and visible as ERROR.
            self._log.error("Modbus sensor not started", error=str(e))
            await self._notify_status("ERROR")
            return

        assert self._bus is not None
        self._running = True
        self._connected = True
        self._log.info(
            "Modbus sensor scheduled",
            bus=self._bus_key,
            slave_id=self.slave_id,
            transaction_timeout_ms=round(self.transaction_timeout_s * 1000),
            poll_interval_ms=self.poll_interval_ms,
            retry_count=self.retry_count,
        )
        self._bus.register(self)

    async def stop(self) -> None:
        """Unregister from the bus scheduler."""
        if not self._running:
            return
        self._log.info("Stopping driver")
        self._running = False
        try:
            await self.disconnect()
        except Exception as e:
            self._log.error("Error during disconnect", error=str(e))
        self._connected = False
        await self._notify_status("OFFLINE")

    # ─────────────────────────────────────────────────────────────
    # Bus I/O
    # ─────────────────────────────────────────────────────────────

    async def read(self) -> float:
        """Read single register value from Modbus device (legacy, single-register mode)."""
        if not self._client:
            raise ReadError("Not connected")

        try:
            return await self._read_register(
                self.register_type, self.address, self.count
            )
        except ConnectionException as e:
            raise BusConnectionError(str(e)) from e
        except ModbusException as e:
            raise ReadError(f"Modbus read failed: {e}") from e

    async def _read_register(
        self, reg_type: str, address: int, count: int
    ) -> float:
        """Read a single register block and return the decoded float."""
        if reg_type in ("coil", "discrete"):
            bits = await self._read_raw_block(reg_type, address, count)
            return float(bits[0])
        registers = await self._read_raw_block(reg_type, address, count)
        if count == 2:
            return float((registers[0] << 16) | registers[1])
        return float(registers[0])

    def _plan_blocks(self) -> list[tuple[str, list[list[dict[str, Any]]]]]:
        """Group registers by type, then into contiguous blocks (one transaction each)."""
        by_type: dict[str, list[dict[str, Any]]] = {}
        for reg in self._registers:
            rt = reg.get("register_type", "holding")
            by_type.setdefault(rt, []).append(reg)

        plan: list[tuple[str, list[list[dict[str, Any]]]]] = []
        for reg_type, regs in by_type.items():
            regs_sorted = sorted(regs, key=lambda r: r["address"])
            blocks: list[list[dict[str, Any]]] = []
            current_block: list[dict[str, Any]] = []
            prev_end = -999
            for reg in regs_sorted:
                addr = reg["address"]
                cnt = reg.get("count", 1)
                if current_block and (addr - prev_end) > _MAX_GAP:
                    blocks.append(current_block)
                    current_block = []
                current_block.append(reg)
                prev_end = addr + cnt
            if current_block:
                blocks.append(current_block)
            plan.append((reg_type, blocks))
        return plan

    async def read_registers(self) -> list[dict[str, Any]]:
        """
        Batch-read all configured registers for this sensor.

        Groups contiguous registers by type to minimize Modbus transactions.
        Returns a list of dicts: {register_id, address, raw_value, register_type, count}.
        """
        if not self._client:
            raise ReadError("Not connected")

        if not self._registers:
            return []

        results: list[dict[str, Any]] = []

        for reg_type, blocks in self._plan_blocks():
            # Read each block in one Modbus transaction
            for block in blocks:
                start_addr = block[0]["address"]
                end_reg = block[-1]
                total_count = (end_reg["address"] - start_addr) + end_reg.get("count", 1)

                try:
                    raw_registers = await self._read_raw_block(
                        reg_type, start_addr, total_count
                    )
                except ConnectionException as e:
                    raise BusConnectionError(str(e)) from e
                except ModbusException as e:
                    raise ReadError(
                        f"Batch read failed for {reg_type} at {start_addr}: {e}"
                    ) from e

                for reg in block:
                    offset = reg["address"] - start_addr
                    cnt = reg.get("count", 1)

                    if cnt == 1:
                        val = float(raw_registers[offset])
                    elif cnt == 2:
                        val = float((raw_registers[offset] << 16) | raw_registers[offset + 1])
                    else:
                        val = float(raw_registers[offset])

                    results.append({
                        "register_id": reg["id"],
                        "name": reg.get("name", f"reg_{reg['address']}"),
                        "address": reg["address"],
                        "raw_value": val,
                        "register_type": reg_type,
                        "count": cnt,
                        "unit": reg.get("unit"),
                        "formula": reg.get("data_formula", "val"),
                        "decimal_places": reg.get("decimal_places", 2),
                        "twin_attribute": reg.get("twin_attribute"),
                    })

        return results

    async def _read_raw_block(
        self, reg_type: str, address: int, count: int
    ) -> list[int]:
        """Read one block (ONE Modbus transaction) and return raw int values.

        Must only be called while the bus is held (scheduler round or
        ``BusScheduler.exclusive``); it applies this sensor's transaction deadline.
        """
        if not self._client:
            raise ReadError("Not connected")

        methods = {
            "holding": "read_holding_registers",
            "input": "read_input_registers",
            "coil": "read_coils",
            "discrete": "read_discrete_inputs",
        }
        method_name = methods.get(reg_type)
        if method_name is None:
            raise ReadError(f"Unknown register type: {reg_type}")
        method = getattr(self._client, method_name)

        modbus_bus.set_response_timeout(self._client, self.transaction_timeout_s)
        result = await method(
            address=address, count=count, **_unit_kwarg(method, self.slave_id)
        )

        if result.isError():
            raise ReadError(f"Modbus error: {result}")

        if reg_type in ("coil", "discrete"):
            return [int(b) for b in result.bits[:count]]
        return list(result.registers)

    def set_registers(self, registers: list[dict[str, Any]]) -> None:
        """Update the register configuration (hot-reload)."""
        self._registers = registers

    async def write(self, value: float) -> bool:
        """Write value to Modbus register (waits for the bus between rounds)."""
        if not self._client:
            raise WriteError("Not connected")

        int_value = int(value)

        async def _do_write(client: Any) -> Any:
            if self.register_type == "holding":
                method = client.write_register
                return await method(
                    address=self.address,
                    value=int_value,
                    **_unit_kwarg(method, self.slave_id),
                )
            if self.register_type == "coil":
                method = client.write_coil
                return await method(
                    address=self.address,
                    value=bool(int_value),
                    **_unit_kwarg(method, self.slave_id),
                )
            raise WriteError(f"Cannot write to {self.register_type} registers")

        try:
            if self._bus is not None:
                async with self._bus.exclusive(self.transaction_timeout_s) as client:
                    result = await _do_write(client)
            else:
                modbus_bus.set_response_timeout(self._client, self.transaction_timeout_s)
                result = await _do_write(self._client)
        except (ModbusException, BusConnectionError) as e:
            raise WriteError(f"Modbus write failed: {e}") from e

        if result.isError():
            raise WriteError(f"Modbus write error: {result}")

        return True
