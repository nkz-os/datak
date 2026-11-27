"""Read-only locking by canonical IP address, with NO name resolution anywhere.

Configured endpoints are IP literals. A bus whose host is not an IP literal is
locked as soon as anything is read-only (fail closed): a name can point
anywhere, now or later, and DNS can stall the event loop.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.drivers import modbus_bus
from app.drivers.base import WriteError
from app.drivers.modbus_bus import BusScheduler, BusSettings, literal_address

PORT = 5020
LOOPBACK_SPELLINGS = [
    "127.0.0.1", "127.1", "2130706433", "0x7f000001", "0177.0.0.1", "0177.0.0.01",
    "::ffff:127.0.0.1", "[::ffff:127.0.0.1]", "::ffff:7f00:1",
]


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    previous = modbus_bus.current_settings()
    modbus_bus._reset_read_only_latch()
    yield
    modbus_bus._reset_read_only_latch()
    modbus_bus.configure(previous)


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Nothing in this feature may resolve a name: any such call fails the test."""
    calls: list[str] = []

    def boom(*args: Any, **_kw: Any) -> Any:
        calls.append(repr(args))
        raise AssertionError(f"name resolution attempted: {args!r}")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        monkeypatch.setattr(socket, name, boom)
    yield calls
    assert calls == []


def _client() -> AsyncModbusTcpClient:
    client = AsyncModbusTcpClient("localhost", port=1, retries=0)
    client.ctx = MagicMock()
    client.execute = MagicMock()
    return client


def _bus(host: str, endpoints: tuple[str, ...], port: int = PORT) -> BusScheduler:
    settings = BusSettings(read_only_endpoints=endpoints)
    modbus_bus.configure(settings)
    return BusScheduler(f"tcp://{host}:{port}", _client(), settings)


# ── canonical literal forms, no DNS ──────────────────────────────────────────


@pytest.mark.parametrize("host", LOOPBACK_SPELLINGS)
def test_loopback_spellings_canonicalise_to_one_ipv4(host: str) -> None:
    assert literal_address(host) == "127.0.0.1"


@pytest.mark.parametrize(
    "host,expected",
    [("::1", "::1"), ("[::1]", "::1"), ("0:0:0:0:0:0:0:1", "::1"), ("[0::1]", "::1"),
     ("2001:DB8::1", "2001:db8::1")],
)
def test_ipv6_literals_canonicalise(host: str, expected: str) -> None:
    assert literal_address(host) == expected


@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST", "localhost.", "plc.example", "127.0.0.1.", "local host", "127.0.0.1 x",
     "", " ", "10.0.0.256", "10.0.0.1.5", "::1::2", "[::1", "127.0.0.1\n", "fe80::1%eth0x y"],
)
def test_non_literals_are_none(host: str) -> None:
    assert literal_address(host) is None


# ── configuration: IP literals only ──────────────────────────────────────────


def test_configured_endpoints_are_canonicalised() -> None:
    s = BusSettings(read_only_endpoints=("127.1:502", "0x7f000002:0503", "[::1]:504", "::ffff:10.0.0.1:505"))
    assert s.read_only_endpoints == ("127.0.0.1:502", "127.0.0.2:503", "[::1]:504", "10.0.0.1:505")
    assert s.read_only_addresses == {
        ("127.0.0.1", 502), ("127.0.0.2", 503), ("::1", 504), ("10.0.0.1", 505)
    }


def test_ipv6_endpoint_with_or_without_brackets() -> None:
    assert BusSettings(read_only_endpoints=("::1:502",)).read_only_addresses == {("::1", 502)}
    assert BusSettings(read_only_endpoints=("[::1]:502",)).read_only_addresses == {("::1", 502)}


@pytest.mark.parametrize(
    "bad",
    ["localhost:502", "plc.example:502", "LOCALHOST:502", "localhost.:502", "127.0.0.1.:502",
     "127.0.0.1", "[::1]", "127.0.0.1:0", "127.0.0.1:70000", "127.0.0.1:x", "local host:502",
     ":502", "", "[::1:502", "host:502:3"],
)
def test_names_and_malformed_endpoints_fail_startup(bad: str) -> None:
    with pytest.raises(ValueError):
        BusSettings(read_only_endpoints=(bad,))


def test_a_name_in_config_explains_itself() -> None:
    with pytest.raises(ValueError, match="IP literal"):
        BusSettings(read_only_endpoints=("localhost:502",))


# ── locking by address ───────────────────────────────────────────────────────


