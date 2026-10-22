"""System status and metrics routes."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

from app.api.deps import CurrentUser
from app.api.routes.websocket import manager as ws_manager
from app.config import get_settings
from app.db.influx import influx_client
from app.services.buffer import buffer_queue
from app.services.orchestrator import orchestrator

router = APIRouter(prefix="/system", tags=["System"])
settings = get_settings()

# ─────────────────────────────────────────────────────────────
# Prometheus Metrics
# ─────────────────────────────────────────────────────────────

# Counters
SENSOR_READINGS_TOTAL = Counter(
    "datak_sensor_readings_total",
    "Total number of sensor readings processed",
    ["sensor_name", "protocol"],
)

DRIVER_ERRORS_TOTAL = Counter(
    "datak_driver_errors_total",
    "Total number of driver errors",
    ["sensor_name", "error_type"],
)

# Gauges
SENSORS_ONLINE = Gauge(
    "datak_sensors_online",
    "Number of sensors currently online",
)

SENSORS_OFFLINE = Gauge(
    "datak_sensors_offline",
    "Number of sensors currently offline",
)

BUFFER_QUEUE_SIZE = Gauge(
    "datak_buffer_queue_size",
    "Number of readings in the Store & Forward buffer",
)

WEBSOCKET_CONNECTIONS = Gauge(
    "datak_websocket_connections",
    "Number of active WebSocket connections",
)

# Histograms
SENSOR_READ_DURATION = Histogram(
    "datak_sensor_read_duration_seconds",
    "Time taken to read from a sensor",
    ["protocol"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)


def update_metrics() -> None:
    """Update Prometheus metrics with current state."""
    # Sensor status
    all_status = orchestrator.get_all_status()
    online = sum(1 for s in all_status.values() if s.get("connected"))
    offline = len(all_status) - online

    SENSORS_ONLINE.set(online)
    SENSORS_OFFLINE.set(offline)

    # WebSocket connections
    WEBSOCKET_CONNECTIONS.set(ws_manager.connection_count)


@router.get("/metrics")
async def get_prometheus_metrics() -> Response:
    """Prometheus metrics endpoint."""
    update_metrics()
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/status")
async def get_system_status(_user: CurrentUser) -> dict[str, Any]:
    """
    Get comprehensive system status.

    Includes:
        - Gateway info
        - Sensor statistics
        - Buffer queue status
        - Service health
    """
    # Sensor stats
    all_status = orchestrator.get_all_status()
    online_count = sum(1 for s in all_status.values() if s.get("connected"))

    # Buffer stats
    buffer_stats = await buffer_queue.get_queue_stats()

    return {
        "gateway": {
            "name": settings.gateway_name,
            "version": "0.1.0",
            "uptime": "TODO",  # Would need to track start time
            "timestamp": datetime.utcnow().isoformat(),
        },
        "sensors": {
            "total": len(all_status),
            "online": online_count,
            "offline": len(all_status) - online_count,
        },
        "buffer": buffer_stats,
        "services": {
            "influxdb": influx_client.is_connected,
            "websocket_clients": ws_manager.connection_count,
        },
    }


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/sensors/summary")
async def get_sensors_summary(_user: CurrentUser) -> dict[str, Any]:
    """Get summary of all sensors with their current values."""
    all_status = orchestrator.get_all_status()

    summary = []
    for sensor_id, status in all_status.items():
        summary.append({
            "sensor_id": sensor_id,
            "running": status.get("running", False),
            "connected": status.get("connected", False),
            "last_value": status.get("last_value"),
            "error_count": status.get("error_count", 0),
        })

    return {
        "count": len(summary),
        "sensors": summary,
    }
