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
* Feasibility is checked per bus (``assess_feasibility``) after every
  registration change and every ``FEASIBILITY_LOG_PERIOD_S``: bus utilisation
  (typical = measured latency, worst case = every transaction timing out) and
  round budget vs. the shortest poll interval. Problems are logged as warnings
  so an impossible configuration cannot go unnoticed.

* Read-only buses: a bus is locked when its endpoint is listed in
  ``modbus.read_only_endpoints`` or when any sensor registered on it carries
  ``connection_params.read_only``. The lock is latched per bus key for the life
  of the process (a sensor update removes and re-adds sensors, destroying the
  bus in between). The pymodbus client is ALWAYS reached through
  ``ReadGuardedClient``; while the bus is locked it exposes only the four read
  methods, connect/close/connected, and ``execute`` for function codes 1-4.
  Everything else (every write, diagnostics, anything added by a future
  pymodbus) raises ``ReadOnlyEndpointError``. Deny by default.

The planning rules are pure functions (``plan_round``, ``next_health``) so they
are unit-testable without I/O.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

import structlog
from pymodbus.pdu import ModbusPDU

from app.drivers.base import ReadOnlyEndpointError

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

# How often the measured bus utilisation is re-assessed and logged.
FEASIBILITY_LOG_PERIOD_S = 60.0
# Delay before re-assessing after a registration change (startup debounce).
_ASSESS_DEBOUNCE_S = 2.0
# Typical utilisation above this is reported as close to saturation.
UTILISATION_WARN = 0.8
# Weight of the newest sample in the moving average of successful latency.
_LATENCY_EWMA_ALPHA = 0.2

# Exponent cap so 2**k never grows unbounded for long-dead sensors.
_MAX_BACKOFF_EXPONENT = 30


class SensorHealth(StrEnum):
    """Per-sensor health as seen by the bus scheduler."""

    ONLINE = "ONLINE"
    FAILING = "FAILING"
    QUARANTINED = "QUARANTINED"


class BusConnectionError(Exception):
    """The bus itself (gateway / serial port) is unusable. Not the sensor's fault."""



def literal_address(host: str) -> str | None:
    """Canonical IP of an IP LITERAL host, or None for anything else (names included).

    No name resolution, ever. IPv4 goes through ``socket.inet_aton`` (so the
    legacy numeric forms 127.1, 2130706433, 0x7f000001, 0177.0.0.1 collapse to
    one address); IPv6 through ``ipaddress`` (brackets optional, IPv4-mapped
    addresses fold to their IPv4).
    """
    if not isinstance(host, str):
        return None
    text = host.strip("[]") if host.startswith("[") and host.endswith("]") else host
    if not text or any(c.isspace() for c in text) or "[" in text or "]" in text:
        return None
    try:
        if ":" in text:
            # A scope id (%eth0, %1) is not part of the address: drop it, but an
            # empty one is malformed.
            base, pct, zone = text.partition("%")
            if pct and not zone:
                return None
            ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.IPv6Address(base)
            if ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            return str(ip)
        return str(ipaddress.IPv4Address(socket.inet_aton(text)))
    except (OSError, ValueError):
        return None


def _split_endpoint(value: str) -> tuple[str, int]:
    """Strict config parser: ``ip:port`` or ``[ipv6]:port`` (unbracketed IPv6 + port also accepted).

    Returns (canonical ip, port). ValueError for names and anything malformed.
    """
    bad = ValueError(
        f"read-only endpoint must be 'ip:port' or '[ipv6]:port' with an IP literal "
        f"(host names are not allowed), got {value!r}"
    )
    if not isinstance(value, str):
        raise bad
    text = value.strip()
    if not text or any(c.isspace() for c in text):
        raise bad
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise bad
        port = rest[1:]
    else:
        host, sep, port = text.rpartition(":")
        if not sep:
            raise bad
    if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
        raise bad
    address = literal_address(host)
    if address is None:
        raise bad
    return address, int(port)


def normalise_endpoint(value: str) -> str:
    """Canonical ``ip:port`` / ``[ipv6]:port``. ValueError if not an IP-literal endpoint."""
    address, port = _split_endpoint(value)
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def _key_host_port(key: str) -> tuple[str, int] | None:
    """``tcp://host:port`` -> (host without brackets, port); None for serial keys / bad ports."""
    if not key.startswith("tcp://"):
        return None
    host, sep, port = key[len("tcp://"):].rpartition(":")
    if not sep or not host or not (port.isascii() and port.isdigit()):
        return None
    return host, int(port)


