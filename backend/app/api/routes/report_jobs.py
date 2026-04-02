from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.report import ReportJob
from app.schemas.report import ReportJobCreate, ReportJobResponse, ReportJobUpdate

router = APIRouter(prefix="/report-jobs", tags=["Report Jobs"])

@router.get("", response_model=list[ReportJobResponse])
async def list_jobs(
    db: DbSession,
    _user: CurrentUser,
) -> list[ReportJob]:
    """List all statistical report jobs."""
    query = select(ReportJob).order_by(ReportJob.name)
    result = await db.execute(query)
    return list(result.scalars().all())

@router.post("", response_model=ReportJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: ReportJobCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ReportJob:
    """Create a new report job."""
    # Check duplicate name
    existing = await db.execute(select(ReportJob).where(ReportJob.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Job with this name already exists")

    job = ReportJob(
        name=body.name,
        description=body.description,
        interval_minutes=body.interval_minutes,
        sensor_ids=body.sensor_ids,
        stat_types=body.stat_types,
        is_active=body.is_active,
        # Set next run to now + interval (or just now if we want immediate?)
        # Let's say next run is aligned to the start of the next interval block
        next_run_at=datetime.utcnow() + timedelta(minutes=body.interval_minutes),
    )

    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job

@router.get("/{job_id}", response_model=ReportJobResponse)
async def get_job(
    job_id: int,
    db: DbSession,
    _user: CurrentUser,
) -> ReportJob:
    """Get job details."""
    job = await db.get(ReportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.patch("/{job_id}", response_model=ReportJobResponse)
async def update_job(
    job_id: int,
    body: ReportJobUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ReportJob:
    """Update a report job."""
    job = await db.get(ReportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = body.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(job, field, value)

    # Recalculate next run if interval changed?
    # For simplicity, we just leave next_run_at as is, unless it's in the past

    await db.commit()
    await db.refresh(job)
    return job

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int,
    db: DbSession,
    _user: CurrentUser,
) -> None:
    """Delete a report job."""
    job = await db.get(ReportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    await db.delete(job)
    await db.commit()
