"""FIWARE Smart Data Model (SDM) attribute inference.

Maps a human-readable sensor/register name to a canonical SDM attribute so the
gateway publishes canonical keys that the Nekazari platform can match against a
device profile. Pure utility — no runtime dependencies.
"""

from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """Normalize a string to a URL-friendly key (e.g. "Sensor 1" -> "sensor_1")."""
    value = str(value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "_", value)


def get_sdm_attribute(name: str) -> str:
    """
    Infer a standard FIWARE Smart Data Model attribute from a sensor/register
    name. Falls back to a slugified version for custom/unknown names.
    """
    n = name.lower()

    mapping = [
        (["temp", "t_", "termomet"], "airTemperature"),
        (["hum", "h_", "rh", "humedad"], "relativeHumidity"),
        (["soil", "tierra", "moist", "suelo"], "soilMoisture"),
        (["pres", "baro", "atm"], "atmosphericPressure"),
        (["wind", "viento", "anemo", "speed", "veloc"], "windSpeed"),
        (["solar", "rad", "sun", "pira", "pyra", "insol"], "solarRadiation"),
        (["bat", "volt", "bater", "nivel"], "batteryLevel"),
        (["tilt", "inclin", "angle", "panel_incl"], "panelInclination"),
    ]

    for keywords, attr in mapping:
        if any(k in n for k in keywords):
            return attr

    return slugify(name)
