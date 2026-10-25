#!/usr/bin/env python3
"""
Nekazari auto-configuration for the DaTaK gateway.

Usage: python setup_nekazari.py <datalogger-config.json>

Reads the datalogger config JSON downloaded from the platform
(GET /api/sensors/dataloggers/<id>/config) and updates gateway.yaml with the
broker, the datalogger username and the list of Devices (one topic each).

The MQTT password is shown once by the platform and is NOT in the JSON: it is
prompted here and stored in configs/gateway.env (mode 0600, git-ignored) as
DATAK_DIGITAL_TWIN_PASSWORD. It is never written to gateway.yaml or printed.
"""

import getpass
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "configs"
GATEWAY_CONFIG = CONFIG_DIR / "gateway.yaml"
EXAMPLE_CONFIG = CONFIG_DIR / "gateway.example.yaml"
ENV_FILE = CONFIG_DIR / "gateway.env"
PASSWORD_VAR = "DATAK_DIGITAL_TWIN_PASSWORD"


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def load_config(json_path):
    """Load and validate the platform datalogger config JSON."""
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot read {json_path}: {e}")

    mqtt = data.get("mqtt")
    if not isinstance(mqtt, dict):
        fail("invalid config file: missing 'mqtt'")
    if mqtt.get("configured") is False:
        fail(
            "the platform has no external MQTT endpoint for this installation "
            f"({mqtt.get('reason', 'no reason given')}). "
            "Ask the platform administrator to configure it, then download the config again."
        )
    for key in ("host", "port", "username"):
        if not mqtt.get(key):
            fail(f"invalid config file: missing 'mqtt.{key}'")

    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        fail(
            "invalid config file: no 'devices' (register a Device on this datalogger first)"
        )
    for dev in devices:
        if not dev.get("externalId") or not dev.get("topic"):
            fail("invalid config file: every device needs 'externalId' and 'topic'")
    return data


def store_password(password):
    """Write the password to the 0600 env file, replacing any previous value."""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if ENV_FILE.exists():
        lines = [
            line
            for line in ENV_FILE.read_text().splitlines()
            if not line.startswith(f"{PASSWORD_VAR}=")
        ]
    lines.append(f"{PASSWORD_VAR}={password}")
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)


def update_gateway_yaml(data):
    """Update gateway.yaml with the datalogger settings."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config = {}
    if GATEWAY_CONFIG.exists():
        print(f"Reading existing config: {GATEWAY_CONFIG}")
        config = yaml.safe_load(GATEWAY_CONFIG.read_text()) or {}
    elif EXAMPLE_CONFIG.exists():
        print(f"Starting from example: {EXAMPLE_CONFIG}")
        config = yaml.safe_load(EXAMPLE_CONFIG.read_text()) or {}

    mqtt = data["mqtt"]
    config.setdefault("gateway", {})
    if data.get("name"):
        config["gateway"]["name"] = data["name"]

    dt = config.setdefault("digital_twin", {})
    dt["enabled"] = True
    dt["host"] = mqtt["host"]
    dt["port"] = int(mqtt["port"])
    dt["username"] = mqtt["username"]
    dt["password"] = None  # secret: lives in gateway.env
    dt["entity_type"] = "Device"
    # Publishing is per Device now; a leftover single topic would send there.
    dt["topic"] = ""
    dt["devices"] = data["devices"]

    if GATEWAY_CONFIG.exists():
        backup = GATEWAY_CONFIG.with_suffix(".yaml.bak")
        shutil.copy(GATEWAY_CONFIG, backup)
        print(f"Backup created: {backup}")

    with open(GATEWAY_CONFIG, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print("\nNew configuration:")
    print(f"  Broker:   {dt['host']}:{dt['port']}")
    print(f"  Username: {dt['username']}")
    print(f"  Devices:  {len(dt['devices'])}")
    for dev in dt["devices"]:
        print(f"    - {dev['externalId']} -> {dev['topic']}")
    print(f"\nSaved to {GATEWAY_CONFIG}")


def run(json_path):
    data = load_config(json_path)
    password = getpass.getpass("MQTT password (shown once by the platform): ")
    if not password:
        fail("a password is required")
    update_gateway_yaml(data)
    store_password(password)
    print(f"Password stored in {ENV_FILE} (mode 0600).")
    print("Restart the DaTaK service to apply changes.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_nekazari.py <datalogger-config.json>")
        sys.exit(1)
    json_path = Path(sys.argv[1])
    if not json_path.exists():
        fail(f"file not found: {json_path}")
    print(f"Configuring DaTaK with: {json_path.name}")
    run(json_path)


if __name__ == "__main__":
    main()
