
import asyncio
import contextlib
import re
from typing import Any

import structlog

from app.db.influx import influx_client
from app.services.orchestrator import orchestrator

logger = structlog.get_logger()

class AutomationRule:
    def __init__(
        self,
        rule_id: str,
        name: str,
        condition: str,
        target_sensor_id: int,
        target_value: float,
        cooldown_s: int = 5,
        target_formula: str | None = None
    ):
        self.id = rule_id
        self.name = name
        self.condition = condition
        self.target_sensor_id = target_sensor_id
        self.target_value = target_value
        self.target_formula = target_formula
        self.cooldown_s = cooldown_s
        self.last_triggered = 0.0

class AutomationEngine:
    """
    Evaluates automation rules based on sensor data updates.

    Architecture:
    - Subscribes to Orchestrator 'on_processed_value'
    - Maintains a local cache of latest sensor values (by name)
    - On update, evaluates relevant rules
    - If condition met, calls orchestrator.write_sensor
    """

    def __init__(self):
        self._log = logger.bind(component="automation_engine")
        self._rules: dict[str, AutomationRule] = {}
        self._sensor_values: dict[str, float] = {} # name -> value
        self._stats_values: dict[str, float] = {} # stat_key -> value
        self._running = False
        self._stats_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the automation engine."""
        self._running = True
        orchestrator.on_processed_value(self._handle_update)
        self._stats_task = asyncio.create_task(self._update_stats_loop())
        self._log.info("Automation engine started")

    async def stop(self) -> None:
        self._running = False
        if self._stats_task:
            self._stats_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stats_task
        self._log.info("Automation engine stopped")

    def add_rule(self, rule: AutomationRule) -> None:
        self._rules[rule.id] = rule
        self._log.info("Rule added", rule=rule.name)

    async def _handle_update(
        self,
        sensor_id: int,
        _raw: float,
        value: float,
        _timestamp: Any,
    ) -> None:
        if not self._running:
            return

        # Get sensor name from orchestrator
        # Accessing protected member _drivers is a bit naughty but efficient for internal service
        driver = orchestrator._drivers.get(sensor_id)
        if not driver:
            return

        sensor_name = driver.sensor_name
        self._sensor_values[sensor_name] = value

        # Evaluate rules
        # Optimization: Could map sensor_name -> dependent rules.
        # For now, iterate all (MVP).
        await self._evaluate_rules()

    async def _evaluate_rules(self) -> None:
        import time
        now = time.time()

        for rule in self._rules.values():
            if now - rule.last_triggered < rule.cooldown_s:
                continue

            try:
                # Prepare context
                # Safe context: Math functions + sensor values
                # We use app.core.formula logic but slightly different context
                # evaluate_formula takes 'val', we need strict names.
                # Let's use RestrictedPython directly or just simple eval with safe types?
                # Using standard eval with limited locals is risky but this is internal/admin defined.
                # app.core.formula uses RestrictedPython.

                # Context dict
                context = {**self._sensor_values, **self._stats_values}

                # Simple boolean evaluation
                # Note: This regex/parsing might be needed if using app.core.formula helpers
                # But let's assume valid python syntax for now (e.g. "temp > 50")
                # Warning: eval is dangerous. Ensure rules come from trusted admin.

                # Implementation using simple eval for MVP with restricted scope
                # Using empty globals and sensor values as locals
                allowed_names = {"abs": abs, "max": max, "min": min, "round": round}
                eval_locals = {**allowed_names, **context}

                is_met = eval(rule.condition, {"__builtins__": {}}, eval_locals)

                if is_met:
                    self._log.info("Rule triggered", rule=rule.name, condition=rule.condition)

                    # Calculate target value
                    value_to_write = rule.target_value
                    if rule.target_formula:
                        try:
                            # Use same eval context for target formula
                            value_to_write = float(eval(rule.target_formula, {"__builtins__": {}}, eval_locals))
                        except Exception as e:
                            self._log.error("Target formula error", rule=rule.name, error=str(e))
                            # Fallback or abort? Abort to be safe
                            return

                    await orchestrator.write_sensor(rule.target_sensor_id, value_to_write)
                    self._log.info("Write completed", rule=rule.name, sensor_id=rule.target_sensor_id, value=value_to_write)
                    rule.last_triggered = now

            except Exception as e:
                # Log exception for debugging
                self._log.warning("Rule evaluation error", rule=rule.name, error=str(e))

    async def _update_stats_loop(self) -> None:
        """Periodically update statistical variables required by rules."""
        while self._running:
            try:
                # 1. Identify required stats from all active rules
                required_stats = set()
                pattern = re.compile(r"stat_(\w+)_(\w+)_(\w+)")

                for rule in self._rules.values():
                    # Parse stats from BOTH condition AND target_formula
                    texts_to_parse = [rule.condition]
                    if rule.target_formula:
                        texts_to_parse.append(rule.target_formula)

                    for text in texts_to_parse:
                        matches = pattern.findall(text)
                        for match in matches:
                            # Match: (sensor_name, func, window)
                            # Reconstruct key: stat_Sensor_mean_1h
                            key = f"stat_{match[0]}_{match[1]}_{match[2]}"
                            required_stats.add((key, match[0], match[1], match[2]))

                if required_stats:
                    self._log.info("Stats required", stats=list(required_stats))

                # 2. Query InfluxDB for each
                for key, sensor, func, window in required_stats:
                    # Map window to start time (influx/flux format)
                    # e.g. 1h -> -1h
                    start_time = f"-{window}"

                    # Func mapping: influx returns dict with keys 'mean', 'max', etc.
                    # We query all stats (efficient enough) or could optimize
                    stats = await influx_client.query_statistics(
                        sensor_name=sensor,
                        start=start_time,
                        stop="now()"
                    )

                    self._log.debug("InfluxDB stats query", sensor=sensor, window=window, result=stats)

                    if stats and func in stats and stats[func] is not None:
                         val = stats[func]
                         # Safely cast to float
                         if isinstance(val, (int, float)):
                             self._stats_values[key] = float(val)
                             self._log.info("Stat stored", key=key, value=val)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error("Error updating stats", error=str(e))

            # Update every 30 seconds
            await asyncio.sleep(30)

# Global instance
automation_engine = AutomationEngine()
