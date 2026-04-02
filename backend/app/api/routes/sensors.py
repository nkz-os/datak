"""Sensor CRUD and management routes."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OperatorUser
from app.core.formula import validate_formula
from app.models.audit import AuditAction, AuditLog
from app.models.sensor import Sensor, SensorProtocol, SensorStatus
from app.services.orchestrator import orchestrator

router = APIRouter(prefix="/sensors", tags=["Sensors"])


# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────


class SensorCreate(BaseModel):
    """Schema for creating a new sensor."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    protocol: str = Field(..., description="One of: MODBUS_TCP, MODBUS_RTU, CAN, MQTT, VIRTUAL")
    connection_params: dict[str, Any] = Field(
        ...,
        description="Protocol-specific connection parameters",
        examples=[
            {"host": "192.168.1.10", "port": 502, "slave_id": 1, "address": 40001},
        ],
    )
    data_formula: str = Field(default="val", description="Transformation formula")
    unit: str | None = None
    poll_interval_ms: int = Field(default=1000, ge=100, le=60000)
    timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    retry_count: int = Field(default=3, ge=1, le=10)
    twin_entity_id: str | None = None
    twin_attribute: str | None = None


class SensorUpdate(BaseModel):
    """Schema for updating a sensor."""

    description: str | None = None
    connection_params: dict[str, Any] | None = None
    data_formula: str | None = None
    unit: str | None = None
    poll_interval_ms: int | None = Field(default=None, ge=100, le=60000)
    timeout_ms: int | None = Field(default=None, ge=1000, le=30000)
    retry_count: int | None = Field(default=None, ge=1, le=10)
    is_active: bool | None = None
    twin_entity_id: str | None = None
    twin_attribute: str | None = None


class SensorResponse(BaseModel):
    """Sensor response with status."""

    id: int
    name: str
    description: str | None
    protocol: str
    connection_params: dict[str, Any]
    data_formula: str
    unit: str | None
    poll_interval_ms: int
    timeout_ms: int
    retry_count: int
    is_active: bool
    status: str
    last_seen: datetime | None
    last_value: float | None
    error_count: int
    created_at: datetime
    updated_at: datetime | None

    class Config:
        from_attributes = True


class FormulaTestRequest(BaseModel):
    """Request to test a formula."""

    formula: str
    test_value: float = 100.0


class FormulaTestResponse(BaseModel):
    """Formula test result."""

    valid: bool
    result: float | None
    error: str | None


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────


@router.get("", response_model=list[SensorResponse])
async def list_sensors(
    db: DbSession,
    _user: CurrentUser,
    active_only: bool = False,
    protocol: str | None = None,
) -> list[Sensor]:
    """
    List all sensors with optional filtering.
    """
    query = select(Sensor).where(Sensor.deleted_at == None)  # noqa: E711

    if active_only:
        query = query.where(Sensor.is_active == True)  # noqa: E712

    if protocol:
        query = query.where(Sensor.protocol == protocol)

    result = await db.execute(query.order_by(Sensor.name))
    return list(result.scalars().all())


@router.get("/{sensor_id}", response_model=SensorResponse)
async def get_sensor(
    sensor_id: int,
    db: DbSession,
    _user: CurrentUser,
) -> Sensor:
    """Get a specific sensor by ID."""
    result = await db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.deleted_at == None)  # noqa: E711
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")

    return sensor


