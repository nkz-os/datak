"""System monitoring driver using psutil."""

from typing import ClassVar

import psutil

from app.drivers.base import BaseDriver, ReadError


class SystemDriver(BaseDriver):
    """
    Driver for reading system metrics (CPU, Memory, Disk, Temp).

    Configuration example:
    {
        "metric": "cpu_percent" | "memory_percent" | "disk_usage" | "temperature",
        "path": "/",  # For disk_usage (optional, default /)
        "sensor_label": "coretemp_package_id_0" # For temperature (optional, fuzzy match)
    }
    """

    # Static cache for sensor keys to avoid frequent string parsing
    _temp_sensor_keys: ClassVar[list[str]] = []

    async def connect(self) -> bool:
        """System driver is always connected."""
        # Just check if we can import psutil, which we already did
        return True

    async def disconnect(self) -> None:
        """Nothing to disconnect."""
        pass

    async def read(self) -> float:
        """Read system metric based on config."""
        metric = self.config.get("metric")

        try:
            # CPU Usage
            if metric == "cpu_percent":
                # Blocking call but usually fast. interval=None means non-blocking (returns since last call)
                # First call returns 0.0, subsequent calls return avg since last
                return psutil.cpu_percent(interval=None)

            # Memory Usage
            elif metric == "memory_percent":
                return psutil.virtual_memory().percent

            # Disk Usage
            elif metric == "disk_usage":
                path = self.config.get("path", "/")
                return psutil.disk_usage(path).percent

            # Temperature (Linux only mostly)
            elif metric == "temperature":
                return self._read_temperature()

            else:
                raise ReadError(f"Unknown system metric: {metric}")

        except Exception as e:
            raise ReadError(f"Failed to read system metric {metric}: {e}") from e

    def _read_temperature(self) -> float:
        """
        Read hardware temperature.
        Tries to find a sensor matching 'sensor_label' in config.
        If not specified, tries to find first available 'coretemp' or comparable.
        """
        if not hasattr(psutil, "sensors_temperatures"):
            raise ReadError("Temperature sensors not supported on this platform")

        temps = psutil.sensors_temperatures()
        if not temps:
            raise ReadError("No temperature sensors found")

        target_label = self.config.get("sensor_label")

        # Configured label search
        if target_label:
            for name, entries in temps.items():
                for entry in entries:
                    if entry.label and target_label in entry.label:
                        return entry.current
                    # Some systems use name as the main identifier
                    if target_label in name:
                        return entry.current
            raise ReadError(f"Temperature sensor '{target_label}' not found")

        # Default fallback: try to find CPU temp
        # Common names: coretemp, cpu_thermal, k10temp
        for name in ["coretemp", "cpu_thermal", "k10temp", "acpitz"]:
            if temps.get(name):
                return temps[name][0].current

        # Fallback: return the very first sensor found
        first_group = next(iter(temps.values()))
        if first_group:
            return first_group[0].current

        raise ReadError("Could not determine system temperature")

    async def write(self, value: float) -> bool:
        """Writing to system metrics is not supported."""
        raise NotImplementedError("SystemDriver does not support write")
