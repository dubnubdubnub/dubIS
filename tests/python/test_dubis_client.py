"""tools/dubis_client: discovery (env / port-file) + V1Client + curation.

Ported from the retired tests/python/test_dubis_mcp_client.py. Two changes
from that suite:

  - The spawn-fallback cases are gone with the fallback itself. A CLI runs one
    process per invocation, so an implicit spawn would pay a ~0.57s boot on
    every command and, worse, take the data-dir lock — two concurrent
    invocations would make the second fail against a server the first just
    started. connect() now raises NoServerFoundError instead, and that is what
    is asserted here.
  - The curation helpers (projections, key derivation, the adjust precheck)
    moved out of the MCP tool bodies into dubis_client.curate, so they are
    covered here directly rather than through a tool call.

No HTTP mocking — every test hits a real server via start_server, per this
repo's live-server-harness policy.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from server.run import start_server, stop_server
from tools.dubis_client import (
    NoServerFoundError,
    PartNotFoundError,
    V1Client,
    V1Error,
    compact_part,
    connect,
    derive_part_key,
    find_part,
    matches_part,
    precheck_adjust,
    resolve_canonical_key,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ── V1Client error mapping ───────────────────────────────────────────────────


def test_v1client_get_raises_v1error_with_server_message(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(tmp_path))
    try:
        assert _wait_until((tmp_path / ".v1_port").exists)
        client = V1Client(f"http://127.0.0.1:{port}")
        with pytest.raises(V1Error) as exc_info:
            client.get("/v1/nope-not-a-real-route")
        assert exc_info.value.status == 404
        assert exc_info.value.message
    finally:
        stop_server(server, data_dir=str(tmp_path))


def test_v1client_health_and_meta_roundtrip(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(tmp_path))
    try:
        assert _wait_until((tmp_path / ".v1_port").exists)
        client = V1Client(f"http://127.0.0.1:{port}")
        assert client.get("/v1/health") == {"ok": True}
        assert "schema_version" in client.get("/v1/meta")
    finally:
        stop_server(server, data_dir=str(tmp_path))


# ── discovery precedence ─────────────────────────────────────────────────────


def test_connect_env_var_wins(api, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(data_dir))
    try:
        assert _wait_until((data_dir / ".v1_port").exists)
        other_port = _free_port()
        monkeypatch.setenv("DUBIS_URL", f"http://127.0.0.1:{other_port}")
        client = connect(str(tmp_path))
        assert client.discovered_via == "env"
        assert client.base_url == f"http://127.0.0.1:{other_port}"
    finally:
        stop_server(server, data_dir=str(data_dir))
        monkeypatch.delenv("DUBIS_URL", raising=False)


def test_connect_uses_port_file_when_healthy(api, tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_URL", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(data_dir))
    try:
        assert _wait_until((data_dir / ".v1_port").exists)
        client = connect(str(tmp_path))
        assert client.discovered_via == "port_file"
        assert client.get("/v1/health") == {"ok": True}
    finally:
        stop_server(server, data_dir=str(data_dir))


def test_connect_honours_explicit_data_dir(api, tmp_path, monkeypatch):
    """`dubis --data-dir X` must probe X, not <repo_root>/data."""
    monkeypatch.delenv("DUBIS_URL", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(elsewhere))
    try:
        assert _wait_until((elsewhere / ".v1_port").exists)
        client = connect(str(tmp_path), data_dir=str(elsewhere))
        assert client.discovered_via == "port_file"
    finally:
        stop_server(server, data_dir=str(elsewhere))


def test_connect_raises_when_nothing_is_running(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_URL", raising=False)
    (tmp_path / "data").mkdir()
    with pytest.raises(NoServerFoundError):
        connect(str(tmp_path))


def test_connect_ignores_stale_port_file(tmp_path, monkeypatch):
    """A port file pointing at a dead port must be ignored, not trusted —
    and with no spawn fallback left, that means raising."""
    monkeypatch.delenv("DUBIS_URL", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dead_port = _free_port()  # bound-then-released; nothing listens here
    (data_dir / ".v1_port").write_text(str(dead_port), encoding="utf-8")
    with pytest.raises(NoServerFoundError):
        connect(str(tmp_path))


def test_no_server_error_names_the_fix(tmp_path, monkeypatch):
    """The exit-4 message is the first thing a user hits on a fresh machine,
    so its wording is pinned rather than left to drift."""
    monkeypatch.delenv("DUBIS_URL", raising=False)
    (tmp_path / "data").mkdir()
    with pytest.raises(NoServerFoundError) as exc_info:
        connect(str(tmp_path))
    message = str(exc_info.value)
    assert "dubis serve" in message
    assert "DUBIS_URL" in message


def test_client_module_exposes_no_spawn_api():
    """Regression: the spawn fallback must stay gone. Restoring it would
    reintroduce per-invocation lock contention between concurrent commands."""
    from tools.dubis_client import v1client

    assert not hasattr(v1client, "_spawn_server")
    assert not hasattr(v1client, "shutdown_spawned")


# ── auth header ──────────────────────────────────────────────────────────────


def test_v1client_attaches_bearer_token_when_given():
    client = V1Client("http://example.invalid", token="abc123")
    assert client._client.headers["Authorization"] == "Bearer abc123"


def test_v1client_omits_authorization_header_when_no_token():
    client = V1Client("http://example.invalid")
    assert "Authorization" not in client._client.headers


def test_connect_passes_dubis_token_env_to_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_URL", "http://example.invalid")
    monkeypatch.setenv("DUBIS_TOKEN", "abc123")
    client = connect(str(tmp_path))
    assert client._client.headers["Authorization"] == "Bearer abc123"


def test_connect_without_dubis_token_omits_header(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_URL", "http://example.invalid")
    monkeypatch.delenv("DUBIS_TOKEN", raising=False)
    client = connect(str(tmp_path))
    assert "Authorization" not in client._client.headers


# ── curation helpers (pure) ──────────────────────────────────────────────────


def test_derive_part_key_prefers_c_prefixed_lcsc():
    assert derive_part_key({"lcsc": "C1000", "mpn": "CL05B104"}) == "C1000"


def test_derive_part_key_falls_back_to_mpn_when_lcsc_not_c_prefixed():
    assert derive_part_key({"lcsc": "not-a-c-number", "mpn": "LM358DR"}) == "LM358DR"


def test_derive_part_key_precedence_order():
    assert derive_part_key({"digikey": "DK1", "pololu": "P1", "mouser": "M1"}) == "DK1"
    assert derive_part_key({"pololu": "P1", "mouser": "M1"}) == "P1"


def test_derive_part_key_empty_when_no_identifiers():
    assert derive_part_key({"description": "mystery"}) == ""


def test_matches_part_accepts_alias_pn():
    item = {"lcsc": "C1000", "mpn": "CL05B104KO5NNNC"}
    assert matches_part(item, "C1000")
    assert matches_part(item, "CL05B104KO5NNNC")
    assert not matches_part(item, "nope")


def test_compact_part_is_the_six_field_projection():
    compact = compact_part({"lcsc": "C1000", "qty": 5, "description": "cap", "extra": "dropped"})
    assert set(compact) == {
        "part_key", "description", "qty", "section", "package", "unit_price",
    }
    assert "extra" not in compact


# ── curation helpers (against a live server) ─────────────────────────────────


@pytest.fixture
def seeded_client(tmp_path):
    """A live /v1 server with one alias-bearing part: canonical key C1000,
    reachable by its MPN too."""
    from tests.python.helpers import make_api, make_part, write_ledger
    from tests.python.server.conftest import start_live_server

    api = make_api(tmp_path)
    write_ledger(api, [
        make_part(lcsc="C1000", mpn="CL05B104KO5NNNC", qty=500,
                  desc="Capacitor MLCC 100nF 16V X7R 0402", pkg="0402",
                  unit_price="0.002", ext_price="1.00"),
    ])
    server, thread, base_url = start_live_server(api)
    client = V1Client(base_url)
    try:
        yield client
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        api.shutdown()


def test_find_part_resolves_alias_pn(seeded_client):
    item = find_part(seeded_client, "CL05B104KO5NNNC")
    assert item is not None
    assert derive_part_key(item) == "C1000"


def test_resolve_canonical_key_maps_alias_to_canonical(seeded_client):
    """The canonical-key-strict routes (adjust, consume, prices, history) key
    straight off what they are given; an alias PN would read or write against
    a key naming no real part."""
    _, canonical = resolve_canonical_key(seeded_client, "CL05B104KO5NNNC")
    assert canonical == "C1000"


def test_resolve_canonical_key_raises_on_unknown(seeded_client):
    with pytest.raises(PartNotFoundError):
        resolve_canonical_key(seeded_client, "NOT-A-PART")


def test_precheck_adjust_rejects_add_on_unknown_part(seeded_client):
    """/v1 silently no-ops add/remove on an unknown key (only `set`
    materializes a row), returning a misleading success. The precheck is what
    turns that into an error."""
    with pytest.raises(PartNotFoundError):
        precheck_adjust(seeded_client, "NOT-A-PART", "add")


def test_precheck_adjust_rejects_remove_on_unknown_part(seeded_client):
    with pytest.raises(PartNotFoundError):
        precheck_adjust(seeded_client, "NOT-A-PART", "remove")


def test_precheck_adjust_allows_set_on_unknown_part(seeded_client):
    """`set` creates parts on purpose — it must stay exempt."""
    assert precheck_adjust(seeded_client, "BRAND-NEW", "set") == "BRAND-NEW"


def test_precheck_adjust_canonicalizes_alias(seeded_client):
    assert precheck_adjust(seeded_client, "CL05B104KO5NNNC", "add") == "C1000"
