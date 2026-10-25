"""Per-device topic routing for the northbound MQTT publisher."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import structlog

from app.config import DeviceRoute
from app.services import cloud_sync as cs

TOPIC_A = "/0123456789abcdef/dev-a/attrs"
TOPIC_B = "/0123456789abcdef/dev-b/attrs"

DEVICES = [
    DeviceRoute(
        externalId="dev-a",
        name="A",
        topic=TOPIC_A,
        measurements=[
            {"key": "temp", "sdmAttribute": "airTemperature", "unit": "CEL"},
        ],
    ),
    DeviceRoute(
        externalId="dev-b",
        name="B",
        topic=TOPIC_B,
        measurements=[
            {"key": "relativeHumidity", "sdmAttribute": "relativeHumidity", "unit": "P1"},
        ],
    ),
]


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.setattr(cs.settings, "digital_twin_enabled", True)
    monkeypatch.setattr(cs.settings, "digital_twin_topic", "")
    monkeypatch.setattr(cs.settings, "digital_twin_devices", list(DEVICES))
    monkeypatch.setattr(cs.settings, "digital_twin_send_timestamp", False)
    s = cs.CloudSync()
    s._connected = True
    s._client = AsyncMock()
    return s


async def _send(s, name="Air temp", attribute="airTemperature", twin=None, value=21.5):
    return await s.send_reading(
        sensor_id=1,
        sensor_name=name,
        value=value,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        entity_id=twin,
        attribute=attribute,
    )


async def test_reading_goes_to_its_device_topic(sync):
    assert await _send(sync, twin="dev-a") is True
    topic, payload = sync._client.publish.call_args.args
    assert topic == TOPIC_A
    # platform maps the measurement key, not the SDM attribute
    assert payload == '{"temp": 21.5}'

    sync._client.publish.reset_mock()
    assert await _send(sync, name="Hum", attribute="relativeHumidity", twin="dev-b", value=50)
    topic, payload = sync._client.publish.call_args.args
    assert topic == TOPIC_B
    assert payload == '{"relativeHumidity": 50}'


async def test_twin_entity_urn_resolves_by_external_id(sync):
    assert await _send(sync, twin="urn:ngsi-ld:Device:acme:dev-a")
    assert sync._client.publish.call_args.args[0] == TOPIC_A


async def test_unknown_twin_entity_is_not_published_and_logged(sync):
    with structlog.testing.capture_logs() as logs:
        assert await _send(sync, twin="dev-zzz") is False
    sync._client.publish.assert_not_called()
    skipped = [entry for entry in logs if entry["event"] == "Sensor skipped: not a platform device"]
    assert skipped and skipped[0]["twin_entity_id"] == "dev-zzz"
    assert skipped[0]["sensor_name"] == "Air temp"


async def test_attribute_without_measurement_is_not_published(sync):
    assert await _send(sync, attribute="windSpeed", twin="dev-a") is False
    sync._client.publish.assert_not_called()


async def test_legacy_single_topic_when_no_twin_entity(sync, monkeypatch):
    monkeypatch.setattr(cs.settings, "digital_twin_devices", [])
    monkeypatch.setattr(cs.settings, "digital_twin_topic", "/legacy/topic")
    assert await _send(sync, twin="whatever")
    topic, payload = sync._client.publish.call_args.args
    assert topic == "/legacy/topic"
    assert payload == '{"airTemperature": 21.5}'


async def test_sensor_without_twin_entity_uses_legacy_topic_if_set(sync, monkeypatch):
    monkeypatch.setattr(cs.settings, "digital_twin_topic", "/legacy/topic")
    assert await _send(sync, twin=None)
    assert sync._client.publish.call_args.args[0] == "/legacy/topic"


async def test_sensor_without_twin_entity_and_no_legacy_topic_is_skipped(sync):
    assert await _send(sync, twin=None) is False
    sync._client.publish.assert_not_called()


async def test_timestamp_sent_only_if_supported(sync, monkeypatch):
    await _send(sync, twin="dev-a")
    assert "TimeInstant" not in sync._client.publish.call_args.args[1]

    monkeypatch.setattr(cs.settings, "digital_twin_send_timestamp", True)
    await _send(sync, twin="dev-a")
    assert '"TimeInstant": "2026-01-01T00:00:00Z"' in sync._client.publish.call_args.args[1]
