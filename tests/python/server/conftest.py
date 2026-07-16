"""Shared fixtures for /v1 server tests."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger


@pytest.fixture
def api(tmp_path):
    """InventoryApi wired to a temp directory, seeded with a minimal ledger."""
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


@pytest.fixture
def client(api):
    """TestClient over the /v1 FastAPI app, backed by a real InventoryApi."""
    with TestClient(create_app(api)) as c:
        yield c
    api.shutdown()


def start_live_server(api, **config_kwargs):
    """Start a real uvicorn server bound to an ephemeral loopback port.

    Binds directly to port 0 and reads the real port back from the started
    server (rather than pre-binding a probe socket, closing it, and hoping
    the port is still free) — avoids a bind-close-rebind TOCTOU race.

    Returns (server, thread, base_url). Caller is responsible for shutdown:
    set server.should_exit = True, then thread.join(...) and assert it
    stopped.

    Extracted from test_events.py's `_start_live_server` so other server
    tests (e.g. lifecycle) needing a real socket can reuse the exact same
    startup/teardown pattern instead of inventing a new one.

    `timeout_graceful_shutdown` defaults to 3s (matching the original
    hardcoded value): uvicorn's HTTP protocol implementations (h11/
    httptools) only mark an in-flight streaming response `keep_alive =
    False` on shutdown — they never force a still-connected response to
    complete. Without a bound, `Server.shutdown()` awaits response
    completion with no timeout at all and can hang forever against a
    client that never disconnects (e.g. an open SSE stream).
    """
    import uvicorn

    config_kwargs.setdefault("timeout_graceful_shutdown", 3)
    config = uvicorn.Config(
        create_app(api),
        host="127.0.0.1",
        port=0,
        log_level="warning",
        **config_kwargs,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn server did not start in time"
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}"
