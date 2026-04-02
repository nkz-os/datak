"""
DaTaK Gateway - Main FastAPI Application Entry Point.

This is the main entry point for the IoT Edge Gateway service.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.api.routes import auth, automation, config, export, report_jobs, sensors, system, websocket
from app.config import get_settings
from app.db.influx import influx_client
from app.db.session import close_db, init_db
from app.services.automation import automation_engine
from app.services.buffer import buffer_queue
from app.services.cloud_sync import cloud_sync
from app.services.command_listener import command_listener
from app.services.csv_engine import csv_generator
from app.services.orchestrator import orchestrator
from app.services.telemetry_pipeline import telemetry_pipeline

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    """
    logger.info(
        "Starting DaTaK Gateway",
        name=settings.gateway_name,
        version="0.1.0",
    )

    # Initialize SQLite database
    await init_db()
    logger.info("SQLite database initialized")

    # Connect to InfluxDB
    influx_connected = await influx_client.connect()
    if influx_connected:
        logger.info("InfluxDB connected")
    else:
        logger.warning("InfluxDB not available, running in buffer mode")

    # Start services
    await orchestrator.start()
    logger.info("Driver orchestrator started")

    await buffer_queue.start()
    logger.info("Buffer queue started")

    if settings.reports_enabled:
        await csv_generator.start()
        logger.info("CSV generator started")

    await cloud_sync.start()
    logger.info("Cloud sync started")

    await telemetry_pipeline.start()
    logger.info("Telemetry pipeline started")

    await command_listener.start()
    logger.info("Command listener started")

    await automation_engine.start()
    logger.info("Automation engine started")

    # Setup WebSocket callbacks
    websocket.setup_websocket_callbacks()
    logger.info("WebSocket callbacks registered")

    # Load existing active sensors and start their drivers
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.sensor import Sensor, SensorProtocol

    async with async_session_factory() as session:
        result = await session.execute(
            select(Sensor)
            .where(Sensor.is_active == True)  # noqa: E712
            .where(Sensor.deleted_at == None)  # noqa: E711
        )
        sensors_list = result.scalars().all()

        for sensor in sensors_list:
            if sensor.protocol != SensorProtocol.VIRTUAL.value:
                await orchestrator.add_sensor(
                    sensor_id=sensor.id,
                    sensor_name=sensor.name,
                    protocol=sensor.protocol,
                    connection_params=sensor.connection_params,
                    formula=sensor.data_formula,
                    poll_interval_ms=sensor.poll_interval_ms,
                    timeout_ms=sensor.timeout_ms,
                    retry_count=sensor.retry_count,
                )

        logger.info("Loaded active sensors", count=len(sensors_list))

    yield

    # Shutdown
    logger.info("Shutting down DaTaK Gateway")

    # Stop services in reverse order
    await automation_engine.stop()
    await command_listener.stop()
    await telemetry_pipeline.stop()
    await cloud_sync.stop()
    await csv_generator.stop()
    await buffer_queue.stop()
    await orchestrator.stop()
    logger.info("All services stopped")

    # Close databases
    await influx_client.disconnect()
    await close_db()
    logger.info("Database connections closed")


# Create FastAPI app
app = FastAPI(
    title="DaTaK Gateway",
    description="Industrial IoT Edge Gateway & Data Aggregator",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(auth.router, prefix="/api")
app.include_router(sensors.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(report_jobs.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(automation.router, prefix="/api")
app.include_router(websocket.router)

# Mount Prometheus metrics
if settings.metrics_enabled:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "gateway": settings.gateway_name,
    }


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with basic info."""
    return {
        "name": "DaTaK Gateway",
        "version": "0.1.0",
        "docs": "/docs",
    }


def main() -> None:
    """Run the application with uvicorn."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

