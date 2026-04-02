"""InfluxDB client for time-series data storage."""

from datetime import datetime
from typing import Any

import structlog
from influxdb_client import Point, WritePrecision
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.write_api_async import WriteApiAsync

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class InfluxDBClient:
    """
    Async InfluxDB client for storing sensor time-series data.

    Features:
        - Batch writes for performance
        - Automatic retry on failure
        - Query support for CSV generation
    """

    def __init__(self):
        self._client: InfluxDBClientAsync | None = None
        self._write_api: WriteApiAsync | None = None
        self._log = logger.bind(component="influxdb")
        self._connected = False

    async def connect(self) -> bool:
        """Initialize InfluxDB connection."""
        try:
            self._client = InfluxDBClientAsync(
                url=settings.influxdb_url,
                token=settings.influxdb_token,
                org=settings.influxdb_org,
            )

            # Test connection
            ready = await self._client.ping()
            if ready:
                self._write_api = self._client.write_api()
                self._connected = True
                self._log.info("Connected to InfluxDB", url=settings.influxdb_url)
                return True
            else:
                self._log.error("InfluxDB not ready")
                return False

        except Exception as e:
            self._log.error("Failed to connect to InfluxDB", error=str(e))
            return False

    async def disconnect(self) -> None:
        """Close InfluxDB connection."""
        if self._client:
            await self._client.close()
            self._client = None
            self._write_api = None
            self._connected = False

    async def write_sensor_value(
        self,
        sensor_id: int,
        sensor_name: str,
        value: float,
        raw_value: float | None = None,
        timestamp: datetime | None = None,
        tags: dict[str, str] | None = None,
    ) -> bool:
        """
        Write a sensor reading to InfluxDB.

        Args:
            sensor_id: Unique sensor identifier
            sensor_name: Human-readable sensor name
            value: Processed value (after formula)
            raw_value: Raw value from device
            timestamp: Reading timestamp (default: now)
            tags: Additional tags for filtering
        """
        if not self._connected or not self._write_api:
            self._log.warning("Not connected, cannot write")
            return False

        try:
            point = (
                Point("sensor_reading")
                .tag("sensor_id", str(sensor_id))
                .tag("sensor_name", sensor_name)
                .field("value", float(value))
            )

            if raw_value is not None:
                point = point.field("raw_value", float(raw_value))

            if tags:
                for key, val in tags.items():
                    point = point.tag(key, val)

            if timestamp:
                point = point.time(timestamp, WritePrecision.MS)

            await self._write_api.write(
                bucket=settings.influxdb_bucket,
                org=settings.influxdb_org,
                record=point,
            )
            return True

        except Exception as e:
            self._log.error("Failed to write to InfluxDB", error=str(e))
            return False

    async def write_batch(
        self,
        readings: list[dict[str, Any]],
    ) -> int:
        """
        Write multiple readings in a batch.

        Args:
            readings: List of dicts with keys: sensor_id, sensor_name, value, raw_value, timestamp

        Returns:
            Number of successfully written points
        """
        if not self._connected or not self._write_api:
            return 0

        points = []
        for reading in readings:
            point = (
                Point("sensor_reading")
                .tag("sensor_id", str(reading["sensor_id"]))
                .tag("sensor_name", reading["sensor_name"])
                .field("value", float(reading["value"]))
            )

            if reading.get("raw_value") is not None:
                point = point.field("raw_value", float(reading["raw_value"]))

            if reading.get("timestamp"):
                point = point.time(reading["timestamp"], WritePrecision.MS)

            points.append(point)

        try:
            await self._write_api.write(
                bucket=settings.influxdb_bucket,
                org=settings.influxdb_org,
                record=points,
            )
            return len(points)
        except Exception as e:
            self._log.error("Batch write failed", error=str(e), count=len(points))
            return 0

    async def query_sensor_data(
        self,
        sensor_name: str,
        start: str = "-1h",
        stop: str = "now()",
        aggregation: str | None = None,
        window: str = "1m",
    ) -> list[dict[str, Any]]:
        """
        Query sensor data for a time range.

        Args:
            sensor_name: Name of sensor to query
            start: Start time (Flux format: -1h, -7d, 2024-01-01T00:00:00Z)
            stop: End time
            aggregation: Optional aggregation: mean, min, max, sum
            window: Window for aggregation (1m, 5m, 1h)

        Returns:
            List of data points
        """
        if not self._client:
            return []

        try:
            query_api = self._client.query_api()

            if aggregation:
                flux = f'''
                from(bucket: "{settings.influxdb_bucket}")
                    |> range(start: {start}, stop: {stop})
                    |> filter(fn: (r) => r["_measurement"] == "sensor_reading")
                    |> filter(fn: (r) => r["sensor_name"] == "{sensor_name}")
                    |> filter(fn: (r) => r["_field"] == "value")
                    |> aggregateWindow(every: {window}, fn: {aggregation}, createEmpty: false)
                    |> yield(name: "{aggregation}")
                '''
            else:
                flux = f'''
                from(bucket: "{settings.influxdb_bucket}")
                    |> range(start: {start}, stop: {stop})
                    |> filter(fn: (r) => r["_measurement"] == "sensor_reading")
                    |> filter(fn: (r) => r["sensor_name"] == "{sensor_name}")
                    |> filter(fn: (r) => r["_field"] == "value")
                '''

            tables = await query_api.query(flux, org=settings.influxdb_org)

            results = []
            for table in tables:
                for record in table.records:
                    results.append({
                        "time": record.get_time(),
                        "value": record.get_value(),
                        "sensor_name": record.values.get("sensor_name"),
                    })

            return results

        except Exception as e:
            self._log.error("Query failed", error=str(e))
            return []

    async def query_statistics(
        self,
        sensor_name: str,
        start: str,
        stop: str,
    ) -> dict[str, float | None]:
        """
        Calculate statistics for a sensor over a time range.

        Returns:
            Dict with mean, min, max, stddev, count
        """
        if not self._client:
            return {}

        try:
            query_api = self._client.query_api()

            flux = f'''
            data = from(bucket: "{settings.influxdb_bucket}")
                |> range(start: {start}, stop: {stop})
                |> filter(fn: (r) => r["_measurement"] == "sensor_reading")
                |> filter(fn: (r) => r["sensor_name"] == "{sensor_name}")
                |> filter(fn: (r) => r["_field"] == "value")

            mean_val = data |> mean() |> yield(name: "mean")
            min_val = data |> min() |> yield(name: "min")
            max_val = data |> max() |> yield(name: "max")
            stddev_val = data |> stddev() |> yield(name: "stddev")
            count_val = data |> count() |> yield(name: "count")
            '''

            tables = await query_api.query(flux, org=settings.influxdb_org)

            stats: dict[str, float | None] = {
                "mean": None,
                "min": None,
                "max": None,
                "stddev": None,
                "count": None,
            }

            for table in tables:
                for record in table.records:
                    result_name = record.values.get("result")
                    if result_name in stats:
                        stats[result_name] = record.get_value()

            return stats

        except Exception as e:
            self._log.error("Statistics query failed", error=str(e))
            return {}

    async def export_data(
        self,
        sensor_names: list[str],
        start: str,
        stop: str,
    ) -> str:
        """
        Query and pivot data for export to CSV.

        Returns:
            CSV string
        """
        if not self._client:
            return ""

        try:
            query_api = self._client.query_api()

            # Format sensor names for Flux set
            sensors_set = "[" + ", ".join(f'"{s}"' for s in sensor_names) + "]"

            flux = f'''
            from(bucket: "{settings.influxdb_bucket}")
                |> range(start: time(v: "{start}"), stop: time(v: "{stop}"))
                |> filter(fn: (r) => r["_measurement"] == "sensor_reading")
                |> filter(fn: (r) => contains(value: r["sensor_name"], set: {sensors_set}))
                |> filter(fn: (r) => r["_field"] == "value")
                |> pivot(rowKey:["_time"], columnKey: ["sensor_name"], valueColumn: "_value")
                |> drop(columns: ["_start", "_stop", "_measurement", "_field"])
                |> sort(columns: ["_time"])
            '''

            # Execute query
            tables = await query_api.query(flux, org=settings.influxdb_org)

            if not tables:
                return ""

            # Use csv module to generate CSV string
            import csv
            import io

            output = io.StringIO()

            # Fields: time, sensor1, sensor2...
            fieldnames = ["time", *sorted(sensor_names)]

            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()

            for table in tables:
                for record in table.records:
                    row = {"time": record.get_time().isoformat()}
                    # The record values contain keys for each sensor_name due to pivot
                    for sensor in sensor_names:
                        val = record.values.get(sensor)
                        if val is not None:
                            row[sensor] = val

                    writer.writerow(row)

            return output.getvalue()

        except Exception as e:
            self._log.error("Export query failed", error=str(e))
            return ""

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def update_retention_policy(self, days: int) -> bool:
        """Update the retention policy for the sensor bucket."""
        if not self._client:
            return False

        try:
            buckets_api = self._client.buckets_api()
            # Find bucket
            bucket = await buckets_api.find_bucket_by_name(settings.influxdb_bucket)
            if not bucket:
                self._log.error("Bucket not found", bucket=settings.influxdb_bucket)
                return False

            # Update retention
            # InfluxDB Client expects BucketRetentionRules objects usually,
            # but sometimes accepts dicts depending on version.
            # Let's import BucketRetentionRules to be safe if available, or just pass the object update.
            # Actually, update_bucket takes a Bucket object.

            from influxdb_client.domain.bucket_retention_rules import BucketRetentionRules

            # 0 means infinite retention
            seconds = days * 86400 if days > 0 else 0

            bucket.retention_rules = [BucketRetentionRules(type="expire", every_seconds=seconds)]

            await buckets_api.update_bucket(bucket)
            self._log.info("Updated retention policy", days=days)
            return True

        except Exception as e:
            self._log.error("Failed to update retention", error=str(e))
            return False


# Global client instance
influx_client = InfluxDBClient()
