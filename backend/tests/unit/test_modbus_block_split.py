"""_plan_blocks must never plan a transaction above the Modbus protocol limit."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.drivers import modbus_bus
from app.drivers.modbus import ModbusDriver

MAX_WORDS = 125
MAX_BITS = 2000


def _driver(registers: list[dict[str, Any]]) -> ModbusDriver:
    driver = ModbusDriver(
        sensor_id=1, sensor_name="big",
        config={"host": "ignored", "transaction_timeout_ms": 100}, registers=registers,
    )
    async def sink(*_a: Any) -> None:
        return None
    driver.on_value_batch(sink)
    return driver


def _regs(n: int, rtype: str = "holding", start: int = 0, step: int = 1, count: int = 1) -> list[dict[str, Any]]:
    return [
        {"id": i, "name": f"r{i}", "address": start + i * step, "count": count, "register_type": rtype}
        for i in range(n)
    ]


def _spans(driver: ModbusDriver) -> list[tuple[str, int, int]]:
    """(type, start, words) of every planned transaction."""
    out = []
    for rtype, blocks in driver._plan_blocks():
        for block in blocks:
            out.append((rtype, block[0]["address"], driver._block_span(block)))
    return out


@pytest.mark.parametrize(
    "n,transactions", [(1, 1), (124, 1), (125, 1), (126, 2), (250, 2), (251, 3)]
)
def test_word_blocks_split_at_125(n: int, transactions: int) -> None:
    spans = _spans(_driver(_regs(n)))
    assert len(spans) == transactions
    assert all(words <= MAX_WORDS for _, _, words in spans)
    assert sum(words for _, _, words in spans) == n  # every word still covered exactly once


@pytest.mark.parametrize("rtype", ["input", "holding"])
def test_input_and_holding_share_the_limit(rtype: str) -> None:
    spans = _spans(_driver(_regs(200, rtype)))
    assert [t for t, _, _ in spans] == [rtype, rtype]
    assert all(words <= MAX_WORDS for _, _, words in spans)


@pytest.mark.parametrize(
    "rtype,n,transactions",
    [("coil", 2000, 1), ("coil", 2001, 2), ("discrete", 2000, 1), ("discrete", 2001, 2), ("coil", 4001, 3)],
)
def test_bit_blocks_split_at_2000(rtype: str, n: int, transactions: int) -> None:
    spans = _spans(_driver(_regs(n, rtype)))
    assert len(spans) == transactions
    assert all(bits <= MAX_BITS for _, _, bits in spans)


def test_multi_word_register_is_never_cut_at_the_boundary() -> None:
    # 62 two-word registers = 124 words, then one more two-word register would reach 126.
    regs = _regs(63, count=2, step=2)
    spans = _spans(_driver(regs))
    assert [w for _, _, w in spans] == [124, 2]
    driver = _driver(regs)
    for _rtype, blocks in driver._plan_blocks():
        for block in blocks:
            for reg in block:  # whole register inside its block
                assert reg["address"] + reg["count"] <= block[0]["address"] + driver._block_span(block)


def test_gaps_within_the_merge_limit_still_merge_but_span_is_capped() -> None:
    # registers 10 words apart: each gap merges, the total span must still be capped
    spans = _spans(_driver(_regs(20, step=10)))
    assert len(spans) >= 2
    assert all(words <= MAX_WORDS for _, _, words in spans)


def test_gap_above_the_merge_limit_still_splits_small_blocks() -> None:
    regs = _regs(2, step=100)
    assert len(_spans(_driver(regs))) == 2


def test_small_configuration_is_planned_exactly_as_before() -> None:
    regs = [
        {"id": 1, "name": "a", "address": 0, "count": 1, "register_type": "holding"},
        {"id": 2, "name": "b", "address": 1, "count": 2, "register_type": "holding"},
        {"id": 3, "name": "c", "address": 40, "count": 1, "register_type": "holding"},
    ]
    assert _spans(_driver(regs)) == [("holding", 0, 3), ("holding", 40, 1)]


def test_worst_case_follows_the_number_of_transactions() -> None:
    driver = _driver(_regs(300))  # 3 transactions of <= 125 words
    assert driver._transaction_count() == 3
    assert driver.worst_case_s == pytest.approx(3 * 0.1)


async def test_read_issues_bounded_transactions_and_returns_every_value() -> None:
    driver = _driver(_regs(300))
    driver._client = MagicMock()
    counts: list[int] = []

    async def read(address: int, count: int, **_kw: Any) -> Any:
        counts.append(count)
        result = MagicMock()
        result.isError.return_value = False
        result.registers = [1000 + address + i for i in range(count)]
        return result

    driver._client.read_holding_registers = AsyncMock(side_effect=read)
    readings = await driver.read_registers()
    assert counts == [125, 125, 50]
    assert {r["register_id"]: r["raw_value"] for r in readings} == {i: 1000.0 + i for i in range(300)}


def test_scheduler_budget_counts_the_extra_transactions() -> None:
    driver = _driver(_regs(300))
    assert driver.worst_case_s > modbus_bus.BusSettings().default_transaction_timeout_s


def test_gap_is_measured_from_the_furthest_end_of_overlapping_registers() -> None:
    regs = [
        {"id": 1, "name": "a", "address": 0, "count": 4, "register_type": "holding"},
        {"id": 2, "name": "b", "address": 1, "count": 1, "register_type": "holding"},  # inside a
        {"id": 3, "name": "c", "address": 20, "count": 1, "register_type": "holding"},  # gap 16 from a's end
    ]
    assert _spans(_driver(regs)) == [("holding", 0, 21)]
