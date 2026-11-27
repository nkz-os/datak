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
    ok = BusSettings(read_only_endpoints=("192.0.2.10:0502",))
    assert ok.read_only_endpoints == ("192.0.2.10:502",)
    with pytest.raises(ValueError):
        BusSettings(read_only_endpoints=("no-port",))


def test_startup_wiring_passes_the_endpoints_to_the_bus_contract() -> None:
    from app.main import build_bus_settings

    built = build_bus_settings(Settings(modbus_read_only_endpoints=["192.0.2.10:502"]))
    assert built.read_only_endpoints == ("192.0.2.10:502",)
    assert build_bus_settings(Settings()).read_only_endpoints == ()


def test_startup_refuses_a_malformed_endpoint() -> None:
    from app.main import build_bus_settings

    with pytest.raises(ValueError):
        build_bus_settings(Settings(modbus_read_only_endpoints=["192.0.2.10"]))


def test_startup_refuses_a_host_name_endpoint() -> None:
    from app.main import build_bus_settings

    with pytest.raises(ValueError, match="IP literal"):
        build_bus_settings(Settings(modbus_read_only_endpoints=["plc.example:502"]))


@pytest.mark.parametrize(
    "key", ["read_only_endpoint", "readonly_endpoints", "read_only_endpoints_", "Read_Only_Endpoints", "round_budget"]
)
def test_misspelt_modbus_key_fails_startup(tmp_path: Path, key: str) -> None:
    path = _write(tmp_path, f"modbus:\n  {key}:\n    - plc.example:502\n")
    with pytest.raises(ValueError, match=key):
        Settings.from_yaml(path)


@pytest.mark.parametrize("value", ["~", "null", '""', "plc.example:502", "5", "{a: 1}"])
def test_read_only_endpoints_must_be_an_explicit_list(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, f"modbus:\n  read_only_endpoints: {value}\n")
    with pytest.raises(ValueError, match="read_only_endpoints"):
        Settings.from_yaml(path)


def test_known_modbus_keys_and_absent_key_are_fine(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "modbus:\n  round_budget_ms: 1000\n  transaction_timeout_ms: 250\n"
        "  quarantine_max_s: 60\n  gateway_settle_ms: 0\n",
    )
    assert Settings.from_yaml(path).modbus_read_only_endpoints == []


def test_startup_logs_the_effective_resolved_list(caplog: pytest.LogCaptureFixture) -> None:
    from app.main import describe_read_only

    caplog.set_level("INFO")
    built = BusSettings(read_only_endpoints=("127.1:502",))
    describe_read_only(built)
    assert "127.0.0.1:502" in caplog.text
