"""Multi-register persistence through the Store & Forward buffer.

A multi-register sensor produces one reading per register, each tagged with
``register_id`` / ``register_name``. When InfluxDB is down those readings are
buffered in SQLite; once it is back, ``BufferQueue.flush()`` must forward the
register identity so the values do not collapse onto each other in InfluxDB.

These tests pin that contract (currently failing — see buffer.py::flush).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.services import buffer as buffer_module
from app.services.buffer import BufferQueue


class FakeInflux:
    """Minimal InfluxDB stand-in that records the last write_batch payload."""

    def __init__(self) -> None:
        self.is_connected = True
        self.captured: list[dict] | None = None

    async def write_batch(self, readings: list[dict]) -> int:
        self.captured = [dict(r) for r in readings]
        return len(readings)


@pytest.fixture
async def isolated_buffer(monkeypatch) -> AsyncIterator[tuple[BufferQueue, FakeInflux]]:
    """A BufferQueue backed by in-memory SQLite and a fake InfluxDB client."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(buffer_module, "async_session_factory", factory)

    fake_influx = FakeInflux()
    monkeypatch.setattr(buffer_module, "influx_client", fake_influx)

    # Fresh instance (avoid the module-level singleton's state).
    bq = BufferQueue()
    bq._cloud_available = False  # force the SQLite buffering path in add()
    yield bq, fake_influx

    await engine.dispose()


async def test_flush_preserves_register_identity(isolated_buffer) -> None:
    """Each flushed reading must carry register_id and register_name."""
    bq, fake = isolated_buffer

    base = datetime(2026, 1, 1, 12, 0, 0)
    payload = [
        ("airTemperature", 1, 25.3),
        ("solarRadiation", 2, 812.0),
    ]
    for i, (name, rid, val) in enumerate(payload):
        await bq.add(
            sensor_id=10,
            sensor_name="estacion",
            value=val,
            raw_value=val,
            timestamp=base.replace(minute=i),
            register_id=rid,
            register_name=name,
        )

    synced = await bq.flush()

    assert synced == len(payload)
    assert fake.captured is not None
    assert len(fake.captured) == len(payload)

    captured_by_register = {row["register_id"]: row for row in fake.captured}
    for name, rid, _val in payload:
        assert rid in captured_by_register, f"register {rid} missing from flush batch"
        row = captured_by_register[rid]
        assert row["register_id"] == rid
        assert row["register_name"] == name, (
            f"register_name lost in flush for register {rid}: {row}"
        )


async def test_single_register_readings_still_flush(isolated_buffer) -> None:
    """Readings without a register must keep flushing (no register_id key)."""
    bq, fake = isolated_buffer

    await bq.add(
        sensor_id=20,
        sensor_name="simple",
        value=42.0,
        raw_value=42.0,
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
    )

    synced = await bq.flush()

    assert synced == 1
    assert fake.captured is not None
    assert len(fake.captured) == 1
    row = fake.captured[0]
    # Single-register readings carry no register identity.
    assert row.get("register_id") is None
