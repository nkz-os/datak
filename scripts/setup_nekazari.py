#!/usr/bin/env python3
"""
Nekazari Auto-Configuration Script for DaTaK Gateway.
Usage: python setup_nekazari.py <device-config.json>

This script reads the JSON credentials downloaded from Nekazari Entity Wizard
and automatically updates the DaTaK gateway.yaml configuration.
"""

import sys
import json
import yaml
import shutil
from pathlib import Path

# Configuration Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "configs"
GATEWAY_CONFIG = CONFIG_DIR / "gateway.yaml"
EXAMPLE_CONFIG = CONFIG_DIR / "gateway.example.yaml"


def load_nekazari_json(json_path):
    """Load and validate the Nekazari configuration JSON."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Basic validation
        required_keys = ['device_id', 'mqtt']
        for key in required_keys:
            if key not in data:
                raise ValueError(f"Invalid config file: missing '{key}'")

        mqtt_config = data['mqtt']
        if 'api_key' not in mqtt_config:
            raise ValueError("Invalid config file: missing 'mqtt.api_key'")

        return data
    except Exception as e:
        print(f"❌ Error reading JSON: {e}")
        sys.exit(1)


def update_gateway_yaml(nekazari_conf):
    """Update gateway.yaml with Nekazari settings."""
    
    # 1. Ensure config directory exists
    if not CONFIG_DIR.exists():
        print(f"Creating directory: {CONFIG_DIR}")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Load existing config or example
    config = {}
    if GATEWAY_CONFIG.exists():
        print(f"Reading existing config: {GATEWAY_CONFIG}")
        with open(GATEWAY_CONFIG, 'r') as f:
            config = yaml.safe_load(f) or {}
    elif EXAMPLE_CONFIG.exists():
        print(f"Start fresh from example: {EXAMPLE_CONFIG}")
        with open(EXAMPLE_CONFIG, 'r') as f:
            config = yaml.safe_load(f) or {}
    else:
        print("⚠️ No existing config found. Creating a minimal new structure.")
        config = {"mqtt": {}, "digital_twin": {}}

    # 3. Apply changes
    device_id = nekazari_conf.get('device_id')
    dev_name = nekazari_conf.get('device_name', f"DaTaK-{device_id[:8]}")
    mqtt_conf = nekazari_conf['mqtt']
    
    # Update Gateway Name
    if 'gateway' not in config:
        config['gateway'] = {}
    config['gateway']['name'] = dev_name
    
    # Update Digital Twin (The connection to Nekazari)
    if 'digital_twin' not in config:
        config['digital_twin'] = {}
        
    dt = config['digital_twin']
    dt['enabled'] = True
    dt['host'] = mqtt_conf.get('host', 'localhost')
    dt['port'] = int(mqtt_conf.get('port', 1883))
    
    # Logic to select the best topic
    # Nekazari provides explicit topics in the JSON
    topics = mqtt_conf.get('topics', {})
    # Prefer JSON topic (better for SDM as it might send dicts)
    pub_topic = topics.get('publish_data_json', topics.get('publish_data'))
    
    if pub_topic:
        # Normalize: Nekazari typically shows topics with leading '/'
        dt['topic'] = pub_topic if pub_topic.startswith("/") else f"/{pub_topic}"
    else:
        print("⚠️ Warning: No explicit publish topic found in JSON. Using default pattern.")
        dt['topic'] = f"/json/{mqtt_conf.get('api_key')}/{device_id}/attrs"

    # Credentials
    dt['username'] = device_id
    dt['password'] = mqtt_conf.get('api_key')
    
    # Also set entity type if available
    dt['entity_type'] = "AgriDevice" 
    
    print("\n✅ New Configuration:")
    print(f"  - Host: {dt['host']}:{dt['port']}")
    print(f"  - Device ID: {device_id}")
    print(f"  - Topic: {dt['topic']}")
    print("  - Auth: API Key configured")

    # 4. Save
    # Backup first if exists
    if GATEWAY_CONFIG.exists():
        backup_path = GATEWAY_CONFIG.with_suffix('.yaml.bak')
        shutil.copy(GATEWAY_CONFIG, backup_path)
        print(f"  - Backup created: {backup_path}")

    with open(GATEWAY_CONFIG, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n🎉 Successfully saved to {GATEWAY_CONFIG}")
    print("👉 Please restart the DaTaK service to apply changes.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_nekazari.py <device-config.json>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"❌ File not found: {json_path}")
        sys.exit(1)

    print(f"🚀 Configuring DaTaK with: {json_path.name}")
    data = load_nekazari_json(json_path)
    update_gateway_yaml(data)


if __name__ == "__main__":
    main()
