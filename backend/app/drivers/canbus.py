"""CANbus driver with DBC file parsing using python-can and cantools."""

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import can
import cantools

from app.drivers.base import BaseDriver, ConnectionError, ReadError


class CANDriver(BaseDriver):
    """
    Async CANbus driver using python-can with DBC decoding via cantools.

    Configuration:
        {
            "interface": "socketcan",
            "channel": "can0",
            "bitrate": 500000,
            "arbitration_id": "0x123",
            "dbc_file": "configs/dbc/motor.dbc",
            "signal_name": "Engine_RPM",
            "message_name": "EngineStatus"  # Optional, helps filter
        }

    For testing without hardware, use "virtual" interface:
        {
            "interface": "virtual",
            "channel": "vcan0",
            ...
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

        self.interface = config.get("interface", "socketcan")
        self.channel = config.get("channel", "can0")
        self.bitrate = config.get("bitrate", 500000)
        self.arbitration_id_str = config.get("arbitration_id", "0x0")
        self.arbitration_id = int(self.arbitration_id_str, 16)
        self.dbc_file = config.get("dbc_file")
        self.signal_name = config.get("signal_name")
        self.message_name = config.get("message_name")

        self._bus: can.Bus | None = None
        self._dbc: cantools.db.Database | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._last_signal_value: float | None = None

    async def connect(self) -> bool:
        """Initialize CAN bus and load DBC file."""
        try:
            # Load DBC file if specified
            if self.dbc_file:
                dbc_path = Path(self.dbc_file)
                if dbc_path.exists():
                    self._dbc = cantools.database.load_file(str(dbc_path))
                    self._log.info("Loaded DBC file", path=self.dbc_file)
                else:
                    self._log.warning("DBC file not found", path=self.dbc_file)

            # Initialize CAN bus
            self._bus = can.Bus(
                interface=self.interface,
                channel=self.channel,
                bitrate=self.bitrate,
            )

            self._log.info(
                "Connected to CAN bus",
                interface=self.interface,
                channel=self.channel,
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_loop())

            return True

        except Exception as e:
            self._log.error("CAN connection failed", error=str(e))
            raise ConnectionError(f"Failed to connect to CAN: {e}") from e

    async def disconnect(self) -> None:
        """Close CAN bus connection."""
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        if self._bus:
            self._bus.shutdown()
            self._bus = None

    async def read(self) -> float:
        """Return the last decoded signal value."""
        if self._last_signal_value is None:
            raise ReadError("No CAN message received yet")
        return self._last_signal_value

    async def _read_loop(self) -> None:
        """Continuously read CAN messages in background."""
        if not self._bus:
            return

        try:
            while self._running:
                # Run blocking recv in executor
                loop = asyncio.get_event_loop()
                message = await loop.run_in_executor(
                    None,
                    lambda: self._bus.recv(timeout=1.0) if self._bus else None,
                )

                if message is None:
                    continue

                # Filter by arbitration ID if specified
                if self.arbitration_id and message.arbitration_id != self.arbitration_id:
                    continue

                # Decode message
                value = self._decode_message(message)
                if value is not None:
                    self._last_signal_value = value

                    # Notify callback
                    from datetime import datetime

                    if self._on_value:
                        await self._on_value(
                            self.sensor_id,
                            value,
                            value,
                            datetime.utcnow(),
                        )

                    await self._notify_status("ONLINE")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log.error("CAN read loop error", error=str(e))

    def _decode_message(self, message: can.Message) -> float | None:
        """Decode CAN message to extract signal value."""
        # If we have DBC and signal name, use cantools to decode
        if self._dbc and self.signal_name:
            try:
                # Get message definition
                if self.message_name:
                    msg_def = self._dbc.get_message_by_name(self.message_name)
                else:
                    msg_def = self._dbc.get_message_by_frame_id(message.arbitration_id)

                # Decode message
                decoded = msg_def.decode(message.data)

                # Get signal value
                if self.signal_name in decoded:
                    return float(decoded[self.signal_name])
                else:
                    self._log.warning(
                        "Signal not found in message",
                        signal=self.signal_name,
                        available=list(decoded.keys()),
                    )
                    return None

            except Exception as e:
                self._log.debug("DBC decode failed", error=str(e))
                return None

        # Fallback: interpret data as raw bytes
        # Return first 2 bytes as unsigned int (common format)
        if len(message.data) >= 2:
            return float(int.from_bytes(message.data[:2], byteorder="big"))
        elif len(message.data) == 1:
            return float(message.data[0])
        else:
            return None

    async def _poll_loop(self) -> None:
        """
        Override poll loop for CAN.

        CAN is message-driven via the read_loop, so just monitor status.
        """
        while self._running:
            if not self._connected:
                self._connected = await self._try_reconnect()
            await asyncio.sleep(self.poll_interval_ms / 1000)
