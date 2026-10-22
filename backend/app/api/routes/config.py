"""Configuration and device profile routes."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.config import get_settings
from app.models.audit import AuditAction, AuditLog, ConfigVersion
from app.models.sensor import Sensor
from app.services.buffer import buffer_queue
from app.services.cloud_sync import cloud_sync
from app.services.csv_engine import csv_generator

router = APIRouter(prefix="/config", tags=["Configuration"])
settings = get_settings()


class ConfigExport(BaseModel):
    """Configuration export request."""

    include_credentials: bool = False


class SystemConfig(BaseModel):
    """System-wide configuration."""

    influxdb_retention_days: int
    digital_twin_enabled: bool
    digital_twin_host: str
    digital_twin_port: int
    digital_twin_topic: str
    digital_twin_username: str | None = None
    digital_twin_password: str | None = None
    digital_twin_entity_type: str = "AgriSensor"
    gateway_name: str | None = None  # Allow renaming gateway


@router.get("/system")
async def get_system_config(_user: AdminUser) -> SystemConfig:
    """Get current system configuration."""
    return SystemConfig(
        influxdb_retention_days=settings.influxdb_retention_days,
        digital_twin_enabled=settings.digital_twin_enabled,
        digital_twin_host=settings.digital_twin_host,
        digital_twin_port=settings.digital_twin_port,
        digital_twin_topic=settings.digital_twin_topic,
        digital_twin_username=settings.digital_twin_username,
        digital_twin_password=settings.digital_twin_password,
        digital_twin_entity_type=settings.digital_twin_entity_type,
        gateway_name=settings.gateway_name,
    )


@router.put("/system")
async def update_system_config(
    body: SystemConfig,
    _user: AdminUser,
) -> SystemConfig:
    """
    Update system configuration.

    Persists changes to configs/gateway.yaml and applies them immediately where possible.
    """
    # Update settings
    settings.influxdb_retention_days = body.influxdb_retention_days
    settings.digital_twin_enabled = body.digital_twin_enabled
    settings.digital_twin_host = body.digital_twin_host
    settings.digital_twin_port = body.digital_twin_port
    settings.digital_twin_topic = body.digital_twin_topic
    settings.digital_twin_username = body.digital_twin_username
    settings.digital_twin_password = body.digital_twin_password
    settings.digital_twin_entity_type = body.digital_twin_entity_type
    if body.gateway_name:
        settings.gateway_name = body.gateway_name

    # Save to file
    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {e}") from e

    # Apply InfluxDB retention
    from app.db.influx import influx_client
    await influx_client.update_retention_policy(body.influxdb_retention_days)

    # Restart Cloud Sync if needed
    if body.digital_twin_enabled:
        await cloud_sync.stop()
        await cloud_sync.start()
    else:
        await cloud_sync.stop()

    return body


class ConfigImport(BaseModel):
    """Configuration import request."""

    config: dict[str, Any]
    reason: str | None = None


@router.get("/device-profile")
async def get_device_profile(_user: CurrentUser) -> dict[str, Any]:
    """
    Generate and return the device profile JSON.

    This can be used to configure the Digital Twin platform.
    """
    profile = await cloud_sync.generate_device_profile()
    return profile


@router.get("/device-profile/download")
async def download_device_profile(_user: CurrentUser) -> JSONResponse:
    """Download the device profile as a JSON file."""
    profile = await cloud_sync.generate_device_profile()

    filename = f"device-profile-{settings.gateway_name}-{datetime.utcnow().strftime('%Y%m%d')}.json"

    return JSONResponse(
        content=profile,
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


@router.get("/export")
async def export_configuration(
    db: DbSession,
    _user: AdminUser,
    include_credentials: bool = False,
) -> dict[str, Any]:
    """
    Export the complete gateway configuration.

    Requires ADMIN role.
    """
    # Get all sensors
    result = await db.execute(
        select(Sensor).where(Sensor.deleted_at == None)  # noqa: E711
    )
    sensors = list(result.scalars().all())

    sensors_list: list[dict[str, Any]] = []
    config: dict[str, Any] = {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat(),
        "gateway_name": settings.gateway_name,
        "sensors": sensors_list,
    }

    for sensor in sensors:
        sensor_config = {
            "name": sensor.name,
            "description": sensor.description,
            "protocol": sensor.protocol,
            "connection_params": sensor.connection_params,
            "data_formula": sensor.data_formula,
            "unit": sensor.unit,
            "poll_interval_ms": sensor.poll_interval_ms,
            "timeout_ms": sensor.timeout_ms,
            "retry_count": sensor.retry_count,
            "is_active": sensor.is_active,
            "twin_entity_id": sensor.twin_entity_id,
            "twin_attribute": sensor.twin_attribute,
        }

        if not include_credentials:
            # Remove sensitive data from connection_params
            params = sensor_config["connection_params"].copy()
            params.pop("password", None)
            params.pop("api_key", None)
            sensor_config["connection_params"] = params

        sensors_list.append(sensor_config)

    return config


@router.post("/import")
async def import_configuration(
    request: Request,
    body: ConfigImport,
    db: DbSession,
    user: AdminUser,
) -> dict[str, Any]:
    """
    Import a configuration (creates new sensors, updates existing).

    Requires ADMIN role.
    """
    config = body.config
    imported = 0
    updated = 0
    errors: list[str] = []

    # Validate config version
    if config.get("version") != "1.0":
        raise HTTPException(status_code=400, detail="Unsupported config version")

    for sensor_data in config.get("sensors", []):
        try:
            name = sensor_data.get("name")
            if not name:
                errors.append("Sensor missing name")
                continue

            # Check if exists
            result = await db.execute(
                select(Sensor).where(Sensor.name == name)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing sensor
                for key, value in sensor_data.items():
                    if key != "name" and hasattr(existing, key):
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                # Create new sensor
                sensor = Sensor(**sensor_data)
                db.add(sensor)
                imported += 1

        except Exception as e:
            errors.append(f"Error with sensor {sensor_data.get('name', '?')}: {e}")

    # Create config version snapshot
    version_result = await db.execute(
        select(func.max(ConfigVersion.version_number))
    )
    max_version = version_result.scalar() or 0

    snapshot = ConfigVersion(
        version_number=max_version + 1,
        full_config_snapshot=config,
        created_by_id=user.id,
        reason=body.reason or "Configuration import",
    )
    db.add(snapshot)

    # Audit log
    db.add(AuditLog(
        user_id=user.id,
        action=AuditAction.CONFIG_IMPORT.value,
        details=f"Imported {imported}, updated {updated} sensors",
        ip_address=request.client.host if request.client else None,
    ))

    await db.commit()

    return {
        "success": True,
        "imported": imported,
        "updated": updated,
        "errors": errors,
        "version": max_version + 1,
    }


@router.get("/versions")
async def list_config_versions(
    db: DbSession,
    _user: AdminUser,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List configuration version history."""
    result = await db.execute(
        select(ConfigVersion)
        .order_by(ConfigVersion.version_number.desc())
        .limit(limit)
    )
    versions = result.scalars().all()

    return [
        {
            "version": v.version_number,
            "created_at": v.created_at.isoformat(),
            "reason": v.reason,
        }
        for v in versions
    ]


