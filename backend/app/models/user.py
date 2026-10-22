"""User model for authentication and authorization."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class UserRole(StrEnum):
    """User role enumeration."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class User(Base, TimestampMixin):
    """User account for gateway access."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_username_active", "username", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        default=UserRole.VIEWER.value,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    last_login: Mapped[datetime | None] = mapped_column(default=None)

    # Relationships
    audit_logs = relationship("AuditLog", back_populates="user", lazy="dynamic")
    config_versions = relationship("ConfigVersion", back_populates="created_by", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role})>"

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == UserRole.ADMIN.value

    @property
    def can_write(self) -> bool:
        """Check if user can modify configuration."""
        return self.role in (UserRole.ADMIN.value, UserRole.OPERATOR.value)
