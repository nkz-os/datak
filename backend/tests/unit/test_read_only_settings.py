"""modbus.read_only_endpoints: yaml -> Settings -> BusSettings, and back to yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.drivers.modbus_bus import BusSettings


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "gateway.yaml"
    path.write_text(body)
    return path


def test_default_is_empty() -> None:
    assert Settings().modbus_read_only_endpoints == []


def test_loaded_from_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "modbus:\n  read_only_endpoints:\n    - plc.example:502\n    - plc2.example:1502\n")
    settings = Settings.from_yaml(path)
    assert settings.modbus_read_only_endpoints == ["plc.example:502", "plc2.example:1502"]


def test_empty_list_in_yaml_is_accepted(tmp_path: Path) -> None:
    settings = Settings.from_yaml(_write(tmp_path, "modbus:\n  read_only_endpoints: []\n"))
    assert settings.modbus_read_only_endpoints == []


def test_save_then_reload_keeps_the_endpoints(tmp_path: Path) -> None:
    """A config save through the API must not silently drop the protection."""
    settings = Settings(modbus_read_only_endpoints=["plc.example:502"])
    path = tmp_path / "gateway.yaml"
    settings.save_to_yaml(path)
    assert yaml.safe_load(path.read_text())["modbus"]["read_only_endpoints"] == ["plc.example:502"]
    assert Settings.from_yaml(path).modbus_read_only_endpoints == ["plc.example:502"]


def test_bus_settings_built_from_settings_normalises_and_rejects_typos() -> None:
    ok = BusSettings(read_only_endpoints=("PLC.example:0502",))
    assert ok.read_only_endpoints == ("plc.example:502",)
    with pytest.raises(ValueError):
        BusSettings(read_only_endpoints=("no-port",))


def test_startup_wiring_passes_the_endpoints_to_the_bus_contract() -> None:
    from app.main import build_bus_settings

    built = build_bus_settings(Settings(modbus_read_only_endpoints=["plc.example:502"]))
    assert built.read_only_endpoints == ("plc.example:502",)
    assert build_bus_settings(Settings()).read_only_endpoints == ()


def test_startup_refuses_a_malformed_endpoint() -> None:
    from app.main import build_bus_settings

    with pytest.raises(ValueError):
        build_bus_settings(Settings(modbus_read_only_endpoints=["plc.example"]))