@router.post("", response_model=SensorResponse, status_code=status.HTTP_201_CREATED)
async def create_sensor(
    request: Request,
    body: SensorCreate,
    db: DbSession,
    user: OperatorUser,
) -> Sensor:
    """
    Create a new sensor and start its driver.

    Requires OPERATOR or ADMIN role.
    """
    # Validate protocol
    try:
        req_json = await request.json()
        print(f"DEBUG: Full Request Body: {req_json}", flush=True)
    except Exception as e:
        print(f"DEBUG: Failed to read request body: {e}", flush=True)

    print(f"DEBUG: Received protocol: '{body.protocol}' type: {type(body.protocol)}", flush=True)
    try:
        SensorProtocol(body.protocol)
    except ValueError:
        valid_protocols = [p.value for p in SensorProtocol]
        print(f"DEBUG: Invalid protocol. Valid: {valid_protocols}", flush=True)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid protocol '{body.protocol}'. Must be one of: {valid_protocols}",
        ) from None

    # Validate formula
    is_valid, error = validate_formula(body.data_formula)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid formula: {error}")

    # Check for duplicate name
    existing = await db.execute(select(Sensor).where(Sensor.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Sensor with this name already exists")

    # Create sensor
    sensor = Sensor(
        name=body.name,
        description=body.description,
        protocol=body.protocol,
        connection_params=body.connection_params,
        data_formula=body.data_formula,
        unit=body.unit,
        poll_interval_ms=body.poll_interval_ms,
        timeout_ms=body.timeout_ms,
        retry_count=body.retry_count,
        twin_entity_id=body.twin_entity_id,
        twin_attribute=body.twin_attribute,
        status=SensorStatus.UNKNOWN.value,
    )
    db.add(sensor)
    await db.flush()  # Get ID

    # Start driver (hot-reload)
    if body.protocol != SensorProtocol.VIRTUAL.value:
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

    # Audit log
    db.add(
        AuditLog(
            user_id=user.id,
            action=AuditAction.SENSOR_CREATE.value,
            resource_type="sensor",
            resource_id=sensor.id,
            details=f"Created sensor: {sensor.name}",
            ip_address=request.client.host if request.client else None,
        )
    )

    await db.commit()
    await db.refresh(sensor)

    return sensor


@router.patch("/{sensor_id}", response_model=SensorResponse)
async def update_sensor(
    request: Request,
    sensor_id: int,
    body: SensorUpdate,
    db: DbSession,
    user: OperatorUser,
) -> Sensor:
    """
    Update a sensor configuration.

    If connection params or is_active changes, driver will be restarted.
    """
    result = await db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.deleted_at == None)  # noqa: E711
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")

    # Track if we need to restart driver
    needs_restart = False
    changes: list[str] = []

    # Update fields
    update_data = body.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if value is not None and getattr(sensor, field) != value:
            # Validate formula if changing
            if field == "data_formula":
                is_valid, error = validate_formula(value)
                if not is_valid:
                    raise HTTPException(status_code=400, detail=f"Invalid formula: {error}")

            setattr(sensor, field, value)
            changes.append(field)

            # Determine if restart needed
            if field in ("connection_params", "is_active", "poll_interval_ms", "timeout_ms"):
                needs_restart = True

    sensor.updated_at = datetime.utcnow()

    # Restart driver if needed
    if needs_restart and sensor.protocol != SensorProtocol.VIRTUAL.value:
        if sensor.is_active:
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
        else:
            await orchestrator.remove_sensor(sensor.id)
    elif "data_formula" in changes:
        # Just update formula, no restart
        await orchestrator.update_formula(sensor.id, sensor.data_formula)

    # Audit log
    db.add(
        AuditLog(
            user_id=user.id,
            action=AuditAction.SENSOR_UPDATE.value,
            resource_type="sensor",
            resource_id=sensor.id,
            details=f"Updated fields: {', '.join(changes)}",
            ip_address=request.client.host if request.client else None,
        )
    )

    await db.commit()
    await db.refresh(sensor)

    return sensor


@router.delete("/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor(
    request: Request,
    sensor_id: int,
    db: DbSession,
    user: OperatorUser,
) -> None:
    """
    Soft-delete a sensor and stop its driver.
    """
    result = await db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.deleted_at == None)  # noqa: E711
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")

    # Stop driver
    await orchestrator.remove_sensor(sensor.id)

    # Soft delete
    sensor.deleted_at = datetime.utcnow()
    sensor.is_active = False

    # Audit log
    db.add(
        AuditLog(
            user_id=user.id,
            action=AuditAction.SENSOR_DELETE.value,
            resource_type="sensor",
            resource_id=sensor.id,
            details=f"Deleted sensor: {sensor.name}",
            ip_address=request.client.host if request.client else None,
        )
    )

    await db.commit()


class SensorWriteRequest(BaseModel):
    """Request schema for writing to a sensor."""
    value: float


@router.post("/{sensor_id}/write")
async def write_to_sensor(
    sensor_id: int,
    body: SensorWriteRequest,
    _user: OperatorUser,
) -> dict[str, str]:
    """
    Write a value to a sensor (actuator).

    Requires OPERATOR or ADMIN role.
    """
    try:
        success = await orchestrator.write_sensor(sensor_id, body.value)
        if success:
            return {"message": "Write successful"}
        else:
            raise HTTPException(status_code=500, detail="Write failed (driver returned False)")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/test-formula", response_model=FormulaTestResponse)
async def test_formula(
    body: FormulaTestRequest,
    _user: CurrentUser,
) -> FormulaTestResponse:
    """
    Test a formula with a sample value.

    Useful for validating formulas before saving.
    """
    from app.core.formula import test_formula as do_test

    result = do_test(body.formula, body.test_value)
    return FormulaTestResponse(**result)


@router.post("/{sensor_id}/restart")
async def restart_sensor(
    sensor_id: int,
    db: DbSession,
    _user: OperatorUser,
) -> dict[str, str]:
    """Restart a sensor's driver."""
    result = await db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.deleted_at == None)  # noqa: E711
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")

    success = await orchestrator.restart_sensor(sensor_id)

    if success:
        return {"message": f"Sensor {sensor.name} restarted"}
    else:
        raise HTTPException(status_code=500, detail="Failed to restart sensor")


@router.get("/{sensor_id}/status")
async def get_sensor_status(
    sensor_id: int,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Get real-time status of a sensor driver."""
    status = orchestrator.get_status(sensor_id)

    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="Sensor not running")

    return status
