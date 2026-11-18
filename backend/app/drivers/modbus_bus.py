"""Per-bus Modbus scheduler: one owner per physical bus, bounded rounds.

Frozen contract
---------------
* Every Modbus sensor on the same bus (a Modbus-TCP gateway ``host:port``, or a
  serial port) is polled by ONE scheduler task. Drivers never touch the bus
  concurrently.
* Each Modbus transaction has its own response deadline
  (``transaction_timeout_ms`` per sensor, default 250 ms). Time spent waiting
  for the bus is NOT part of that deadline, so a healthy sensor is never blamed
  for a slow neighbour.
* Work is grouped in rounds. A round admits at most ``round_budget_s`` of
  worst-case work (default 1 s); a sensor's worst case is
  ``transactions x transaction timeout``. Due sensors that do not fit are
  deferred to the next round, most-overdue first. The first sensor of a round
  is always admitted, so an oversized sensor cannot starve.
* Between rounds the bus is released: writes (actuators) wait at most one round.
* Per-sensor health FSM: ONLINE -> FAILING -> QUARANTINED. After
  ``retry_count`` consecutive failed polls a sensor is quarantined and probed
  with exponential backoff (``interval x 2^k``, capped at ``quarantine_max_s``).
  One successful poll returns it to ONLINE. A missing/dead slave therefore costs
  one transaction timeout per backoff period, not per poll.
* Bus-level failures (gateway unreachable, connection lost) never count against
  a sensor's health; the bus reconnects with its own capped backoff.
* Values are handed to a per-bus delivery worker through a bounded queue
  (formulas, InfluxDB buffer, northbound MQTT...). A slow or stalled consumer
  (e.g. cloud link down) never slows polling; if the queue fills, readings are
  dropped with an error log, never silently.
* ``gateway_settle_s`` (default 0): pause after a failed transaction, for
  TCP->RTU gateways whose own serial timeout is longer than the client
  deadline (the gateway stays busy with the dead slave). It is charged to the
  round budget. Preferably configure the gateway timeout below the deadline.

The planning rules are pure functions (``plan_round``, ``next_health``) so they
are unit-testable without I/O.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

# Extra time granted on top of a sensor's worst case before the scheduler
# forcibly cancels its poll. Only a safety net: pymodbus enforces the real
# per-transaction deadline itself.
BACKSTOP_MARGIN_S = 0.2

# Bus reconnect backoff (seconds).
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0

# Max time close() waits for queued readings to reach consumers.
_CLOSE_DRAIN_S = 2.0

# Exponent cap so 2**k never grows unbounded for long-dead sensors.
_MAX_BACKOFF_EXPONENT = 30


class SensorHealth(StrEnum):
    """Per-sensor health as seen by the bus scheduler."""

    ONLINE = "ONLINE"
    FAILING = "FAILING"
    QUARANTINED = "QUARANTINED"


class BusConnectionError(Exception):
    """The bus itself (gateway / serial port) is unusable. Not the sensor's fault."""


@dataclass(frozen=True)
class BusSettings:
    """Bus-level timing contract (configurable via the ``modbus:`` config section)."""

    round_budget_s: float = 1.0
    default_transaction_timeout_s: float = 0.25
    quarantine_max_s: float = 300.0
    gateway_settle_s: float = 0.0
    delivery_queue_max: int = 10_000

    def __post_init__(self) -> None:
        if self.round_budget_s <= 0:
            raise ValueError(f"round_budget_s must be > 0, got {self.round_budget_s}")
        if self.default_transaction_timeout_s <= 0:
            raise ValueError(
                "default_transaction_timeout_s must be > 0, "
                f"got {self.default_transaction_timeout_s}"
            )
        if self.default_transaction_timeout_s > self.round_budget_s:
            raise ValueError(
                "default_transaction_timeout_s "
                f"({self.default_transaction_timeout_s}) cannot exceed round_budget_s "
                f"({self.round_budget_s})"
            )
        if self.quarantine_max_s <= 0:
            raise ValueError(f"quarantine_max_s must be > 0, got {self.quarantine_max_s}")
        if self.gateway_settle_s < 0:
            raise ValueError(f"gateway_settle_s must be >= 0, got {self.gateway_settle_s}")
        if self.delivery_queue_max < 1:
            raise ValueError(f"delivery_queue_max must be >= 1, got {self.delivery_queue_max}")


