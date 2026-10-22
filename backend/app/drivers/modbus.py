"""Modbus TCP/RTU async driver using pymodbus."""

import asyncio
from typing import Any

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from app.drivers.base import BaseDriver, ConnectionError, ReadError, WriteError

# Sentinel to group contiguous registers for batch reading
_MAX_GAP = 16  # max gap between registers to batch them together


# Shared client instances and locks for Modbus RTU and TCP gateways (multi-drop bus)
_SHARED_SERIAL_CLIENTS: dict[str, AsyncModbusSerialClient] = {}
_SHARED_SERIAL_LOCKS: dict[str, asyncio.Lock] = {}
_SHARED_TCP_CLIENTS: dict[str, AsyncModbusTcpClient] = {}
_SHARED_TCP_LOCKS: dict[str, asyncio.Lock] = {}


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

        # Multi-register support
        self._registers: list[dict[str, Any]] = registers or []

        self._client: AsyncModbusTcpClient | AsyncModbusSerialClient | None = None
        self._serial_lock: asyncio.Lock | None = None

    async def connect(self) -> bool:
        """Establish Modbus connection."""
        try:
            if self.mode == "tcp":
                host = self.config.get("host", "localhost")
                port = self.config.get("port", 502)

                tcp_key = f"{host}_{port}"
                if tcp_key not in _SHARED_TCP_LOCKS:
                    _SHARED_TCP_LOCKS[tcp_key] = asyncio.Lock()
                self._serial_lock = _SHARED_TCP_LOCKS[tcp_key]

                async with self._serial_lock:
                    if tcp_key in _SHARED_TCP_CLIENTS and _SHARED_TCP_CLIENTS[tcp_key].connected:
                        self._client = _SHARED_TCP_CLIENTS[tcp_key]
                        return True

                    client = AsyncModbusTcpClient(host=host, port=port, timeout=1.0)
                    self._log.info("Connecting to Modbus TCP", host=host, port=port)
                    connected = await client.connect()
                    if connected:
                        _SHARED_TCP_CLIENTS[tcp_key] = client
                        self._client = client
                    return connected

            elif self.mode == "rtu":
                serial_port = self.config.get("port", "/dev/ttyUSB0")
                baudrate = self.config.get("baudrate", 9600)
                parity = self.config.get("parity", "N")
                stopbits = self.config.get("stopbits", 1)
                bytesize = self.config.get("bytesize", 8)

                port_key = f"{serial_port}_{baudrate}_{parity}_{stopbits}_{bytesize}"
                if port_key not in _SHARED_SERIAL_LOCKS:
                    _SHARED_SERIAL_LOCKS[port_key] = asyncio.Lock()
                self._serial_lock = _SHARED_SERIAL_LOCKS[port_key]

                async with self._serial_lock:
                    if port_key in _SHARED_SERIAL_CLIENTS and _SHARED_SERIAL_CLIENTS[port_key].connected:
                        self._client = _SHARED_SERIAL_CLIENTS[port_key]
                        return True

                    client = AsyncModbusSerialClient(
                        port=serial_port,
                        baudrate=baudrate,
                        parity=parity,
                        stopbits=stopbits,
                        bytesize=bytesize,
                        timeout=0.5,
                    )
                    self._log.info(
                        "Connecting to Modbus RTU",
                        port=serial_port,
                        baudrate=baudrate,
                    )
                    connected = await client.connect()
                    if connected:
                        _SHARED_SERIAL_CLIENTS[port_key] = client
                        self._client = client
                    return connected
            else:
                raise ConnectionError(f"Unknown Modbus mode: {self.mode}")

        except Exception as e:
            self._log.error("Modbus connection failed", error=str(e))
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def disconnect(self) -> None:
        """Close Modbus connection."""
        self._client = None

    async def read(self) -> float:
        """Read single register value from Modbus device (legacy, single-register mode)."""
        if not self._client:
            raise ReadError("Not connected")

        try:
            return await self._read_register(
                self.register_type, self.address, self.count
            )
        except ModbusException as e:
            raise ReadError(f"Modbus read failed: {e}") from e

    async def _read_register(
        self, reg_type: str, address: int, count: int
    ) -> float:
        """Read a single register block and return the decoded float."""
        if not self._client:
            raise ReadError("Not connected")

        # Pymodbus 3.15+ uses device_id, earlier versions use slave
        unit_kwarg = {"slave": self.slave_id}
        method = getattr(self._client, f"read_{reg_type}_registers", None) or getattr(self._client, "read_holding_registers", None)
        if method:
            try:
                import inspect
                sig = inspect.signature(method)
                if "device_id" in sig.parameters and "slave" not in sig.parameters:
                    unit_kwarg = {"device_id": self.slave_id}
            except Exception:
                pass

        async def _do_read():
            if reg_type == "holding":
                return await self._client.read_holding_registers(
                    address=address, count=count, **unit_kwarg,
                )
            elif reg_type == "input":
                return await self._client.read_input_registers(
                    address=address, count=count, **unit_kwarg,
                )
            elif reg_type == "coil":
                return await self._client.read_coils(
                    address=address, count=count, **unit_kwarg,
                )
            elif reg_type == "discrete":
                return await self._client.read_discrete_inputs(
                    address=address, count=count, **unit_kwarg,
                )
            else:
                raise ReadError(f"Unknown register type: {reg_type}")

        if self._serial_lock:
            async with self._serial_lock:
                result = await _do_read()
        else:
            result = await _do_read()

        if result.isError():
            raise ReadError(f"Modbus error: {result}")

        if reg_type in ("coil", "discrete"):
            return float(result.bits[0])
        elif count == 1:
            return float(result.registers[0])
        elif count == 2:
            high = result.registers[0]
            low = result.registers[1]
            return float((high << 16) | low)
        else:
            return float(result.registers[0])

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

        # Group registers by type, then by contiguous address blocks
        by_type: dict[str, list[dict[str, Any]]] = {}
        for reg in self._registers:
            rt = reg.get("register_type", "holding")
            by_type.setdefault(rt, []).append(reg)

        for reg_type, regs in by_type.items():
            regs_sorted = sorted(regs, key=lambda r: r["address"])

            # Group into contiguous blocks
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

            # Read each block in one Modbus transaction
            for block in blocks:
                start_addr = block[0]["address"]
                end_reg = block[-1]
                total_count = (end_reg["address"] - start_addr) + end_reg.get("count", 1)

                try:
                    raw_registers = await self._read_raw_block(
                        reg_type, start_addr, total_count
                    )
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
        """Read a block of registers and return raw int values."""
        if not self._client:
            raise ReadError("Not connected")

        # Pymodbus 3.15+ uses device_id, earlier versions use slave
        unit_kwarg = {"slave": self.slave_id}
        method = getattr(self._client, f"read_{reg_type}_registers", None) or getattr(self._client, "read_holding_registers", None)
        if method:
            try:
                import inspect
                sig = inspect.signature(method)
                if "device_id" in sig.parameters and "slave" not in sig.parameters:
                    unit_kwarg = {"device_id": self.slave_id}
            except Exception:
                pass

        async def _do_raw_read():
            if reg_type == "holding":
                return await self._client.read_holding_registers(
                    address=address, count=count, **unit_kwarg,
                )
            elif reg_type == "input":
                return await self._client.read_input_registers(
                    address=address, count=count, **unit_kwarg,
                )
            elif reg_type == "coil":
                res = await self._client.read_coils(
                    address=address, count=count, **unit_kwarg,
                )
                return [int(b) for b in res.bits] if not res.isError() else res
            elif reg_type == "discrete":
                res = await self._client.read_discrete_inputs(
                    address=address, count=count, **unit_kwarg,
                )
                return [int(b) for b in res.bits] if not res.isError() else res
            else:
                raise ReadError(f"Unknown register type: {reg_type}")

        if self._serial_lock:
            async with self._serial_lock:
                result = await _do_raw_read()
        else:
            result = await _do_raw_read()

        if isinstance(result, list):
            return result

        if result.isError():
            raise ReadError(f"Modbus error: {result}")

        return list(result.registers)

    def set_registers(self, registers: list[dict[str, Any]]) -> None:
        """Update the register configuration (hot-reload)."""
        self._registers = registers

    async def write(self, value: float) -> bool:
        """Write value to Modbus register."""
        if not self._client:
            raise WriteError("Not connected")

        # Pymodbus 3.15+ uses device_id, earlier versions use slave
        unit_kwarg = {"slave": self.slave_id}
        if hasattr(self._client, "write_register"):
            try:
                import inspect
                sig = inspect.signature(self._client.write_register)
                if "device_id" in sig.parameters and "slave" not in sig.parameters:
                    unit_kwarg = {"device_id": self.slave_id}
            except Exception:
                pass

        try:
            int_value = int(value)

            async def _do_write():
                if self.register_type == "holding":
                    return await self._client.write_register(
                        address=self.address,
                        value=int_value,
                        **unit_kwarg,
                    )
                elif self.register_type == "coil":
                    return await self._client.write_coil(
                        address=self.address,
                        value=bool(int_value),
                        **unit_kwarg,
                    )
                else:
                    raise WriteError(f"Cannot write to {self.register_type} registers")

            if self._serial_lock:
                async with self._serial_lock:
                    result = await _do_write()
            else:
                result = await _do_write()

            if result.isError():
                raise WriteError(f"Modbus write error: {result}")

            return True

        except ModbusException as e:
            raise WriteError(f"Modbus write failed: {e}") from e
