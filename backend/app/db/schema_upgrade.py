"""Expand-only schema steps run at startup (DaTaK has no migration tool).

Each step only ADDS what is missing; nothing is dropped, renamed or rewritten,
and running it again is a no-op.
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = structlog.get_logger()

# column name -> SQL type (all nullable: NULL = behaviour of the previous release)
REGISTER_DECODING_COLUMNS: dict[str, str] = {
    "data_type": "VARCHAR(10)",
    "word_order": "VARCHAR(10)",
    "bit_offset": "INTEGER",
    "bit_length": "INTEGER",
}


def add_missing_register_columns(conn: Connection) -> list[str]:
    """Add the register decoding columns to ``sensor_registers`` if absent (SQLite).

    Returns the names of the columns added. A missing table is left alone:
    ``create_all`` creates it with the columns.
    """
    if conn.dialect.name != "sqlite":
        raise RuntimeError(f"schema upgrade supports SQLite only, got {conn.dialect.name}")
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(sensor_registers)"))}
    if not existing:
        return []
    added: list[str] = []
    for name, sql_type in REGISTER_DECODING_COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE sensor_registers ADD COLUMN {name} {sql_type}"))
            added.append(name)
    if added:
        logger.info("Schema upgraded: sensor_registers columns added", columns=added)
    return added
