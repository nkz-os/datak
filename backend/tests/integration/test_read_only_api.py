"""Read-only sensors through the HTTP API: 409 on write, rules refused."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import create_access_token
from app.drivers import modbus_bus
from app.drivers.base import ReadOnlyEndpointError
from app.services.automation import automation_engine
from app.services.orchestrator import orchestrator


@pytest_asyncio.fixture(autouse=True)
async def _clean() -> AsyncGenerator[None, None]:
    modbus_bus._reset_read_only_latch()
    yield
    for sid in list(orchestrator._drivers):
        await orchestrator.remove_sensor(sid)
    automation_engine._rules.clear()
    modbus_bus._reset_read_only_latch()


def _auth(user: Any) -> dict[str, str]:
    token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"Authorization": f"Bearer {token.access_token}"}


def _sensor(name: str, host: str, **extra: Any) -> dict[str, Any]:
    params = {"host": host, "port": 502, "slave_id": 1, "address": 5, "register_type": "holding"}
    params.update(extra)
    return {"name": name, "protocol": "MODBUS_TCP", "connection_params": params}


async def _create(client: AsyncClient, user: Any, body: dict[str, Any]) -> int:
    response = await client.post("/api/sensors", json=body, headers=_auth(user))
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def test_write_to_read_only_sensor_answers_409(async_client: AsyncClient, test_user: Any) -> None:
    sid = await _create(async_client, test_user, _sensor("ro", "ro.example", read_only=True))
    response = await async_client.post(
        f"/api/sensors/{sid}/write", json={"value": 1.0}, headers=_auth(test_user)
    )
    assert response.status_code == 409
    assert "read-only" in response.json()["detail"]
    assert "1.0" not in response.json()["detail"]


async def test_write_to_sensor_on_configured_read_only_endpoint_answers_409(
    async_client: AsyncClient, test_user: Any
) -> None:
    previous = modbus_bus.current_settings()
    modbus_bus.configure(modbus_bus.BusSettings(read_only_endpoints=("cfg.example:502",)))
    try:
        sid = await _create(async_client, test_user, _sensor("cfg", "cfg.example"))
        response = await async_client.post(
            f"/api/sensors/{sid}/write", json={"value": 1.0}, headers=_auth(test_user)
        )
        assert response.status_code == 409
    finally:
        modbus_bus.configure(previous)


async def test_write_to_other_sensor_is_not_a_409(async_client: AsyncClient, test_user: Any) -> None:
    """A writable sensor on another bus still reaches the write path (500: gateway unreachable)."""
    await _create(async_client, test_user, _sensor("ro", "127.0.0.1", read_only=True, port=1502))
    sid = await _create(async_client, test_user, _sensor("rw", "127.0.0.1", port=1503))
    response = await async_client.post(
        f"/api/sensors/{sid}/write", json={"value": 1.0}, headers=_auth(test_user)
    )
    assert response.status_code != 409


async def test_orchestrator_write_sensor_raises_for_read_only(
    async_client: AsyncClient, test_user: Any
) -> None:
    sid = await _create(async_client, test_user, _sensor("ro", "ro.example", read_only=True))
    with pytest.raises(ReadOnlyEndpointError):
        await orchestrator.write_sensor(sid, 1.0)


async def test_rule_targeting_read_only_sensor_is_refused(
    async_client: AsyncClient, test_user: Any
) -> None:
    sid = await _create(async_client, test_user, _sensor("ro", "ro.example", read_only=True))
    response = await async_client.post(
        "/api/automation/rules",
        json={"name": "r", "condition": "True", "target_sensor_id": sid, "target_value": 1.0},
        headers=_auth(test_user),
    )
    assert response.status_code == 409
    assert "read-only" in response.json()["detail"]
    assert automation_engine._rules == {}


async def test_rule_targeting_inactive_read_only_sensor_is_refused(
    async_client: AsyncClient, test_user: Any
) -> None:
    """No running driver to ask: the stored sensor row decides."""
    body = _sensor("ro-off", "ro.example", read_only=True)
    sid = await _create(async_client, test_user, body)
    await orchestrator.remove_sensor(sid)
    response = await async_client.post(
        "/api/automation/rules",
        json={"name": "r", "condition": "True", "target_sensor_id": sid, "target_value": 1.0},
        headers=_auth(test_user),
    )
    assert response.status_code == 409


async def test_rule_targeting_sensor_on_a_locked_bus_is_refused(
    async_client: AsyncClient, test_user: Any
) -> None:
    """A writable-looking sensor sharing a bus with a read-only one cannot be automated."""
    await _create(async_client, test_user, _sensor("ro", "shared.example", read_only=True))
    sid = await _create(async_client, test_user, _sensor("plain", "shared.example"))
    response = await async_client.post(
        "/api/automation/rules",
        json={"name": "r", "condition": "True", "target_sensor_id": sid, "target_value": 1.0},
        headers=_auth(test_user),
    )
    assert response.status_code == 409


async def test_rule_targeting_writable_sensor_is_accepted(
    async_client: AsyncClient, test_user: Any
) -> None:
    sid = await _create(async_client, test_user, _sensor("rw", "rw.example"))
    response = await async_client.post(
        "/api/automation/rules",
        json={"name": "r", "condition": "True", "target_sensor_id": sid, "target_value": 1.0},
        headers=_auth(test_user),
    )
    assert response.status_code == 200


async def test_write_to_flagged_sensor_is_409_even_when_its_driver_is_not_running(
    async_client: AsyncClient, test_user: Any
) -> None:
    sid = await _create(async_client, test_user, _sensor("ro-off", "127.0.0.1", read_only=True))
    await orchestrator.remove_sensor(sid)
    modbus_bus._reset_read_only_latch()  # nothing in memory: only the stored row knows
    response = await async_client.post(
        f"/api/sensors/{sid}/write", json={"value": 1.0}, headers=_auth(test_user)
    )
    assert response.status_code == 409


async def test_write_to_sensor_on_configured_endpoint_is_409_even_when_driver_is_not_running(
    async_client: AsyncClient, test_user: Any
) -> None:
    previous = modbus_bus.current_settings()
    modbus_bus.configure(modbus_bus.BusSettings(read_only_endpoints=("localhost:502",)))
    try:
        sid = await _create(async_client, test_user, _sensor("cfg-off", "127.0.0.1"))
        await orchestrator.remove_sensor(sid)
        response = await async_client.post(
            f"/api/sensors/{sid}/write", json={"value": 1.0}, headers=_auth(test_user)
        )
        assert response.status_code == 409
    finally:
        modbus_bus.configure(previous)