def key_address(key: str) -> tuple[str, int] | None:
    """Canonical (ip, port) of a TCP bus key, or None when its host is not an IP literal."""
    parts = _key_host_port(key)
    if parts is None:
        return None
    address = literal_address(parts[0])
    return (address, parts[1]) if address is not None else None


def bus_key_for(mode: str, params: dict[str, Any]) -> str | None:
    """The scheduler key a sensor with these connection params lands on."""
    try:
        if mode == "tcp":
            return f"tcp://{params.get('host', 'localhost')}:{int(params.get('port', 502))}"
        if mode == "rtu":
            return f"rtu://{params.get('port', '/dev/ttyUSB0')}"
    except (TypeError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class BusSettings:
    """Bus-level timing contract (configurable via the ``modbus:`` config section)."""

    round_budget_s: float = 1.0
    default_transaction_timeout_s: float = 0.25
    quarantine_max_s: float = 60.0
    gateway_settle_s: float = 0.0
    delivery_queue_max: int = 10_000
    # "host:port" of Modbus-TCP endpoints DaTaK must never write to.
    read_only_endpoints: tuple[str, ...] = ()
    # Derived at construction: canonical (ip, port) of the endpoints. No DNS.
    read_only_addresses: frozenset[tuple[str, int]] = field(init=False, default=frozenset())

    def __post_init__(self) -> None:
        # Names and malformed entries fail startup: a typo or a name must not
        # silently leave a write-capable controller unprotected.
        endpoints = tuple(normalise_endpoint(e) for e in self.read_only_endpoints)
        object.__setattr__(self, "read_only_endpoints", endpoints)
        object.__setattr__(
            self, "read_only_addresses", frozenset(_split_endpoint(e) for e in endpoints)
        )
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
    avg_ok_latency_s: float | None = None  # EWMA over successful polls only


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


@dataclass(frozen=True)
class LoadEntry:
    """Planning view of one sensor for the feasibility check."""

    sensor_id: int
    interval_s: float
    worst_case_s: float
    typical_s: float | None  # measured; None until the sensor answered once
    quarantined: bool = False


@dataclass(frozen=True)
class Feasibility:
    worst_case_utilisation: float
    typical_utilisation: float
    min_interval_s: float | None
    warnings: tuple[str, ...]


def assess_feasibility(
    entries: Sequence[LoadEntry], settings: BusSettings
) -> Feasibility:
    """Can this bus serve its sensors at their configured intervals?

    Utilisation = sum(time on the bus per poll / poll interval). Above 1.0 the
    bus cannot keep up: sensors are deferred (polled less often than
    configured), never lost. Worst case assumes every transaction times out;
    it is informative only, since quarantine removes dead slaves from the load.
    Typical uses the measured latency; a sensor not measured yet counts with
    its worst case, and a quarantined one is excluded (its probes are rare).
    """
    if not entries:
        return Feasibility(0.0, 0.0, None, ())
    settle = settings.gateway_settle_s
    worst = sum((e.worst_case_s + settle) / e.interval_s for e in entries)
    typical = sum(
        ((e.typical_s if e.typical_s is not None else e.worst_case_s + settle) / e.interval_s)
        for e in entries
        if not e.quarantined
    )
    shortest = min(entries, key=lambda e: e.interval_s)

    warnings: list[str] = []
    if settings.round_budget_s > shortest.interval_s:
        warnings.append(
            f"round budget {settings.round_budget_s:.3f}s exceeds the shortest poll "
            f"interval {shortest.interval_s:.3f}s (sensor {shortest.sensor_id}): "
            "that sensor can be delayed by up to one round"
        )
    if typical > 1.0:
        warnings.append(
            f"bus over capacity: typical utilisation {typical:.0%} > 100%; sensors "
            "will be polled less often than configured (raise intervals or split the bus)"
        )
    elif typical > UTILISATION_WARN:
        warnings.append(
            f"bus close to saturation: typical utilisation {typical:.0%}"
        )
    return Feasibility(worst, typical, shortest.interval_s, tuple(warnings))


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


# ── read-only guard ──────────────────────────────────────────────────────────

# The only client attributes reachable while a bus is locked.
_READ_METHODS = frozenset(
    {"read_coils", "read_discrete_inputs", "read_holding_registers", "read_input_registers"}
)
_LIFECYCLE = frozenset({"connect", "close"})
_READ_FUNCTION_CODES = frozenset({1, 2, 3, 4})


def read_only_requested(params: Any) -> bool:
    """Is a sensor's ``read_only`` flag set? Fails towards read-only.

    Only an absent key, None, False or 0 means writable. A misconfigured value
    such as the string ``"false"`` locks the sensor: a refused write is
    recoverable, a tracker that moved is not.
    """
    if not isinstance(params, dict) or "read_only" not in params:
        return False
    value = params["read_only"]
    return not (value is None or value is False or (type(value) is int and value == 0))


class _ReadOnlyCtxView:
    """What a locked client shows of ``ctx``: only the timeout parameters.

    ``ModbusBaseClient.execute`` delegates to ``ctx.execute``; handing out the
    real ctx would leave a write path around the guard. The real ctx is held in
    a name-mangled attribute, not reachable as ``_ctx``.
    """

    def __init__(self, ctx: Any, refuse: Any) -> None:
        self.__ctx = ctx
        self.__refuse = refuse

    @property
    def comm_params(self) -> Any:
        return self.__ctx.comm_params

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        self.__refuse(f"ctx.{name}")
        raise AssertionError("unreachable")  # pragma: no cover


class ReadGuardedClient:
    """Allow-list proxy around a pymodbus client (see module docstring).

    The wrapped client is name-mangled so it is not reachable as a plain
    attribute (``guard._client`` does not exist).
    """

    def __init__(self, client: Any, key: str, is_locked: Any) -> None:
        self.__client = client
        self.__key = key
        self.__is_locked = is_locked
        self.__refused: set[str] = set()
        self.__log = logger.bind(component="modbus_bus", bus=key)

    def __refuse(self, name: str) -> None:
        if name not in self.__refused:
            self.__refused.add(name)
            self.__log.warning("Write refused by read-only client guard", method=name)
        raise ReadOnlyEndpointError(
            f"Modbus endpoint {self.__key} is read-only: '{name}' refused"
        )

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not defined on this class.
        if name.startswith("_"):
            raise AttributeError(name)
        if self.__is_locked() and name not in _READ_METHODS and name not in _LIFECYCLE:
            self.__refuse(name)
        return getattr(self.__client, name)

    @property
    def connected(self) -> bool:
        return bool(self.__client.connected)

    @property
    def ctx(self) -> Any:
        if self.__is_locked():
            return _ReadOnlyCtxView(self.__client.ctx, self.__refuse)
        return self.__client.ctx

    def execute(self, no_response_expected: bool, request: Any) -> Any:
        if self.__is_locked():
            code = getattr(request, "function_code", None)
            class_code = getattr(type(request), "function_code", None)
            if not (
                isinstance(request, ModbusPDU)
                and type(code) is int
                and code == class_code
                and code in _READ_FUNCTION_CODES
            ):
                self.__refuse(f"execute(function_code={code!r})")
        return self.__client.execute(no_response_expected, request)


# Bus keys (and canonical addresses of literal hosts) locked for the life of the process,
# whatever happens to the bus object.
_read_only_latch: set[str] = set()
_latched_addresses: set[tuple[str, int]] = set()
# A flagged sensor whose host is a NAME: we cannot know which address it means,
# so every TCP bus is locked.
_all_tcp_latched = False


def lock_bus(key: str, reason: str) -> None:
    """Latch ``key`` read-only (idempotent); a literal host also latches its canonical address.

    A latch on a non-literal host needs nothing more: while any latch exists,
    every non-literal host is locked (see ``bus_is_locked``). Cannot be undone
    short of a restart.
    """
    global _all_tcp_latched
    address = key_address(key)
    if address is not None:
        _latched_addresses.add(address)
    elif key.startswith("tcp://"):
        _all_tcp_latched = True
    if key not in _read_only_latch:
        _read_only_latch.add(key)
        logger.info(
            "Modbus bus locked read-only",
            bus=key,
            reason=reason,
            address=None if address is None else f"{address[0]}:{address[1]}",
        )


def _reset_read_only_latch() -> None:
    """Test helper: forget all latched keys/addresses."""
    global _all_tcp_latched
    _read_only_latch.clear()
    _latched_addresses.clear()
    _all_tcp_latched = False


def bus_is_locked(key: str, settings: BusSettings | None = None) -> bool:
    """Is this bus read-only? By latch key, by canonical address, or fail-closed.

    No name resolution. A TCP bus whose host is not an IP literal is locked as
    soon as anything is read-only (configured endpoint or any latch): a name
    can point anywhere, now or later. With nothing read-only, names work as before.
    """
    if key in _read_only_latch:
        return True
    if not key.startswith("tcp://"):
        return False
    if _all_tcp_latched:
        return True
    configs = [_settings] if settings is None else [_settings, settings]
    if not (_read_only_latch or any(c.read_only_endpoints for c in configs)):
        return False
    address = key_address(key)
    if address is None:
        return True  # fail closed
    if address in _latched_addresses:
        return True
    return any(address in c.read_only_addresses for c in configs)


def row_refuses_writes(protocol: str, params: dict[str, Any]) -> str | None:
    """Why a STORED sensor row can never be written (no driver needed), or None.

    The bus key is derived as the driver sees it: the orchestrator forces the
    mode from the protocol (MODBUS_TCP/MODBUS_RTU), otherwise ``connection_params
    ["mode"]`` (default tcp). Both are checked, so a contradictory row cannot slip through.
    """
    if read_only_requested(params):
        return "sensor is read-only"
    if protocol not in ("MODBUS_TCP", "MODBUS_RTU"):
        return None
    modes = {"MODBUS_TCP": "tcp", "MODBUS_RTU": "rtu"}[protocol], params.get("mode", "tcp")
    for mode in dict.fromkeys(modes):
        key = bus_key_for(str(mode), params)
        if key is not None and bus_is_locked(key):
            return "endpoint is read-only"
    return None


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
        # Always guarded; the guard locks/unlocks dynamically, so every holder
        # of this object (drivers cache it) sees a later lock.
        self.client: Any = ReadGuardedClient(client, key, self._locked)
        self.settings = settings
        self._regs: dict[int, _Registration] = {}
        self._bus_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Explicit stop flag: cancellation alone is not enough, because
        # asyncio.wait_for (3.12) can swallow a cancel that races with the wake
        # event, leaving the loop asleep with no sensors.
        self._closed = False
        self._assess_due = 0.0  # loop time of the next feasibility log
        self._deliveries: asyncio.Queue[tuple[BusJob, Any, datetime]] = asyncio.Queue(
            maxsize=settings.delivery_queue_max
        )
        self._deliver_task: asyncio.Task[None] | None = None
        self._reconnect_delay_s = 0.0
        self._reconnect_at = 0.0
        self._log = logger.bind(component="modbus_bus", bus=key)

    def _locked(self) -> bool:
        return bus_is_locked(self.key, self.settings)

    @property
    def read_only(self) -> bool:
        return self._locked()

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
        # Re-assess shortly after registrations settle (debounced at startup).
        self._assess_due = loop.time() + _ASSESS_DEBOUNCE_S
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
        self._assess_due = asyncio.get_running_loop().time() + _ASSESS_DEBOUNCE_S
        self._wake.set()
        return not self._regs

    def health(self, sensor_id: int) -> HealthState | None:
        reg = self._regs.get(sensor_id)
        return reg.state if reg else None

    def feasibility(self) -> Feasibility:
        entries = [
            LoadEntry(
                sid,
                r.job.interval_s,
                r.job.worst_case_s,
                r.stats.avg_ok_latency_s,
                quarantined=r.state.health is SensorHealth.QUARANTINED,
            )
            for sid, r in self._regs.items()
        ]
        return assess_feasibility(entries, self.settings)

    def _log_feasibility(self) -> None:
        f = self.feasibility()
        fields: dict[str, Any] = {
            "sensors": len(self._regs),
            "typical_utilisation": round(f.typical_utilisation, 3),
            "quarantined": sum(
                1 for r in self._regs.values() if r.state.health is SensorHealth.QUARANTINED
            ),
            "worst_case_utilisation": round(f.worst_case_utilisation, 3),
            "min_interval_s": f.min_interval_s,
            "round_budget_s": self.settings.round_budget_s,
        }
        if f.warnings:
            for w in f.warnings:
                self._log.warning("Bus feasibility", problem=w, **fields)
        else:
            self._log.info("Bus feasibility OK", **fields)

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
                if now >= self._assess_due and self._regs:
                    self._log_feasibility()
                    self._assess_due = now + FEASIBILITY_LOG_PERIOD_S
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
        if error is None:
            prev = reg.stats.avg_ok_latency_s
            reg.stats.avg_ok_latency_s = (
                latency if prev is None
                else prev + _LATENCY_EWMA_ALPHA * (latency - prev)
            )
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


def get_or_create_bus(
    key: str, client_factory: Any, *, read_only: bool = False
) -> BusScheduler:
    """Return the scheduler for ``key``, creating it (and its client) once.

    ``read_only`` latches the key read-only (also for an already existing bus).
    """
    if read_only:
        lock_bus(key, "read-only sensor registered")
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