@dataclass(frozen=True)
class PollSlot:
    """Immutable planning view of one registered sensor."""

    sensor_id: int
    next_due: float
    worst_case_s: float


@dataclass(frozen=True)
class HealthState:
    """Immutable per-sensor health/scheduling state."""

    health: SensorHealth = SensorHealth.ONLINE
    consecutive_failures: int = 0
    next_due: float = 0.0


@dataclass
class SensorStats:
    """Counters for observability (logs / metrics). Monotonic, never reset."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    deferred_rounds: int = 0
    dropped_deliveries: int = 0
    last_latency_s: float | None = None
    max_latency_s: float = 0.0


def plan_round(slots: Sequence[PollSlot], now: float, budget_s: float) -> list[int]:
    """Choose which due sensors run in this round.

    Most-overdue first (ties by sensor_id for determinism). A sensor is admitted
    if its worst case still fits in the remaining budget; the first sensor is
    always admitted (progress guarantee).
    """
    due = sorted(
        (s for s in slots if s.next_due <= now),
        key=lambda s: (s.next_due, s.sensor_id),
    )
    admitted: list[int] = []
    used = 0.0
    for slot in due:
        if not admitted or used + slot.worst_case_s <= budget_s + 1e-9:
            admitted.append(slot.sensor_id)
            used += slot.worst_case_s
    return admitted


def next_health(
    state: HealthState,
    *,
    success: bool,
    now: float,
    interval_s: float,
    retry_count: int,
    quarantine_max_s: float,
) -> HealthState:
    """Health FSM transition after one poll attempt."""
    if success:
        next_due = state.next_due + interval_s
        if next_due <= now:  # fell behind: re-anchor instead of bursting to catch up
            next_due = now + interval_s
        return HealthState(SensorHealth.ONLINE, 0, next_due)

    failures = state.consecutive_failures + 1
    threshold = max(1, retry_count)
    if failures < threshold:
        return HealthState(SensorHealth.FAILING, failures, now + interval_s)

    exponent = min(failures - threshold + 1, _MAX_BACKOFF_EXPONENT)
    backoff = min(interval_s * (2**exponent), quarantine_max_s)
    backoff = max(backoff, interval_s)
    return HealthState(SensorHealth.QUARANTINED, failures, now + backoff)


class BusJob(Protocol):
    """What the scheduler needs from a sensor driver."""

    sensor_id: int

    @property
    def interval_s(self) -> float: ...

    @property
    def worst_case_s(self) -> float: ...

    @property
    def retry_count(self) -> int: ...

    async def transact(self) -> Any:
        """Perform this sensor's bus I/O only. Raise on failure."""
        ...

    async def deliver(self, result: Any, timestamp: datetime) -> None:
        """Hand a successful result to consumers (runs with the bus released)."""
        ...

    async def report(
        self, health: SensorHealth, error: str | None, *, bus_ok: bool
    ) -> None:
        """Health/status notification after each attempt."""
        ...


def set_response_timeout(client: Any, timeout_s: float) -> None:
    """Set pymodbus' per-request response deadline on a client owned by one bus.

    pymodbus 3.15 reads ``ctx.comm_params.timeout_connect`` for every
    attempt (pinned in pyproject; ``assert_supported_client`` fails fast otherwise); the scheduler serialises all requests on a bus, so mutating it
    right before each transaction gives a per-sensor deadline without
    cancelling requests from outside the library.
    """
    client.ctx.comm_params.timeout_connect = timeout_s


def assert_supported_client(client: Any) -> None:
    """Fail fast if the pymodbus internals we rely on are missing."""
    try:
        _ = client.ctx.comm_params.timeout_connect
    except AttributeError as exc:  # pragma: no cover - depends on installed pymodbus
        raise RuntimeError(
            "Unsupported pymodbus version: client.ctx.comm_params.timeout_connect "
            "not found (per-transaction timeouts cannot be enforced)"
        ) from exc


