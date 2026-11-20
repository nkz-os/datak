"""Fixed-window aggregation of northbound readings.

Sampling stays fast locally (raw readings go to InfluxDB untouched); the platform
receives one message per Device per window instead of one per reading.

Contract:
- Windows are aligned to the clock: [k*interval, (k+1)*interval) in UTC epoch seconds.
- A window is stamped with its END. The platform reads that as the observation time
  of the aggregate, like the interval averages of a weather station.
- Each key is the mean of the valid samples in the window, except the keys listed
  as last-value (running counters: a mean across a counter rollover is meaningless),
  which carry the latest sample of the window.
- A key with no valid sample (None, NaN, inf) is omitted; a Device with no key in a
  window publishes nothing.
- A reading for a window that was already closed is dropped and counted, never
  sent as a second message for the same window.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class _Series:
    last_value_only: bool = False
    total: float = 0.0
    count: int = 0
    last_value: float = 0.0
    last_ts: float = -math.inf

    def add(self, value: float, ts: float) -> None:
        self.total += value
        self.count += 1
        if ts >= self.last_ts:
            self.last_value, self.last_ts = value, ts


@dataclass
class ClosedWindow:
    topic: str
    end: datetime
    values: dict[str, float] = field(default_factory=dict)


def _epoch(ts: datetime) -> float:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).timestamp()


class WindowAggregator:
    """Accumulates readings per (topic, window) and hands back closed windows."""

    def __init__(self, interval_s: int, last_value_keys: Iterable[str] = ()) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self.interval_s = interval_s
        self._last_value_keys = frozenset(last_value_keys)
        self._open: dict[tuple[str, float], dict[str, _Series]] = {}
        self._closed_until = -math.inf  # end of the latest window handed out
        self.late_dropped = 0

    def window_end(self, ts: datetime) -> float:
        return (math.floor(_epoch(ts) / self.interval_s) + 1) * self.interval_s

    def add(self, topic: str, key: str, value: float | None, ts: datetime,
            attribute: str | None = None) -> bool:
        """Account one reading. False when it is not a valid sample or arrived too late."""
        if value is None or isinstance(value, bool):
            return False
        value = float(value)
        if not math.isfinite(value):
            return False
        end = self.window_end(ts)
        if end <= self._closed_until:
            self.late_dropped += 1
            return False
        window = self._open.setdefault((topic, end), {})
        if key not in window:
            # The rule may name the platform key or the SDM attribute it maps to.
            last_only = key in self._last_value_keys or attribute in self._last_value_keys
            window[key] = _Series(last_value_only=last_only)
        window[key].add(value, _epoch(ts))
        return True

    def pop_closed(self, now: datetime, grace_s: float = 0.0) -> list[ClosedWindow]:
        """Windows whose end + grace is not after `now`, oldest first."""
        cutoff = _epoch(now) - grace_s
        ready = sorted((k for k in self._open if k[1] <= cutoff), key=lambda k: (k[1], k[0]))
        out: list[ClosedWindow] = []
        for topic, end in ready:
            series = self._open.pop((topic, end))
            values = {
                key: (s.last_value if s.last_value_only else round(s.total / s.count, 4))
                for key, s in series.items()
                if s.count
            }
            if values:
                out.append(ClosedWindow(topic, datetime.fromtimestamp(end, UTC), values))
            self._closed_until = max(self._closed_until, end)
        return out

    def discard_open(self) -> int:
        """Drop the windows still open (shutdown). Returns how many were dropped."""
        dropped = len(self._open)
        self._open.clear()
        return dropped
