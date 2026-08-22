"""Tests for fleet_client: the model-fleet registry client.

Every HTTP call is mocked — nothing here touches the tailnet or the cluster, so
these run on CI and on a laptop with no fleet at all (which is also the
situation the client must degrade gracefully in: no registry means ``None``,
never a raw urllib traceback).

Two shapes of registry reply are exercised on purpose. The client is written
against a service in another repo, so it tolerates both a bare JSON list and a
``{"models": [...]}`` envelope rather than guessing one and breaking on the
other.
"""
from __future__ import annotations

import io
import json
import logging
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import fleet_client
from fleet_client import FleetClient, FleetUnavailableError, LeaseConflictError

TAILNET = "https://fleet.miku-parore.ts.net"
IN_CLUSTER = "http://fleet-registry.fleet.svc.cluster.local"


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

class _Resp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, payload, status=200):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install(monkeypatch, handler):
    """Patch urlopen with `handler(call) -> _Resp`, recording every request."""
    calls: list[SimpleNamespace] = []

    def fake_urlopen(req, timeout=None):
        call = SimpleNamespace(
            method=req.get_method(),
            url=req.full_url,
            timeout=timeout,
            body=json.loads(req.data.decode("utf-8")) if req.data else None,
            headers={k.lower(): v for k, v in req.header_items()},
        )
        calls.append(call)
        return handler(call)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _fleet(entries, envelope=False):
    """A handler answering GET /fleet with `entries`."""
    payload = {"models": list(entries)} if envelope else list(entries)

    def handler(call):
        assert call.url.startswith(TAILNET + "/fleet") or "/fleet" in call.url
        return _Resp(payload)

    return handler


