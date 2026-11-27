"""Read-only Modbus endpoints: the client itself must be unable to write.

Two independent layers lock a bus: the gateway setting
``modbus.read_only_endpoints`` and a per-sensor ``connection_params.read_only``.
Once locked, the pymodbus client of that bus is reached only through an
allow-list proxy, so that even a driver bug cannot put a write on the wire.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pymodbus.pdu as pdu_pkg
import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.pdu import ModbusPDU

from app.drivers import modbus_bus
from app.drivers.base import WriteError
from app.drivers.modbus import ModbusDriver
from app.drivers.modbus_bus import (
    BusScheduler,
    BusSettings,
    ReadGuardedClient,
    read_only_requested,
)

ENDPOINT = "192.0.2.10:502"
KEY = "tcp://192.0.2.10:502"
ALLOWED = {"read_coils", "read_discrete_inputs", "read_holding_registers", "read_input_registers"}


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    previous = modbus_bus.current_settings()
    modbus_bus._reset_read_only_latch()
    yield
    modbus_bus._reset_read_only_latch()
    modbus_bus.configure(previous)


def _real_client() -> AsyncModbusTcpClient:
    """A real pymodbus client whose transport layer is a spy (no I/O possible)."""
    client = AsyncModbusTcpClient("localhost", port=1, retries=0)
    client.ctx = MagicMock()
    client.ctx.comm_params.timeout_connect = 0.25
    client.execute = MagicMock(name="client.execute")
    return client


def _locked_proxy(client: Any) -> ReadGuardedClient:
    return ReadGuardedClient(client, KEY, is_locked=lambda: True)


# ── enumerate every public callable of the installed client ──────────────────


def _public_callables(client: Any) -> list[str]:
    return sorted(
        n
        for n in dir(client)
        if not n.startswith("_") and callable(getattr(type(client), n, None))
    )


async def test_every_public_client_callable_outside_the_allowlist_is_refused() -> None:
    client = _real_client()
    proxy = _locked_proxy(client)
    names = _public_callables(client)
    # sanity: the enumeration really sees pymodbus 3.15's write methods
    for write_name in (
        "write_register", "write_registers", "write_coil", "write_coils",
        "mask_write_register", "readwrite_registers", "write_file_record", "execute",
    ):
        assert write_name in names
    refused = [n for n in names if n not in ALLOWED | {"connect", "close", "execute"}]
    assert refused  # not vacuous
    for name in refused:
        with pytest.raises(WriteError):
            getattr(proxy, name)
    # nothing leaked to the real client
    client.execute.assert_not_called()
    client.ctx.execute.assert_not_called()


@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.write_register(address=0, value=1, device_id=1),
        lambda p: p.write_registers(address=0, values=[1], device_id=1),
        lambda p: p.write_coil(address=0, value=True, device_id=1),
        lambda p: p.write_coils(address=0, values=[True], device_id=1),
        lambda p: p.mask_write_register(address=0, and_mask=0, or_mask=1, device_id=1),
        lambda p: p.readwrite_registers(read_address=0, read_count=1, address=0, values=[1], device_id=1),
        lambda p: p.write_file_record(records=[], device_id=1),
        lambda p: p.diag_restart_communication(False, device_id=1),
        lambda p: p.diag_force_listen_only(device_id=1),
        lambda p: p.diag_clear_counters(device_id=1),
    ],
    ids=range(10),
)
async def test_calling_a_write_method_is_refused_before_any_io(call: Any) -> None:
    client = _real_client()
    proxy = _locked_proxy(client)
    with pytest.raises(WriteError):
        await call(proxy)
    client.execute.assert_not_called()
    client.ctx.execute.assert_not_called()


def _all_pdu_classes() -> list[type[ModbusPDU]]:
    found: dict[str, type[ModbusPDU]] = {}
    for info in pkgutil.walk_packages(pdu_pkg.__path__, "pymodbus.pdu."):
        mod = importlib.import_module(info.name)
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, ModbusPDU) and cls.__module__ == mod.__name__:
                found[name] = cls
    return [found[k] for k in sorted(found)]


@pytest.mark.parametrize("cls", _all_pdu_classes(), ids=lambda c: c.__name__)
async def test_execute_passes_only_read_function_codes_1_to_4(cls: type[ModbusPDU]) -> None:
    client = _real_client()
    sentinel = object()
    client.execute.return_value = sentinel
    proxy = _locked_proxy(client)
    request = cls.__new__(cls)  # no constructor args needed; only function_code matters
    if cls.function_code in (1, 2, 3, 4):
        assert proxy.execute(False, request) is sentinel
        client.execute.assert_called_once()
    else:
        with pytest.raises(WriteError):
            proxy.execute(False, request)
        client.execute.assert_not_called()


async def test_execute_refuses_objects_without_a_function_code() -> None:
    client = _real_client()
    proxy = _locked_proxy(client)
    with pytest.raises(WriteError):
        proxy.execute(False, object())  # type: ignore[arg-type]
    client.execute.assert_not_called()


async def test_ctx_does_not_leak_a_write_path() -> None:
    """ModbusBaseClient.execute delegates to ctx.execute: the view must not expose it."""
    client = _real_client()
    proxy = _locked_proxy(client)
    with pytest.raises(WriteError):
        _ = proxy.ctx.execute
    with pytest.raises(WriteError):
        _ = proxy.ctx.transport
    # what the scheduler needs still works
    modbus_bus.assert_supported_client(proxy)
    modbus_bus.set_response_timeout(proxy, 0.5)
    assert client.ctx.comm_params.timeout_connect == 0.5


async def test_reads_and_lifecycle_pass_through_when_locked() -> None:
    client = _real_client()
    client.read_holding_registers = AsyncMock(return_value="ok")
    client.read_input_registers = AsyncMock(return_value="ok")
    client.read_coils = AsyncMock(return_value="ok")
    client.read_discrete_inputs = AsyncMock(return_value="ok")
    client.connect = AsyncMock(return_value=True)
    client.close = MagicMock()
    proxy = _locked_proxy(client)
    for name in sorted(ALLOWED):
        assert await getattr(proxy, name)(address=0, count=1, device_id=1) == "ok"
    assert await proxy.connect() is True
    proxy.close()
    client.close.assert_called_once()
    assert proxy.connected is True  # real property over the spied ctx.is_active()


async def test_unlocked_proxy_forwards_everything() -> None:
    client = _real_client()
    client.write_register = AsyncMock(return_value="written")
    proxy = ReadGuardedClient(client, KEY, is_locked=lambda: False)
    assert await proxy.write_register(address=0, value=1, device_id=1) == "written"
    assert proxy.ctx is client.ctx


async def test_refusal_is_logged_once_per_method_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    proxy = _locked_proxy(_real_client())
    for _ in range(3):
        with pytest.raises(WriteError):
            proxy.write_register(address=0, value=4242, device_id=1)
    out = caplog.text
    assert out.count("Write refused") == 1
    assert "4242" not in out
    assert KEY in out


# ── bus locking: endpoint setting + per-sensor latch ─────────────────────────


def _bus(key: str = KEY, endpoints: tuple[str, ...] = ()) -> BusScheduler:
    settings = BusSettings(read_only_endpoints=endpoints)
    modbus_bus.configure(settings)
    return BusScheduler(key, _real_client(), settings)


async def test_bus_is_always_wrapped_and_unlocked_by_default() -> None:
    bus = _bus()
    assert isinstance(bus.client, ReadGuardedClient)
    assert bus.read_only is False


async def test_bus_locked_by_configured_endpoint() -> None:
    bus = _bus(endpoints=(ENDPOINT,))
    assert bus.read_only is True
    with pytest.raises(WriteError):
        _ = bus.client.write_register


async def test_endpoint_matching_normalises_space_port_and_numeric_forms() -> None:
    bus = _bus(key="tcp://0xc000020a:0502", endpoints=("  192.0.2.10:502 ",))
    assert bus.read_only is True


async def test_other_endpoint_is_not_locked() -> None:
    # (resolvable hosts: an unresolvable bus host fails closed, see test_read_only_addresses)
    bus = _bus(key="tcp://127.0.0.1:503", endpoints=("127.0.0.1:502",))
    assert bus.read_only is False


async def test_rtu_bus_is_not_matched_by_a_tcp_endpoint() -> None:
    bus = _bus(key="rtu://192.0.2.10:502", endpoints=(ENDPOINT,))
    assert bus.read_only is False


async def test_malformed_endpoint_in_settings_is_rejected_loudly() -> None:
    for bad in ("192.0.2.10", "192.0.2.10:x", ":502", "192.0.2.10:70000", "", "plc.example:502"):
        with pytest.raises(ValueError):
            BusSettings(read_only_endpoints=(bad,))


async def test_latch_locks_the_bus_for_every_holder_of_the_client() -> None:
    """A sensor that cached bus.client before the lock must still be blocked."""
    bus = _bus()
    cached = bus.client
    _ = cached.write_register
    modbus_bus.lock_bus(KEY, "sensor 7")
    assert bus.read_only is True
    with pytest.raises(WriteError):
        _ = cached.write_register


async def test_latch_outlives_the_bus_object() -> None:
    """Sensor update = remove + add; the bus is destroyed in between."""
    modbus_bus.configure(BusSettings())
    bus = modbus_bus.get_or_create_bus(KEY, _real_client, read_only=True)
    assert bus.read_only
    await modbus_bus.release_bus(KEY, 1)  # last sensor leaves -> bus closed
    assert modbus_bus.get_bus(KEY) is None
    fresh = modbus_bus.get_or_create_bus(KEY, _real_client)  # a writable sensor joins
    assert fresh.read_only is True
    with pytest.raises(WriteError):
        _ = fresh.client.write_coil
    await modbus_bus.release_bus(KEY, 2)


async def test_get_or_create_bus_locks_an_existing_unlocked_bus() -> None:
    modbus_bus.configure(BusSettings())
    first = modbus_bus.get_or_create_bus(KEY, _real_client)
    cached = first.client
    again = modbus_bus.get_or_create_bus(KEY, _real_client, read_only=True)
    assert again is first
    with pytest.raises(WriteError):
        _ = cached.write_register
    await modbus_bus.release_bus(KEY, 1)


# ── per-sensor flag parsing: fail towards read-only ──────────────────────────


@pytest.mark.parametrize("params", [{}, {"read_only": None}, {"read_only": False}, {"read_only": 0}])
async def test_read_only_requested_false_only_for_absent_or_explicit_false(params: dict[str, Any]) -> None:
    assert read_only_requested(params) is False


@pytest.mark.parametrize(
    "value", [True, 1, "true", "True", "false", "no", "0", "", "yes", [1], {"a": 1}]
)
async def test_read_only_requested_anything_else_locks(value: Any) -> None:
    assert read_only_requested({"read_only": value}) is True


# ── driver guard ─────────────────────────────────────────────────────────────


def _driver(config: dict[str, Any] | None = None) -> ModbusDriver:
    cfg = {"mode": "tcp", "host": "192.0.2.10", "port": 502, "slave_id": 1,
           "register_type": "holding", "address": 5}
    cfg.update(config or {})
    return ModbusDriver(sensor_id=7, sensor_name="tracker", config=cfg)


async def test_driver_write_refused_for_read_only_sensor_before_any_io() -> None:
    """Unwrapped mock client + no bus: only the driver guard can stop this."""
    driver = _driver({"read_only": True})
    driver._client = MagicMock()
    driver._client.write_register = AsyncMock()
    with pytest.raises(WriteError, match="read-only"):
        await driver.write(1.0)
    driver._client.write_register.assert_not_called()


async def test_driver_write_refused_when_sensor_misconfigured_as_string() -> None:
    driver = _driver({"read_only": "false"})
    driver._client = MagicMock()
    driver._client.write_register = AsyncMock()
    with pytest.raises(WriteError):
        await driver.write(1.0)
    driver._client.write_register.assert_not_called()


async def test_driver_write_refused_when_endpoint_configured_read_only() -> None:
    modbus_bus.configure(BusSettings(read_only_endpoints=(ENDPOINT,)))
    driver = _driver()  # sensor row says nothing: the gateway setting decides
    driver._client = MagicMock()
    driver._client.write_register = AsyncMock()
    with pytest.raises(WriteError, match="read-only"):
        await driver.write(1.0)
    driver._client.write_register.assert_not_called()


async def test_driver_write_refused_when_a_neighbour_locked_the_bus() -> None:
    modbus_bus.configure(BusSettings())
    writer = _driver()  # not flagged itself
    await writer.connect()
    try:
        writer._client.write_register = AsyncMock()  # type: ignore[union-attr]
        ro = ModbusDriver(
            sensor_id=8, sensor_name="ro",
            config={"mode": "tcp", "host": "192.0.2.10", "port": 502, "read_only": True},
        )
        await ro.connect()
        with pytest.raises(WriteError, match="read-only"):
            await writer.write(1.0)
        await ro.disconnect()
    finally:
        await writer.disconnect()


async def test_driver_write_still_works_on_a_writable_bus() -> None:
    modbus_bus.configure(BusSettings())
    driver = _driver()
    driver._client = MagicMock()
    result = MagicMock()
    result.isError.return_value = False
    driver._client.write_register = AsyncMock(return_value=result)
    assert await driver.write(3.0) is True
    driver._client.write_register.assert_awaited_once()


async def test_driver_refusal_logged_once_at_warning_with_sensor_and_endpoint_not_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    driver = _driver({"read_only": True})
    driver._client = MagicMock()
    for _ in range(3):
        with pytest.raises(WriteError):
            await driver.write(98765.0)
    out = caplog.text
    assert out.count("Write refused") == 1
    assert "98765" not in out
    assert "192.0.2.10:502" in out
    assert "sensor_id" in out and "7" in out
