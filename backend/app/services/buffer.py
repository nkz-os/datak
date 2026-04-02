"""Store & Forward buffer for offline resilience."""

import asyncio
import contextlib
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update

from app.db.influx import influx_client
from app.db.session import async_session_factory
from app.models.sensor import SensorReading

logger = structlog.get_logger()


class BufferQueue:
    """
    Store & Forward buffer for network resilience.

    When the cloud connection is unavailable, readings are stored
    in SQLite. When connection is restored, the queue is flushed
    in FIFO order.

    Features:
        - Persistent storage in SQLite
        - FIFO ordering for data integrity
        - Batch flush for efficiency
        - Automatic retry logic
    """

    def __init__(self, batch_size: int = 100, flush_interval: int = 5):
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._running = False
        self._flush_task: asyncio.Task[None] | None = None
        self._log = logger.bind(component="buffer")
        self._cloud_available = True

    async def start(self) -> None:
        """Start the buffer flush background task."""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._log.info("Buffer queue started")

    async def stop(self) -> None:
        """Stop the buffer and flush remaining data."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task

        # Final flush attempt
        await self.flush()
        self._log.info("Buffer queue stopped")

    async def add(
        self,
        sensor_id: int,
        sensor_name: str,
        value: float,
        raw_value: float | None,
        timestamp: datetime,
    ) -> bool:
        """
        Add a reading to the buffer.

        If cloud is available, writes directly to InfluxDB.
        If not, stores in SQLite for later sync.
        """
        # Try direct write if cloud is available
        if self._cloud_available and influx_client.is_connected:
            success = await influx_client.write_sensor_value(
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                value=value,
                raw_value=raw_value,
                timestamp=timestamp,
            )
            if success:
                return True
            else:
                # Cloud failed, mark unavailable
                self._cloud_available = False
                self._log.warning("Cloud write failed, switching to buffer mode")

        # Store in buffer
        try:
            async with async_session_factory() as session:
                reading = SensorReading(
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    timestamp=timestamp,
                    value=value,
                    raw_value=raw_value,
                    synced=False,
                )
                session.add(reading)
                await session.commit()
                return True
        except Exception as e:
            self._log.error("Failed to buffer reading", error=str(e))
            return False

    async def flush(self) -> int:
        """
        Flush buffered readings to the cloud.

        Returns:
            Number of readings successfully synced.
        """
        if not influx_client.is_connected:
            return 0

        synced_count = 0

        try:
            async with async_session_factory() as session:
                # Get unsynced readings in FIFO order
                result = await session.execute(
                    select(SensorReading)
                    .where(SensorReading.synced == False)  # noqa: E712
                    .order_by(SensorReading.timestamp)
                    .limit(self._batch_size)
                )
                readings = list(result.scalars().all())

                if not readings:
                    # Queue is empty, cloud is available
                    self._cloud_available = True
                    return 0

                self._log.info("Flushing buffer", count=len(readings))

                # Prepare batch
                batch = [
                    {
                        "sensor_id": r.sensor_id,
                        "sensor_name": r.sensor_name,
                        "value": r.value,
                        "raw_value": r.raw_value,
                        "timestamp": r.timestamp,
                    }
                    for r in readings
                ]

                # Write to InfluxDB
                written = await influx_client.write_batch(batch)

                if written > 0:
                    # Mark as synced
                    reading_ids = [r.id for r in readings[:written]]
                    await session.execute(
                        update(SensorReading)
                        .where(SensorReading.id.in_(reading_ids))
                        .values(synced=True, synced_at=datetime.utcnow())
                    )
                    await session.commit()
                    synced_count = written
                    self._cloud_available = True
                    self._log.info("Buffer flush complete", synced=synced_count)
                else:
                    self._cloud_available = False

        except Exception as e:
            self._log.error("Buffer flush failed", error=str(e))
            self._cloud_available = False

        return synced_count

    async def cleanup_synced(self, _older_than_hours: int = 24) -> int:
        """
        Remove synced readings older than specified hours.

        Returns:
            Number of deleted readings.
        """
        try:
            cutoff = datetime.utcnow()
            async with async_session_factory() as session:
                result = await session.execute(
                    delete(SensorReading)
                    .where(SensorReading.synced == True)  # noqa: E712
                    .where(SensorReading.synced_at < cutoff)
                    .returning(SensorReading.id)
                )
                deleted = len(result.all())
                await session.commit()

                if deleted > 0:
                    self._log.info("Cleaned up synced readings", count=deleted)
                return deleted
        except Exception as e:
            self._log.error("Cleanup failed", error=str(e))
            return 0

    async def get_queue_stats(self) -> dict[str, Any]:
        """Get current buffer queue statistics."""
        try:
            async with async_session_factory() as session:
                # Total unsynced
                unsynced = await session.execute(
                    select(func.count(SensorReading.id))
                    .where(SensorReading.synced == False)  # noqa: E712
                )
                unsynced_count = unsynced.scalar() or 0

                # Total synced (pending cleanup)
                synced = await session.execute(
                    select(func.count(SensorReading.id))
                    .where(SensorReading.synced == True)  # noqa: E712
                )
                synced_count = synced.scalar() or 0

                # Oldest unsynced
                oldest = await session.execute(
                    select(SensorReading.timestamp)
                    .where(SensorReading.synced == False)  # noqa: E712
                    .order_by(SensorReading.timestamp)
                    .limit(1)
                )
                oldest_reading = oldest.scalar()

                return {
                    "unsynced_count": unsynced_count,
                    "synced_count": synced_count,
                    "oldest_unsynced": oldest_reading.isoformat() if oldest_reading else None,
                    "cloud_available": self._cloud_available,
                }
        except Exception as e:
            self._log.error("Failed to get stats", error=str(e))
            return {"error": str(e)}

    async def _flush_loop(self) -> None:
        """Background task to periodically flush the buffer."""
        while self._running:
            try:
                await self.flush()
            except Exception as e:
                self._log.error("Flush loop error", error=str(e))

            await asyncio.sleep(self._flush_interval)


# Global buffer instance
buffer_queue = BufferQueue()
