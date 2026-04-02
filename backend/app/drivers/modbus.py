"""Modbus TCP/RTU async driver using pymodbus."""

from typing import Any

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from app.drivers.base import BaseDriver, ConnectionError, ReadError, WriteError


class ModbusDriver(BaseDriver):
    """
    Async Modbus driver supporting both TCP and RTU modes.

    Configuration for TCP:
        {
            "mode": "tcp",
            "host": "192.168.1.10",
            "port": 502,
            "slave_id": 1,
            "address": 40001,
            "count": 1,
            "register_type": "holding"  # holding, input, coil, discrete
        }

    Configuration for RTU:
        {
            "mode": "rtu",
            "port": "/dev/ttyUSB0",
            "baudrate": 9600,
            "parity": "N",
            "stopbits": 1,
            "bytesize": 8,
            "slave_id": 1,
            "address": 40001,
            "count": 1,
            "register_type": "holding"
        }
    """

    def __init__(
        self,
        sensor_id: int,
        sensor_name: str,
        config: dict[str, Any],
        **kwargs: Any,
    ):
        super().__init__(sensor_id, sensor_name, config, **kwargs)

        self.mode = config.get("mode", "tcp")
        self.slave_id = config.get("slave_id", 1)
        self.address = config.get("address", 0)
        self.count = config.get("count", 1)
        self.register_type = config.get("register_type", "holding")

        self._client: AsyncModbusTcpClient | AsyncModbusSerialClient | None = None

    async def connect(self) -> bool:
        """Establish Modbus connection."""
        try:
            if self.mode == "tcp":
                host = self.config.get("host", "localhost")
                port = self.config.get("port", 502)
                self._client = AsyncModbusTcpClient(host=host, port=port)
                self._log.info("Connecting to Modbus TCP", host=host, port=port)

            elif self.mode == "rtu":
                serial_port = self.config.get("port", "/dev/ttyUSB0")
                baudrate = self.config.get("baudrate", 9600)
                parity = self.config.get("parity", "N")
                stopbits = self.config.get("stopbits", 1)
                bytesize = self.config.get("bytesize", 8)

                self._client = AsyncModbusSerialClient(
                    port=serial_port,
                    baudrate=baudrate,
                    parity=parity,
                    stopbits=stopbits,
                    bytesize=bytesize,
                )
                self._log.info(
                    "Connecting to Modbus RTU",
                    port=serial_port,
                    baudrate=baudrate,
                )
            else:
                raise ConnectionError(f"Unknown Modbus mode: {self.mode}")

            connected = await self._client.connect()
            return connected

        except Exception as e:
            self._log.error("Modbus connection failed", error=str(e))
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def disconnect(self) -> None:
        """Close Modbus connection."""
        if self._client:
            self._client.close()
            self._client = None

    async def read(self) -> float:
        """Read register value from Modbus device."""
        if not self._client:
            raise ReadError("Not connected")

        try:
            # Read based on register type
            if self.register_type == "holding":
                result = await self._client.read_holding_registers(
                    address=self.address,
                    count=self.count,
                    slave=self.slave_id,
                )
            elif self.register_type == "input":
                result = await self._client.read_input_registers(
                    address=self.address,
                    count=self.count,
                    slave=self.slave_id,
                )
            elif self.register_type == "coil":
                result = await self._client.read_coils(
                    address=self.address,
                    count=self.count,
                    slave=self.slave_id,
                )
            elif self.register_type == "discrete":
                result = await self._client.read_discrete_inputs(
                    address=self.address,
                    count=self.count,
                    slave=self.slave_id,
                )
            else:
                raise ReadError(f"Unknown register type: {self.register_type}")

            if result.isError():
                raise ReadError(f"Modbus error: {result}")

            # Extract value
            if self.register_type in ("coil", "discrete"):
                return float(result.bits[0])
            else:
                # For multi-register values, combine them
                if self.count == 1:
                    return float(result.registers[0])
                elif self.count == 2:
                    # 32-bit value (big-endian)
                    high = result.registers[0]
                    low = result.registers[1]
                    return float((high << 16) | low)
                else:
                    # Return first register for now
                    return float(result.registers[0])

        except ModbusException as e:
            raise ReadError(f"Modbus read failed: {e}") from e

    async def write(self, value: float) -> bool:
        """Write value to Modbus register."""
        if not self._client:
            raise WriteError("Not connected")

        try:
            int_value = int(value)

            if self.register_type == "holding":
                result = await self._client.write_register(
                    address=self.address,
                    value=int_value,
                    slave=self.slave_id,
                )
            elif self.register_type == "coil":
                result = await self._client.write_coil(
                    address=self.address,
                    value=bool(int_value),
                    slave=self.slave_id,
                )
            else:
                raise WriteError(f"Cannot write to {self.register_type} registers")

            if result.isError():
                raise WriteError(f"Modbus write error: {result}")

            return True

        except ModbusException as e:
            raise WriteError(f"Modbus write failed: {e}") from e
