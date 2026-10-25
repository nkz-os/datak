"""setup_nekazari.py consumes the platform datalogger config JSON."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "setup_nekazari.py"
PASSWORD = "pw-from-prompt"  # synthetic

CONFIG = {
    "datalogger_id": "dl-1",
    "name": "Field Logger",
    "mqtt": {
        "host": "mqtt.example.org",
        "port": 8883,
        "username": "dl_0123456789abcdef",
        "apikey": "0123456789abcdef",
        "password": None,
    },
    "devices": [
        {
            "externalId": "dev-a",
            "name": "A",
            "topic": "/0123456789abcdef/dev-a/attrs",
            "measurements": [{"key": "temp", "sdmAttribute": "airTemperature", "unit": "CEL"}],
        }
    ],
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_nekazari", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = tmp_path / "configs"
    monkeypatch.setattr(mod, "CONFIG_DIR", cfg)
    monkeypatch.setattr(mod, "GATEWAY_CONFIG", cfg / "gateway.yaml")
    monkeypatch.setattr(mod, "ENV_FILE", cfg / "gateway.env")
    monkeypatch.setattr(mod, "EXAMPLE_CONFIG", cfg / "none.yaml")
    monkeypatch.setattr(mod.getpass, "getpass", lambda *_a, **_k: PASSWORD)
    return mod, tmp_path


def _run(mod, tmp_path, data):
    src = tmp_path / "dl.json"
    src.write_text(json.dumps(data))
    return mod.run(src)


def test_setup_writes_devices_and_username_from_config(setup, capsys):
    mod, tmp = setup
    _run(mod, tmp, CONFIG)
    conf = yaml.safe_load((tmp / "configs" / "gateway.yaml").read_text())
    dt = conf["digital_twin"]
    assert dt["enabled"] is True
    assert dt["host"] == "mqtt.example.org"
    assert dt["port"] == 8883
    assert dt["username"] == "dl_0123456789abcdef"
    assert dt["entity_type"] == "Device"
    assert dt["topic"] == ""
    assert dt["devices"] == CONFIG["devices"]
    assert "/json/" not in json.dumps(dt)
    # password never in yaml, never printed; stored in the 0600 env file
    assert PASSWORD not in (tmp / "configs" / "gateway.yaml").read_text()
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err
    env = tmp / "configs" / "gateway.env"
    assert f"DATAK_DIGITAL_TWIN_PASSWORD={PASSWORD}" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_setup_is_idempotent_and_keeps_other_env_lines(setup):
    mod, tmp = setup
    env = tmp / "configs" / "gateway.env"
    env.parent.mkdir()
    env.write_text("DATAK_JWT_SECRET=x\nDATAK_DIGITAL_TWIN_PASSWORD=old\n")
    _run(mod, tmp, CONFIG)
    _run(mod, tmp, CONFIG)
    lines = env.read_text().splitlines()
    assert lines.count(f"DATAK_DIGITAL_TWIN_PASSWORD={PASSWORD}") == 1
    assert "DATAK_JWT_SECRET=x" in lines
    assert len(lines) == 2


def test_setup_stops_when_endpoint_not_configured(setup, capsys):
    mod, tmp = setup
    bad = {
        **CONFIG,
        "mqtt": {
            "configured": False,
            "reason": "no external endpoint",
            "username": "dl_0123456789abcdef",
        },
    }
    with pytest.raises(SystemExit) as exc:
        _run(mod, tmp, bad)
    assert exc.value.code != 0
    assert "no external endpoint" in capsys.readouterr().out
    assert not (tmp / "configs" / "gateway.yaml").exists()


def test_setup_ignores_password_in_json(setup):
    mod, tmp = setup
    data = json.loads(json.dumps(CONFIG))
    data["mqtt"]["password"] = "should-not-be-used"
    _run(mod, tmp, data)
    assert "should-not-be-used" not in (tmp / "configs" / "gateway.env").read_text()