def _entry(model, node, endpoint, *, caps=("vision",), ctx_max=32768,
           state="healthy", path="tailnet", location="tailnet", health=None):
    return {
        "model": model,
        "node": node,
        "endpoint": endpoint,
        "capabilities": list(caps),
        "ctx_max": ctx_max,
        "state": state,
        "path": path,
        "location": location,
        "health": health if health is not None else {"last_ok_age_s": 3.0, "tps": 42.5},
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient fleet config leaks into a test."""
    for name in ("DUBIS_FLEET_URL", "DUBIS_FLEET_IN_CLUSTER", "KUBERNETES_SERVICE_HOST"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# base URL / configuration
# --------------------------------------------------------------------------

def test_default_base_url_is_the_tailnet_registry():
    assert FleetClient().base_url == TAILNET


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("DUBIS_FLEET_URL", "http://127.0.0.1:9999/")
    assert FleetClient().base_url == "http://127.0.0.1:9999"


def test_explicit_base_url_beats_env(monkeypatch):
    monkeypatch.setenv("DUBIS_FLEET_URL", "http://127.0.0.1:9999")
    assert FleetClient(base_url="http://elsewhere:8080/").base_url == "http://elsewhere:8080"


def test_in_cluster_defaults_to_the_clusterip_registry(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    client = FleetClient()
    assert client.can_reach_cluster is True
    assert client.base_url == IN_CLUSTER


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

def test_discover_returns_top_ranked_entry(monkeypatch):
    entries = [
        _entry("qwen2.5-vl-7b", "mauler", "http://mauler:8080"),
        _entry("qwen2.5-vl-3b", "y740", "http://y740:8080"),
    ]
    _install(monkeypatch, _fleet(entries))
    picked = FleetClient().discover(need_caps=["vision"])
    assert picked is not None
    assert (picked.model, picked.node) == ("qwen2.5-vl-7b", "mauler")
    assert picked.endpoint == "http://mauler:8080"
    assert picked.capabilities == ("vision",)
    assert picked.ctx_max == 32768
    assert picked.health == {"last_ok_age_s": 3.0, "tps": 42.5}


def test_discover_accepts_an_enveloped_reply(monkeypatch):
    _install(monkeypatch, _fleet([_entry("m", "n", "http://n:8080")], envelope=True))
    assert FleetClient().discover().model == "m"


def test_discover_sends_need_caps_and_never_include_all(monkeypatch):
    calls = _install(monkeypatch, _fleet([_entry("m", "n", "http://n:8080")]))
    FleetClient().discover(need_caps=["vision"])
    assert len(calls) == 1
    assert "need_caps=vision" in calls[0].url
    # Stale/unhealthy entries are excluded server-side; asking for them on the
    # normal path would drag them back in.
    assert "include=all" not in calls[0].url
    assert "include" not in calls[0].url
    assert calls[0].method == "GET"


def test_discover_without_caps_sends_no_filter(monkeypatch):
    calls = _install(monkeypatch, _fleet([_entry("m", "n", "http://n:8080")]))
    FleetClient().discover(need_caps=None)
    assert "need_caps" not in calls[0].url


def test_discover_none_when_registry_advertises_nothing(monkeypatch):
    """Stale and unhealthy entries never reach us on the normal path — the
    registry filters them out, so an all-stale fleet is simply an empty list."""
    calls = _install(monkeypatch, _fleet([]))
    assert FleetClient().discover(need_caps=["vision"]) is None
    assert "include=all" not in calls[0].url


def test_discover_skips_bad_states_if_the_registry_ever_leaks_them(monkeypatch):
    entries = [
        _entry("stale-one", "a", "http://a:8080", state="stale"),
        _entry("sick-one", "b", "http://b:8080", state="unhealthy"),
        _entry("good-one", "c", "http://c:8080", state="degraded"),
    ]
    _install(monkeypatch, _fleet(entries))
    assert FleetClient().discover().model == "good-one"


def test_discover_skips_entries_lacking_the_capability(monkeypatch):
    entries = [
        _entry("text-only", "a", "http://a:8080", caps=("chat",)),
        _entry("sees", "b", "http://b:8080", caps=("chat", "vision")),
    ]
    _install(monkeypatch, _fleet(entries))
    assert FleetClient().discover(need_caps=["vision"]).model == "sees"


def test_discover_skips_entries_with_no_endpoint(monkeypatch):
    entries = [
        _entry("no-address", "a", ""),
        _entry("dialable", "b", "http://b:8080"),
    ]
    _install(monkeypatch, _fleet(entries))
    assert FleetClient().discover().model == "dialable"


def test_discover_skips_in_cluster_endpoints_from_off_cluster(monkeypatch):
    """A ClusterIP URL is not reachable from the tailnet, so advertising it to a
    caller that cannot reach the cluster would hand back a dead endpoint."""
    entries = [
        _entry("in-cluster-7b", "y740",
               "http://llamacpp.win-runners.svc.cluster.local:8080",
               path="cluster", location="in-cluster"),
        _entry("tailnet-3b", "mauler", "http://mauler:8080"),
    ]
    _install(monkeypatch, _fleet(entries))
    client = FleetClient(can_reach_cluster=False)
    picked = client.discover()
    assert picked.model == "tailnet-3b"


def test_discover_keeps_in_cluster_endpoints_when_in_cluster(monkeypatch):
    entries = [_entry("in-cluster-7b", "y740",
                      "http://llamacpp.win-runners.svc.cluster.local:8080",
                      path="cluster", location="in-cluster")]
    _install(monkeypatch, _fleet(entries))
    picked = FleetClient(can_reach_cluster=True).discover()
    assert picked.model == "in-cluster-7b"
    assert picked.is_in_cluster is True


def test_in_cluster_detected_from_the_hostname_alone(monkeypatch):
    """`path`/`location` are honoured when present; a `.svc.cluster.local` host
    is the fallback so a node that omits them is still classified correctly."""
    entries = [_entry("m", "n", "http://llamacpp.win-runners.svc.cluster.local:8080",
                      path="", location="")]
    _install(monkeypatch, _fleet(entries))
    assert FleetClient(can_reach_cluster=False).discover() is None
    assert FleetClient(can_reach_cluster=True).discover().model == "m"


def test_advertise_all_asks_for_diagnostics(monkeypatch):
    entries = [_entry("sick", "a", "http://a:8080", state="unhealthy")]
    calls = _install(monkeypatch, _fleet(entries))
    listed = FleetClient().advertise(include_all=True)
    assert "include=all" in calls[0].url
    assert [e.state for e in listed] == ["unhealthy"]


# --------------------------------------------------------------------------
# discover: registry trouble
# --------------------------------------------------------------------------

def _raiser(exc):
    def handler(_call):
        raise exc
    return handler


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("connection refused"),
    socket.timeout("timed out"),
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    urllib.error.HTTPError("http://x/fleet", 503, "Unavailable", {}, None),
])
def test_discover_returns_none_when_the_registry_is_unreachable(monkeypatch, caplog, exc):
    _install(monkeypatch, _raiser(exc))
    with caplog.at_level(logging.WARNING, logger="fleet_client"):
        assert FleetClient().discover() is None
    assert caplog.records, "an unreachable registry must be logged, not swallowed"


def test_discover_returns_none_on_non_json(monkeypatch, caplog):
    _install(monkeypatch, lambda _call: _Resp(b"<html>gateway timeout</html>"))
    with caplog.at_level(logging.WARNING, logger="fleet_client"):
        assert FleetClient().discover() is None
    assert caplog.records


def test_discover_returns_none_on_unexpected_json_shape(monkeypatch):
    _install(monkeypatch, lambda _call: _Resp({"unexpected": "shape"}))
    assert FleetClient().discover() is None


def test_discover_uses_a_short_probe_timeout(monkeypatch):
    calls = _install(monkeypatch, _fleet([]))
    FleetClient().discover()
    assert 0 < calls[0].timeout <= 10


# --------------------------------------------------------------------------
# leases
# --------------------------------------------------------------------------

def _lease_handler(lease_id="lease-1", *, conflict=None, raise_exc=None):
    def handler(call):
        if raise_exc is not None:
            raise raise_exc
        if call.method == "POST" and call.url.endswith("/leases"):
            if conflict is not None:
                raise urllib.error.HTTPError(
                    call.url, 409, "Conflict", {},
                    io.BytesIO(json.dumps(conflict).encode("utf-8")))
            return _Resp({"id": lease_id, "model": call.body.get("model"),
                          "holder": call.body.get("holder"),
                          "ttl_s": call.body.get("ttl_s"),
                          "expires_at": "2026-08-21T12:00:00Z"}, status=201)
        if call.method == "POST" and call.url.endswith("/renew"):
            return _Resp({"id": lease_id, "expires_at": "2026-08-21T12:05:00Z"})
        if call.method == "DELETE":
            return _Resp(b"", status=204)
        raise AssertionError(f"unexpected request {call.method} {call.url}")

    return handler


def test_acquire_lease_posts_the_documented_body(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    lease = FleetClient().acquire_lease("qwen2.5-vl-7b", holder="dubis-reader", ttl_s=120)
    assert calls[0].method == "POST"
    assert calls[0].url == TAILNET + "/leases"
    assert calls[0].body == {"model": "qwen2.5-vl-7b", "holder": "dubis-reader", "ttl_s": 120}
    assert calls[0].headers["content-type"] == "application/json"
    assert lease.id == "lease-1"
    assert lease.model == "qwen2.5-vl-7b"
    assert lease.holder == "dubis-reader"
    assert lease.expires_at == "2026-08-21T12:00:00Z"
    assert lease.released is False


def test_acquire_lease_includes_node_when_given(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    FleetClient().acquire_lease("m", holder="h", ttl_s=60, node="y740")
    assert calls[0].body["node"] == "y740"


def test_acquire_lease_omits_node_when_not_given(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    FleetClient().acquire_lease("m", holder="h", ttl_s=60)
    assert "node" not in calls[0].body


def test_conflict_surfaces_the_named_holder(monkeypatch):
    conflict = {"detail": {"error": "held", "model": "qwen2.5-vl-7b",
                           "holder": "someone-else", "expires_at": "2026-08-21T12:09:00Z"}}
    _install(monkeypatch, _lease_handler(conflict=conflict))
    with pytest.raises(LeaseConflictError) as caught:
        FleetClient().acquire_lease("qwen2.5-vl-7b", holder="dubis-reader", ttl_s=60)
    err = caught.value
    assert err.holder == "someone-else"
    assert err.model == "qwen2.5-vl-7b"
    assert err.expires_at == "2026-08-21T12:09:00Z"
    assert "someone-else" in str(err)


def test_conflict_holder_read_from_a_flat_body(monkeypatch):
    _install(monkeypatch, _lease_handler(conflict={"holder": "other-agent"}))
    with pytest.raises(LeaseConflictError) as caught:
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert caught.value.holder == "other-agent"


def test_conflict_holder_read_from_a_string_detail(monkeypatch):
    _install(monkeypatch, _lease_handler(
        conflict={"detail": "model m is leased by holder ci-runner until 12:09Z"}))
    with pytest.raises(LeaseConflictError) as caught:
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert caught.value.holder == "ci-runner"


def test_conflict_with_an_unparseable_body_still_raises_typed(monkeypatch):
    _install(monkeypatch, _lease_handler(conflict="not-a-mapping"))
    with pytest.raises(LeaseConflictError) as caught:
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert caught.value.holder == ""
    assert caught.value.model == "m"


def test_conflict_is_a_dubis_error():
    from dubis_errors import DubISError
    assert issubclass(LeaseConflictError, DubISError)
    assert issubclass(FleetUnavailableError, DubISError)


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("connection refused"),
    socket.timeout("timed out"),
    urllib.error.HTTPError("http://x/leases", 500, "Boom", {}, None),
])
def test_acquire_lease_wraps_transport_failures_in_a_typed_error(monkeypatch, exc):
    _install(monkeypatch, _lease_handler(raise_exc=exc))
    with pytest.raises(FleetUnavailableError):
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)


def test_acquire_lease_wraps_a_non_json_reply(monkeypatch):
    _install(monkeypatch, lambda _call: _Resp(b"<html>nope</html>"))
    with pytest.raises(FleetUnavailableError):
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)


def test_renew_extends_the_lease(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert lease.renew() is True
    assert calls[1].method == "POST"
    assert calls[1].url == TAILNET + "/leases/lease-1/renew"
    assert lease.expires_at == "2026-08-21T12:05:00Z"


def test_renew_can_request_a_different_ttl(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    FleetClient().acquire_lease("m", holder="me", ttl_s=60).renew(ttl_s=300)
    assert calls[1].body == {"ttl_s": 300}


def test_renew_is_false_after_release(monkeypatch):
    _install(monkeypatch, _lease_handler())
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    lease.release()
    assert lease.renew() is False


def _dies_after_acquire():
    """Answers the POST /leases, then fails every later call — a registry that
    restarted (and so dropped every lease) mid-flight."""
    state = {"acquired": False}

    def handler(call):
        if not state["acquired"]:
            state["acquired"] = True
            return _lease_handler()(call)
        raise urllib.error.URLError("registry restarted")

    return handler


def test_renew_does_not_raise_when_the_registry_dies(monkeypatch, caplog):
    _install(monkeypatch, _dies_after_acquire())
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    with caplog.at_level(logging.WARNING, logger="fleet_client"):
        assert lease.renew() is False
    assert caplog.records


def test_release_deletes_and_is_idempotent(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert lease.release() is True
    assert calls[1].method == "DELETE"
    assert calls[1].url == TAILNET + "/leases/lease-1"
    assert lease.released is True
    # Second call is a no-op: no second DELETE on the wire, no exception.
    assert lease.release() is False
    assert len(calls) == 2


def test_release_swallows_a_transport_failure(monkeypatch, caplog):
    _install(monkeypatch, _dies_after_acquire())
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    with caplog.at_level(logging.WARNING, logger="fleet_client"):
        assert lease.release() is False
    assert caplog.records
    assert lease.released is True  # never retried; the lease expires on its own


def test_lease_context_manager_releases(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    with FleetClient().acquire_lease("m", holder="me", ttl_s=60) as lease:
        assert lease.released is False
    assert lease.released is True
    assert calls[-1].method == "DELETE"


def test_client_close_releases_outstanding_leases(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    client = FleetClient()
    client.acquire_lease("m", holder="me", ttl_s=60)
    client.close()
    assert [c.method for c in calls] == ["POST", "DELETE"]
    client.close()  # idempotent
    assert [c.method for c in calls] == ["POST", "DELETE"]


def test_client_context_manager_releases(monkeypatch):
    calls = _install(monkeypatch, _lease_handler())
    with FleetClient() as client:
        client.acquire_lease("m", holder="me", ttl_s=60)
    assert calls[-1].method == "DELETE"


def test_leases_are_released_on_shutdown(monkeypatch):
    """The process-exit hook releases what is still held, so a crash-free exit
    hands capacity back immediately instead of waiting out the TTL."""
    calls = _install(monkeypatch, _lease_handler())
    client = FleetClient()
    client.acquire_lease("m", holder="me", ttl_s=60)
    fleet_client._release_at_exit()
    assert calls[-1].method == "DELETE"
    assert calls[-1].url == TAILNET + "/leases/lease-1"
    assert client.leases == ()


def test_shutdown_hook_tolerates_a_dead_registry(monkeypatch):
    _install(monkeypatch, _dies_after_acquire())
    client = FleetClient()
    client.acquire_lease("m", holder="me", ttl_s=60)
    fleet_client._release_at_exit()  # must not raise at interpreter shutdown
    assert client.leases == ()


def test_lease_id_read_from_alternate_key_spellings(monkeypatch):
    _install(monkeypatch, lambda _call: _Resp({"lease": {"lease_id": "L9"}}, status=201))
    lease = FleetClient().acquire_lease("m", holder="me", ttl_s=60)
    assert lease.id == "L9"


def test_missing_lease_id_is_a_typed_error(monkeypatch):
    _install(monkeypatch, lambda _call: _Resp({"ok": True}, status=201))
    with pytest.raises(FleetUnavailableError):
        FleetClient().acquire_lease("m", holder="me", ttl_s=60)


# --------------------------------------------------------------------------
# the module says out loud that a lease is not a mutex
# --------------------------------------------------------------------------

def test_docstring_warns_that_leases_are_not_a_mutex():
    doc = (fleet_client.__doc__ or "").lower()
    assert "not a mutex" in doc
    assert "cooperative" in doc
