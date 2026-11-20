"""CSV reports: one row per register, clock-aligned windows, register units."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db import influx as influx_mod
from app.services import csv_engine as ce

NOW = datetime(2026, 11, 20, 9, 24, 18, tzinfo=UTC)


def _sensor(sid, name, unit="", registers=()):
    return SimpleNamespace(id=sid, name=name, unit=unit,
                           registers=[SimpleNamespace(name=n, unit=u) for n, u in registers])


SOIL = _sensor(23, "Soil-TH-Sensor", unit="%", registers=[("Soil Moisture", "%"), ("Soil Temperature", "°C")])
PAR = _sensor(26, "PAR-Quantum-Sensor", unit="umol/m2s")


def _st(mean, count=30):
    return {"mean": mean, "min": mean - 1, "max": mean + 1, "stddev": 0.5, "count": count}


# ---- windows ---------------------------------------------------------------

@pytest.mark.parametrize("interval,start,end", [
    (timedelta(minutes=1), "09:23", "09:24"),
    (timedelta(minutes=5), "09:15", "09:20"),
    (timedelta(hours=1), "08:00", "09:00"),
])
def test_window_is_the_latest_closed_one_aligned_to_the_clock(interval, start, end):
    s, e = ce.window_bounds(NOW, interval)
    assert (s.strftime("%H:%M"), e.strftime("%H:%M")) == (start, end)
    assert e - s == interval


def test_consecutive_windows_do_not_overlap():
    a = ce.window_bounds(NOW, timedelta(minutes=1))
    b = ce.window_bounds(NOW + timedelta(minutes=1), timedelta(minutes=1))
    assert a[1] == b[0]


# ---- rows ------------------------------------------------------------------

def test_each_register_is_its_own_row_with_its_unit():
    stats = {"Soil-TH-Sensor": {"Soil Temperature": _st(11.7), "Soil Moisture": _st(21.9)}}
    rows = ce.register_rows([SOIL], stats)
    assert [(r["register_name"], r["unit"], r["mean"]) for r in rows] == [
        ("Soil Moisture", "%", 21.9), ("Soil Temperature", "°C", 11.7)]
    assert all(r["sensor_id"] == 23 and r["count"] == 30 for r in rows)


def test_single_register_readings_use_the_sensor_unit():
    rows = ce.register_rows([PAR], {"PAR-Quantum-Sensor": {None: _st(185.0)}})
    assert rows == [{"sensor_id": 26, "sensor_name": "PAR-Quantum-Sensor", "register_name": "",
                     "unit": "umol/m2s", "count": 30, "mean": 185.0, "min": 184.0, "max": 186.0,
                     "stddev": 0.5}]


def test_register_no_longer_configured_keeps_its_name_without_unit():
    rows = ce.register_rows([SOIL], {"Soil-TH-Sensor": {"Old Register": _st(5.0)}})
    assert rows[0]["register_name"] == "Old Register" and rows[0]["unit"] == ""


def test_registers_without_readings_are_left_out():
    stats = {"Soil-TH-Sensor": {"Soil Moisture": _st(21.9, count=0)}}
    assert ce.register_rows([SOIL], stats) == []


# ---- influx parsing ----------------------------------------------------------

class _Rec:
    def __init__(self, result, register, value):
        self.values = {"result": result, "register_name": register}
        self._value = value

    def get_value(self):
        return self._value


def test_statistics_are_folded_per_register():
    tables = [SimpleNamespace(records=[_Rec("mean", "Soil Moisture", 21.9), _Rec("mean", "Soil Temperature", 11.7)]),
              SimpleNamespace(records=[_Rec("count", "Soil Moisture", 30), _Rec("count", "Soil Temperature", 30)]),
              SimpleNamespace(records=[_Rec("mean", None, 185.0), _Rec("other", None, 1)])]
    out = influx_mod.parse_register_statistics(tables)
    assert out["Soil Moisture"]["mean"] == 21.9 and out["Soil Moisture"]["count"] == 30
    assert out["Soil Temperature"]["mean"] == 11.7
    assert out[None]["mean"] == 185.0 and out[None]["min"] is None


# ---- report files ------------------------------------------------------------

@pytest.fixture
def generator(tmp_path, monkeypatch):
    gen = ce.CSVReportGenerator()
    gen._output_dir = tmp_path
    calls = []

    async def load(active_only):  # noqa: ARG001
        return [SOIL, PAR]

    async def stats(sensor_name, start, stop):
        calls.append((sensor_name, start, stop))
        return {"Soil-TH-Sensor": {"Soil Moisture": _st(21.9), "Soil Temperature": _st(11.7)},
                "PAR-Quantum-Sensor": {None: _st(185.0)}}[sensor_name]

    monkeypatch.setattr(gen, "_load_sensors", load)
    monkeypatch.setattr(ce.influx_client, "query_register_statistics", stats)
    gen.calls = calls
    return gen


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def test_report_covers_exactly_the_closed_window(generator):
    path = await generator.generate_report("5min", now=NOW)
    assert path.name == "report_5min_2026-11-20_0920.csv"
    assert {c[1:] for c in generator.calls} == {("2026-11-20T09:15:00Z", "2026-11-20T09:20:00Z")}
    rows = _read(path)
    assert list(rows[0]) == ce.REPORT_FIELDS
    assert [(r["sensor_name"], r["register_name"], r["unit"], r["mean"]) for r in rows] == [
        ("Soil-TH-Sensor", "Soil Moisture", "%", "21.9"),
        ("Soil-TH-Sensor", "Soil Temperature", "°C", "11.7"),
        ("PAR-Quantum-Sensor", "", "umol/m2s", "185.0"),
    ]
    assert rows[0]["timestamp"] == "2026-11-20T09:20:00Z"
    assert rows[0]["window_start"] == "2026-11-20T09:15:00Z"


async def test_report_for_the_same_window_rewrites_the_same_file(generator):
    a = await generator.generate_report("1min", now=NOW)
    b = await generator.generate_report("1min", now=NOW + timedelta(seconds=20))
    assert a == b


async def test_daily_summary_covers_the_whole_utc_day_per_register(generator):
    path = await generator.generate_daily_summary(datetime(2026, 11, 19, 15, 0, tzinfo=UTC))
    assert path.name == "daily_summary_2026-11-19.csv"
    assert {c[1:] for c in generator.calls} == {("2026-11-19T00:00:00Z", "2026-11-20T00:00:00Z")}
    rows = _read(path)
    assert list(rows[0]) == ce.DAILY_FIELDS
    assert [(r["register_name"], r["unit"]) for r in rows] == [
        ("Soil Moisture", "%"), ("Soil Temperature", "°C"), ("", "umol/m2s")]


async def test_unknown_interval_writes_nothing(generator):
    assert await generator.generate_report("2min", now=NOW) is None


# ---- raw export --------------------------------------------------------------

class _PivotRec:
    def __init__(self, t, **cols):
        self._t = t
        self.values = {"_time": t, "result": "_result", "table": 0, **cols}

    def get_time(self):
        return self._t


async def test_raw_export_has_one_column_per_register(monkeypatch):
    t = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    tables = [SimpleNamespace(records=[
        _PivotRec(t, **{"Soil-TH-Sensor / Soil Moisture": 21.9, "Soil-TH-Sensor / Soil Temperature": 11.7,
                        "PAR-Quantum-Sensor": 185.0}),
    ])]
    captured = {}

    class _Api:
        async def query(self, flux, org):  # noqa: ARG002
            captured["flux"] = flux
            return tables

    client = influx_mod.influx_client
    monkeypatch.setattr(client, "_client", SimpleNamespace(query_api=lambda: _Api()))
    out = await client.export_data(["Soil-TH-Sensor", "PAR-Quantum-Sensor"],
                                   "2026-11-20T08:00:00Z", "2026-11-20T10:00:00Z")
    lines = out.strip().splitlines()
    assert lines[0] == "time,PAR-Quantum-Sensor,Soil-TH-Sensor / Soil Moisture,Soil-TH-Sensor / Soil Temperature"
    assert lines[1].endswith(",185.0,21.9,11.7")
    assert 'columnKey: ["column"]' in captured["flux"]