@pytest.mark.parametrize("configured", ["127.0.0.1", "127.1", "0x7f000001", "::ffff:127.0.0.1"])
@pytest.mark.parametrize("sensor_host", LOOPBACK_SPELLINGS)
async def test_every_literal_spelling_of_a_configured_ip_is_locked(configured: str, sensor_host: str) -> None:
    endpoint = f"[{configured}]:{PORT}" if ":" in configured else f"{configured}:{PORT}"
    assert _bus(sensor_host, (endpoint,)).read_only is True


async def test_ipv6_loopback_literal_forms() -> None:
    for host in ("::1", "[::1]", "0:0:0:0:0:0:0:1"):
        assert _bus(host, (f"[::1]:{PORT}",), PORT).read_only is True


async def test_different_port_or_address_is_not_locked() -> None:
    assert _bus("127.0.0.1", (f"127.0.0.1:{PORT + 1}",)).read_only is False
    assert _bus("192.0.2.1", (f"127.0.0.1:{PORT}",)).read_only is False
    assert _bus("::1", (f"127.0.0.1:{PORT}",)).read_only is False


@pytest.mark.parametrize("name", ["localhost", "LOCALHOST", "localhost.", "plc.example", "local host", "127.0.0.1."])
async def test_a_bus_host_that_is_not_a_literal_is_locked_when_an_endpoint_is_configured(name: str) -> None:
    assert _bus(name, (f"127.0.0.1:{PORT}",)).read_only is True


async def test_names_keep_working_when_nothing_is_read_only() -> None:
    assert _bus("localhost", ()).read_only is False
    assert _bus("plc.example", ()).read_only is False


async def test_a_name_never_resolves_to_a_different_decision_over_time() -> None:
    """DNS is irrelevant: the decision for a name is the same whatever it points to."""
    bus = _bus("plc.example", (f"127.0.0.1:{PORT}",))
    assert bus.read_only is True
    with pytest.raises(WriteError):
        _ = bus.client.write_register


# ── latch ────────────────────────────────────────────────────────────────────


async def test_latch_by_a_literal_locks_every_literal_spelling_of_it() -> None:
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://127.1:{PORT}", "sensor")
    for host in LOOPBACK_SPELLINGS:
        bus = BusScheduler(f"tcp://{host}:{PORT}", _client(), BusSettings())
        assert bus.read_only is True, host
        with pytest.raises(WriteError):
            _ = bus.client.write_register
    assert BusScheduler(f"tcp://127.0.0.1:{PORT + 1}", _client(), BusSettings()).read_only is False


async def test_latch_locks_every_non_literal_host() -> None:
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://127.0.0.1:{PORT}", "sensor")
    assert BusScheduler(f"tcp://localhost:{PORT + 9}", _client(), BusSettings()).read_only is True


async def test_latch_by_a_name_locks_every_tcp_bus_literals_included() -> None:
    """We cannot know which address the name means: fail closed for all TCP buses."""
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://ncu.local:{PORT}", "sensor")
    assert BusScheduler(f"tcp://other.example:{PORT + 1}", _client(), BusSettings()).read_only is True
    bus = BusScheduler(f"tcp://10.1.2.3:{PORT}", _client(), BusSettings())
    assert bus.read_only is True
    with pytest.raises(WriteError):
        _ = bus.client.write_register


async def test_latch_by_a_literal_does_not_lock_unrelated_literals() -> None:
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://127.0.0.1:{PORT}", "sensor")
    assert BusScheduler(f"tcp://10.1.2.3:{PORT}", _client(), BusSettings()).read_only is False


async def test_rtu_bus_is_not_touched_by_a_name_latch() -> None:
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://ncu.local:{PORT}", "sensor")
    assert BusScheduler("rtu:///dev/ttyS9", _client(), BusSettings()).read_only is False


async def test_flagged_sensor_on_a_name_locks_an_unflagged_sensor_on_an_ip() -> None:
    from app.drivers.modbus import ModbusDriver

    modbus_bus.configure(BusSettings())
    flagged = ModbusDriver(
        sensor_id=1, sensor_name="a",
        config={"mode": "tcp", "host": "ncu.local", "port": PORT, "read_only": True},
    )
    plain = ModbusDriver(
        sensor_id=2, sensor_name="b", config={"mode": "tcp", "host": "10.1.2.3", "port": PORT},
    )
    await flagged.connect()
    await plain.connect()
    try:
        plain._client.write_register = MagicMock()  # type: ignore[union-attr]
        with pytest.raises(WriteError):
            await plain.write(1.0)
    finally:
        await plain.disconnect()
        await flagged.disconnect()


# ── IPv6 scope ids are not part of the address ───────────────────────────────


@pytest.mark.parametrize("host", ["fe80::1%lo", "fe80::1%eth0", "fe80::1%1", "[fe80::1%lo]", "FE80::1"])
def test_scope_id_is_dropped(host: str) -> None:
    assert literal_address(host) == "fe80::1"


