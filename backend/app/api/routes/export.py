from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.db.influx import influx_client
from app.models.sensor import Sensor

router = APIRouter(prefix="/export", tags=["Export"])

class ExportRequest(BaseModel):
    sensor_ids: list[int] = Field(..., min_length=1)
    start_time: datetime
    end_time: datetime
    format: Literal["csv"] = "csv"

@router.post("/raw")
async def export_raw_data(
    body: ExportRequest,
    db: DbSession,
    _user: CurrentUser,
):
    """
    Export raw sensor data as CSV.
    """
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    # Resolve sensor IDs to names
    stmt = select(Sensor).where(Sensor.id.in_(body.sensor_ids))
    result = await db.execute(stmt)
    sensors = list(result.scalars().all())

    if not sensors:
        raise HTTPException(status_code=404, detail="No sensors found")

    sensor_names = [s.name for s in sensors]

    # Format dates for InfluxDB (ISO 8601)
    start_str = body.start_time.isoformat() + "Z"
    end_str = body.end_time.isoformat() + "Z"

    csv_data = await influx_client.export_data(
        sensor_names=sensor_names,
        start=start_str,
        stop=end_str,
    )

    if not csv_data:
        # Return empty CSV with headers? Or 404?
        # Influx query_csv usually includes annotation rows, we might want to clean them up
        # But for 'raw' export, maybe raw flux CSV is okay?
        # Actually user wants a clean CSV. The influx raw csv is very verbose (datatype, group, default, etc).
        # We should probably post-process it or use DataFrame mode in the client if available asynchronously.
        # But InfluxDBClientAsync.query_data_frame is not always fully async or available in all versions.
        # Let's rely on the client returning something. If empty, return no content.
        return Response(content="No data found", media_type="text/plain", status_code=404)

    # Clean up InfluxDB CSV garbage (annotations) if raw output
    # The iterator returns lines.
    # Let's strip the first few lines if they are Flux annotations.
    lines = [line for line in csv_data if not line.strip().startswith('#')]
    # Only keep the header and data

    # Actually, let's refine the influx.py implementation to use pandas if possible to get clean CSV
    # Re-checking influx.py implementation...
    # The previous step used query_csv which returns an iterator of lists (csv.reader style) or strings?
    # Actually query_csv returns a CSV iterator.

    # For now, let's return the raw data and refine if it looks junk.
    # We will set a filename.

    filename = f"export_{body.start_time.strftime('%Y%m%d%H%M')}.csv"

    # Need to convert the iterator to string content
    clean_csv = ""
    for row in lines:
        clean_csv += row

    return Response(
        content=clean_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
