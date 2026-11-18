"""Pure scheduling rules of the per-bus Modbus scheduler.

These pin the frozen contract: bounded rounds (worst-case admission against the
round budget, most-overdue first, progress guarantee) and the per-sensor health
FSM (ONLINE -> FAILING -> QUARANTINED with capped exponential backoff).
"""

from __future__ import annotations

import pytest

from app.drivers.modbus_bus import (
    BusSettings,
    HealthState,
    PollSlot,
    SensorHealth,
    next_health,
    plan_round,
)

# ── plan_round ────────────────────────────────────────────────────────────────


def test_plan_round_admits_only_due_sensors() -> None:
    slots = [
        PollSlot(sensor_id=1, next_due=10.0, worst_case_s=0.25),
        PollSlot(sensor_id=2, next_due=10.5, worst_case_s=0.25),  # not due yet
    ]
    assert plan_round(slots, now=10.0, budget_s=1.0) == [1]


def test_plan_round_respects_budget_with_worst_case_cost() -> None:
    # 5 sensors x 250 ms worst case = 1.25 s > 1 s budget -> only 4 fit.
    slots = [PollSlot(sensor_id=i, next_due=0.0, worst_case_s=0.25) for i in range(1, 6)]
    assert plan_round(slots, now=1.0, budget_s=1.0) == [1, 2, 3, 4]


def test_plan_round_orders_most_overdue_first() -> None:
    slots = [
        PollSlot(sensor_id=1, next_due=5.0, worst_case_s=0.25),
        PollSlot(sensor_id=2, next_due=1.0, worst_case_s=0.25),
        PollSlot(sensor_id=3, next_due=3.0, worst_case_s=0.25),
    ]
    assert plan_round(slots, now=6.0, budget_s=1.0) == [2, 3, 1]


def test_plan_round_deferred_sensor_goes_first_next_round() -> None:
    slots = [PollSlot(sensor_id=i, next_due=float(i), worst_case_s=0.5) for i in range(1, 4)]
    first = plan_round(slots, now=10.0, budget_s=1.0)
    assert first == [1, 2]
    # Sensor 3 was not polled: it is still the most overdue in the next round.
    remaining = [s for s in slots if s.sensor_id not in first]
    assert plan_round(remaining, now=11.0, budget_s=1.0)[0] == 3


def test_plan_round_always_admits_first_sensor_even_if_oversized() -> None:
    # A sensor whose worst case exceeds the budget must not starve forever.
    slots = [
        PollSlot(sensor_id=1, next_due=0.0, worst_case_s=1.5),
        PollSlot(sensor_id=2, next_due=0.5, worst_case_s=0.25),
    ]
    assert plan_round(slots, now=1.0, budget_s=1.0) == [1]


def test_plan_round_fills_gaps_with_smaller_sensors() -> None:
    slots = [
        PollSlot(sensor_id=1, next_due=0.0, worst_case_s=0.5),
        PollSlot(sensor_id=2, next_due=0.1, worst_case_s=0.75),  # does not fit after 1
        PollSlot(sensor_id=3, next_due=0.2, worst_case_s=0.25),  # fits
    ]
    assert plan_round(slots, now=1.0, budget_s=1.0) == [1, 3]


def test_plan_round_empty() -> None:
    assert plan_round([], now=0.0, budget_s=1.0) == []


# ── next_health (FSM) ─────────────────────────────────────────────────────────


def _fail(state: HealthState, now: float, interval: float = 5.0) -> HealthState:
    return next_health(
        state, success=False, now=now, interval_s=interval, retry_count=3, quarantine_max_s=300.0
    )


def test_success_keeps_cadence() -> None:
    state = HealthState(next_due=100.0)
    new = next_health(
        state, success=True, now=100.2, interval_s=5.0, retry_count=3, quarantine_max_s=300.0
    )
    assert new.health is SensorHealth.ONLINE
    assert new.consecutive_failures == 0
    assert new.next_due == pytest.approx(105.0)  # anchored on schedule, not on 'now'


def test_success_after_falling_behind_does_not_burst() -> None:
    state = HealthState(next_due=100.0)
    new = next_health(
        state, success=True, now=112.0, interval_s=5.0, retry_count=3, quarantine_max_s=300.0
    )
    assert new.next_due == pytest.approx(117.0)


def test_failures_go_failing_then_quarantined_with_backoff() -> None:
    s1 = _fail(HealthState(next_due=0.0), now=0.0)
    assert (s1.health, s1.consecutive_failures, s1.next_due) == (SensorHealth.FAILING, 1, 5.0)
    s2 = _fail(s1, now=5.0)
    assert (s2.health, s2.next_due) == (SensorHealth.FAILING, 10.0)
    s3 = _fail(s2, now=10.0)  # 3rd consecutive failure == retry_count -> quarantine
    assert s3.health is SensorHealth.QUARANTINED
    assert s3.next_due == pytest.approx(10.0 + 10.0)  # interval x 2
    s4 = _fail(s3, now=20.0)
    assert s4.next_due == pytest.approx(20.0 + 20.0)  # interval x 4


def test_quarantine_backoff_is_capped() -> None:
    state = HealthState(health=SensorHealth.QUARANTINED, consecutive_failures=200, next_due=0.0)
    new = _fail(state, now=1000.0)
    assert new.health is SensorHealth.QUARANTINED
    assert new.next_due == pytest.approx(1300.0)


def test_one_success_leaves_quarantine() -> None:
    state = HealthState(health=SensorHealth.QUARANTINED, consecutive_failures=7, next_due=50.0)
    new = next_health(
        state, success=True, now=50.1, interval_s=5.0, retry_count=3, quarantine_max_s=300.0
    )
    assert new.health is SensorHealth.ONLINE
    assert new.consecutive_failures == 0


def test_backoff_never_shorter_than_interval() -> None:
    # quarantine_max below the poll interval must not speed polling up.
    state = HealthState(consecutive_failures=2, next_due=0.0)
    new = next_health(
        state, success=False, now=0.0, interval_s=60.0, retry_count=3, quarantine_max_s=10.0
    )
    assert new.next_due == pytest.approx(60.0)


# ── BusSettings validation (fail-fast) ────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"round_budget_s": 0.0},
        {"round_budget_s": -1.0},
        {"quarantine_max_s": 0.0},
        {"default_transaction_timeout_s": 0.0},
        {"default_transaction_timeout_s": 2.0, "round_budget_s": 1.0},
    ],
)
def test_bus_settings_rejects_invalid(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BusSettings(**kwargs)


def test_bus_settings_defaults() -> None:
    s = BusSettings()
    assert s.round_budget_s == 1.0
    assert s.default_transaction_timeout_s == 0.25
    assert s.quarantine_max_s == 300.0
