"""Register decoding: data_type / word_order / bit fields.

Fixture words were read from a real solar-tracker controller (copied here so
the tests do not depend on any file).
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.drivers.modbus import ModbusDriver
from app.drivers.register_decode import decode_words, validate_decoding

# tracker 1 compact block (22 words)
COMPACT = [0, 128, 0, 0, 32800, 48441, 9665, 48922, 0, 0, 48651, 49013,
           13, 84, 4828, 0, 26680, 0, 0, 2905, 2850, 100]
# weather station data block (first 7 words)
WEATHER = [5636, 0, 0, 16520, 16421, 61046, 17294]
# tracker 1 full block (first 11 words)
FULL = [9745, 10001, 1860, 0, 128, 0, 0, 0, 0, 32800, 65191]


def _dec(words: list[int], data_type: str | None, **kw: Any) -> float:
    return decode_words(
        words,
        data_type=data_type,
        word_order=kw.pop("word_order", "high_first"),
        bit_offset=kw.pop("bit_offset", None),
        bit_length=kw.pop("bit_length", None),
        count=kw.pop("count", len(words)),
    )


# ── real controller words ────────────────────────────────────────────────────


def test_float32_low_word_first_angle_radians_to_degrees() -> None:
    radians = _dec(COMPACT[6:8], "float32", word_order="low_first")
    assert math.degrees(radians) == pytest.approx(-34.5, abs=1e-3)


def test_float32_low_word_first_target_angle() -> None:
    radians = _dec(COMPACT[10:12], "float32", word_order="low_first")
    assert math.degrees(radians) == pytest.approx(-55.0, abs=1e-3)


def test_float32_wind_speed_and_direction_low_first() -> None:
    assert _dec(WEATHER[3:5], "float32", word_order="low_first") == pytest.approx(2.58206, abs=1e-4)
    assert _dec(WEATHER[5:7], "float32", word_order="low_first") == pytest.approx(285.863, abs=1e-3)


def test_same_words_high_first_give_a_different_number() -> None:
    assert _dec(WEATHER[3:5], "float32", word_order="high_first") != pytest.approx(2.58206, abs=1e-2)


def test_int16_negative() -> None:
    assert _dec(FULL[10:11], "int16", count=1) == -345.0
    assert _dec(FULL[10:11], "uint16", count=1) == 65191.0


def test_bit_field_low_byte_is_state_of_charge() -> None:
    assert _dec(COMPACT[13:14], "uint16", count=1, bit_offset=0, bit_length=8) == 84.0
    assert _dec(COMPACT[13:14], "uint16", count=1, bit_offset=8, bit_length=8) == 0.0


def test_plain_uint16_k_times_ten() -> None:
    assert _dec(COMPACT[19:20], "uint16", count=1) == 2905.0
    assert _dec(COMPACT[20:21], "uint16", count=1) == 2850.0


# ── type matrix ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "words,data_type,order,expected",
    [
        ([0x0001, 0x0002], "uint32", "high_first", 0x00010002),
        ([0x0001, 0x0002], "uint32", "low_first", 0x00020001),
        ([0xFFFF, 0xFFFE], "int32", "high_first", -2.0),
        ([0xFFFE, 0xFFFF], "int32", "low_first", -2.0),
        ([0x3FC0, 0x0000], "float32", "high_first", 1.5),
        ([0x0000, 0x3FC0], "float32", "low_first", 1.5),
        ([0x8000], "int16", "high_first", -32768.0),
        ([0x7FFF], "int16", "high_first", 32767.0),
        ([0x8000], "uint16", "high_first", 32768.0),
        ([0x8000], "int16", "low_first", -32768.0),  # word order is irrelevant for 16-bit
    ],
)
def test_type_matrix(words: list[int], data_type: str, order: str, expected: float) -> None:
    assert _dec(words, data_type, word_order=order) == expected


def test_bit_field_in_the_middle_of_a_word() -> None:
    assert _dec([0b1011_0100_0000_0000], "uint16", count=1, bit_offset=10, bit_length=4) == 0b1101
    assert _dec([0b0000_0000_0000_0100], "uint16", count=1, bit_offset=2, bit_length=1) == 1.0
    assert _dec([0xFFFF], "uint16", count=1, bit_offset=0, bit_length=16) == 65535.0


def test_bit_field_is_unsigned_even_for_int16() -> None:
    # ASSUMPTION: a bit field is the unsigned value of those bits, whatever the base type.
    assert _dec([0x00FF], "int16", count=1, bit_offset=0, bit_length=8) == 255.0


def test_float32_non_finite_is_refused_not_published() -> None:
    with pytest.raises(ValueError, match="finite"):
        _dec([0x7FC0, 0x0000], "float32")
    with pytest.raises(ValueError, match="finite"):
        _dec([0x7F80, 0x0000], "float32")


# ── NULL data_type keeps today's behaviour exactly ───────────────────────────


def test_null_data_type_count1_is_uint16() -> None:
    assert _dec([65191], None, count=1) == 65191.0


def test_null_data_type_count2_is_uint32_high_word_first() -> None:
    assert _dec([1, 2], None, count=2) == float((1 << 16) | 2)


def test_null_data_type_other_counts_take_first_word() -> None:
    assert _dec([7, 8, 9], None, count=3) == 7.0
    assert _dec([7, 8, 9, 10], None, count=4) == 7.0


# ── validation ───────────────────────────────────────────────────────────────


def _ok(**kw: Any) -> None:
    base: dict[str, Any] = {
        "data_type": None, "word_order": "high_first", "bit_offset": None,
        "bit_length": None, "count": 1, "register_type": "holding",
    }
    base.update(kw)
    validate_decoding(**base)


def test_valid_combinations() -> None:
    _ok()
    _ok(data_type="uint16")
    _ok(data_type="int16", bit_offset=0, bit_length=16)
    _ok(data_type="uint16", bit_offset=15, bit_length=1)
    _ok(data_type="float32", count=2, word_order="low_first")
    _ok(data_type="int32", count=2)
    _ok(data_type="uint32", count=2, register_type="input")


@pytest.mark.parametrize(
    "kw",
    [
        {"data_type": "float64"},
        {"data_type": "float32", "count": 1},
        {"data_type": "uint32", "count": 4},
        {"data_type": "int32", "count": 1},
        {"data_type": "uint16", "count": 2},
        {"data_type": "int16", "count": 2},
        {"data_type": "uint16", "word_order": "middle"},
        {"data_type": "uint16", "bit_offset": 0},  # offset without length
        {"data_type": "uint16", "bit_length": 4},  # length without offset
        {"data_type": "uint16", "bit_offset": 16, "bit_length": 1},
        {"data_type": "uint16", "bit_offset": -1, "bit_length": 1},
        {"data_type": "uint16", "bit_offset": 0, "bit_length": 0},
        {"data_type": "uint16", "bit_offset": 0, "bit_length": 17},
        {"data_type": "uint16", "bit_offset": 12, "bit_length": 8},  # runs past bit 15
        {"data_type": "uint32", "count": 2, "bit_offset": 0, "bit_length": 4},
        {"data_type": "float32", "count": 2, "bit_offset": 0, "bit_length": 4},
        {"data_type": None, "bit_offset": 0, "bit_length": 4},
        {"data_type": None, "word_order": "low_first"},
        {"data_type": "uint16", "register_type": "coil"},
        {"data_type": "uint16", "register_type": "discrete"},
    ],
    ids=lambda kw: ",".join(f"{k}={v}" for k, v in kw.items()),
)
def test_invalid_combinations_are_rejected(kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _ok(**kw)


def test_decode_rejects_a_row_that_bypassed_api_validation() -> None:
    with pytest.raises(ValueError):
        _dec([1], "float32", count=1)
    with pytest.raises(ValueError):
        _dec([1], "uint16", count=1, bit_offset=20, bit_length=1)


# ── driver integration ───────────────────────────────────────────────────────


def _block(words: list[int]) -> Any:
    result = MagicMock()
    result.isError.return_value = False
    result.registers = words
    return result


def _driver(registers: list[dict[str, Any]]) -> ModbusDriver:
    return ModbusDriver(
        sensor_id=1, sensor_name="tracker",
        config={"host": "ignored", "slave_id": 1}, registers=registers,
    )


def _reg(rid: int, address: int, count: int, **extra: Any) -> dict[str, Any]:
    return {"id": rid, "name": f"r{rid}", "address": address, "count": count,
            "register_type": "holding", **extra}


async def test_read_registers_decodes_each_register_from_one_block() -> None:
    driver = _driver([
        _reg(1, 6, 2, data_type="float32", word_order="low_first"),
        _reg(2, 13, 1, data_type="uint16", bit_offset=0, bit_length=8),
        _reg(3, 19, 1, data_type="uint16"),
        _reg(4, 5, 1),  # NULL data_type: legacy
    ])
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_block(COMPACT[5:20]))
    readings = {r["register_id"]: r for r in await driver.read_registers()}
    assert math.degrees(readings[1]["raw_value"]) == pytest.approx(-34.5, abs=1e-3)
    assert readings[2]["raw_value"] == 84.0
    assert readings[3]["raw_value"] == 2905.0
    assert readings[4]["raw_value"] == float(COMPACT[5])
    assert driver._client.read_holding_registers.await_count == 1


async def test_formula_still_applies_after_decoding() -> None:
    from app.services.orchestrator import DriverOrchestrator

    orch = DriverOrchestrator()
    seen: list[tuple[float, float]] = []

    async def cb(_sid: int, raw: float, processed: float, _ts: Any, _r: Any = None) -> None:
        seen.append((raw, processed))

    orch.on_processed_value(cb)
    driver = _driver([_reg(1, 10, 1, data_type="int16", data_formula="val / 10")])
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_block([FULL[10]]))
    readings = await driver.read_registers()
    assert readings[0]["raw_value"] == -345.0  # decoded value is the raw_value
    assert readings[0]["formula"] == "val / 10"
    await orch._handle_value_batch(1, readings, __import__("datetime").datetime.now())
    assert seen == [(-345.0, -34.5)]


async def test_bad_decoding_row_fails_the_poll_visibly() -> None:
    from app.drivers.base import ReadError

    driver = _driver([_reg(1, 0, 1, data_type="float32")])  # count 1: invalid row
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_block([1, 2]))
    with pytest.raises(ReadError):
        await driver.read_registers()


async def test_overlapping_registers_over_the_same_words_read_enough_words() -> None:
    """A word decoded two ways (status word + bit field) must not shrink the block."""
    driver = _driver([
        _reg(1, 6, 2, data_type="float32", word_order="low_first"),
        _reg(2, 6, 1, data_type="uint16"),  # same start address, shorter, sorted after
    ])
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_block(COMPACT[6:8]))
    readings = {r["register_id"]: r["raw_value"] for r in await driver.read_registers()}
    assert math.degrees(readings[1]) == pytest.approx(-34.5, abs=1e-3)
    kwargs = driver._client.read_holding_registers.await_args.kwargs
    assert kwargs["count"] == 2
