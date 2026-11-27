"""Decoding of Modbus register words into a number (pure functions, no I/O).

``data_type`` NULL keeps the historical behaviour exactly: count 1 -> unsigned
16-bit, count 2 -> unsigned 32-bit with the HIGH word first, any other count ->
the first word. Typed registers use ``data_type`` / ``word_order`` and may
extract a bit field from a 16-bit word.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence

DATA_TYPES_16 = ("uint16", "int16")
DATA_TYPES_32 = ("uint32", "int32", "float32")
DATA_TYPES = DATA_TYPES_16 + DATA_TYPES_32
WORD_ORDERS = ("high_first", "low_first")
_WORD_TYPES = ("holding", "input")


class NonFiniteValueError(ValueError):
    """A float32 decoded to NaN or infinity."""


def validate_decoding(
    *,
    data_type: str | None,
    word_order: str | None,
    bit_offset: int | None,
    bit_length: int | None,
    count: int,
    register_type: str = "holding",
) -> None:
    """Raise ValueError unless the combination is decodable."""
    order = word_order or "high_first"
    if order not in WORD_ORDERS:
        raise ValueError(f"word_order must be one of {WORD_ORDERS}, got {word_order!r}")
    has_bits = bit_offset is not None or bit_length is not None

    if data_type is None:
        if order != "high_first":
            raise ValueError("word_order requires a data_type")
        if has_bits:
            raise ValueError("bit_offset/bit_length require a 16-bit data_type")
        return

    if data_type not in DATA_TYPES:
        raise ValueError(f"data_type must be one of {DATA_TYPES}, got {data_type!r}")
    if register_type not in _WORD_TYPES:
        raise ValueError(f"data_type is only valid for holding/input registers, not {register_type!r}")

    if data_type in DATA_TYPES_32:
        if count != 2:
            raise ValueError(f"{data_type} requires count 2, got {count}")
        if has_bits:
            raise ValueError("bit fields are only valid with uint16/int16")
        return

    # ASSUMPTION: 16-bit types require count 1 (the brief fixes this for bit
    # fields only; a 16-bit value spanning several words has no meaning).
    if count != 1:
        raise ValueError(f"{data_type} requires count 1, got {count}")
    if has_bits:
        if bit_offset is None or bit_length is None:
            raise ValueError("bit_offset and bit_length must be given together")
        if not 0 <= bit_offset <= 15:
            raise ValueError(f"bit_offset must be 0..15, got {bit_offset}")
        if not 1 <= bit_length <= 16:
            raise ValueError(f"bit_length must be 1..16, got {bit_length}")
        if bit_offset + bit_length > 16:
            raise ValueError(
                f"bit field {bit_offset}+{bit_length} runs past bit 15 of a 16-bit word"
            )


def decode_words(
    words: Sequence[int],
    *,
    data_type: str | None,
    word_order: str | None,
    bit_offset: int | None,
    bit_length: int | None,
    count: int,
) -> float:
    """Decode the ``count`` words at the start of ``words`` (bit 0 = least significant).

    Raises ValueError for an undecodable row or a non-finite float32.
    """
    validate_decoding(
        data_type=data_type, word_order=word_order, bit_offset=bit_offset,
        bit_length=bit_length, count=count,
    )
    if len(words) < max(count, 1):
        raise ValueError(f"need {max(count, 1)} word(s), got {len(words)}")

    if data_type is None:  # legacy
        if count == 2:
            return float((words[0] << 16) | words[1])
        return float(words[0])

    if data_type in DATA_TYPES_16:
        word = words[0] & 0xFFFF
        if bit_offset is not None and bit_length is not None:
            # ASSUMPTION: bit fields are unsigned, whatever the base type.
            return float((word >> bit_offset) & ((1 << bit_length) - 1))
        if data_type == "int16" and word >= 0x8000:
            return float(word - 0x10000)
        return float(word)

    first, second = (words[0], words[1]) if (word_order or "high_first") == "high_first" else (words[1], words[0])
    raw = struct.pack(">HH", first & 0xFFFF, second & 0xFFFF)
    if data_type == "uint32":
        return float(struct.unpack(">I", raw)[0])
    if data_type == "int32":
        return float(struct.unpack(">i", raw)[0])
    value = float(struct.unpack(">f", raw)[0])
    if not math.isfinite(value):
        raise NonFiniteValueError("float32 value is not finite (NaN/inf)")
    return value
