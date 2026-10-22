"""Virtual Output driver for automation rule targets.

This driver stores values written by automation rules and makes them
available for display in the Dashboard and storage in InfluxDB.
"""

from datetime import datetime
from typing import Any

from app.db.influx import influx_client
from app.db.session import async_session_factory
from app.drivers.base import BaseDriver
from app.models.sensor import Sensor


class VirtualOutputDriver(BaseDriver):
    """
    Driver for virtual output sensors used as automation rule targets.

    This driver:
    - Does not poll external sources (read returns last written value)
    - Accepts writes from automation engine
    - Stores value in memory and writes directly to InfluxDB
    - Updates SQLite sensor record for UI visibility
    - Does NOT trigger automation callbacks to prevent recursion

    Configuration:
        {
            "initial_value": 0  # Optional default value
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
        self._current_value: float = config.get("initial_value", 0.0)
        self._last_write_time: datetime | None = None

    async def connect(self) -> bool:
        """Virtual output is always connected."""
        self._log.info("Virtual output ready", sensor_name=self.sensor_name)
        return True

    async def disconnect(self) -> None:
        """Nothing to disconnect for virtual output."""
        pass

    async def read(self) -> float:
        """Return the last written value."""
        return self._current_value

    async def write(self, value: float) -> bool:
        """
        Store the value and write directly to InfluxDB.

        This is called by the automation engine when a rule triggers.
        We write directly to InfluxDB to avoid triggering automation
        callbacks which would cause infinite recursion.
        """
        self._current_value = value
        self._last_write_time = datetime.utcnow()
        self._log.info(
            "Virtual output updated",
            sensor_name=self.sensor_name,
            value=value,
        )

        # Write to InfluxDB for time-series history
        try:
            await influx_client.write_sensor_value(
                sensor_id=self.sensor_id,
                sensor_name=self.sensor_name,
                value=value,
                raw_value=value,
                timestamp=self._last_write_time,
            )
        except Exception as e:
            self._log.warning("Failed to write to InfluxDB", error=str(e))

        # Update SQLite sensor record for UI/API visibility
        try:
            async with async_session_factory() as session:
                sensor = await session.get(Sensor, self.sensor_id)
                if sensor:
                    sensor.mark_online(value, value)
                    await session.commit()
        except Exception as e:
            self._log.warning("Failed to update sensor in DB", error=str(e))

        # Update in-memory last_value
        self._last_value = value

        return True

    async def _poll_loop(self) -> None:
        """
        Override poll loop - virtual outputs don't poll.
        They only update when written to by automation.
        """
        import asyncio

        # Just keep the driver "running" but don't poll
        while self._running:
            await asyncio.sleep(1)
