"""The configured log level must reach the stdlib root logger (info was dropped)."""

import logging

from app.main import configure_stdlib_logging


def test_info_level_is_applied() -> None:
    assert configure_stdlib_logging("INFO") == logging.INFO
    assert logging.getLogger().getEffectiveLevel() == logging.INFO
    assert logging.getLogger("app.drivers.modbus_bus").isEnabledFor(logging.INFO)


def test_third_party_stays_at_warning() -> None:
    configure_stdlib_logging("DEBUG")
    assert logging.getLogger("pymodbus").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("app").isEnabledFor(logging.DEBUG)


def test_unknown_level_falls_back_to_info() -> None:
    assert configure_stdlib_logging("verbose") == logging.INFO
