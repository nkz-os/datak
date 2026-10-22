"""CSV statistical report generator."""

import asyncio
import contextlib
import csv
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import structlog

from app.config import get_settings
from app.db.influx import influx_client
from app.db.session import async_session_factory
from app.models.sensor import Sensor

logger = structlog.get_logger()
settings = get_settings()


class CSVReportGenerator:
    """
        - Automatic file rotation
        - Compression of old files
        - Retention policy enforcement
    """

    INTERVAL_MAP: ClassVar[dict[str, timedelta]] = {
        "1min": timedelta(minutes=1),
        "5min": timedelta(minutes=5),
        "10min": timedelta(minutes=10),
        "1hour": timedelta(hours=1),
    }

    def __init__(self):
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._log = logger.bind(component="csv_engine")
        self._output_dir = settings.reports_output_dir

    async def start(self) -> None:
        """Start the CSV generation background task."""
        self._running = True

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._task = asyncio.create_task(self._generation_loop())
        self._log.info("CSV Report Generator started", output_dir=str(self._output_dir))

    async def stop(self) -> None:
        """Stop the CSV generator."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._log.info("CSV Report Generator stopped")

    async def generate_report(
        self,
        interval: str = "5min",
        lookback_minutes: int | None = None,
    ) -> Path | None:
        """
        Generate a statistical report for all sensors.

        Args:
            interval: Aggregation window (1min, 5min, 10min, 1hour)
            lookback_minutes: Time range to query (default: 2x interval)

        Returns:
            Path to generated CSV file, or None if failed.
        """
        if interval not in self.INTERVAL_MAP:
            self._log.error("Invalid interval", interval=interval)
            return None

        if lookback_minutes is None:
            # Default: look back 2x the interval
            lookback_minutes = int(self.INTERVAL_MAP[interval].total_seconds() / 60) * 2

        try:
            # Get active sensors
            async with async_session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Sensor)
                    .where(Sensor.is_active == True)  # noqa: E712
                    .where(Sensor.deleted_at == None)  # noqa: E711
                )
                sensors = list(result.scalars().all())

            if not sensors:
                self._log.warning("No active sensors found")
                return None

            # Query statistics for each sensor
            now = datetime.utcnow()
            now - timedelta(minutes=lookback_minutes)

            rows: list[dict[str, Any]] = []

            for sensor in sensors:
                stats = await influx_client.query_statistics(
                    sensor_name=sensor.name,
                    start=f"-{lookback_minutes}m",
                    stop="now()",
                )

                if stats.get("count"):
                    rows.append({
                        "timestamp": now.isoformat(),
                        "sensor_id": sensor.id,
                        "sensor_name": sensor.name,
                        "unit": sensor.unit or "",
                        "interval": interval,
                        "count": stats.get("count"),
                        "mean": round(stats.get("mean") or 0, 4),
                        "min": round(stats.get("min") or 0, 4),
                        "max": round(stats.get("max") or 0, 4),
                        "stddev": round(stats.get("stddev") or 0, 4),
                    })

            if not rows:
                self._log.debug("No data to report")
                return None

            # Generate filename
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H%M")
            filename = f"report_{interval}_{date_str}_{time_str}.csv"
            filepath = self._output_dir / filename

            # Write CSV
            df = pd.DataFrame(rows)
            df.to_csv(filepath, index=False, quoting=csv.QUOTE_NONNUMERIC)

            self._log.info(
                "Report generated",
                file=filename,
                sensors=len(rows),
                interval=interval,
            )
            return filepath

        except Exception as e:
            self._log.exception("Report generation failed", error=str(e))
            return None

    async def generate_daily_summary(self, date: datetime | None = None) -> Path | None:
        """
        Generate a daily summary report with all intervals.

        Args:
            date: Date to summarize (default: yesterday)
        """
        if date is None:
            date = datetime.utcnow() - timedelta(days=1)

        date_str = date.strftime("%Y-%m-%d")
        start = f"{date_str}T00:00:00Z"
        stop = f"{date_str}T23:59:59Z"

        try:
            # Get all sensors
            async with async_session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(select(Sensor))
                sensors = list(result.scalars().all())

            rows: list[dict[str, Any]] = []

            for sensor in sensors:
                # Query full day stats
                data = await influx_client.query_sensor_data(
                    sensor_name=sensor.name,
                    start=start,
                    stop=stop,
                )

                if data:
                    values = [d["value"] for d in data if d.get("value") is not None]
                    if values:
                        rows.append({
                            "date": date_str,
                            "sensor_id": sensor.id,
                            "sensor_name": sensor.name,
                            "unit": sensor.unit or "",
                            "count": len(values),
                            "mean": round(sum(values) / len(values), 4),
                            "min": round(min(values), 4),
                            "max": round(max(values), 4),
                            "stddev": round(pd.Series(values).std(), 4) if len(values) > 1 else 0,
                        })

            if not rows:
                return None

            filename = f"daily_summary_{date_str}.csv"
            filepath = self._output_dir / filename

            df = pd.DataFrame(rows)
            df.to_csv(filepath, index=False, quoting=csv.QUOTE_NONNUMERIC)

            self._log.info("Daily summary generated", file=filename, sensors=len(rows))
            return filepath

        except Exception as e:
            self._log.exception("Daily summary failed", error=str(e))
            return None

    async def compress_old_files(self, days: int = 7) -> int:
        """Compress CSV files older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        compressed = 0

        try:
            for file in self._output_dir.glob("*.csv"):
                mtime = datetime.fromtimestamp(file.stat().st_mtime)
                if mtime < cutoff:
                    gz_path = file.with_suffix(".csv.gz")
                    with open(file, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    file.unlink()
                    compressed += 1

            if compressed > 0:
                self._log.info("Compressed old files", count=compressed)
            return compressed

        except Exception as e:
            self._log.error("Compression failed", error=str(e))
            return 0

    async def cleanup_old_files(self, days: int = 365) -> int:
        """Delete files older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = 0

        try:
            for pattern in ["*.csv", "*.csv.gz"]:
                for file in self._output_dir.glob(pattern):
                    mtime = datetime.fromtimestamp(file.stat().st_mtime)
                    if mtime < cutoff:
                        file.unlink()
                        deleted += 1

            if deleted > 0:
                self._log.info("Deleted old files", count=deleted)
            return deleted

        except Exception as e:
            self._log.error("Cleanup failed", error=str(e))
            return 0

    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        """List available report files."""
        files = []

        for pattern in ["*.csv", "*.csv.gz"]:
            for file in self._output_dir.glob(pattern):
                stat = file.stat()
                files.append({
                    "name": file.name,
                    "path": str(file),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "compressed": file.suffix == ".gz",
                })

        # Sort by modified time, newest first
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files[:limit]

    async def _generation_loop(self) -> None:
        """Background task for periodic report generation."""
        # Generate reports at different intervals
        last_1min = datetime.utcnow()
        last_5min = datetime.utcnow()
        last_1hour = datetime.utcnow()
        last_daily = datetime.utcnow().date()

        while self._running:
            try:
                now = datetime.utcnow()

                # 1-minute reports
                if (now - last_1min).total_seconds() >= 60:
                    await self.generate_report("1min")
                    last_1min = now

                # 5-minute reports
                if (now - last_5min).total_seconds() >= 300:
                    await self.generate_report("5min")
                    last_5min = now

                # Hourly reports
                if (now - last_1hour).total_seconds() >= 3600:
                    await self.generate_report("1hour")
                    last_1hour = now

                    # Also run maintenance tasks hourly
                    await self.compress_old_files()
                    await self.cleanup_old_files()

                # Daily summary (at midnight)
                if now.date() > last_daily:
                    await self.generate_daily_summary()
                    last_daily = now.date()

            except Exception as e:
                self._log.error("Generation loop error", error=str(e))

            # Sleep for 30 seconds between checks
            await asyncio.sleep(30)


# Global instance
csv_generator = CSVReportGenerator()
