"""Tests for server/run.py: start_server/stop_server thread-mode lifecycle.

This is the one test module using a real uvicorn thread started via the
production `start_server`/`stop_server` helpers (all other server tests use
TestClient). It picks a free loopback port itself (start_server's signature
takes a fixed port, unlike the port-0 pattern used elsewhere in
tests/python/server/conftest.py) and polls /v1/health until the server is
actually accepting connections.
"""

from __future__ import annotations

import os
import socket
import time

import httpx
import pytest

from server.__main__ import _build_api
from server.run import start_server, stop_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_build_api_repoints_every_base_dir_derived_attribute(tmp_path):
    """server/__main__.py's _build_api must repoint ALL base_dir-derived

    attributes onto --data-dir, not just the ones dubis_headless.py already
    covers. Regression for the bug where cache_db_path/events_dir were left
    pointed at the repo's own data/events dirs, so a standalone server would
    write its SQLite cache and price/part events outside --data-dir.
    """
    data_dir = str(tmp_path / "standalone-data")
    os.makedirs(data_dir, exist_ok=True)

    api = _build_api(data_dir)

    repointed = {
        "base_dir": api.base_dir,
        "input_csv": api.input_csv,
        "output_csv": api.output_csv,
        "adjustments_csv": api.adjustments_csv,
        "prefs_json": api.prefs_json,
        "cache_db_path": api.cache_db_path,
        "events_dir": api.events_dir,
    }
    for name, path in repointed.items():
        assert os.path.commonpath([os.path.abspath(path), data_dir]) == data_dir, (
            f"api.{name} ({path!r}) is not under the target data dir {data_dir!r}"
        )

    assert api.cache_db_path == os.path.join(data_dir, "cache.db")
    assert api.events_dir == os.path.join(data_dir, "events")


def test_start_server_serves_health_then_stop_closes_port(api):
    port = _free_port()
    server = start_server(api, port=port)
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        resp = None
        last_exc = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{base_url}/v1/health", timeout=1)
                break
            except httpx.TransportError as exc:
                last_exc = exc
                time.sleep(0.05)
        assert resp is not None, f"server never became reachable ({last_exc!r})"
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        stop_server(server)

    # Poll for the port to actually close rather than asserting immediately —
    # should_exit triggers an async shutdown that hasn't necessarily finished
    # by the time stop_server() returns.
    deadline = time.monotonic() + 10
    closed = False
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/v1/health", timeout=1)
            time.sleep(0.05)
        except httpx.TransportError:
            closed = True
            break
    assert closed, "port did not close after stop_server"


def test_stop_server_is_idempotent_with_should_exit_flag(api):
    port = _free_port()
    server = start_server(api, port=port)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.05)
    finally:
        stop_server(server)
        assert server.should_exit is True
        # Calling again must not raise.
        stop_server(server)


def _wait_until_serving(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=1)
            return
        except httpx.TransportError:
            time.sleep(0.05)
    raise AssertionError(f"server on port {port} never became reachable")


def test_stop_server_joins_thread_before_returning(api):
    """Regression for the review finding that stop_server() used to set
    should_exit and return immediately, without waiting for uvicorn's
    background thread to actually finish — the caller (e.g. app.pyw's
    _cleanup()) could then proceed to release the data-dir lock, or close
    the cache, while the server thread was still mid-shutdown.

    Against the OLD code (should_exit = True; release lock; return, no
    join), server._dubis_thread.is_alive() right after stop_server()
    returns is not reliably False — the assertion below can only be
    trusted to pass deterministically once stop_server() actually joins
    the thread before returning."""
    port = _free_port()
    server = start_server(api, port=port)
    _wait_until_serving(port)

    stop_server(server)

    assert server._dubis_thread is not None
    assert server._dubis_thread.is_alive() is False, (
        "stop_server() returned before the uvicorn thread actually stopped"
    )


def test_stop_server_release_lock_false_defers_lock_release(tmp_path, api):
    """Proves the release_lock=False seam app.pyw's _cleanup() relies on:
    the lock survives stop_server() and is only released by an explicit
    later call — so a second process cannot acquire it (and start writing
    to the same CSVs/cache.db) until the caller has finished its own
    teardown (e.g. api.shutdown() committing/closing cache.db)."""
    from dubis_errors import DataDirLockedError
    from server.lockfile import acquire_lock

    data_dir = str(tmp_path)
    port = _free_port()
    server = start_server(api, port=port, data_dir=data_dir)
    _wait_until_serving(port)

    stop_server(server, data_dir=data_dir, release_lock=False)

    assert server._dubis_thread.is_alive() is False
    # Lock must still be held — a second acquire fails.
    with pytest.raises(DataDirLockedError):
        acquire_lock(data_dir)

    # Caller finishes its own teardown, then releases explicitly.
    server._dubis_lock.release()

    # Now a second acquire succeeds immediately.
    handle = acquire_lock(data_dir)
    handle.release()