@pytest.mark.parametrize("host", ["fe80::1%", "fe80::1%a b", "127.0.0.1%lo", "%lo"])
def test_malformed_scope_is_not_a_literal(host: str) -> None:
    assert literal_address(host) is None


async def test_config_scope_ids_and_keys_agree_both_directions() -> None:
    s = BusSettings(read_only_endpoints=("[fe80::1%lo]:502",))
    assert s.read_only_addresses == {("fe80::1", 502)}
    for host in ("fe80::1", "fe80::1%eth0", "fe80::1%1", "[fe80::1%lo]"):
        modbus_bus.configure(s)
        assert BusScheduler(f"tcp://{host}:502", _client(), s).read_only is True, host
    s2 = BusSettings(read_only_endpoints=("[fe80::1]:502",))
    modbus_bus.configure(s2)
    assert BusScheduler("tcp://fe80::1%eth0:502", _client(), s2).read_only is True


async def test_alias_bus_created_after_release_is_still_locked() -> None:
    modbus_bus.configure(BusSettings())
    bus = modbus_bus.get_or_create_bus(f"tcp://0x7f000001:{PORT}", _client, read_only=True)
    assert bus.read_only
    await modbus_bus.release_bus(f"tcp://0x7f000001:{PORT}", 1)
    fresh = modbus_bus.get_or_create_bus(f"tcp://127.0.0.1:{PORT}", _client)
    assert fresh.read_only is True
    await modbus_bus.release_bus(f"tcp://127.0.0.1:{PORT}", 2)


# ── row check derives the key exactly as the driver does ─────────────────────


@pytest.mark.parametrize(
    "protocol,params",
    [
        ("MODBUS_TCP", {"host": "127.1", "port": PORT}),
        ("MODBUS_TCP", {"host": "0x7f000001", "port": PORT, "mode": "rtu"}),  # orchestrator forces tcp
        ("MODBUS_TCP", {"host": "localhost", "port": PORT}),  # name: fail closed
        ("MODBUS_TCP", {"host": "[::ffff:127.0.0.1]", "port": PORT}),
    ],
)
async def test_row_refuses_writes_for_every_driver_equivalent_key(protocol: str, params: dict[str, Any]) -> None:
    modbus_bus.configure(BusSettings(read_only_endpoints=(f"127.0.0.1:{PORT}",)))
    assert modbus_bus.row_refuses_writes(protocol, params) is not None


async def test_row_key_matches_the_driver_key_including_defaults() -> None:
    from app.drivers.modbus import ModbusDriver

    for params in ({"host": "10.1.2.3", "port": 1502}, {"host": "10.1.2.3"}, {}, {"port": "503"}):
        config = {**params, "mode": "tcp"}  # what the orchestrator hands the driver
        driver = ModbusDriver(sensor_id=1, sensor_name="x", config=config)
        assert modbus_bus.bus_key_for("tcp", params) == driver._bus_spec()[0]
    config = {"port": "/dev/ttyS1", "mode": "rtu"}
    assert modbus_bus.bus_key_for("rtu", config) == ModbusDriver(1, "x", config)._bus_spec()[0]


# ── client internals are not reachable as plain attributes ───────────────────


async def test_wrapped_client_and_ctx_are_not_plain_attributes() -> None:
    guard = _bus("127.0.0.1", (f"127.0.0.1:{PORT}",)).client
    assert not hasattr(guard, "_client")
    assert not hasattr(guard, "_is_locked")
    assert not hasattr(guard.ctx, "_ctx")


def test_the_feature_has_no_resolution_api_left() -> None:
    for gone in ("resolve_addresses", "refresh_key_addresses", "_key_addresses"):
        assert not hasattr(modbus_bus, gone), gone
    assert not hasattr(BusSettings(), "read_only_unresolved")


async def test_driver_write_refusal_and_row_check_never_resolve_names() -> None:
    """Whole decision path under the no-DNS fixture (it fails the test on any lookup)."""
    from app.drivers.modbus import ModbusDriver

    modbus_bus.configure(BusSettings(read_only_endpoints=(f"127.0.0.1:{PORT}",)))
    for host in ("localhost", "127.1", "plc.example", "::ffff:127.0.0.1"):
        driver = ModbusDriver(
            sensor_id=1, sensor_name="x",
            config={"mode": "tcp", "host": host, "port": PORT, "register_type": "holding"},
        )
        driver._client = MagicMock()
        with pytest.raises(WriteError):
            await driver.write(1.0)
        assert modbus_bus.row_refuses_writes("MODBUS_TCP", {"host": host, "port": PORT}) is not None
