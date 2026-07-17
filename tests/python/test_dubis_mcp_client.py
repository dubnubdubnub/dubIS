"""tools/dubis-mcp/v1client.py: discovery (env / port-file / spawn) + V1Client.

Covers Task 1 of docs/plans/2026-07-16-phase2-mcp-server-plan.md:
  - server/run.py's port-file write/remove roundtrip (start_server/stop_server
    given data_dir=...)
  - v1client.connect(repo_root)'s discovery precedence: env wins over the port
    file at <repo_root>/data/.v1_port; a stale port file (dead port) is
    ignored and discovery falls through to spawning
  - V1Client.get/.post raise V1Error with the server's {"error": ...} message
    on non-2xx
  - the spawn fallback: `python -m server --data-dir <repo_root>/data --port 0`
    really comes up and connect() returns a healthy client talking to it

No HTTP mocking — every test hits a real server (either the in-thread
start_server or a spawned `python -m server` child), per this repo's
live-server-harness policy. connect()'s spawn path relies on the subprocess
inheriting this test process's cwd (the repo root, same convention
tools/dev-tools-mcp/server.py uses via Path.cwd()) so `python -m server`
resolves — it never overrides cwd itself, only --data-dir.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

from server.run import start_server, stop_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools" / "dubis-mcp"))

import v1client  # noqa: E402
from v1client import V1Client, V1Error, connect  # noqa: E402


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


def _read_text_retrying(path: Path, timeout: float = 2.0, interval: float = 0.05) -> str:
    """Read a just-written file, tolerating a transient Windows PermissionError
    (observed flakily right after os.replace() — likely AV/indexer briefly
    holding the freshly-renamed file open) rather than failing outright."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)


# ── server/run.py port-file plumbing ────────────────────────────────────────


def test_start_server_writes_port_file(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(tmp_path))
    port_file = tmp_path / ".v1_port"
    try:
        assert _wait_until(port_file.exists), "port file was never written"
        assert _read_text_retrying(port_file).strip() == str(port)
    finally:
        stop_server(server, data_dir=str(tmp_path))


def test_stop_server_removes_port_file(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(tmp_path))
    port_file = tmp_path / ".v1_port"
    assert _wait_until(port_file.exists)

    stop_server(server, data_dir=str(tmp_path))

    assert _wait_until(lambda: not port_file.exists()), "port file was not removed on stop"


def test_no_port_file_written_without_data_dir(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port)
    try:
        time.sleep(0.3)
        assert not (tmp_path / ".v1_port").exists()
    finally:
        stop_server(server)


# ── V1Client error mapping ───────────────────────────────────────────────────


def test_v1client_get_raises_v1error_with_server_message(api, tmp_path):
    port = _free_port()
    server = start_server(api, port=port, data_dir=str(tmp_path))
    try:
        assert _wait_until((tmp_path / ".v1_port").exists)
        client = V1Client(f"http://127.0.0.1:{port}")
        with pytest.raises(V1Error) as exc_info:
            client.get("/v1/nope-not-a-real-route")
        # Unmatched route -> FastAPI's default 404 {"detail": "Not Found"};
        # V1Client must surface that message, not swallow it.
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
        meta = client.get("/v1/meta")
        assert "schema_version" in meta
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


def test_connect_ignores_stale_port_file_and_spawns(tmp_path, monkeypatch):
    """A port file pointing at a dead port must be ignored, not trusted."""
    monkeypatch.delenv("DUBIS_URL", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    dead_port = _free_port()  # bound-then-released; nothing listens here
    (data_dir / ".v1_port").write_text(str(dead_port), encoding="utf-8")

    try:
        client = connect(str(tmp_path))
        assert client.discovered_via == "spawned"
        assert _wait_until(lambda: _healthy(client), timeout=30)
    finally:
        v1client.shutdown_spawned()


def _healthy(client: V1Client) -> bool:
    try:
        return client.get("/v1/health") == {"ok": True}
    except Exception:  # noqa: BLE001
        return False


# ── spawn fallback ───────────────────────────────────────────────────────────


def test_connect_spawns_standalone_server_when_nothing_else_available(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_URL", raising=False)

    try:
        client = connect(str(tmp_path))
        assert client.discovered_via == "spawned"
        assert _wait_until(lambda: _healthy(client), timeout=30), "spawned server never became healthy"
        assert (tmp_path / "data" / ".v1_port").exists()
    finally:
        v1client.shutdown_spawned()


# ── DUBIS_TOKEN bearer auth (Phase 1c Task 7) ────────────────────────────────
#
# docs/plans/2026-07-16-phase1c-remote-deploy-design.md §7: "v1client gains
# an Authorization header from DUBIS_TOKEN env when set".
#
# The two tests below prove V1Client actually attaches (or omits) the header
# httpx would send on the wire — the thing that matters for a real remote
# server. They don't hit a live server: server/auth.py's AuthMiddleware
# unconditionally trusts loopback peers (identity "local") regardless of any
# token (see server/auth.py's resolution order, item 1), so a real HTTP round
# trip against a server bound to 127.0.0.1 — the only address available in
# this test environment — could never observe a 401-without-token /
# 200-with-token difference; the loopback bypass always wins first. That
# distinction (token vs. no token, for a genuinely non-loopback caller) is
# exactly what tests/python/server/test_auth.py's
# test_bearer_token_allowed / test_bearer_token_wrong_value_rejected already
# cover via FastAPI's TestClient with a simulated remote peer address — this
# module doesn't duplicate that; it covers the client-side half (does
# V1Client build the header V1Client is supposed to build).


def test_v1client_attaches_bearer_token_when_given():
    client = V1Client("http://example.invalid", token="abc123")
    assert client._client.headers["Authorization"] == "Bearer abc123"


def test_v1client_omits_authorization_header_when_no_token():
    client = V1Client("http://example.invalid")
    assert "Authorization" not in client._client.headers


def test_connect_passes_dubis_token_env_to_client(tmp_path, monkeypatch):
    """connect()'s env-URL path (the one used against a real remote deploy)
    must forward DUBIS_TOKEN onto the returned V1Client."""
    monkeypatch.setenv("DUBIS_URL", "http://example.invalid")
    monkeypatch.setenv("DUBIS_TOKEN", "abc123")
    try:
        client = connect(str(tmp_path))
        assert client._client.headers["Authorization"] == "Bearer abc123"
    finally:
        monkeypatch.delenv("DUBIS_URL", raising=False)
        monkeypatch.delenv("DUBIS_TOKEN", raising=False)


def test_connect_without_dubis_token_omits_header(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_URL", "http://example.invalid")
    monkeypatch.delenv("DUBIS_TOKEN", raising=False)
    try:
        client = connect(str(tmp_path))
        assert "Authorization" not in client._client.headers
    finally:
        monkeypatch.delenv("DUBIS_URL", raising=False)
