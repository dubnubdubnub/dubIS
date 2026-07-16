"""Tests for server/run.py: start_server/stop_server thread-mode lifecycle.

This is the one test module using a real uvicorn thread started via the
production `start_server`/`stop_server` helpers (all other server tests use
TestClient). It picks a free loopback port itself (start_server's signature
takes a fixed port, unlike the port-0 pattern used elsewhere in
tests/python/server/conftest.py) and polls /v1/health until the server is
actually accepting connections.
"""

from __future__ import annotations

import socket
import time

import httpx
import pytest

from server.run import start_server, stop_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
