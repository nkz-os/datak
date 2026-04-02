"""Abstract base class for async protocol drivers."""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


class DriverError(Exception):
    """Base exception for driver errors."""

    pass


class ConnectionError(DriverError):
    """Connection-related errors."""

    pass


class ReadError(DriverError):
    """Read operation errors."""

    pass


class WriteError(DriverError):
    """Write operation errors."""

    pass


class BaseDriver(ABC):
    """
    Abstract base class for async protocol drivers.

    All drivers must implement:
        - connect(): Establish connection to device
        - disconnect(): Close connection
        - read(): Read current value from device
        - write(): Write value to device (optional, can raise NotImplementedError)

    The driver manages its own polling loop and error handling.
    """

    def __init__(
        self,
        sensor_id: int,
        sensor_name: str,
        config: dict[str, Any],
        poll_interval_ms: int = 1000,
        timeout_ms: int = 5000,
        retry_count: int = 3,
    ):
        self.sensor_id = sensor_id
        self.sensor_name = sensor_name
        self.config = config
        self.poll_interval_ms = poll_interval_ms
        self.timeout_ms = timeout_ms
        self.retry_count = retry_count

        self._running = False
        self._connected = False
        self._task: asyncio.Task[None] | None = None
        self._error_count = 0
        self._last_value: float | None = None
        self._last_read_time: datetime | None = None

        # Callbacks
        self._on_value: Callable[[int, float, float | None, datetime], Awaitable[None]] | None = (
            None
        )
        self._on_error: Callable[[int, str], Awaitable[None]] | None = None
        self._on_status_change: Callable[[int, str], Awaitable[None]] | None = None

        self._log = logger.bind(
            driver=self.__class__.__name__,
            sensor_id=sensor_id,
            sensor_name=sensor_name,
        )

    # ─────────────────────────────────────────────────────────────
    # Abstract Methods (must be implemented by subclasses)
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the device.

        Returns:
            True if connection successful, False otherwise.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the device."""
        ...

    @abstractmethod
    async def read(self) -> float:
        """
        Read the current raw value from the device.

        Returns:
            The raw sensor value (before formula transformation).

        Raises:
            ReadError: If read fails.
        """
        ...

    async def write(self, value: float) -> bool:
        """
        Write a value to the device (for bidirectional control).

        Args:
            value: The value to write.

        Returns:
            True if write successful.

        Raises:
            WriteError: If write fails.
            NotImplementedError: If driver doesn't support writing.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support write operations")

    # ─────────────────────────────────────────────────────────────
    # Lifecycle Methods
    # ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the driver polling loop."""
        if self._running:
            self._log.warning("Driver already running")
            return

        self._log.info("Starting driver")
        self._running = True
        self._error_count = 0

        # Try initial connection
        try:
            self._connected = await self.connect()
            if self._connected:
                self._log.info("Connected successfully")
                await self._notify_status("ONLINE")
            else:
                self._log.warning("Initial connection failed, will retry")
                await self._notify_status("ERROR")
        except Exception as e:
            self._log.error("Connection error", error=str(e))
            await self._notify_status("ERROR")

        # Start polling task
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the driver and close connections."""
        if not self._running:
            return

        self._log.info("Stopping driver")
        self._running = False

        if self._task:
            self._task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5.0)

        try:
            await self.disconnect()
        except Exception as e:
            self._log.error("Error during disconnect", error=str(e))

        self._connected = False
        await self._notify_status("OFFLINE")

    async def restart(self) -> None:
        """Restart the driver (hot-reload friendly)."""
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()

    # ─────────────────────────────────────────────────────────────
    # Callback Registration
    # ─────────────────────────────────────────────────────────────

    def on_value(
        self,
        callback: Callable[[int, float, float | None, datetime], Awaitable[None]],
    ) -> None:
        """
        Register callback for new values.

        Callback signature: (sensor_id, processed_value, raw_value, timestamp)
        """
        self._on_value = callback

    def on_error(self, callback: Callable[[int, str], Awaitable[None]]) -> None:
        """
        Register callback for errors.

        Callback signature: (sensor_id, error_message)
        """
        self._on_error = callback

    def on_status_change(self, callback: Callable[[int, str], Awaitable[None]]) -> None:
        """
        Register callback for status changes.

        Callback signature: (sensor_id, new_status)
        """
        self._on_status_change = callback

    # ─────────────────────────────────────────────────────────────
    # Internal Methods
    # ─────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Main polling loop with error handling and retry logic."""
        while self._running:
            try:
                # Reconnect if needed
                if not self._connected:
                    self._connected = await self._try_reconnect()
                    if not self._connected:
                        await asyncio.sleep(self.poll_interval_ms / 1000)
                        continue

                # Read value with timeout
                try:
                    raw_value = await asyncio.wait_for(
                        self.read(),
                        timeout=self.timeout_ms / 1000,
                    )

                    # Success - reset error count
                    self._error_count = 0
                    self._last_value = raw_value
                    self._last_read_time = datetime.utcnow()

                    # Notify callback
                    if self._on_value:
                        await self._on_value(
                            self.sensor_id,
                            raw_value,
                            raw_value,
                            self._last_read_time,
                        )

                    await self._notify_status("ONLINE")

                except TimeoutError:
                    await self._handle_error("Read timeout")
                except ReadError as e:
                    await self._handle_error(str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.exception("Unexpected error in poll loop", error=str(e))
                await self._handle_error(f"Unexpected: {e}")

            # Wait for next poll
            await asyncio.sleep(self.poll_interval_ms / 1000)

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect to the device."""
        self._log.info("Attempting reconnection")
        try:
            return await self.connect()
        except Exception as e:
            self._log.warning("Reconnection failed", error=str(e))
            return False

    async def _handle_error(self, error_msg: str) -> None:
        """Handle read/connection errors with retry logic."""
        self._error_count += 1
        self._log.warning(
            "Error occurred",
            error=error_msg,
            error_count=self._error_count,
            retry_count=self.retry_count,
        )

        if self._on_error:
            await self._on_error(self.sensor_id, error_msg)

        if self._error_count >= self.retry_count:
            self._log.error("Max retries exceeded, marking offline")
            self._connected = False
            await self._notify_status("OFFLINE")

    async def _notify_status(self, status: str) -> None:
        """Notify status change callback."""
        if self._on_status_change:
            await self._on_status_change(self.sensor_id, status)

    # ─────────────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Check if driver is running."""
        return self._running

    @property
    def is_connected(self) -> bool:
        """Check if driver is connected."""
        return self._connected

    @property
    def last_value(self) -> float | None:
        """Get last read value."""
        return self._last_value

    @property
    def error_count(self) -> int:
        """Get current error count."""
        return self._error_count
