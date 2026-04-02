"""Cloud synchronization and Digital Twin integration via MQTT.

Northbound FIWARE IoT Agent JSON integration: collects local sensor
readings and publishes them to a remote MQTT broker using the standard
FIWARE topic format (/<apikey>/<device_id>/attrs).
"""

import asyncio
import contextlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

import aiomqtt
import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.models.sensor import Sensor

logger = structlog.get_logger()
settings = get_settings()

# Maximum reconnect backoff in seconds (caps exponential growth)
_MAX_BACKOFF = 60
_BASE_BACKOFF = 5


def _slugify(value: str) -> str:
    """Normalize string to URL-friendly format (e.g., "Sensor 1" -> "sensor_1")."""
    value = str(value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "_", value)


def _get_sdm_attribute(name: str) -> str:
    """
    Smart Auto-Mapper: infer standard FIWARE Smart Data Model attribute
    from a human-readable sensor name.

    Falls back to a slugified version for custom/unknown sensors.
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
    ]

    for keywords, attr in mapping:
        if any(k in n for k in keywords):
            return attr

    return _slugify(name)


class CloudSync:
    """
    Northbound service for Digital Twin integration via MQTT.

    Thread-safety: all public methods are coroutine-safe. Reconnection
    is serialized via an asyncio.Lock to prevent concurrent stop/start
    races when multiple publish errors arrive simultaneously.
    """

    def __init__(self) -> None:
        self._log = logger.bind(component="cloud_sync")
        self._client: aiomqtt.Client | None = None
        self._connected = False
        self._reconnecting = False
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_attempts = 0
        self._reconnect_task: asyncio.Task[None] | None = None

    @property
    def is_healthy(self) -> bool:
        """Expose connection health for /health or metrics endpoints."""
        return self._connected and self._client is not None

    async def start(self) -> None:
        """Connect to the remote MQTT broker."""
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
            self._connected = True
            self._reconnect_attempts = 0

            self._log.info(
                "Cloud sync connected",
                host=settings.digital_twin_host,
                port=settings.digital_twin_port,
                tls=bool(tls_context),
            )

        except Exception as e:
            self._log.error("Failed to connect to Digital Twin MQTT", error=str(e))
            self._connected = False

    async def stop(self) -> None:
        """Disconnect from the remote MQTT broker."""
        self._connected = False
        if self._client:
            with contextlib.suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None

    async def send_reading(
        self,
        _sensor_id: int,
        sensor_name: str,
        value: float,
        _timestamp: datetime,
        _entity_id: str | None = None,
        attribute: str | None = None,
    ) -> bool:
        """
        Publish a sensor reading to the Digital Twin via MQTT.

        Returns False (without blocking) if the client is disconnected
        or reconnecting — the reconnect loop will restore service.
        """
        if not settings.digital_twin_enabled:
            return False

        if not self._connected or self._client is None:
            return False

        topic = settings.digital_twin_topic
        if not topic:
            return False

        final_attr = attribute or _get_sdm_attribute(sensor_name)
        payload = json.dumps({final_attr: value})

        try:
            await self._client.publish(topic, payload)
            self._log.debug("Twin update", attr=final_attr, val=value, origin=sensor_name)
            return True

        except Exception as e:
            self._log.error("Cloud publish error", error=str(e))
            self._schedule_reconnect()
            return False

    def _trigger_reconnect(self) -> None:
        """Schedule a reconnection attempt if one isn't already running."""
        if not self._reconnecting:
            task = asyncio.create_task(self._reconnect_loop())
            # Store reference to prevent GC
            self._reconnect_task = task


    async def _reconnect_loop(self) -> None:
        """
        Single serialized reconnection loop with capped exponential backoff.

        Retries indefinitely — an edge gateway must recover autonomously.
        The lock guarantees only one reconnect loop runs at a time.
        """
        async with self._reconnect_lock:
            if self._reconnecting:
                return  # another coroutine already handling it
            self._reconnecting = True

        try:
            while True:
                self._reconnect_attempts += 1
                delay = min(_BASE_BACKOFF * (2 ** (self._reconnect_attempts - 1)), _MAX_BACKOFF)

                self._log.info(
                    "Reconnect scheduled",
                    attempt=self._reconnect_attempts,
                    delay_s=delay,
                )
                await asyncio.sleep(delay)

                await self.stop()
                await self.start()

                if self._connected:
                    self._log.info(
                        "Reconnected successfully",
                        attempt=self._reconnect_attempts,
                    )
                    break
        finally:
            self._reconnecting = False

    async def generate_device_profile(self) -> dict[str, Any]:
        """Generate a device profile JSON compatible with Nekazari SDM Integration.

        The profile uses ``sdm_entity_type`` (not ``entityType``) and resolves
        each sensor name through ``_get_sdm_attribute`` so that ``incoming_key``
        matches what DaTaK actually publishes over MQTT.
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

            profile = {
                "name": settings.gateway_name or "DaTaK Gateway",
                "description": "Auto-generated profile from DaTaK Gateway sensors",
                "sdm_entity_type": settings.digital_twin_entity_type or "AgriSensor",
                "mappings": [],
            }

            seen: set[str] = set()
            for sensor in sensors:
                sdm_attr = sensor.twin_attribute or _get_sdm_attribute(sensor.name)
                if sdm_attr in seen:
                    continue
                seen.add(sdm_attr)
                profile["mappings"].append({
                    "incoming_key": sdm_attr,
                    "target_attribute": sdm_attr,
                    "type": "Number",
                    "transformation": "val",
                })

            return profile

        except Exception as e:
            self._log.exception("Profile generation failed", error=str(e))
            return {"error": str(e)}


# Global instance
cloud_sync = CloudSync()
