"""generate_device_profile must emit one mapping per register (Opcion A: identity).

Multi-register sensors contribute one mapping per register, with
``incoming_key`` == ``target_attribute`` == the SDM attribute DaTaK publishes
(``register.twin_attribute`` or the auto-mapper fallback on the register name).
Single-register sensors keep the legacy one-mapping-per-sensor behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.sensor import Sensor, SensorRegister
from app.services import cloud_sync as cloud_sync_module
from app.services.cloud_sync import cloud_sync


def _sensor(**kw: object) -> Sensor:
    defaults: dict[str, object] = {
        "name": "sensor",
        "protocol": "MODBUS_TCP",
        "connection_params": {"host": "x"},
    }
    defaults.update(kw)
    return Sensor(**defaults)  # type: ignore[arg-type]


def _register(sensor: Sensor, **kw: object) -> SensorRegister:
    defaults: dict[str, object] = {"name": "reg", "address": 0}
    defaults.update(kw)
    return SensorRegister(sensor=sensor, **defaults)  # type: ignore[arg-type]


@pytest.fixture
async def profile_session(monkeypatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cloud_sync_module, "async_session_factory", factory)
    yield factory
    await engine.dispose()


async def test_profile_one_mapping_per_register(profile_session) -> None:
    async with profile_session() as s:
        sensor = _sensor(name="estacion")
        _register(sensor, name="Panel Temperature", address=0, twin_attribute=None)
        _register(sensor, name="Irradiance", address=1, twin_attribute="solarRadiation")
        s.add(sensor)
        await s.commit()

    profile = await cloud_sync.generate_device_profile()

    mappings = profile["mappings"]
    incoming = {m["incoming_key"] for m in mappings}
    assert incoming == {"airTemperature", "solarRadiation"}
    for m in mappings:  # identity mapping
        assert m["incoming_key"] == m["target_attribute"]


async def test_profile_single_register_legacy(profile_session) -> None:
    """A sensor without registers keeps one mapping from its name."""
    async with profile_session() as s:
        s.add(_sensor(name="Humidity Sensor"))
        await s.commit()

    profile = await cloud_sync.generate_device_profile()

    mappings = profile["mappings"]
    assert len(mappings) == 1
    assert mappings[0]["incoming_key"] == "relativeHumidity"


async def test_profile_dedupes_repeated_sdm(profile_session) -> None:
    """Two registers collapsing to the same SDM attribute yield one mapping."""
    async with profile_session() as s:
        sensor = _sensor(name="temps")
        _register(sensor, name="Temp 1", address=0)
        _register(sensor, name="Temp 2", address=1)
        s.add(sensor)
        await s.commit()

    profile = await cloud_sync.generate_device_profile()

    mappings = profile["mappings"]
    assert len(mappings) == 1
    assert mappings[0]["incoming_key"] == "airTemperature"
