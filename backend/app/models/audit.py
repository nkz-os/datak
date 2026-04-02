"""Audit and configuration versioning models."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuditAction(StrEnum):
    """Audit log action types."""

    # Authentication
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"

    # Sensor management
    SENSOR_CREATE = "SENSOR_CREATE"
    SENSOR_UPDATE = "SENSOR_UPDATE"
    SENSOR_DELETE = "SENSOR_DELETE"
    SENSOR_ACTIVATE = "SENSOR_ACTIVATE"
    SENSOR_DEACTIVATE = "SENSOR_DEACTIVATE"

    # Configuration
    CONFIG_EXPORT = "CONFIG_EXPORT"
    CONFIG_IMPORT = "CONFIG_IMPORT"
    CONFIG_ROLLBACK = "CONFIG_ROLLBACK"

    # System
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    DRIVER_START = "DRIVER_START"
    DRIVER_STOP = "DRIVER_STOP"


class AuditLog(Base):
    """Immutable audit trail for all system actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(200))

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog {self.timestamp} - {self.action}>"


class ConfigVersion(Base):
    """Configuration snapshots for rollback capability."""

    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_number: Mapped[int] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        nullable=False,
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    reason: Mapped[str | None] = mapped_column(String(200))

    # Full configuration snapshot as JSON
    # Contains: {"sensors": [...], "gateway": {...}}
    full_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Relationships
    created_by = relationship("User", back_populates="config_versions")

    def __repr__(self) -> str:
        return f"<ConfigVersion v{self.version_number} - {self.created_at}>"
