"""Northbound window aggregation: one message per Device per window."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.config import DeviceRoute
from app.services import cloud_sync as cs
from app.services.northbound_window import WindowAggregator

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
TOPIC_A = "/0123456789abcdef/dev-a/attrs"
TOPIC_R = "/0123456789abcdef/rain-1/attrs"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# ---- aggregator ------------------------------------------------------------

def test_windows_are_aligned_to_the_clock_and_stamped_with_their_end():
    w = WindowAggregator(60)
    for s in (0, 2, 58):
        w.add(TOPIC_A, "temp", 10.0 + s, at(s))
    w.add(TOPIC_A, "temp", 99.0, at(60))  # next window
    assert w.pop_closed(at(59)) == []
    [closed] = w.pop_closed(at(60))
    assert closed.topic == TOPIC_A
    assert closed.end == at(60)
    assert closed.values == {"temp": round((10 + 12 + 68) / 3, 4)}


def test_keys_of_a_device_go_in_one_window():
    w = WindowAggregator(60)
    w.add(TOPIC_A, "temp", 20.0, at(1))
    w.add(TOPIC_A, "hum", 50.0, at(1))
    w.add(TOPIC_A, "hum", 60.0, at(3))
    [closed] = w.pop_closed(at(61))
    assert closed.values == {"temp": 20.0, "hum": 55.0}


def test_counter_attributes_carry_the_last_value_not_the_mean():
    w = WindowAggregator(60, last_value_keys=["precipitationAccumulated"])
    # counter rollover inside the window: the mean (3276.75) would be nonsense
    w.add(TOPIC_R, "rain", 6553.4, at(10), attribute="precipitationAccumulated")
    w.add(TOPIC_R, "rain", 0.1, at(30), attribute="precipitationAccumulated")
    [closed] = w.pop_closed(at(60))
    assert closed.values == {"rain": 0.1}


def test_last_value_rule_matches_the_platform_key_too():
    w = WindowAggregator(60, last_value_keys=["rain"])
    w.add(TOPIC_R, "rain", 1.0, at(30))
    w.add(TOPIC_R, "rain", 2.0, at(10))  # older sample arriving later
    [closed] = w.pop_closed(at(60))
    assert closed.values == {"rain": 1.0}


def test_invalid_samples_are_left_out_and_empty_windows_send_nothing():
    w = WindowAggregator(60)
    assert w.add(TOPIC_A, "temp", float("nan"), at(1)) is False
    assert w.add(TOPIC_A, "temp", None, at(2)) is False
    assert w.add(TOPIC_A, "temp", float("inf"), at(3)) is False
    assert w.pop_closed(at(61)) == []
    w.add(TOPIC_A, "temp", 5.0, at(61))
    w.add(TOPIC_A, "hum", float("nan"), at(62))
    [closed] = w.pop_closed(at(121))
    assert closed.values == {"temp": 5.0}  # key without a valid sample omitted


def test_grace_keeps_the_window_open_past_its_end():
    w = WindowAggregator(60)
    w.add(TOPIC_A, "temp", 1.0, at(59))
    assert w.pop_closed(at(61), grace_s=3) == []
    w.add(TOPIC_A, "temp", 3.0, at(59.9))  # polled at the boundary, arrives late
    [closed] = w.pop_closed(at(63), grace_s=3)
    assert closed.values == {"temp": 2.0}


def test_reading_for_a_closed_window_is_dropped_not_sent_twice():
    w = WindowAggregator(60)
    w.add(TOPIC_A, "temp", 1.0, at(10))
    assert len(w.pop_closed(at(60))) == 1
    assert w.add(TOPIC_A, "temp", 2.0, at(20)) is False
    assert w.late_dropped == 1
    assert w.pop_closed(at(200)) == []


def test_interval_must_be_positive():
    with pytest.raises(ValueError):
        WindowAggregator(0)


# ---- cloud sync -------------------------------------------------------------

DEVICES = [
    DeviceRoute(externalId="dev-a", topic=TOPIC_A, measurements=[
        {"key": "airTemperature", "sdmAttribute": "airTemperature"},
        {"key": "relativeHumidity", "sdmAttribute": "relativeHumidity"},
    ]),
    DeviceRoute(externalId="rain-1", topic=TOPIC_R, measurements=[
        {"key": "precipitationAccumulated", "sdmAttribute": "precipitationAccumulated"},
    ]),
]


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.setattr(cs.settings, "digital_twin_enabled", True)
    monkeypatch.setattr(cs.settings, "digital_twin_topic", "")
    monkeypatch.setattr(cs.settings, "digital_twin_devices", list(DEVICES))
    monkeypatch.setattr(cs.settings, "digital_twin_send_timestamp", True)
    monkeypatch.setattr(cs.settings, "digital_twin_publish_interval_s", 60)
    monkeypatch.setattr(cs.settings, "digital_twin_last_value_attributes", ["precipitationAccumulated"])
    s = cs.CloudSync()
    s._connected = True
    s._client = AsyncMock()
    yield s
    if s._flush_task is not None:
        s._flush_task.cancel()


async def _send(s, attribute, value, ts, twin="dev-a"):
    return await s.send_reading(sensor_id=1, sensor_name="x", value=value, timestamp=ts,
                                entity_id=twin, attribute=attribute)


async def test_windowed_readings_are_published_once_per_device_with_window_end(sync):
    assert await _send(sync, "airTemperature", 10.0, at(1)) is True
    await _send(sync, "airTemperature", 12.0, at(3))
    await _send(sync, "relativeHumidity", 70.0, at(2))
    await _send(sync, "precipitationAccumulated", 6553.5, at(5), twin="rain-1")
    await _send(sync, "precipitationAccumulated", 0.2, at(40), twin="rain-1")
    sync._client.publish.assert_not_called()  # nothing sent per reading

    assert await sync.flush_closed(at(70)) == 2
    sent = {c.args[0]: json.loads(c.args[1]) for c in sync._client.publish.call_args_list}
    assert sent[TOPIC_A] == {"airTemperature": 11.0, "relativeHumidity": 70.0,
                             "TimeInstant": "2026-01-01T12:01:00Z"}
    assert sent[TOPIC_R] == {"precipitationAccumulated": 0.2, "TimeInstant": "2026-01-01T12:01:00Z"}


async def test_windows_accumulate_while_disconnected_and_are_dropped_if_still_down(sync):
    sync._connected = False
    assert await _send(sync, "airTemperature", 10.0, at(1)) is True
    assert await sync.flush_closed(at(70)) == 0
    sync._client.publish.assert_not_called()


async def test_interval_zero_keeps_publishing_every_reading(sync, monkeypatch):
    monkeypatch.setattr(cs.settings, "digital_twin_publish_interval_s", 0)
    assert await _send(sync, "airTemperature", 10.0, at(1)) is True
    topic, payload = sync._client.publish.call_args.args
    assert topic == TOPIC_A
    assert json.loads(payload) == {"airTemperature": 10.0, "TimeInstant": "2026-01-01T12:00:01Z"}
    assert sync._flush_task is None


async def test_unrouted_readings_are_not_windowed(sync):
    assert await _send(sync, "windSpeed", 3.0, at(1)) is False
    assert await sync.flush_closed(at(70)) == 0


async def test_close_drops_open_windows_and_stops_the_flusher(sync):
    await _send(sync, "airTemperature", 10.0, at(1))
    assert sync._flush_task is not None
    await sync.close()
    assert sync._flush_task is None
    assert sync._window.pop_closed(at(1000)) == []


def test_settings_read_window_options_from_yaml(tmp_path):
    from app.config import Settings

    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        "digital_twin:\n"
        "  enabled: false\n"
        "  publish_interval_s: 60\n"
        "  last_value_attributes:\n"
        "    - precipitationAccumulated\n"
    )
    s = Settings.from_yaml(cfg)
    assert s.digital_twin_publish_interval_s == 60
    assert s.digital_twin_last_value_attributes == ["precipitationAccumulated"]
    assert Settings().digital_twin_publish_interval_s == 0  # off unless configured
