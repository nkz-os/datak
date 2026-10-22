"""Runtime secret validation (production hardening).

In production the gateway must refuse to boot with insecure default secrets
(forgeable JWT, dev InfluxDB token). In dev the defaults are tolerated.
save_to_yaml must never persist secrets back to the YAML file (env-first).
"""

from __future__ import annotations

import pytest
import yaml

from app.config import Settings


def _prod(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "production": True,
        "jwt_secret": "real-jwt-secret",
        "influxdb_token": "real-influx-token",
        "digital_twin_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_prod_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValueError, match="jwt_secret"):
        _prod(jwt_secret="CHANGE-ME-IN-PRODUCTION")._validate_runtime_secrets()


def test_prod_rejects_empty_jwt_secret() -> None:
    with pytest.raises(ValueError):
        _prod(jwt_secret="")._validate_runtime_secrets()


def test_prod_rejects_default_influx_token() -> None:
    with pytest.raises(ValueError, match="influxdb_token"):
        _prod(influxdb_token="datak-dev-token")._validate_runtime_secrets()


def test_prod_accepts_real_secrets() -> None:
    _prod()._validate_runtime_secrets()  # must not raise


def test_dev_allows_insecure_defaults() -> None:
    # production defaults to False: insecure defaults tolerated for dev convenience.
    Settings(production=False)._validate_runtime_secrets()  # must not raise


def test_digital_twin_enabled_still_requires_password() -> None:
    with pytest.raises(ValueError, match="MQTT password"):
        _prod(digital_twin_enabled=True, digital_twin_password=None)._validate_runtime_secrets()


def test_save_to_yaml_does_not_persist_secrets(tmp_path) -> None:
    s = Settings(
        production=False,
        jwt_secret="super-secret-jwt",
        influxdb_token="super-secret-token",
        mqtt_password="mqtt-pw",
        digital_twin_password="twin-pw",
    )
    out = tmp_path / "gateway.yaml"
    s.save_to_yaml(out)

    data = yaml.safe_load(out.read_text())
    assert data["security"]["jwt_secret"] in (None, "")
    assert data["influxdb"]["token"] in (None, "")
    assert data["mqtt"]["password"] in (None, "")
    assert data["digital_twin"]["password"] in (None, "")
