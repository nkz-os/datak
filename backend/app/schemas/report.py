from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReportJobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    interval_minutes: int = Field(..., ge=1, le=525600) # Min 1 min, Max 1 year
    sensor_ids: list[int] = Field(..., min_length=1)
    stat_types: list[Literal["mean", "min", "max", "stddev", "count"]] = ["mean", "min", "max"]
    is_active: bool = True

class ReportJobUpdate(BaseModel):
    description: str | None = None
    interval_minutes: int | None = Field(None, ge=1, le=525600)
    sensor_ids: list[int] | None = Field(None, min_length=1)
    stat_types: list[Literal["mean", "min", "max", "stddev", "count"]] | None = None
    is_active: bool | None = None

class ReportJobResponse(BaseModel):
    id: int
    name: str
    description: str | None
    interval_minutes: int
    sensor_ids: list[int]
    stat_types: list[str]
    is_active: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime | None

    class Config:
        from_attributes = True
