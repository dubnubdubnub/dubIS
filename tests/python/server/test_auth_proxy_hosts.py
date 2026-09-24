"""`DUBIS_TRUSTED_PROXY_HOSTS`: trust the tailscale operator proxy by the DNS
name of its headless Service rather than a pod IP that churns on every
restart (which, after the dubis-server -> fremont rename, left a stale IP in
the Secret and 401'd every tailnet request).

DNS is never hit: `TrustedProxyHosts` takes an injected resolver and clock,
and the end-to-end tests monkeypatch `server.auth.system_resolver`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

import pytest
from fastapi.testclient import TestClient

from server import auth
from server.app import create_app
from server.auth import TrustedProxyHosts, _parse_trusted_proxy_hosts
from tests.python.helpers import make_api, make_part, write_ledger

HOST = "ts-fremont-hlclt.tailscale.svc.cluster.local"
PROXY = ("10.42.2.176", 51234)
MOVED_PROXY = ("10.42.3.33", 51234)
OTHER_POD = ("10.42.9.9", 51234)
LOGIN = {"Tailscale-User-Login": "alice@example.com"}


def ip(s: str):
    return ipaddress.ip_address(s)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeResolver:
    """Maps name -> set of IP strings; a missing name raises NXDOMAIN."""

    def __init__(self, table: dict[str, set[str]]) -> None:
        self.table = table
        self.calls: list[str] = []

    async def __call__(self, host: str):
        self.calls.append(host)
        if host not in self.table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return frozenset(ip(a) for a in self.table[host])


def run(coro):
    """A private loop, NOT `asyncio.run`: that clears the main thread's
    current event loop on exit, which breaks later tests in the session that
    call `asyncio.get_event_loop()` (test_peercred's protocol test)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def contains(hosts: TrustedProxyHosts, addr: str) -> bool:
    return run(hosts.contains(ip(addr)))


def _hosts(table, **kw):
    clock = FakeClock()
    resolver = FakeResolver(table)
    hosts = TrustedProxyHosts(tuple(kw.pop("names", [HOST])), resolver=resolver, clock=clock, **kw)
    return hosts, resolver, clock


# ── TrustedProxyHosts unit behaviour ─────────────────────────────────────────


def test_matches_resolved_address_a_and_aaaa():
    hosts, _, _ = _hosts({HOST: {"10.42.2.176", "fd00::176"}})
    assert contains(hosts, "10.42.2.176") is True
    assert contains(hosts, "fd00::176") is True


def test_unrelated_peer_not_trusted():
    hosts, _, _ = _hosts({HOST: {"10.42.2.176"}})
    assert contains(hosts, "10.42.9.9") is False


def test_cache_hit_within_ttl_does_not_re_resolve():
    hosts, resolver, clock = _hosts({HOST: {"10.42.2.176"}})
    assert contains(hosts, "10.42.2.176")
    clock.t += 29
    assert contains(hosts, "10.42.2.176")
    assert resolver.calls == [HOST]


def test_ttl_expiry_picks_up_changed_ip():
    """The whole point: the proxy pod restarts, gets a new IP, and the next
    check after the TTL follows it -- and stops trusting the old one."""
    hosts, resolver, clock = _hosts({HOST: {"10.42.2.176"}}, miss_cooldown=1e9)
    assert contains(hosts, "10.42.2.176")
    resolver.table[HOST] = {"10.42.3.33"}
    clock.t += 31
    assert contains(hosts, "10.42.2.176") is False
    assert contains(hosts, "10.42.3.33") is True
    assert len(resolver.calls) == 2


def test_miss_triggers_early_re_resolve_after_cooldown():
    hosts, resolver, clock = _hosts({HOST: {"10.42.2.176"}})
    assert contains(hosts, "10.42.2.176")
    resolver.table[HOST] = {"10.42.3.33"}
    clock.t += 6  # past the 5s miss cooldown, well inside the 30s TTL
    assert contains(hosts, "10.42.3.33") is True
    assert len(resolver.calls) == 2


def test_miss_re_resolution_is_rate_limited():
    """A flood of forged headers from an untrusted peer must not become a DNS
    flood: at most one re-resolution per cooldown window."""
    hosts, resolver, clock = _hosts({HOST: {"10.42.2.176"}})
    assert contains(hosts, "10.42.2.176")
    for _ in range(500):
        assert contains(hosts, "10.42.9.9") is False
    assert len(resolver.calls) == 1  # still inside the cooldown of the first
    clock.t += 5
    for _ in range(500):
        assert contains(hosts, "10.42.9.9") is False
    assert len(resolver.calls) == 2
    clock.t += 1
    assert contains(hosts, "10.42.9.9") is False
    assert len(resolver.calls) == 2


def test_nxdomain_fails_closed_and_warns_once(caplog):
    hosts, resolver, clock = _hosts({})
    with caplog.at_level(logging.WARNING, logger="server.auth"):
        for _ in range(3):
            assert contains(hosts, "10.42.2.176") is False
            clock.t += 6  # each call re-resolves (cooldown elapsed)
    assert len(resolver.calls) == 3
    warnings = [r for r in caplog.records if "could not resolve" in r.message]
    assert len(warnings) == 1
    assert HOST in warnings[0].message
    assert "unresolved" in hosts.describe()


def test_resolution_failure_drops_previously_known_address():
    """Fail closed, not stale-open: once a name stops resolving, the address
    it used to have is no longer trusted."""
    hosts, resolver, clock = _hosts({HOST: {"10.42.2.176"}})
    assert contains(hosts, "10.42.2.176")
    del resolver.table[HOST]
    clock.t += 31
    assert contains(hosts, "10.42.2.176") is False


def test_one_failing_name_does_not_poison_the_others():
    hosts, _, _ = _hosts({HOST: {"10.42.2.176"}}, names=["gone.example.internal", HOST])
    assert contains(hosts, "10.42.2.176") is True


def test_resolver_raising_arbitrary_exception_does_not_crash():
    async def boom(host):
        raise RuntimeError("resolver exploded")

    hosts = TrustedProxyHosts((HOST,), resolver=boom, clock=FakeClock())
    assert contains(hosts, "10.42.2.176") is False


def test_empty_hosts_never_resolve():
    hosts, resolver, _ = _hosts({}, names=[])
    assert contains(hosts, "10.42.2.176") is False
    assert resolver.calls == []


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_hosts():
    assert _parse_trusted_proxy_hosts(f" {HOST} , Other.Example. ,") == (HOST, "other.example.")
    assert _parse_trusted_proxy_hosts("") == ()


@pytest.mark.parametrize("bad", ["10.42.2.176", "10.42.0.0/16", "fd00::1", "bad host", "-x.example", "a..b"])
def test_parse_hosts_rejects_ips_and_garbage(bad):
    with pytest.raises(ValueError):
        _parse_trusted_proxy_hosts(bad)


# ── end to end through AuthMiddleware ────────────────────────────────────────


@pytest.fixture
def resolver(monkeypatch):
    fake = FakeResolver({HOST: {PROXY[0]}})
    monkeypatch.setattr(auth, "system_resolver", fake)
    return fake


def _env(monkeypatch, *, ips="", hosts=""):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", ips)
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_HOSTS", hosts)
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")


def _app(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return create_app(inst)


def _get(app, client, headers=LOGIN):
    with TestClient(app, client=client) as c:
        return c.get("/v1/parts", headers=headers).status_code


def test_header_honored_from_peer_matching_hostname(tmp_path, monkeypatch, resolver):
    _env(monkeypatch, hosts=HOST)
    assert _get(_app(tmp_path), PROXY) == 200


def test_header_ignored_from_peer_not_matching_hostname(tmp_path, monkeypatch, resolver):
    _env(monkeypatch, hosts=HOST)
    assert _get(_app(tmp_path), OTHER_POD) == 401


def test_proxy_move_followed_via_hostname(tmp_path, monkeypatch, resolver):
    """The incident, reproduced: the proxy pod moves to a new IP. With a
    pinned IP it would 401 forever; with the hostname it recovers on the
    next miss re-resolution."""
    _env(monkeypatch, hosts=HOST)
    app = _app(tmp_path)
    assert _get(app, PROXY) == 200
    resolver.table[HOST] = {MOVED_PROXY[0]}
    hosts = app.state.auth_config.trusted_proxy_hosts
    hosts._resolved_at -= hosts.miss_cooldown  # age the cache past the miss cooldown
    assert _get(app, MOVED_PROXY) == 200
    hosts._resolved_at -= hosts.ttl
    assert _get(app, PROXY) == 401


def test_nxdomain_hostname_fails_closed_end_to_end(tmp_path, monkeypatch, resolver):
    _env(monkeypatch, hosts="ts-gone.tailscale.svc.cluster.local")
    assert _get(_app(tmp_path), PROXY) == 401


def test_ips_list_still_works_alone(tmp_path, monkeypatch, resolver):
    _env(monkeypatch, ips=PROXY[0])
    assert _get(_app(tmp_path), PROXY) == 200
    assert resolver.calls == []  # an IP match never touches DNS


def test_mixed_lists_either_matches(tmp_path, monkeypatch, resolver):
    _env(monkeypatch, ips="192.168.5.0/24", hosts=HOST)
    app = _app(tmp_path)
    assert _get(app, ("192.168.5.9", 51234)) == 200
    assert _get(app, PROXY) == 200
    assert _get(app, OTHER_POD) == 401


def test_no_header_means_no_dns(tmp_path, monkeypatch, resolver):
    """Ordinary untrusted traffic (no header at all) must never cost a lookup."""
    _env(monkeypatch, hosts=HOST)
    app = _app(tmp_path)
    for _ in range(5):
        assert _get(app, OTHER_POD, headers={}) == 401
    assert resolver.calls == []


def test_hosts_alone_suppresses_the_empty_config_warning(tmp_path, monkeypatch, resolver, caplog):
    _env(monkeypatch, hosts=HOST)
    with caplog.at_level(logging.WARNING, logger="server.auth"):
        _app(tmp_path)
    assert not [r for r in caplog.records if "both unset/empty" in r.message]


def test_untrusted_peer_warning_is_diagnostic_and_rate_limited(tmp_path, monkeypatch, resolver, caplog):
    _env(monkeypatch, ips="10.1.2.3", hosts=HOST)
    app = _app(tmp_path)
    with caplog.at_level(logging.WARNING, logger="server.auth"):
        for _ in range(10):
            assert _get(app, OTHER_POD) == 401
    warnings = [r for r in caplog.records if "untrusted peer" in r.message]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert OTHER_POD[0] in msg          # who sent it
    assert "10.1.2.3" in msg            # configured IPs
    assert HOST in msg and PROXY[0] in msg  # configured name and what it resolves to


def test_untrusted_peer_warning_silent_when_nothing_configured(tmp_path, monkeypatch, caplog):
    """Both lists empty keeps today's behaviour: one config-load warning, no
    per-request noise."""
    _env(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="server.auth"):
        app = _app(tmp_path)
        assert _get(app, PROXY) == 401
    assert len([r for r in caplog.records if "both unset/empty" in r.message]) == 1
    assert not [r for r in caplog.records if "untrusted peer" in r.message]


def test_malformed_hosts_entry_raises_at_startup(tmp_path, monkeypatch):
    _env(monkeypatch, hosts="10.42.2.176")
    with pytest.raises(ValueError, match="DUBIS_TRUSTED_PROXY_IPS"):
        _app(tmp_path)


def test_system_resolver_resolves_localhost():
    """The real resolver's shape (no DNS server needed: localhost comes from
    the hosts file)."""
    addrs = run(auth.system_resolver("localhost"))
    assert addrs and all(a.is_loopback for a in addrs)
