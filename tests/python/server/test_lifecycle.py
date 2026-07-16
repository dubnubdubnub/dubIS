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