@router.post("/rollback/{version}")
async def rollback_configuration(
    request: Request,
    version: int,
    db: DbSession,
    user: AdminUser,
) -> dict[str, Any]:
    """
    Rollback to a previous configuration version.

    Requires ADMIN role.
    """
    result = await db.execute(
        select(ConfigVersion).where(ConfigVersion.version_number == version)
    )
    config_version = result.scalar_one_or_none()

    if not config_version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Import the old configuration
    config = config_version.full_config_snapshot

    # Use the import logic
    import_body = ConfigImport(
        config=config,
        reason=f"Rollback to version {version}",
    )

    # Recursively call import
    return await import_configuration(request, import_body, db, user)


@router.get("/reports")
async def list_reports(
    _user: CurrentUser,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List available CSV report files."""
    return csv_generator.list_reports(limit=limit)


@router.get("/reports/{filename}")
async def download_report(
    filename: str,
    _user: CurrentUser,
) -> FileResponse:
    """Download a specific CSV report file."""
    filepath = settings.reports_output_dir / filename

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    # Security: ensure file is within reports directory
    try:
        filepath.resolve().relative_to(settings.reports_output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied") from None

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="text/csv" if filename.endswith(".csv") else "application/gzip",
    )


@router.get("/buffer/status")
async def get_buffer_status(_user: CurrentUser) -> dict[str, Any]:
    """Get the current Store & Forward buffer status."""
    return await buffer_queue.get_queue_stats()


@router.post("/buffer/flush")
async def flush_buffer(_user: AdminUser) -> dict[str, Any]:
    """Manually trigger a buffer flush."""
    synced = await buffer_queue.flush()
    return {"synced": synced}
