"""WebSocket endpoint for real-time sensor updates."""

import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from app.services.orchestrator import orchestrator

logger = structlog.get_logger()
router = APIRouter(tags=["WebSocket"])


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.

    Features:
        - Broadcast to all connected clients
        - Per-sensor subscriptions
        - Automatic cleanup on disconnect
    """

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._subscriptions: dict[int, set[WebSocket]] = {}  # sensor_id -> websockets
        self._log = logger.bind(component="websocket")

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self._connections.append(websocket)
        self._log.info("WebSocket connected", total=len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self._connections:
            self._connections.remove(websocket)

        # Remove from all subscriptions
        for sensor_id in list(self._subscriptions.keys()):
            self._subscriptions[sensor_id].discard(websocket)
            if not self._subscriptions[sensor_id]:
                del self._subscriptions[sensor_id]

        self._log.info("WebSocket disconnected", total=len(self._connections))

    def subscribe(self, websocket: WebSocket, sensor_id: int) -> None:
        """Subscribe a WebSocket to a specific sensor."""
        if sensor_id not in self._subscriptions:
            self._subscriptions[sensor_id] = set()
        self._subscriptions[sensor_id].add(websocket)

    def unsubscribe(self, websocket: WebSocket, sensor_id: int) -> None:
        """Unsubscribe a WebSocket from a sensor."""
        if sensor_id in self._subscriptions:
            self._subscriptions[sensor_id].discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        if not self._connections:
            return

        data = json.dumps(message)
        dead_connections = []

        for connection in self._connections:
            try:
                if connection.client_state == WebSocketState.CONNECTED:
                    await connection.send_text(data)
            except Exception:
                dead_connections.append(connection)

        # Clean up dead connections
        for conn in dead_connections:
            self.disconnect(conn)

    async def send_sensor_update(
        self,
        sensor_id: int,
        sensor_name: str,
        value: float,
        raw_value: float | None,
        status: str,
        timestamp: datetime,
    ) -> None:
        """
        Send a sensor update to subscribed clients.

        Also broadcasts to all clients with a different message type.
        """
        message = {
            "type": "sensor_update",
            "data": {
                "sensor_id": sensor_id,
                "sensor_name": sensor_name,
                "value": value,
                "raw_value": raw_value,
                "status": status,
                "timestamp": timestamp.isoformat(),
            },
        }

        # Send to sensor-specific subscribers
        subscribers = self._subscriptions.get(sensor_id, set())
        for ws in list(subscribers):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(json.dumps(message))
            except Exception:
                subscribers.discard(ws)

        # Broadcast to all (for dashboard overview)
        await self.broadcast(message)

    async def send_status_change(
        self,
        sensor_id: int,
        sensor_name: str,
        old_status: str,
        new_status: str,
    ) -> None:
        """Send a sensor status change notification."""
        message = {
            "type": "status_change",
            "data": {
                "sensor_id": sensor_id,
                "sensor_name": sensor_name,
                "old_status": old_status,
                "new_status": new_status,
                "timestamp": datetime.utcnow().isoformat(),
            },
        }
        await self.broadcast(message)

    async def send_alert(
        self,
        level: str,  # info, warning, error
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send an alert to all clients."""
        alert = {
            "type": "alert",
            "data": {
                "level": level,
                "message": message,
                "details": details or {},
                "timestamp": datetime.utcnow().isoformat(),
            },
        }
        await self.broadcast(alert)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Global connection manager
manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for real-time updates.

    Message format from client:
        {"action": "subscribe", "sensor_id": 1}
        {"action": "unsubscribe", "sensor_id": 1}
        {"action": "ping"}

    Message format to client:
        {"type": "sensor_update", "data": {...}}
        {"type": "status_change", "data": {...}}
        {"type": "alert", "data": {...}}
        {"type": "pong", "data": {"timestamp": "..."}}
    """
    await manager.connect(websocket)

    try:
        # Send initial state
        all_status = orchestrator.get_all_status()
        await websocket.send_text(json.dumps({
            "type": "initial_state",
            "data": {
                "sensors": all_status,
                "connection_id": id(websocket),
            },
        }))

        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    sensor_id = message.get("sensor_id")
                    if sensor_id:
                        manager.subscribe(websocket, sensor_id)
                        await websocket.send_text(json.dumps({
                            "type": "subscribed",
                            "data": {"sensor_id": sensor_id},
                        }))

                elif action == "unsubscribe":
                    sensor_id = message.get("sensor_id")
                    if sensor_id:
                        manager.unsubscribe(websocket, sensor_id)
                        await websocket.send_text(json.dumps({
                            "type": "unsubscribed",
                            "data": {"sensor_id": sensor_id},
                        }))

                elif action == "ping":
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "data": {"timestamp": datetime.utcnow().isoformat()},
                    }))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "data": {"message": "Invalid JSON"},
                }))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error", error=str(e))
        manager.disconnect(websocket)


# ─────────────────────────────────────────────────────────────
# Integration with Orchestrator
# ─────────────────────────────────────────────────────────────

async def on_sensor_value(
    sensor_id: int,
    raw_value: float,
    processed_value: float,
    timestamp: datetime,
) -> None:
    """Callback for orchestrator to send updates via WebSocket."""
    # Get sensor info from orchestrator
    status = orchestrator.get_status(sensor_id)

    await manager.send_sensor_update(
        sensor_id=sensor_id,
        sensor_name=f"sensor_{sensor_id}",  # TODO: Get actual name
        value=processed_value,
        raw_value=raw_value,
        status="ONLINE" if status.get("connected") else "OFFLINE",
        timestamp=timestamp,
    )


async def on_sensor_status(sensor_id: int, status: str) -> None:
    """Callback for orchestrator status changes."""
    await manager.send_status_change(
        sensor_id=sensor_id,
        sensor_name=f"sensor_{sensor_id}",
        old_status="UNKNOWN",
        new_status=status,
    )


# Register callbacks with orchestrator
def setup_websocket_callbacks() -> None:
    """Register WebSocket callbacks with the orchestrator."""
    orchestrator.on_processed_value(on_sensor_value)
    orchestrator.on_sensor_status(on_sensor_status)
