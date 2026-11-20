"""CSV statistical report generator."""

import asyncio
import contextlib
import csv
import gzip
import shutil
from datetime import UTC, datetime, timedelta
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



REPORT_FIELDS = ["timestamp", "window_start", "sensor_id", "sensor_name", "register_name", "unit",
                 "interval", "count", "mean", "min", "max", "stddev"]
DAILY_FIELDS = ["date", "sensor_id", "sensor_name", "register_name", "unit",
                "count", "mean", "min", "max", "stddev"]
REPORT_SETTLE = timedelta(seconds=15)


def _iso(ts: datetime) -> str:
    """UTC timestamp with an explicit Z (the old reports wrote naive times)."""
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_bounds(now: datetime, interval: timedelta) -> tuple[datetime, datetime]:
    """The latest closed window [start, end) at ``now``, aligned to the UTC clock."""
    seconds = int(interval.total_seconds())
    ts = int((now if now.tzinfo else now.replace(tzinfo=UTC)).timestamp())
    end = datetime.fromtimestamp(ts - ts % seconds, UTC)
    return end - interval, end


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def register_rows(
    sensors: list[Sensor],
    stats: dict[str, dict[str | None, dict[str, float | None]]],
) -> list[dict[str, Any]]:
    """One row per sensor and register that has readings, with the register's unit.

    A register found in the data but no longer configured keeps its name with an
    empty unit; readings without a register tag use the sensor's unit.
    """
    rows: list[dict[str, Any]] = []
    for sensor in sensors:
        units = {r.name: r.unit or "" for r in sensor.registers}
        by_register = stats.get(sensor.name) or {}
        for register in sorted(by_register, key=lambda r: (r is not None, r or "")):
            st = by_register[register]
            if not st.get("count"):
                continue
            rows.append({
                "sensor_id": sensor.id,
                "sensor_name": sensor.name,
                "register_name": register or "",
                "unit": units.get(register, "") if register else (sensor.unit or ""),
                "count": int(st["count"] or 0),
                "mean": _round(st.get("mean")),
                "min": _round(st.get("min")),
                "max": _round(st.get("max")),
                "stddev": _round(st.get("stddev")),
            })
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    df = pd.DataFrame(rows, columns=fields)
    df.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


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
        now: datetime | None = None,
    ) -> Path | None:
        """
        Write the statistics of the latest closed window of ``interval``.

        The window is aligned to the clock, [end - interval, end) in UTC, so
        consecutive reports neither overlap nor leave gaps. One row per sensor
        and register, with the register's own unit. The file name carries the
        window end, so writing the same window twice gives the same file.

        Returns:
            Path to the generated CSV file, or None if there was nothing to write.
        """
        if interval not in self.INTERVAL_MAP:
            self._log.error("Invalid interval", interval=interval)
            return None

        start, end = window_bounds(now or datetime.now(UTC), self.INTERVAL_MAP[interval])
        try:
            sensors = await self._load_sensors(active_only=True)
            if not sensors:
                self._log.warning("No active sensors found")
                return None

            stats = {
                sensor.name: await influx_client.query_register_statistics(
                    sensor_name=sensor.name, start=_iso(start), stop=_iso(end),
                )
                for sensor in sensors
            }
            rows = [
                {"timestamp": _iso(end), "window_start": _iso(start), **row,
                 "interval": interval}
                for row in register_rows(sensors, stats)
            ]
            if not rows:
                self._log.debug("No data to report", interval=interval, window_end=_iso(end))
                return None

            filename = f"report_{interval}_{end.strftime('%Y-%m-%d_%H%M')}.csv"
            filepath = self._output_dir / filename
            _write_csv(filepath, rows, REPORT_FIELDS)

            self._log.info("Report generated", file=filename, rows=len(rows), interval=interval)
            return filepath

        except Exception as e:
            self._log.exception("Report generation failed", error=str(e))
            return None

    async def generate_daily_summary(self, date: datetime | None = None) -> Path | None:
        """
        Write the statistics of one UTC day, one row per sensor and register.

        Args:
            date: Day to summarize (default: yesterday, UTC)
        """
        day = (date or datetime.now(UTC) - timedelta(days=1)).date()
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        try:
            sensors = await self._load_sensors(active_only=False)
            stats = {
                sensor.name: await influx_client.query_register_statistics(
                    sensor_name=sensor.name, start=_iso(start), stop=_iso(end),
                )
                for sensor in sensors
            }
            rows = [{"date": day.isoformat(), **row} for row in register_rows(sensors, stats)]
            if not rows:
                return None

            filepath = self._output_dir / f"daily_summary_{day.isoformat()}.csv"
            _write_csv(filepath, rows, DAILY_FIELDS)

            self._log.info("Daily summary generated", file=filepath.name, rows=len(rows))
            return filepath

        except Exception as e:
            self._log.exception("Daily summary failed", error=str(e))
            return None

    async def _load_sensors(self, active_only: bool) -> list[Sensor]:
        async with async_session_factory() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            query = select(Sensor).options(selectinload(Sensor.registers))
            if active_only:
                query = query.where(Sensor.is_active == True)  # noqa: E712
            query = query.where(Sensor.deleted_at == None)  # noqa: E711
            result = await session.execute(query)
            return list(result.scalars().all())

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
        """Write each interval's report once its clock-aligned window has closed."""
        last_end: dict[str, datetime] = {}
        last_daily = datetime.now(UTC).date()

        while self._running:
            try:
                # Readings reach InfluxDB through a buffer: wait a little past
                # the window end so the last seconds are in.
                now = datetime.now(UTC) - REPORT_SETTLE
                for interval in ("1min", "5min", "1hour"):
                    _, end = window_bounds(now, self.INTERVAL_MAP[interval])
                    if last_end.get(interval) == end:
                        continue
                    await self.generate_report(interval, now=now)
                    last_end[interval] = end
                    if interval == "1hour":
                        await self.compress_old_files()
                        await self.cleanup_old_files()

                if now.date() > last_daily:
                    await self.generate_daily_summary(now - timedelta(days=1))
                    last_daily = now.date()

            except Exception as e:
                self._log.error("Generation loop error", error=str(e))

            await asyncio.sleep(10)


# Global instance
csv_generator = CSVReportGenerator()
