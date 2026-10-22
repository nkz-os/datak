"""Telemetry pipeline: persist locally and forward northbound."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.services.buffer import buffer_queue
from app.services.cloud_sync import cloud_sync
from app.services.orchestrator import orchestrator

logger = structlog.get_logger()


class TelemetryPipeline:
    """
    Connects orchestrator values to:
    - Local persistence (InfluxDB via buffer queue)
    - Northbound forwarding (Digital Twin / NKZ via MQTT)
    """

    def __init__(self) -> None:
        self._running = False
        self._log = logger.bind(component="telemetry_pipeline")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        orchestrator.on_processed_value(self._handle_value)
        self._log.info("Telemetry pipeline started")

    async def stop(self) -> None:
        self._running = False
        self._log.info("Telemetry pipeline stopped")

    async def _handle_value(
        self,
        sensor_id: int,
        raw_value: float,
        value: float,
        timestamp: datetime,
        register_ctx: dict[str, Any] | None = None,
    ) -> None:
        if not self._running:
            return

        driver = orchestrator._drivers.get(sensor_id)
        sensor_name = driver.sensor_name if driver else f"sensor_{sensor_id}"

        register_id = register_ctx.get("register_id") if register_ctx else None
        register_name = register_ctx.get("name") if register_ctx else None

        # 1) Persist locally (InfluxDB or buffer)
        await buffer_queue.add(
            sensor_id=sensor_id,
            sensor_name=sensor_name,
            value=value,
            raw_value=raw_value,
            timestamp=timestamp,
            register_id=register_id,
            register_name=register_name,
        )

        # 2) Forward northbound (best-effort)
        ok = await cloud_sync.send_reading(
            sensor_id=sensor_id,
            sensor_name=sensor_name,
            value=value,
            timestamp=timestamp,
            attribute=register_ctx.get("twin_attribute") if register_ctx else None,
        )
        if not ok:
            self._log.debug("Northbound publish skipped/failed", sensor_id=sensor_id, sensor_name=sensor_name)


telemetry_pipeline = TelemetryPipeline()

