"""Driver orchestrator for lifecycle management and hot-reload."""

import asyncio
from datetime import datetime
from typing import Any

import structlog

from app.core.formula import FormulaError, evaluate_formula
from app.drivers.base import BaseDriver
from app.drivers.canbus import CANDriver
from app.drivers.modbus import ModbusDriver
from app.drivers.mqtt import MQTTDriver
from app.drivers.system import SystemDriver
from app.drivers.virtual_output import VirtualOutputDriver
from app.models.sensor import SensorProtocol

logger = structlog.get_logger()

# Driver class mapping
DRIVER_CLASSES: dict[str, type[BaseDriver]] = {
    SensorProtocol.MODBUS_TCP.value: ModbusDriver,
    SensorProtocol.MODBUS_RTU.value: ModbusDriver,
    SensorProtocol.CAN.value: CANDriver,
    SensorProtocol.MQTT.value: MQTTDriver,
    SensorProtocol.SYSTEM.value: SystemDriver,
    SensorProtocol.VIRTUAL_OUTPUT.value: VirtualOutputDriver,
}


class DriverOrchestrator:
    """
    Manages driver lifecycle with support for hot-reload.

    Features:
        - Start/stop individual drivers without affecting others
        - Apply formulas to transform raw values
        - Route values to persistence layer
        - Handle status updates
    """

    def __init__(self):
        self._drivers: dict[int, BaseDriver] = {}
        self._formulas: dict[int, str] = {}
        self._running = False
        self._log = logger.bind(component="orchestrator")

        # Callbacks for external integration
        self._on_processed_value: (
            Any | None
        ) = None  # (sensor_id, raw, processed, timestamp) -> None
        self._on_sensor_status: Any | None = None  # (sensor_id, status) -> None

    async def start(self) -> None:
        """Start the orchestrator."""
        self._running = True
        self._log.info("Orchestrator started")

    async def stop(self) -> None:
        """Stop all drivers and shutdown."""
        self._log.info("Stopping orchestrator")
        self._running = False

        # Stop all drivers concurrently
        stop_tasks = [driver.stop() for driver in self._drivers.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        self._drivers.clear()
        self._formulas.clear()
        self._log.info("Orchestrator stopped")

    async def add_sensor(
        self,
        sensor_id: int,
        sensor_name: str,
        protocol: str,
        connection_params: dict[str, Any],
        formula: str = "val",
        poll_interval_ms: int = 1000,
        timeout_ms: int = 5000,
        retry_count: int = 3,
        registers: list[dict[str, Any]] | None = None,
    ) -> bool:
        """
        Add and start a new sensor driver (hot-reload).

        Returns True if driver started successfully.
        """
        if sensor_id in self._drivers:
            self._log.warning("Sensor already exists, updating", sensor_id=sensor_id)
            await self.remove_sensor(sensor_id)

        # Get driver class
        driver_class = DRIVER_CLASSES.get(protocol)
        if not driver_class:
            self._log.error("Unknown protocol", protocol=protocol)
            return False

        # Special handling for Modbus modes
        config = connection_params.copy()
        if protocol == SensorProtocol.MODBUS_TCP.value:
            config["mode"] = "tcp"
        elif protocol == SensorProtocol.MODBUS_RTU.value:
            config["mode"] = "rtu"

        # Create driver
        try:
            driver_kwargs: dict[str, Any] = {
                "sensor_id": sensor_id,
                "sensor_name": sensor_name,
                "config": config,
                "poll_interval_ms": poll_interval_ms,
                "timeout_ms": timeout_ms,
                "retry_count": retry_count,
            }
            # Pass registers if this is a Modbus driver
            if registers and driver_class == ModbusDriver:
                driver_kwargs["registers"] = registers

            driver = driver_class(**driver_kwargs)

            # Register callbacks
            driver.on_value(self._handle_value)
            if hasattr(driver, "on_value_batch"):
                driver.on_value_batch(self._handle_value_batch)
            driver.on_error(self._handle_error)
            driver.on_status_change(self._handle_status)

            # Store formula
            self._formulas[sensor_id] = formula

            # Start driver
            await driver.start()

            self._drivers[sensor_id] = driver
            self._log.info(
                "Sensor added and started",
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                protocol=protocol,
            )
            return True

        except Exception as e:
            self._log.exception("Failed to add sensor", sensor_id=sensor_id, error=str(e))
            return False

    async def remove_sensor(self, sensor_id: int) -> bool:
        """
        Stop and remove a sensor driver (hot-reload).

        Returns True if driver was removed.
        """
        driver = self._drivers.pop(sensor_id, None)
        self._formulas.pop(sensor_id, None)

        if driver:
            await driver.stop()
            self._log.info("Sensor removed", sensor_id=sensor_id)
            return True

        return False

    async def update_formula(self, sensor_id: int, formula: str) -> bool:
        """Update the formula for a sensor without restarting driver."""
        if sensor_id not in self._drivers:
            return False

        self._formulas[sensor_id] = formula
        self._log.info("Formula updated", sensor_id=sensor_id, formula=formula)
        return True

    async def restart_sensor(self, sensor_id: int) -> bool:
        """Restart a specific sensor driver."""
        driver = self._drivers.get(sensor_id)
        if driver:
            await driver.restart()
            return True
        return False

    async def write_sensor(self, sensor_id: int, value: float) -> bool:
        """Write a value to a sensor."""
        driver = self._drivers.get(sensor_id)
        if not driver:
            self._log.warning("Write failed: driver not found", sensor_id=sensor_id, registered_ids=list(self._drivers.keys()))
            return False

        if not driver.is_running:
            self._log.warning("Write failed: driver not running", sensor_id=sensor_id)
            return False

        try:
            return await driver.write(value)
        except Exception as e:
            self._log.error("Write failed", sensor_id=sensor_id, error=str(e))
            raise


    def get_status(self, sensor_id: int) -> dict[str, Any]:
        """Get current status of a sensor."""
        driver = self._drivers.get(sensor_id)
        if not driver:
            return {"exists": False}

        return {
            "exists": True,
            "running": driver.is_running,
            "connected": driver.is_connected,
            "last_value": driver.last_value,
            "error_count": driver.error_count,
        }

    def get_all_status(self) -> dict[int, dict[str, Any]]:
        """Get status of all sensors."""
        return {sid: self.get_status(sid) for sid in self._drivers}

    # ─────────────────────────────────────────────────────────────
    # Callback Registration
    # ─────────────────────────────────────────────────────────────


    def on_processed_value(self, callback: Any) -> None:
        """Register callback for processed values."""
        if self._on_processed_value is None:
            self._on_processed_value = []
        if isinstance(self._on_processed_value, list):
            self._on_processed_value.append(callback)
        else:
             # Handle legacy single callback if any (though we initialized to None typings might be weird)
             self._on_processed_value = [callback]

    def on_sensor_status(self, callback: Any) -> None:
        """Register callback for status changes."""
        self._on_sensor_status = callback

    # ─────────────────────────────────────────────────────────────
    # Internal Handlers
    # ─────────────────────────────────────────────────────────────

    async def _handle_value(
        self,
        sensor_id: int,
        raw_value: float,
        _: float | None,
        timestamp: datetime,
    ) -> None:
        """Process incoming value from driver (single-register mode)."""
        # Apply formula
        formula = self._formulas.get(sensor_id, "val")
        try:
            processed_value = evaluate_formula(formula, raw_value)
        except FormulaError as e:
            self._log.warning(
                "Formula error, using raw value",
                sensor_id=sensor_id,
                error=str(e),
            )
            processed_value = raw_value

        # Notify callbacks
        if self._on_processed_value:
            if isinstance(self._on_processed_value, list):
                for cb in self._on_processed_value:
                    try:
                        await cb(sensor_id, raw_value, processed_value, timestamp)
                    except Exception as e:
                        self._log.error("Callback failed", error=str(e))
            elif callable(self._on_processed_value):
                await self._on_processed_value(sensor_id, raw_value, processed_value, timestamp)

    async def _handle_value_batch(
        self,
        sensor_id: int,
        register_readings: list[dict[str, Any]],
        timestamp: datetime,
    ) -> None:
        """Process batch readings from a multi-register sensor."""
        for reading in register_readings:
            raw_value = reading["raw_value"]
            formula = reading.get("formula", "val")
            try:
                processed_value = evaluate_formula(formula, raw_value)
            except FormulaError as e:
                self._log.warning(
                    "Formula error, using raw value",
                    sensor_id=sensor_id,
                    register_id=reading["register_id"],
                    error=str(e),
                )
                processed_value = raw_value

            # Attach register info to the processed value context
            reading["processed_value"] = processed_value

            if self._on_processed_value:
                if isinstance(self._on_processed_value, list):
                    for cb in self._on_processed_value:
                        try:
                            await cb(sensor_id, raw_value, processed_value, timestamp, reading)
                        except Exception as e:
                            self._log.error("Callback failed", error=str(e))
                elif callable(self._on_processed_value):
                    await self._on_processed_value(
                        sensor_id, raw_value, processed_value, timestamp, reading
                    )

    async def _handle_error(self, sensor_id: int, error: str) -> None:
        """Handle driver error."""
        self._log.warning("Driver error", sensor_id=sensor_id, error=error)

    async def _handle_status(self, sensor_id: int, status: str) -> None:
        """Handle driver status change."""
        if self._on_sensor_status:
            await self._on_sensor_status(sensor_id, status)


# Global orchestrator instance
orchestrator = DriverOrchestrator()
