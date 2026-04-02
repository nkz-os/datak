"""Sensor model for device configuration and status tracking."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class SensorProtocol(StrEnum):
    """Supported communication protocols."""

    MODBUS_TCP = "MODBUS_TCP"
    MODBUS_RTU = "MODBUS_RTU"
    CAN = "CAN"
    MQTT = "MQTT"
    SYSTEM = "SYSTEM"
    VIRTUAL = "VIRTUAL"  # For testing/simulation
    VIRTUAL_OUTPUT = "VIRTUAL_OUTPUT"  # For automation rule outputs


class SensorStatus(StrEnum):
    """Sensor connection status."""

    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class Sensor(Base, TimestampMixin, SoftDeleteMixin):
    """Sensor/device configuration and runtime status."""

    __tablename__ = "sensors"
    __table_args__ = (
        Index("ix_sensors_protocol_active", "protocol", "is_active"),
        Index("ix_sensors_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(String(500))
    protocol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    # Connection parameters (protocol-specific, stored as JSON)
    # Modbus TCP: {"host": "192.168.1.10", "port": 502, "slave_id": 1, "address": 40001, "count": 1}
    # Modbus RTU: {"port": "/dev/ttyUSB0", "baudrate": 9600, "slave_id": 1, "address": 40001}
    # CAN: {"interface": "can0", "arbitration_id": "0x123", "dbc_file": "motor.dbc", "signal": "RPM"}
    # MQTT: {"topic": "sensors/temp1", "json_path": "$.value"}
    connection_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Data transformation
    data_formula: Mapped[str] = mapped_column(
        String(200),
        default="val",
        nullable=False,
    )
    unit: Mapped[str | None] = mapped_column(String(20))
    decimal_places: Mapped[int] = mapped_column(default=2)

    # Polling configuration
    poll_interval_ms: Mapped[int] = mapped_column(default=1000)
    timeout_ms: Mapped[int] = mapped_column(default=5000)
    retry_count: Mapped[int] = mapped_column(default=3)

    # Runtime status (updated by drivers)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=SensorStatus.UNKNOWN.value,
    )
    last_seen: Mapped[datetime | None] = mapped_column(default=None)
    last_value: Mapped[float | None] = mapped_column(Float, default=None)
    last_raw_value: Mapped[float | None] = mapped_column(Float, default=None)
    error_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    # Digital Twin mapping
    twin_entity_id: Mapped[str | None] = mapped_column(String(100))
    twin_attribute: Mapped[str | None] = mapped_column(String(50))

    def __repr__(self) -> str:
        return f"<Sensor {self.name} [{self.protocol}] - {self.status}>"

    @property
    def is_online(self) -> bool:
        """Check if sensor is currently online."""
        return self.status == SensorStatus.ONLINE.value

    def mark_online(self, value: float, raw_value: float | None = None) -> None:
        """Update status when successful read."""
        self.status = SensorStatus.ONLINE.value
        self.last_seen = datetime.utcnow()
        self.last_value = value
        self.last_raw_value = raw_value
        self.error_count = 0
        self.last_error = None

    def mark_error(self, error: str) -> None:
        """Update status on error."""
        self.error_count += 1
        self.last_error = error
        if self.error_count >= self.retry_count:
            self.status = SensorStatus.OFFLINE.value
        else:
            self.status = SensorStatus.ERROR.value


class SensorReading(Base):
    """Buffered sensor reading for Store & Forward queue."""

    __tablename__ = "sensor_readings"
    __table_args__ = (
        Index("ix_readings_timestamp", "timestamp"),
        Index("ix_readings_synced", "synced"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sensor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    raw_value: Mapped[float | None] = mapped_column(Float)
    synced: Mapped[bool] = mapped_column(default=False, index=True)
    synced_at: Mapped[datetime | None] = mapped_column(default=None)
