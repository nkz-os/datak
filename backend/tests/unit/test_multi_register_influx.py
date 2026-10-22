"""InfluxDB write_batch must tag multi-register points with register identity.

Parallel to write_sensor_value (which already accepts tags), the batch path
used by BufferQueue.flush() must write register_id / register_name as tags so
the values of a multi-register sensor do not collapse together.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.db.influx import influx_client


class FakeWriteAPI:
    """Captures the Points handed to write() without touching InfluxDB."""

    def __init__(self) -> None:
        self.captured: list = []

    async def write(self, *, bucket: str, org: str, record) -> None:  # noqa: ARG002
        if isinstance(record, list):
            self.captured.extend(record)
        else:
            self.captured.append(record)


@pytest.fixture
async def patched_influx(monkeypatch) -> AsyncIterator[FakeWriteAPI]:
    fake_api = FakeWriteAPI()
    monkeypatch.setattr(influx_client, "_write_api", fake_api)
    monkeypatch.setattr(influx_client, "_connected", True)
    yield fake_api


def _line_protocols(fake_api: FakeWriteAPI) -> list[str]:
    return [p.to_line_protocol() for p in fake_api.captured]


async def test_write_batch_tags_register_identity(patched_influx) -> None:
    await influx_client.write_batch(
        [
            {
                "sensor_id": 10,
                "sensor_name": "estacion",
                "value": 25.3,
                "raw_value": 25.3,
                "register_id": 1,
                "register_name": "airTemperature",
            },
            {
                "sensor_id": 20,
                "sensor_name": "simple",
                "value": 42.0,
                "raw_value": 42.0,
            },
        ]
    )

    lines = _line_protocols(patched_influx)
    assert len(lines) == 2

    multi, single = lines
    assert "register_id=1" in multi
    assert "register_name=airTemperature" in multi
    # Single-register reading must not get a register tag.
    assert "register_id" not in single
    assert "register_name" not in single
