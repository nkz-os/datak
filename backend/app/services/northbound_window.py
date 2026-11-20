"""Fixed-window aggregation of northbound readings.

Sampling stays fast locally (raw readings go to InfluxDB untouched); the platform
receives one message per Device per window instead of one per reading.

Contract:
- Windows are aligned to the clock: [k*interval, (k+1)*interval) in UTC epoch seconds.
- A window is stamped with its END. The platform reads that as the observation time
  of the aggregate, like the interval averages of a weather station.
- Each key carries the statistics the caller asked for (mean, min, max, stddev, last,
  sum); without a request it is the mean of the valid samples in the window, except the keys listed
  as last-value (running counters: a mean across a counter rollover is meaningless),
  which carry the latest sample of the window.
- A key with no valid sample (None, NaN, inf) is omitted; a Device with no key in a
  window publishes nothing.
- A window length may differ per topic. A reading for a window of its topic that was already closed is dropped and counted, never
  sent as a second message for the same window.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class _Series:
    outputs: dict[str, str]  # statistic -> output key
    count: int = 0
    total: float = 0.0
    mean: float = 0.0  # Welford running mean / sum of squared deviations
    m2: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    last_value: float = 0.0
    last_ts: float = -math.inf

    def add(self, value: float, ts: float) -> None:
        self.count += 1
        self.total += value
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        if ts >= self.last_ts:
            self.last_value, self.last_ts = value, ts

    def results(self) -> dict[str, float]:
        """Output key -> rounded statistic. stddev (n-1) needs two samples."""
        out: dict[str, float] = {}
        for stat, key in self.outputs.items():
            if stat == "mean":
                out[key] = round(self.total / self.count, 4)
            elif stat == "sum":
                out[key] = round(self.total, 4)
            elif stat == "min":
                out[key] = round(self.minimum, 4)
            elif stat == "max":
                out[key] = round(self.maximum, 4)
            elif stat == "last":
                out[key] = round(self.last_value, 4)
            elif stat == "stddev" and self.count >= 2:
                out[key] = round(math.sqrt(max(self.m2, 0.0) / (self.count - 1)), 4)
        return out


@dataclass
class ClosedWindow:
    topic: str
    end: datetime
    values: dict[str, float] = field(default_factory=dict)


def _epoch(ts: datetime) -> float:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).timestamp()


class WindowAggregator:
    """Accumulates readings per (topic, window) and hands back closed windows."""

    def __init__(self, interval_s: int = 0, last_value_keys: Iterable[str] = ()) -> None:
        self.interval_s = interval_s  # default; each reading may bring its own
        self._last_value_keys = frozenset(last_value_keys)
        self._open: dict[tuple[str, float], dict[str, _Series]] = {}
        self._closed_until: dict[str, float] = {}  # per topic: end of the latest window handed out
        self.late_dropped = 0

    def window_end(self, ts: datetime, interval_s: int | None = None) -> float:
        interval = interval_s or self.interval_s
        if interval <= 0:
            raise ValueError("interval_s must be > 0")
        return (math.floor(_epoch(ts) / interval) + 1) * interval

    def add(self, topic: str, key: str, value: float | None, ts: datetime,
            attribute: str | None = None, interval_s: int | None = None,
            outputs: Mapping[str, str] | None = None) -> bool:
        """Account one reading. False when it is not a valid sample or arrived too late.

        `outputs` maps statistic -> output key; None keeps the legacy single value
        (the latest for last-value keys, else the mean) under `key`.
        """
        end = self.window_end(ts, interval_s)
        if value is None or isinstance(value, bool):
            return False
        value = float(value)
        if not math.isfinite(value):
            return False
        if end <= self._closed_until.get(topic, -math.inf):
            self.late_dropped += 1
            return False
        window = self._open.setdefault((topic, end), {})
        if key not in window:
            if outputs is None:
                # The rule may name the platform key or the SDM attribute it maps to.
                last_only = key in self._last_value_keys or attribute in self._last_value_keys
                outputs = {"last": key} if last_only else {"mean": key}
            window[key] = _Series(outputs=dict(outputs))
        window[key].add(value, _epoch(ts))
        return True

    def pop_closed(self, now: datetime, grace_s: float = 0.0) -> list[ClosedWindow]:
        """Windows whose end + grace is not after `now`, oldest first."""
        cutoff = _epoch(now) - grace_s
        ready = sorted((k for k in self._open if k[1] <= cutoff), key=lambda k: (k[1], k[0]))
        out: list[ClosedWindow] = []
        for topic, end in ready:
            series = self._open.pop((topic, end))
            values: dict[str, float] = {}
            for s in series.values():
                if s.count:
                    values.update(s.results())
            if values:
                out.append(ClosedWindow(topic, datetime.fromtimestamp(end, UTC), values))
            self._closed_until[topic] = max(self._closed_until.get(topic, -math.inf), end)
        return out

    def discard_open(self) -> int:
        """Drop the windows still open (shutdown). Returns how many were dropped."""
        dropped = len(self._open)
        self._open.clear()
        return dropped
