from datetime import datetime

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReportJob(Base, TimestampMixin):
    """
    Configuration for a recurring statistical report job.
    """
    __tablename__ = "report_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    # Schedule
    is_active: Mapped[bool] = mapped_column(default=True)
    interval_minutes: Mapped[int] = mapped_column(nullable=False, default=60) # e.g. 5, 15, 60, 1440 (daily)
    next_run_at: Mapped[datetime] = mapped_column(nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(default=None)

    # Configuration
    sensor_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False) # List of sensor IDs
    stat_types: Mapped[list[str]] = mapped_column(JSON, default=["mean", "min", "max"]) # ["mean", "min", "max", "stddev"]

    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ReportJob {self.name} every {self.interval_minutes}m>"
