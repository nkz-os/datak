"""ModbusDriver multi-register read logic (grouping + offset decoding).

These tests pin the core of the multi-register feature: a single Modbus poll
over a contiguous block must be split back into per-register readings with the
correct offset, count decoding, and register_id mapping.

The Modbus client is mocked, so no network / hardware is required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.drivers.modbus import ModbusDriver


def _fake_block(registers: list[int]) -> Any:
    """A fake Modbus read result exposing isError() + .registers."""
    result = MagicMock()
    result.isError.return_value = False
    result.registers = registers
    return result


def _driver(registers: list[dict[str, Any]]) -> ModbusDriver:
    return ModbusDriver(
        sensor_id=1,
        sensor_name="multi",
        config={"host": "ignored", "register_type": "holding", "slave_id": 1},
        registers=registers,
    )


async def test_read_registers_splits_contiguous_block() -> None:
    """Two contiguous holding registers read in one poll must split correctly."""
    driver = _driver(
        [
            {"id": 11, "name": "temp", "address": 0, "count": 1, "register_type": "holding", "twin_attribute": "airTemperature"},
            {"id": 12, "name": "hum", "address": 1, "count": 1, "register_type": "holding", "twin_attribute": "relativeHumidity"},
        ]
    )
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_fake_block([100, 200]))

    readings = await driver.read_registers()

    assert len(readings) == 2
    by_id = {r["register_id"]: r for r in readings}
    assert by_id[11]["raw_value"] == 100.0
    assert by_id[12]["raw_value"] == 200.0
    # Each reading must carry the SDM attribute it will publish northbound.
    assert by_id[11]["twin_attribute"] == "airTemperature"
    assert by_id[12]["twin_attribute"] == "relativeHumidity"
    # A single contiguous block => one Modbus transaction.
    assert driver._client.read_holding_registers.await_count == 1


async def test_read_registers_decodes_32bit_value() -> None:
    """A count=2 register must decode as big-endian 32-bit (high << 16 | low)."""
    driver = _driver(
        [{"id": 21, "name": "power", "address": 0, "count": 2, "register_type": "holding"}]
    )
    driver._client = MagicMock()
    # A count=2 register must decode as big-endian 32-bit (high << 16 | low).
    # Registers [1, 2] => (1 << 16) | 2 = 0x00010002 = 65538.
    driver._client.read_holding_registers = AsyncMock(return_value=_fake_block([1, 2]))

    readings = await driver.read_registers()

    assert len(readings) == 1
    assert readings[0]["raw_value"] == 65538.0


async def test_read_registers_groups_by_type() -> None:
    """Holding and input registers are read in separate transactions."""
    driver = _driver(
        [
            {"id": 31, "name": "h0", "address": 0, "count": 1, "register_type": "holding"},
            {"id": 32, "name": "i0", "address": 5, "count": 1, "register_type": "input"},
        ]
    )
    driver._client = MagicMock()
    driver._client.read_holding_registers = AsyncMock(return_value=_fake_block([7]))
    driver._client.read_input_registers = AsyncMock(return_value=_fake_block([9]))

    readings = await driver.read_registers()

    by_id = {r["register_id"]: r for r in readings}
    assert by_id[31]["raw_value"] == 7.0
    assert by_id[32]["raw_value"] == 9.0
    assert driver._client.read_holding_registers.await_count == 1
    assert driver._client.read_input_registers.await_count == 1
