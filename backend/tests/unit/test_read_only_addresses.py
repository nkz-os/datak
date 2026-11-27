"""Read-only locking is by resolved ADDRESS, not by the spelling of the host."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.drivers import modbus_bus
from app.drivers.base import WriteError
from app.drivers.modbus_bus import BusScheduler, BusSettings, resolve_addresses

PORT = 5020
UNRESOLVABLE = "no-such-host.invalid"


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    previous = modbus_bus.current_settings()
    modbus_bus._reset_read_only_latch()
    yield
    modbus_bus._reset_read_only_latch()
    modbus_bus.configure(previous)


def _client() -> AsyncModbusTcpClient:
    client = AsyncModbusTcpClient("localhost", port=1, retries=0)
    client.ctx = MagicMock()
    client.execute = MagicMock()
    return client


def _locked(host: str, endpoints: tuple[str, ...], port: int = PORT) -> bool:
    settings = BusSettings(read_only_endpoints=endpoints)
    modbus_bus.configure(settings)
    return BusScheduler(f"tcp://{host}:{port}", _client(), settings).read_only


# ── resolution ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "LOCALHOST", "localhost.", "127.1", "2130706433",
     "0x7f000001", "0177.0.0.1", "[::1]", "::1"],
)
def test_resolve_gives_canonical_ip_port_pairs(host: str) -> None:
    got = resolve_addresses(host, PORT)
    assert got is not None
    assert ("127.0.0.1", PORT) in got or ("::1", PORT) in got
    assert all(isinstance(ip, str) for ip, _ in got)


def test_ipv4_mapped_ipv6_is_canonicalised_to_ipv4() -> None:
    assert resolve_addresses("::ffff:127.0.0.1", PORT) == {("127.0.0.1", PORT)}


@pytest.mark.parametrize("host", [UNRESOLVABLE, "local host", ""])
def test_unresolvable_host_gives_none(host: str) -> None:
    assert resolve_addresses(host, PORT) is None


# ── the alias matrix ─────────────────────────────────────────────────────────

ALIASES = ["127.0.0.1", "localhost", "LOCALHOST", "localhost.", "127.1", "2130706433",
           "0x7f000001", "0177.0.0.1"]


@pytest.mark.parametrize("configured", ["127.0.0.1", "localhost", "127.1", "LocalHost."])
@pytest.mark.parametrize("sensor_host", ALIASES)
async def test_every_spelling_of_a_configured_endpoint_is_locked(
    configured: str, sensor_host: str
) -> None:
    assert _locked(sensor_host, (f"{configured}:{PORT}",)) is True


async def test_ipv6_loopback_literal_forms() -> None:
    assert _locked("::1", (f"[::1]:{PORT}",)) is True
    assert _locked("[::1]", (f"[::1]:{PORT}",)) is True
    assert _locked("::1", (f"localhost:{PORT}",)) is True  # localhost also resolves to ::1


async def test_different_port_is_not_locked() -> None:
    assert _locked("127.0.0.1", (f"127.0.0.1:{PORT + 1}",)) is False


async def test_unrelated_resolvable_host_is_not_locked() -> None:
    assert _locked("192.0.2.1", (f"127.0.0.1:{PORT}",)) is False


async def test_unresolvable_bus_host_fails_closed_when_endpoints_are_configured() -> None:
    assert _locked(UNRESOLVABLE, (f"127.0.0.1:{PORT}",)) is True
    assert _locked("local host", (f"127.0.0.1:{PORT}",)) is True


async def test_unresolvable_bus_host_is_not_locked_when_nothing_is_read_only() -> None:
    assert _locked(UNRESOLVABLE, ()) is False


async def test_unresolvable_bus_host_fails_closed_when_a_latch_exists() -> None:
    modbus_bus.lock_bus(f"tcp://127.0.0.1:{PORT}", "test")
    assert _locked(UNRESOLVABLE, ()) is True


async def test_unresolvable_configured_endpoint_still_locks_its_own_spelling() -> None:
    assert _locked(UNRESOLVABLE, (f"{UNRESOLVABLE}:{PORT}",)) is True


def test_config_accepts_bracketed_ipv6_and_rejects_ambiguous_forms() -> None:
    assert BusSettings(read_only_endpoints=("[::1]:502",)).read_only_endpoints == ("[::1]:502",)
    for bad in ("::1:502", "[::1]", "local host:502", "[::1:502", "host:502:3"):
        with pytest.raises(ValueError):
            BusSettings(read_only_endpoints=(bad,))


def test_configured_addresses_are_resolved_at_configure_time() -> None:
    s = BusSettings(read_only_endpoints=("localhost:502",))
    assert ("127.0.0.1", 502) in s.read_only_addresses


# ── latch is by address too ──────────────────────────────────────────────────


async def test_latch_by_one_spelling_locks_every_other_spelling() -> None:
    modbus_bus.configure(BusSettings())
    modbus_bus.lock_bus(f"tcp://localhost:{PORT}", "sensor")
    for host in ALIASES:
        bus = BusScheduler(f"tcp://{host}:{PORT}", _client(), BusSettings())
        assert bus.read_only is True, host
        with pytest.raises(WriteError):
            _ = bus.client.write_register
    other_port = BusScheduler(f"tcp://127.0.0.1:{PORT + 1}", _client(), BusSettings())
    assert other_port.read_only is False


async def test_alias_bus_created_after_release_is_still_locked() -> None:
    modbus_bus.configure(BusSettings())
    bus = modbus_bus.get_or_create_bus(f"tcp://localhost:{PORT}", _client, read_only=True)
    assert bus.read_only
    await modbus_bus.release_bus(f"tcp://localhost:{PORT}", 1)
    fresh = modbus_bus.get_or_create_bus(f"tcp://127.0.0.1:{PORT}", _client)
    assert fresh.read_only is True
    await modbus_bus.release_bus(f"tcp://127.0.0.1:{PORT}", 2)


async def test_resolution_is_not_repeated_on_every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = BusSettings(read_only_endpoints=(f"127.0.0.1:{PORT}",))
    modbus_bus.configure(settings)
    bus = BusScheduler(f"tcp://localhost:{PORT}", _client(), settings)
    calls: list[Any] = []
    monkeypatch.setattr(modbus_bus, "resolve_addresses", lambda *a: calls.append(a) or None)
    for _ in range(50):
        assert bus.read_only is True
    assert calls == []


# ── client internals are not reachable as plain attributes ───────────────────


async def test_wrapped_client_and_ctx_are_not_plain_attributes() -> None:
    bus = BusScheduler(f"tcp://127.0.0.1:{PORT}", _client(), BusSettings(read_only_endpoints=(f"127.0.0.1:{PORT}",)))
    guard = bus.client
    assert not hasattr(guard, "_client")
    assert not hasattr(guard, "_is_locked")
    assert not hasattr(guard.ctx, "_ctx")
