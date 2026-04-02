"""Integration tests for API endpoints."""

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token


@pytest.mark.asyncio
class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    async def test_health_check(self, async_client: AsyncClient):
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


@pytest.mark.asyncio
class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    async def test_login_success(self, async_client: AsyncClient, test_user):
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_invalid_password(self, async_client: AsyncClient, test_user):
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    async def test_login_invalid_user(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "nonexistent", "password": "password"},
        )
        assert response.status_code == 401

    async def test_get_me_authenticated(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"

    async def test_get_me_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401  # No auth header


@pytest.mark.asyncio
class TestSensorEndpoints:
    """Tests for sensor CRUD endpoints."""

    async def test_list_sensors_empty(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        response = await async_client.get(
            "/api/sensors",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_create_sensor(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        sensor_data = {
            "name": "temp_sensor_1",
            "protocol": "MODBUS_TCP",
            "connection_params": {
                "host": "192.168.1.10",
                "port": 502,
                "slave_id": 1,
                "address": 40001,
            },
            "data_formula": "val / 10",
            "unit": "°C",
        }
        response = await async_client.post(
            "/api/sensors",
            json=sensor_data,
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "temp_sensor_1"
        assert data["protocol"] == "MODBUS_TCP"
        assert data["data_formula"] == "val / 10"

    async def test_create_sensor_invalid_formula(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        sensor_data = {
            "name": "bad_sensor",
            "protocol": "MODBUS_TCP",
            "connection_params": {"host": "192.168.1.10", "port": 502},
            "data_formula": "import os",  # Malicious
        }
        response = await async_client.post(
            "/api/sensors",
            json=sensor_data,
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 400
        assert "formula" in response.json()["detail"].lower()

    async def test_create_sensor_invalid_protocol(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        sensor_data = {
            "name": "bad_protocol",
            "protocol": "INVALID_PROTOCOL",
            "connection_params": {},
        }
        response = await async_client.post(
            "/api/sensors",
            json=sensor_data,
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 400

    async def test_test_formula_endpoint(self, async_client: AsyncClient, test_user):
        token = create_access_token(data={"sub": test_user.username, "role": test_user.role})
        response = await async_client.post(
            "/api/sensors/test-formula",
            json={"formula": "val * 2", "test_value": 50.0},
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["result"] == 100.0
