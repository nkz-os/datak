"""Cloud synchronization and Digital Twin integration via MQTT."""

from datetime import datetime
from typing import Any
import asyncio
import json

import aiomqtt
import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.models.sensor import Sensor
import re
import unicodedata

def _slugify(value: str) -> str:
    """Normalize string to URL-friendly format (e.g., "Sensor 1" -> "sensor_1")."""
    value = str(value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "_", value)


def _get_sdm_attribute(name: str) -> str:
    """
    Smart Auto-Mapper: Infer standard SDM attribute from sensor name.
    
    Logic:
    1. Normalize name (lowercase).
    2. Check for keywords.
    3. Fallback: Normalized original name (to support custom sensors).
    """
    n = name.lower()
    
    # 1. Temperature (airTemperature)
    if any(k in n for k in ["temp", "t_", "termomet"]):
        return "airTemperature"
        
    # 2. Humidity (relativeHumidity)
    if any(k in n for k in ["hum", "h_", "rh", "humedad"]):
        return "relativeHumidity"
        
    # 3. Soil Moisture (soilMoisture)
    if any(k in n for k in ["soil", "tierra", "moist", "suelo"]):
        return "soilMoisture"
        
    # 4. Pressure (atmosphericPressure)
    if any(k in n for k in ["pres", "baro", "atm"]):
        return "atmosphericPressure"
        
    # 5. Wind Speed (windSpeed)
    if any(k in n for k in ["wind", "viento", "anemo", "speed", "veloc"]):
        return "windSpeed"
        
    # 6. Solar Radiation (solarRadiation)
    if any(k in n for k in ["solar", "rad", "sun", "pira", "pyra"]):
        return "solarRadiation"
        
    # 7. Battery (batteryLevel)
    if any(k in n for k in ["bat", "volt", "bater", "nivel"]):
        return "batteryLevel"
    
    # Fallback option (Requested by user) -> Preserve original name normalized
    return _slugify(name)


logger = structlog.get_logger()
settings = get_settings()


class CloudSync:
    """
    Northbound service for Digital Twin integration via MQTT.
    """

    def __init__(self):
        self._log = logger.bind(component="cloud_sync")
        self._client: aiomqtt.Client | None = None
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 5  # seconds, doubles each attempt

    async def start(self) -> None:
        """Initialize the cloud sync service."""
        if not settings.digital_twin_enabled:
            self._log.info("Digital Twin integration disabled")
            return

        if not settings.digital_twin_host:
             self._log.warning("Digital Twin enabled but no host configured")
             return

        try:
            tls_context = None
            if settings.digital_twin_port in (8883, 443):
                import ssl
                tls_context = ssl.create_default_context()

            self._client = aiomqtt.Client(
                hostname=settings.digital_twin_host,
                port=settings.digital_twin_port,
                username=settings.digital_twin_username,
                password=settings.digital_twin_password,
                transport="tcp",
                timeout=10,
                tls_context=tls_context,
            )

            await self._client.__aenter__()
            self._running = True
            self._reconnect_attempts = 0

            self._log.info(
                "Cloud sync connected",
                host=settings.digital_twin_host,
                port=settings.digital_twin_port,
                tls=bool(tls_context),
            )

        except Exception as e:
            self._log.error("Failed to connect to Digital Twin MQTT", error=str(e))
            self._running = False

    async def stop(self) -> None:
        """Close cloud sync connections."""
        self._running = False
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None

    async def send_reading(
        self,
        sensor_id: int,
        sensor_name: str,
        value: float,
        timestamp: datetime,
        entity_id: str | None = None,
        attribute: str | None = None,
    ) -> bool:
        """
        Send a sensor reading to the Digital Twin.
        """
        if not self._client or not settings.digital_twin_enabled:
            return False

        try:
            # Format: Simple JSON { "attribute": value }
            final_attr = attribute or _get_sdm_attribute(sensor_name)
            
            # Ensure safe attribute name (no spaces, etc?) User template had keys like "temp_c"
            # We use what's configured.
            
            self._log.info("Sent twin update", attr=final_attr, val=value, origin=sensor_name)
            payload = json.dumps({
                final_attr: value
            })

            topic = settings.digital_twin_topic
            if not topic:
                self._log.warning("No Digital Twin topic configured")
                return False

            await self._client.publish(topic, payload)
            return True

        except Exception as e:
            self._log.error("Cloud publish error", error=str(e))
            asyncio.create_task(self._try_reconnect())
            return False

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect to the remote MQTT broker with exponential backoff."""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self._log.error(
                "Max reconnect attempts reached, giving up",
                attempts=self._reconnect_attempts,
            )
            return

        self._reconnect_attempts += 1
        delay = self._reconnect_delay * (2 ** (self._reconnect_attempts - 1))
        self._log.info(
            "Attempting reconnect",
            attempt=self._reconnect_attempts,
            delay=delay,
        )
        await asyncio.sleep(delay)

        await self.stop()
        await self.start()

    async def generate_device_profile(self) -> dict[str, Any]:
        """
        Generate a device profile JSON for the Digital Twin.
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Sensor)
                    .where(Sensor.is_active == True)  # noqa: E712
                    .where(Sensor.deleted_at == None)  # noqa: E711
                )
                sensors = list(result.scalars().all())

            # Build device profile matching user template
            profile = {
                "name": settings.gateway_name or "DaTaK Gateway",
                "description": "Auto-generated profile from DaTaK Gateway sensors",
                "entityType": settings.digital_twin_entity_type or "AgriSensor",
                "mappings": []
            }

            for sensor in sensors:
                mapping = {
                    "incoming_key": sensor.twin_attribute or sensor.name,
                    "target_attribute": sensor.twin_attribute or sensor.name,
                    "type": "Number",
                    "transformation": "val"
                }
                profile["mappings"].append(mapping)

            return profile

        except Exception as e:
            self._log.exception("Profile generation failed", error=str(e))
            return {"error": str(e)}

    # Command receiving logic removed for now as it requires complex subscription handling
    # and wasn't explicitly requested beyond the topic existence.

# Global instance
cloud_sync = CloudSync()
