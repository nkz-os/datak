"""Register decoding fields through the API, the driver hand-over and the startup loader."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import create_access_token
from app.drivers import modbus_bus
from app.services.orchestrator import orchestrator


@pytest_asyncio.fixture(autouse=True)
async def _clean() -> AsyncGenerator[None, None]:
    modbus_bus._reset_read_only_latch()
    yield
    for sid in list(orchestrator._drivers):
        await orchestrator.remove_sensor(sid)
    modbus_bus._reset_read_only_latch()


def _auth(user: Any) -> dict[str, str]:
    token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"Authorization": f"Bearer {token.access_token}"}


def _body(registers: list[dict[str, Any]], name: str = "trk") -> dict[str, Any]:
    return {
        "name": name, "protocol": "MODBUS_TCP",
        "connection_params": {"host": "trk.example", "port": 502, "slave_id": 1},
        "registers": registers,
    }


def _reg(**extra: Any) -> dict[str, Any]:
    return {"name": "angle", "address": 6, "count": 2, "register_type": "holding", **extra}


async def test_create_with_typed_register_roundtrips(async_client: AsyncClient, test_user: Any) -> None:
    body = _body([
        _reg(data_type="float32", word_order="low_first"),
        {"name": "soc", "address": 13, "count": 1, "data_type": "uint16", "bit_offset": 0, "bit_length": 8},
    ])
    response = await async_client.post("/api/sensors", json=body, headers=_auth(test_user))
    assert response.status_code == 201, response.text
    regs = {r["name"]: r for r in response.json()["registers"]}
    assert (regs["angle"]["data_type"], regs["angle"]["word_order"]) == ("float32", "low_first")
    assert regs["angle"]["bit_offset"] is None
    assert (regs["soc"]["data_type"], regs["soc"]["bit_offset"], regs["soc"]["bit_length"]) == ("uint16", 0, 8)
    assert regs["soc"]["word_order"] == "high_first"  # default

    # the running driver got them (hot-reload path)
    sid = response.json()["id"]
    driver_regs = {r["name"]: r for r in orchestrator._drivers[sid]._registers}
    assert driver_regs["angle"]["data_type"] == "float32"
    assert driver_regs["angle"]["word_order"] == "low_first"
    assert driver_regs["soc"]["bit_length"] == 8


async def test_legacy_register_without_new_fields_is_unchanged(async_client: AsyncClient, test_user: Any) -> None:
    response = await async_client.post(
        "/api/sensors", json=_body([{"name": "t", "address": 0}]), headers=_auth(test_user)
    )
    assert response.status_code == 201, response.text
    reg = response.json()["registers"][0]
    assert reg["data_type"] is None and reg["bit_offset"] is None and reg["bit_length"] is None
    assert reg["word_order"] == "high_first"


@pytest.mark.parametrize(
    "extra",
    [
        {"data_type": "float64"},
        {"data_type": "float32", "count": 1},
        {"data_type": "uint16", "count": 2},
        {"data_type": "uint16", "count": 1, "bit_offset": 12, "bit_length": 8},
        {"data_type": "uint16", "count": 1, "bit_offset": 16, "bit_length": 1},
        {"data_type": "uint16", "count": 1, "bit_offset": 0},
        {"data_type": "uint32", "count": 2, "bit_offset": 0, "bit_length": 2},
        {"data_type": "float32", "word_order": "middle"},
        {"data_type": None, "bit_offset": 0, "bit_length": 1},
        {"data_type": "uint16", "count": 1, "register_type": "coil"},
    ],
    ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()),
)
async def test_invalid_decoding_is_422_on_create(
    async_client: AsyncClient, test_user: Any, extra: dict[str, Any]
) -> None:
    response = await async_client.post(
        "/api/sensors", json=_body([_reg(**extra)]), headers=_auth(test_user)
    )
    assert response.status_code == 422, response.text


async def test_update_replaces_registers_with_decoding_and_validates(
    async_client: AsyncClient, test_user: Any
) -> None:
    created = await async_client.post(
        "/api/sensors", json=_body([{"name": "t", "address": 0}]), headers=_auth(test_user)
    )
    sid = created.json()["id"]
    bad = await async_client.patch(
        f"/api/sensors/{sid}", json={"registers": [_reg(data_type="float32", count=1)]},
        headers=_auth(test_user),
    )
    assert bad.status_code == 422
    ok = await async_client.patch(
        f"/api/sensors/{sid}",
        json={"registers": [_reg(data_type="float32", word_order="low_first")]},
        headers=_auth(test_user),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["registers"][0]["data_type"] == "float32"
    assert orchestrator._drivers[sid]._registers[0]["word_order"] == "low_first"


async def test_startup_loader_hands_decoding_to_the_drivers(
    async_client: AsyncClient, test_user: Any, test_db: Any
) -> None:
    """After a restart the drivers are rebuilt from the database: decoding must survive."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.main import load_active_sensors

    created = await async_client.post(
        "/api/sensors",
        json=_body([_reg(data_type="float32", word_order="low_first")], name="restart"),
        headers=_auth(test_user),
    )
    sid = created.json()["id"]
    await orchestrator.remove_sensor(sid)  # simulate the process restart
    assert sid not in orchestrator._drivers

    factory = async_sessionmaker(test_db.bind, expire_on_commit=False)
    assert await load_active_sensors(factory) >= 1
    reg = orchestrator._drivers[sid]._registers[0]
    assert (reg["data_type"], reg["word_order"]) == ("float32", "low_first")
