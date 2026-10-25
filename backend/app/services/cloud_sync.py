"""Cloud synchronization and Digital Twin integration via MQTT.

Northbound FIWARE IoT Agent JSON integration: collects local sensor
readings and publishes them to a remote MQTT broker using the standard
FIWARE topic format (/<apikey>/<device_id>/attrs).
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import aiomqtt
import structlog

from app.config import get_settings
from app.core.sdm import get_sdm_attribute as _get_sdm_attribute
from app.db.session import async_session_factory
from app.models.sensor import Sensor

logger = structlog.get_logger()
settings = get_settings()

# Maximum reconnect backoff in seconds (caps exponential growth)
_MAX_BACKOFF = 60
_BASE_BACKOFF = 5


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
        self._skip_logged: set[tuple[str, ...]] = set()

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
        sensor_id: int,  # noqa: ARG002
        sensor_name: str,
        value: float,
        timestamp: datetime,
        entity_id: str | None = None,
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

        final_attr = attribute or _get_sdm_attribute(sensor_name)
        route = self._resolve_route(sensor_name, entity_id, final_attr)
        if route is None:
            return False
        topic, key = route

        body: dict[str, Any] = {key: value}
        if settings.digital_twin_send_timestamp:
            ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
            body["TimeInstant"] = ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = json.dumps(body)

        try:
            await self._client.publish(topic, payload)
            self._log.debug("Twin update", attr=final_attr, val=value, origin=sensor_name)
            return True

        except Exception as e:
            self._log.error("Cloud publish error", error=str(e))
            self._trigger_reconnect()
            return False

    def _resolve_route(
        self, sensor_name: str, entity_id: str | None, attribute: str
    ) -> tuple[str, str] | None:
        """Return (topic, payload key) for a reading, or None if it must not be sent.

        Without a configured device list the legacy single topic is used with the
        attribute as key. With a device list, the broker ACL only accepts the
        listed topics, so anything else is skipped (and logged once).
        """
        devices = settings.digital_twin_devices
        if not devices:
            topic = settings.digital_twin_topic
            return (topic, attribute) if topic else None

        if not entity_id:
            topic = settings.digital_twin_topic
            if topic:
                return topic, attribute
            self._skip_once(
                ("none", sensor_name),
                "Sensor skipped: no platform device assigned",
                sensor_name=sensor_name,
            )
            return None

        # twin_entity_id may be the externalId or a Device URN ending in it.
        external_id = entity_id.rsplit(":", 1)[-1]
        device = next((d for d in devices if d.externalId == external_id), None)
        if device is None:
            self._skip_once(
                ("dev", entity_id),
                "Sensor skipped: not a platform device",
                sensor_name=sensor_name,
                twin_entity_id=entity_id,
            )
            return None

        keys = {m.key for m in device.measurements}
        key = next((m.key for m in device.measurements if m.sdmAttribute == attribute), None)
        if key is None and attribute in keys:
            key = attribute
        if key is None:
            self._skip_once(
                ("attr", device.externalId, attribute),
                "Reading skipped: device has no such measurement",
                sensor_name=sensor_name,
                twin_entity_id=entity_id,
                attribute=attribute,
            )
            return None
        return device.topic, key

    def _skip_once(self, token: tuple[str, ...], event: str, **ctx: Any) -> None:
        """Log a skipped reading once per cause, not once per reading."""
        if token in self._skip_logged:
            return
        self._skip_logged.add(token)
        self._log.warning(event, **ctx)

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
        """Generate a device profile JSON for the Nekazari SDM Integration.

        One mapping per published attribute. Multi-register sensors emit one
        mapping per register (``incoming_key`` = the SDM attribute DaTaK actually
        publishes over MQTT); single-register sensors keep one mapping from the
        sensor name. Identity mapping: ``incoming_key`` == ``target_attribute``.
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                result = await session.execute(
                    select(Sensor)
                    .options(selectinload(Sensor.registers))
                    .where(Sensor.is_active == True)  # noqa: E712
                    .where(Sensor.deleted_at == None)  # noqa: E711
                )
                sensors = list(result.scalars().all())

            mappings: list[dict[str, Any]] = []
            profile: dict[str, Any] = {
                "name": settings.gateway_name or "DaTaK Gateway",
                "description": "Auto-generated profile from DaTaK Gateway sensors",
                "sdm_entity_type": settings.digital_twin_entity_type or "Device",
                "mappings": mappings,
            }

            seen: set[str] = set()
            for sensor in sensors:
                if sensor.registers:
                    # Multi-register: one mapping per register.
                    sdm_attrs = [
                        reg.twin_attribute or _get_sdm_attribute(reg.name)
                        for reg in sensor.registers
                    ]
                else:
                    # Single-register (legacy): one mapping from the sensor name.
                    sdm_attrs = [sensor.twin_attribute or _get_sdm_attribute(sensor.name)]

                for sdm_attr in sdm_attrs:
                    if sdm_attr in seen:
                        continue
                    seen.add(sdm_attr)
                    mappings.append({
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