@dataclass
class _Registration:
    job: BusJob
    state: HealthState
    stats: SensorStats = field(default_factory=SensorStats)


class BusScheduler:
    """Owns one bus (client + lock) and polls every sensor registered on it."""

    def __init__(self, key: str, client: Any, settings: BusSettings) -> None:
        assert_supported_client(client)
        self.key = key
        self.client = client
        self.settings = settings
        self._regs: dict[int, _Registration] = {}
        self._bus_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Explicit stop flag: cancellation alone is not enough, because
        # asyncio.wait_for (3.12) can swallow a cancel that races with the wake
        # event, leaving the loop asleep with no sensors.
        self._closed = False
        self._deliveries: asyncio.Queue[tuple[BusJob, Any, datetime]] = asyncio.Queue(
            maxsize=settings.delivery_queue_max
        )
        self._deliver_task: asyncio.Task[None] | None = None
        self._reconnect_delay_s = 0.0
        self._reconnect_at = 0.0
        self._log = logger.bind(component="modbus_bus", bus=key)

    # ── registration ─────────────────────────────────────────────────────────

    def register(self, job: BusJob) -> None:
        if self._closed:
            raise BusConnectionError(f"Bus {self.key} is closed")
        loop = asyncio.get_running_loop()
        if job.worst_case_s > self.settings.round_budget_s:
            self._log.warning(
                "Sensor worst case exceeds round budget; it will run alone in its rounds",
                sensor_id=job.sensor_id,
                worst_case_s=job.worst_case_s,
                round_budget_s=self.settings.round_budget_s,
            )
        self._regs[job.sensor_id] = _Registration(job, HealthState(next_due=loop.time()))
        self._log.info(
            "Sensor registered on bus",
            sensor_id=job.sensor_id,
            interval_s=job.interval_s,
            worst_case_s=job.worst_case_s,
            retry_count=job.retry_count,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"modbus-bus:{self.key}")
        if self._deliver_task is None or self._deliver_task.done():
            self._deliver_task = asyncio.create_task(
                self._deliver_loop(), name=f"modbus-deliver:{self.key}"
            )
        self._wake.set()

    def unregister(self, sensor_id: int) -> bool:
        """Remove a sensor (synchronous). Returns True when the bus has no sensors left."""
        self._regs.pop(sensor_id, None)
        self._wake.set()
        return not self._regs

    def health(self, sensor_id: int) -> HealthState | None:
        reg = self._regs.get(sensor_id)
        return reg.state if reg else None

    def stats(self, sensor_id: int) -> SensorStats | None:
        reg = self._regs.get(sensor_id)
        return reg.stats if reg else None

    @property
    def sensor_count(self) -> int:
        return len(self._regs)

    # ── exclusive access for writes ──────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def exclusive(self, timeout_s: float) -> AsyncIterator[Any]:
        """Hold the bus between rounds (e.g. for a write). Yields the client."""
        async with self._bus_lock:
            if not await self._ensure_connected(force=True):
                raise BusConnectionError(f"Bus {self.key} not connected")
            set_response_timeout(self.client, timeout_s)
            yield self.client

    # ── main loop ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        self._log.info("Bus scheduler started", round_budget_s=self.settings.round_budget_s)
        try:
            while not self._closed:
                self._wake.clear()
                now = loop.time()
                settle = self.settings.gateway_settle_s
                slots = [
                    PollSlot(sid, r.state.next_due, r.job.worst_case_s + settle)
                    for sid, r in self._regs.items()
                ]
                admitted = plan_round(slots, now, self.settings.round_budget_s)
                if not admitted:
                    await self._sleep_until_next(slots, now)
                    continue

                for slot in slots:
                    if slot.next_due <= now and slot.sensor_id not in admitted:
                        self._regs[slot.sensor_id].stats.deferred_rounds += 1

                deliveries = await self._run_round(admitted)

                # Bus released: hand values to the delivery worker (never blocks),
                # then yield so waiting writers get in.
                for item in deliveries:
                    self._enqueue_delivery(item)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            self._log.info("Bus scheduler stopped")
            raise

    def _enqueue_delivery(self, item: tuple[BusJob, Any, datetime]) -> None:
        try:
            self._deliveries.put_nowait(item)
        except asyncio.QueueFull:
            job = item[0]
            reg = self._regs.get(job.sensor_id)
            if reg is not None:
                reg.stats.dropped_deliveries += 1
            self._log.error(
                "Delivery queue full: reading DROPPED (consumers stalled)",
                sensor_id=job.sensor_id,
                queue_max=self.settings.delivery_queue_max,
            )

    async def _deliver_loop(self) -> None:
        while True:
            job, result, ts = await self._deliveries.get()
            try:
                await job.deliver(result, ts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # consumer failure must not kill delivery
                self._log.exception(
                    "Value delivery failed", sensor_id=job.sensor_id, error=str(exc)
                )
            finally:
                self._deliveries.task_done()

    async def _sleep_until_next(self, slots: Sequence[PollSlot], now: float) -> None:
        delay = min((s.next_due for s in slots), default=now + 3600.0) - now
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=max(delay, 0.0))

    async def _run_round(self, admitted: list[int]) -> list[tuple[BusJob, Any, datetime]]:
        loop = asyncio.get_running_loop()
        deliveries: list[tuple[BusJob, Any, datetime]] = []
        async with self._bus_lock:
            round_start = loop.time()
            if not await self._ensure_connected():
                for sid in admitted:
                    await self._bus_down(sid, "Bus not connected")
                return deliveries

            for index, sid in enumerate(admitted):
                reg = self._regs.get(sid)
                if reg is None:  # unregistered mid-round
                    continue
                elapsed = loop.time() - round_start
                cost = reg.job.worst_case_s + self.settings.gateway_settle_s
                if index > 0 and elapsed + cost > self.settings.round_budget_s:
                    # Real overhead consumed the slack: defer instead of overrunning.
                    reg.stats.deferred_rounds += 1
                    continue
                outcome = await self._poll_one(reg)
                if outcome is not None:
                    deliveries.append(outcome)
                elif self.settings.gateway_settle_s > 0:
                    # Let a TCP->RTU gateway finish waiting for the failed slave.
                    await asyncio.sleep(self.settings.gateway_settle_s)

            round_s = loop.time() - round_start
            if round_s > self.settings.round_budget_s:
                self._log.warning(
                    "Round exceeded budget",
                    round_s=round(round_s, 3),
                    round_budget_s=self.settings.round_budget_s,
                    sensors=admitted,
                )
        return deliveries

    async def _poll_one(self, reg: _Registration) -> tuple[BusJob, Any, datetime] | None:
        loop = asyncio.get_running_loop()
        job = reg.job
        reg.stats.attempts += 1
        started = loop.time()
        error: str | None = None
        result: Any = None
        try:
            result = await asyncio.wait_for(
                job.transact(), timeout=job.worst_case_s + BACKSTOP_MARGIN_S
            )
        except BusConnectionError as exc:
            await self._bus_down(job.sensor_id, str(exc))
            return None
        except TimeoutError:
            error = (
                f"Backstop timeout after {job.worst_case_s + BACKSTOP_MARGIN_S:.3f}s "
                "(transaction deadline not enforced by client)"
            )
            self._log.error("Poll backstop fired", sensor_id=job.sensor_id)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__

        latency = loop.time() - started
        reg.stats.last_latency_s = latency
        reg.stats.max_latency_s = max(reg.stats.max_latency_s, latency)
        ok = error is None
        if ok:
            reg.stats.successes += 1
        else:
            reg.stats.failures += 1

        previous = reg.state.health
        reg.state = next_health(
            reg.state,
            success=ok,
            now=loop.time(),
            interval_s=job.interval_s,
            retry_count=job.retry_count,
            quarantine_max_s=self.settings.quarantine_max_s,
        )
        log_kwargs: dict[str, Any] = {
            "sensor_id": job.sensor_id,
            "ok": ok,
            "latency_ms": round(latency * 1000, 1),
            "health": reg.state.health.value,
            "consecutive_failures": reg.state.consecutive_failures,
            "next_poll_in_s": round(reg.state.next_due - loop.time(), 2),
        }
        if ok:
            self._log.debug("Poll", **log_kwargs)
        else:
            self._log.warning("Poll failed", error=error, **log_kwargs)
        if reg.state.health is not previous:
            self._log.info(
                "Sensor health changed",
                sensor_id=job.sensor_id,
                previous=previous.value,
                current=reg.state.health.value,
            )

        try:
            await job.report(reg.state.health, error, bus_ok=True)
        except Exception as exc:
            self._log.exception("Status report failed", sensor_id=job.sensor_id, error=str(exc))

        if ok:
            return job, result, datetime.now(UTC)
        return None

    async def _bus_down(self, sensor_id: int, error: str) -> None:
        """Bus-level failure: reschedule without touching sensor health."""
        reg = self._regs.get(sensor_id)
        if reg is None:
            return
        loop = asyncio.get_running_loop()
        reg.state = replace(reg.state, next_due=loop.time() + reg.job.interval_s)
        try:
            await reg.job.report(reg.state.health, error, bus_ok=False)
        except Exception as exc:
            self._log.exception("Status report failed", sensor_id=sensor_id, error=str(exc))

    async def _ensure_connected(self, *, force: bool = False) -> bool:
        if self.client.connected:
            return True
        loop = asyncio.get_running_loop()
        now = loop.time()
        if not force and now < self._reconnect_at:
            return False
        try:
            set_response_timeout(self.client, self.settings.round_budget_s)
            ok = bool(
                await asyncio.wait_for(self.client.connect(), timeout=self.settings.round_budget_s)
            )
        except Exception as exc:
            self._log.warning("Bus connect failed", error=str(exc))
            ok = False
        if ok:
            if self._reconnect_delay_s:
                self._log.info("Bus reconnected")
            self._reconnect_delay_s = 0.0
            return True
        self._reconnect_delay_s = min(
            max(self._reconnect_delay_s * 2, _RECONNECT_MIN_S), _RECONNECT_MAX_S
        )
        self._reconnect_at = now + self._reconnect_delay_s
        self._log.warning("Bus unavailable", retry_in_s=self._reconnect_delay_s)
        return False

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._deliver_task is not None:
            # Give pending readings a bounded chance to reach consumers.
            try:
                await asyncio.wait_for(self._deliveries.join(), timeout=_CLOSE_DRAIN_S)
            except TimeoutError:
                self._log.error(
                    "Bus closed with undelivered readings",
                    pending=self._deliveries.qsize(),
                )
            self._deliver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._deliver_task
            self._deliver_task = None
        with contextlib.suppress(Exception):
            self.client.close()


# ── registry (one scheduler per physical bus) ────────────────────────────────

_settings = BusSettings()
_buses: dict[str, BusScheduler] = {}


def configure(settings: BusSettings) -> None:
    """Set bus timing defaults. Call once at startup, before sensors are added."""
    global _settings
    _settings = settings


def current_settings() -> BusSettings:
    return _settings


def get_bus(key: str) -> BusScheduler | None:
    return _buses.get(key)


def get_or_create_bus(key: str, client_factory: Any) -> BusScheduler:
    """Return the scheduler for ``key``, creating it (and its client) once."""
    bus = _buses.get(key)
    if bus is None:
        bus = BusScheduler(key, client_factory(), _settings)
        _buses[key] = bus
    return bus


async def release_bus(key: str, sensor_id: int) -> None:
    """Unregister a sensor; close the bus when it has no sensors left."""
    bus = _buses.get(key)
    if bus is None:
        return
    if bus.unregister(sensor_id):
        # Drop from the registry BEFORE awaiting, so a sensor added concurrently
        # gets a fresh bus instead of this one being closed under it.
        _buses.pop(key, None)
        await bus.close()
