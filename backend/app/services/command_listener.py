
import asyncio
import contextlib
import json

import aiomqtt
import structlog

from app.config import get_settings
from app.services.orchestrator import orchestrator

logger = structlog.get_logger()
settings = get_settings()

class CommandListener:
    """
    Listens for remote commands via MQTT and executes them on the gateway.

    Topic structure: datak/{gateway_name}/cmd/#
    Payload: {"sensor_id": 12, "value": 1.0} or {"sensor_name": "temp", "value": 1.0}
    """

    def __init__(self):
        self._log = logger.bind(component="command_listener")
        self._client: aiomqtt.Client | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Topic to subscribe to
        self.command_topic = f"datak/{settings.gateway_name}/cmd/#"

    async def start(self) -> None:
        """Start the command listener."""
        if not settings.mqtt_broker:
            self._log.info("MQTT broker not configured, command listener disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        self._log.info("Command listener started", topic=self.command_topic)

    async def stop(self) -> None:
        """Stop the command listener."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._log.info("Command listener stopped")

    async def _listen_loop(self) -> None:
        """Main MQTT loop."""
        while self._running:
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_broker,
                    port=settings.mqtt_port,
                    identifier=f"{settings.mqtt_client_id}_cmd",
                ) as client:
                    self._client = client
                    await client.subscribe(self.command_topic)
                    self._log.info("Subscribed to command topic")

                    async for message in client.messages:
                        await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error("MQTT listener connection error", error=str(e))
                await asyncio.sleep(5) # Retry delay

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Process incoming command message."""
        try:
            payload = message.payload.decode()
            data = json.loads(payload)
            self._log.info("Received command", payload=data)

            sensor_id = data.get("sensor_id")
            sensor_name = data.get("sensor_name")
            value = data.get("value")

            if value is None:
                self._log.warning("Command missing value", data=data)
                return

            # Resolve sensor_id from name if needed
            if sensor_id is None and sensor_name:
                # We need a way to look up sensor ID by name
                # Orchestrator doesn't expose this directly efficiently,
                # but we can iterate drivers or access a mapping if strictly needed.
                # For now, let's look at orchestrator._drivers
                for sid, driver in orchestrator._drivers.items():
                    if driver.sensor_name == sensor_name:
                        sensor_id = sid
                        break

            if sensor_id is None:
                self._log.warning("Command missing valid sensor_id or name", data=data)
                return

            # Execute write
            success = await orchestrator.write_sensor(sensor_id, float(value))
            if success:
                self._log.info("Command executed successfully", sensor_id=sensor_id, value=value)
            else:
                self._log.warning("Command execution failed", sensor_id=sensor_id)

        except json.JSONDecodeError:
            self._log.warning("Invalid JSON payload", payload=message.payload)
        except Exception as e:
            self._log.error("Error handling command", error=str(e))

# Global instance
command_listener = CommandListener()
