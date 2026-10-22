"""MQTT async driver using aiomqtt."""

import asyncio
import contextlib
import json
from typing import Any

import aiomqtt

from app.drivers.base import BaseDriver, ConnectionError, ReadError


class MQTTDriver(BaseDriver):
    """
    Async MQTT driver for receiving data from ESP32 and other MQTT publishers.

    Unlike Modbus, MQTT is event-driven. This driver subscribes to a topic
    and processes incoming messages. The `read()` method returns the last
    received value.

    Configuration:
        {
            "broker": "localhost",
            "port": 1883,
            "topic": "sensors/temperature/1",
            "username": null,
            "password": null,
            "json_path": "$.value",  # Optional: extract value from JSON
            "qos": 0
        }
    """

    def __init__(
        self,
        sensor_id: int,
        sensor_name: str,
        config: dict[str, Any],
        **kwargs: Any,
    ):
        super().__init__(sensor_id, sensor_name, config, **kwargs)

        self.broker = config.get("broker", "localhost")
        self.port = config.get("port", 1883)
        self.topic = config.get("topic", "")
        self.username = config.get("username")
        self.password = config.get("password")
        self.json_path = config.get("json_path")
        self.qos = config.get("qos", 0)

        self._client: aiomqtt.Client | None = None
        self._last_message_value: float | None = None
        self._subscriber_task: asyncio.Task[None] | None = None

    async def connect(self) -> bool:
        """Establish MQTT connection and subscribe to topic."""
        try:
            self._client = aiomqtt.Client(
                hostname=self.broker,
                port=self.port,
                username=self.username,
                password=self.password,
            )

            await self._client.__aenter__()

            # Subscribe to topic
            await self._client.subscribe(self.topic, qos=self.qos)
            self._log.info("Subscribed to MQTT topic", topic=self.topic)

            # Start message listener
            self._subscriber_task = asyncio.create_task(self._message_loop())

            return True

        except Exception as e:
            self._log.error("MQTT connection failed", error=str(e))
            raise ConnectionError(f"Failed to connect to MQTT: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._subscriber_task:
            self._subscriber_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._subscriber_task

        if self._client:
            with contextlib.suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None

    async def read(self) -> float:
        """Return the last received MQTT message value."""
        if self._last_message_value is None:
            raise ReadError("No message received yet")
        return self._last_message_value

    async def _message_loop(self) -> None:
        """Listen for incoming MQTT messages."""
        if not self._client:
            return

        try:
            async for message in self._client.messages:
                try:
                    value = self._parse_message(message.payload)
                    self._last_message_value = value

                    # Notify callback immediately
                    from datetime import datetime

                    if self._on_value:
                        await self._on_value(
                            self.sensor_id,
                            value,
                            value,
                            datetime.utcnow(),
                        )

                    await self._notify_status("ONLINE")

                except Exception as e:
                    self._log.warning("Failed to parse message", error=str(e))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log.error("MQTT message loop error", error=str(e))

    def _parse_message(self, payload: bytes) -> float:
        """Parse MQTT message payload to extract value."""
        payload_str = payload.decode("utf-8")

        # Try to parse as JSON
        try:
            data = json.loads(payload_str)

            # If json_path is specified, extract that field
            if self.json_path:
                # Simple path parsing (supports $.field or $.nested.field)
                path = self.json_path.lstrip("$").lstrip(".")
                for key in path.split("."):
                    if isinstance(data, dict):
                        data = data.get(key)
                    elif isinstance(data, list) and key.isdigit():
                        data = data[int(key)]
                    else:
                        raise ValueError(f"Cannot extract {key} from {type(data)}")

            # If data is a dict with 'value' key, use that
            if isinstance(data, dict) and "value" in data:
                data = data["value"]

            return float(data)

        except json.JSONDecodeError:
            # Not JSON, try to parse as raw number
            return float(payload_str.strip())

    async def write(self, value: float) -> bool:
        """
        Publish value to the command topic.

        Default command topic is {topic}/set unless 'command_topic' is configured.
        """
        if not self._client:
            raise ConnectionError("Not connected")

        command_topic = self.config.get("command_topic", f"{self.topic}/set")

        try:
            # Publish as simple value or JSON based on config (simple for now)
            # Todo: Support json_template if needed
            payload = str(value)

            await self._client.publish(command_topic, payload, qos=self.qos)
            self._log.info("Published command", topic=command_topic, value=value)
            return True

        except Exception as e:
            self._log.error("MQTT publish failed", error=str(e))
            return False

    async def _poll_loop(self) -> None:
        """
        Override poll loop for MQTT.

        MQTT is message-driven, so we just monitor connection status.
        """
        while self._running:
            # Check if we're still connected
            if not self._connected:
                self._connected = await self._try_reconnect()

            await asyncio.sleep(self.poll_interval_ms / 1000)
