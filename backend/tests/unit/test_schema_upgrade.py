"""Expand-only startup step: add the register decoding columns to an old database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.schema_upgrade import REGISTER_DECODING_COLUMNS, add_missing_register_columns

# sensor_registers exactly as the previous release created it (no decoding columns)
OLD_DDL = """
CREATE TABLE sensor_registers (
    id INTEGER NOT NULL, sensor_id INTEGER NOT NULL, name VARCHAR(100) NOT NULL,
    address INTEGER NOT NULL, count INTEGER NOT NULL, register_type VARCHAR(20) NOT NULL,
    data_formula VARCHAR(200) NOT NULL, unit VARCHAR(20), decimal_places INTEGER NOT NULL,
    last_value FLOAT, last_raw_value FLOAT, twin_attribute VARCHAR(50),
    created_at DATETIME NOT NULL, updated_at DATETIME,
    PRIMARY KEY (id), FOREIGN KEY(sensor_id) REFERENCES sensors (id) ON DELETE CASCADE
)
"""
INSERT = (
    "INSERT INTO sensor_registers (id, sensor_id, name, address, count, register_type, "
    "data_formula, decimal_places, created_at) "
    "VALUES (1, 1, 'temp', 30, 2, 'holding', 'val / 10', 2, '2026-01-01 00:00:00')"
)


@pytest.fixture
async def old_engine(tmp_path: Path) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(OLD_DDL))
        await conn.execute(text(INSERT))
    yield engine  # type: ignore[misc]
    await engine.dispose()


async def _columns(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(sensor_registers)"))).all()
    return [r[1] for r in rows]


async def test_old_database_gains_the_four_columns_and_keeps_its_rows(old_engine: AsyncEngine) -> None:
    before = await _columns(old_engine)
    assert not set(REGISTER_DECODING_COLUMNS) & set(before)

    async with old_engine.begin() as conn:
        await conn.run_sync(add_missing_register_columns)

    after = await _columns(old_engine)
    assert set(REGISTER_DECODING_COLUMNS) == {"data_type", "word_order", "bit_offset", "bit_length"}
    assert set(REGISTER_DECODING_COLUMNS) <= set(after)
    assert after[: len(before)] == before  # expand only: nothing reordered, dropped or renamed

    async with old_engine.connect() as conn:
        row = (await conn.execute(text("SELECT * FROM sensor_registers"))).mappings().one()
    assert (row["name"], row["address"], row["count"], row["data_formula"]) == ("temp", 30, 2, "val / 10")
    assert row["data_type"] is None  # NULL => today's behaviour
    assert row["word_order"] is None and row["bit_offset"] is None and row["bit_length"] is None


async def test_step_is_idempotent(old_engine: AsyncEngine) -> None:
    for _ in range(3):
        async with old_engine.begin() as conn:
            await conn.run_sync(add_missing_register_columns)
    cols = await _columns(old_engine)
    assert len(cols) == len(set(cols))


async def test_partially_upgraded_database_gets_only_the_missing_columns(old_engine: AsyncEngine) -> None:
    async with old_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE sensor_registers ADD COLUMN data_type VARCHAR(10)"))
        await conn.run_sync(add_missing_register_columns)
    assert set(REGISTER_DECODING_COLUMNS) <= set(await _columns(old_engine))


async def test_missing_table_is_left_alone(tmp_path: Path) -> None:
    """create_all makes the table (with the columns) on a fresh database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(add_missing_register_columns)
            tables = (await conn.execute(text("SELECT name FROM sqlite_master"))).all()
        assert tables == []
    finally:
        await engine.dispose()


async def test_init_db_upgrades_an_old_database_and_orm_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import session as session_module
    from app.models.sensor import SensorRegister

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(OLD_DDL))
        await conn.execute(text(INSERT))
    monkeypatch.setattr(session_module, "engine", engine)
    monkeypatch.setattr(
        session_module, "async_session_factory",
        async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
    )
    try:
        await session_module.init_db()
        await session_module.init_db()  # second boot: still fine
        async with session_module.async_session_factory() as session:
            reg = (await session.execute(text("select 1"))).scalar()
            assert reg == 1
            from sqlalchemy import select

            row = (await session.execute(select(SensorRegister))).scalar_one()
            assert row.name == "temp" and row.data_type is None and row.word_order is None
    finally:
        await engine.dispose()


async def test_non_sqlite_url_logs_a_warning_and_skips(caplog: pytest.LogCaptureFixture) -> None:
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.dialect.name = "postgresql"
    caplog.set_level("WARNING")
    assert add_missing_register_columns(conn) == []
    conn.execute.assert_not_called()
    assert "skipped" in caplog.text
