"""Application configuration using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment and config file."""

    model_config = SettingsConfigDict(
        env_prefix="DATAK_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Gateway
    gateway_name: str = "DaTaK-Gateway"
    log_level: str = "INFO"
    data_dir: Path = Path("data")

    # Database (SQLite)
    database_url: str = "sqlite+aiosqlite:///data/gateway.db"
    database_echo: bool = False

    # InfluxDB
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: str = "datak-dev-token"
    influxdb_org: str = "datak"
    influxdb_bucket: str = "sensors"
    influxdb_retention_days: int = 30

    # MQTT
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "datak-gateway"
    mqtt_username: str | None = None
    mqtt_password: str | None = None

    display_timezone: str = "UTC"

    # Digital Twin (MQTT)
    digital_twin_enabled: bool = False
    digital_twin_host: str = ""
    digital_twin_port: int = 8883
    digital_twin_topic: str = ""
    digital_twin_username: str | None = None
    digital_twin_password: str | None = None
    digital_twin_entity_type: str = "AgriSensor"

    # Security
    jwt_secret: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    token_expire_minutes: int = 1440

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Reports
    reports_enabled: bool = True
    reports_output_dir: Path = Path("data/exports")

    # Metrics
    metrics_enabled: bool = True
    metrics_port: int = 9100

    @field_validator("data_dir", "reports_output_dir", mode="before")
    @classmethod
    def ensure_path(cls, v: Any) -> Path:
        """Convert string to Path."""
        return Path(v) if isinstance(v, str) else v

    @classmethod
    def from_yaml(cls, config_path: Path) -> "Settings":
        """Load settings from YAML config file."""
        print(f"DEBUG: Loading config from {config_path.absolute()}")
        if not config_path.exists():
            print("DEBUG: Config file not found, using defaults")
            return cls()

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        # Flatten nested config to match env var style
        flat_config: dict[str, Any] = {}

        def flatten(data: dict, prefix: str = "") -> None:
            for key, value in data.items():
                full_key = f"{prefix}{key}" if prefix else key
                if isinstance(value, dict):
                    flatten(value, f"{full_key}_")
                else:
                    flat_config[full_key] = value

        flatten(config)
        return cls(**flat_config)

    def save_to_yaml(self) -> None:
        """Save current settings to YAML config file."""
        # We need to unflatten usage keys back to nested structure
        # Identify path
        path = Path("configs/gateway.yaml")
        if not path.exists():
            path = Path("/app/configs/gateway.yaml")

        # Build dict
        config = {
            "gateway": {
                "name": self.gateway_name,
                "log_level": self.log_level,
                "data_dir": str(self.data_dir),
            },
            "database": {
                "url": self.database_url,
                "echo": self.database_echo,
            },
            "influxdb": {
                "url": self.influxdb_url,
                "token": self.influxdb_token,
                "org": self.influxdb_org,
                "bucket": self.influxdb_bucket,
                "retention_days": self.influxdb_retention_days,
            },
            "mqtt": {
                "broker": self.mqtt_broker,
                "port": self.mqtt_port,
                "client_id": self.mqtt_client_id,
                "username": self.mqtt_username,
                "password": self.mqtt_password,
            },
            "digital_twin": {
                "enabled": self.digital_twin_enabled,
                "host": self.digital_twin_host,
                "port": self.digital_twin_port,
                "topic": self.digital_twin_topic,
                "username": self.digital_twin_username,
                "password": self.digital_twin_password,
                "entity_type": self.digital_twin_entity_type,
            },
            "security": {
                "jwt_secret": self.jwt_secret,
                "jwt_algorithm": self.jwt_algorithm,
                "token_expire_minutes": self.token_expire_minutes,
            },
            "api": {
                "host": self.api_host,
                "port": self.api_port,
                "cors_origins": self.api_cors_origins,
            },
            "reports": {
                "enabled": self.reports_enabled,
                "output_dir": str(self.reports_output_dir),
            },
            "metrics": {
                "enabled": self.metrics_enabled,
                "port": self.metrics_port,
            }
        }

        with open(path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        # Clear cache so next get_settings() reloads
        get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    # Try multiple paths for robustness
    possible_paths = [Path("/app/configs/gateway.yaml"), Path("configs/gateway.yaml")]
    for path in possible_paths:
        if path.exists():
             return Settings.from_yaml(path)

    print("DEBUG: No config file found in search paths")
    return Settings.from_yaml(Path("configs/gateway.yaml"))
