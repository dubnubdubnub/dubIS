import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from server import events
from server.app import create_app


def test_publish_reaches_subscriber_queue():
    q = events.subscribe()
    try:
        events.publish("inventory.updated", {"reason": "test"})
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data == {"reason": "test"}
    finally:
        events.unsubscribe(q)


def test_publish_without_subscribers_is_noop():
    events.publish("inventory.updated", {"reason": "nobody-listening"})  # must not raise


@pytest.fixture
def live_base_url(api):
    """A real uvicorn server bound to a loopback socket.

    /v1/events is an infinite generator (heartbeats forever), and Starlette's
    TestClient fully drains an ASGI app's response before returning it to
    httpx — so `TestClient(app).stream(...)` deadlocks forever on this
    endpoint (confirmed: it never even yields response headers back to the
    caller). A real socket is required to observe partial/streamed output
    while the generator is still running.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(create_app(api), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn server did not start in time"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_sse_stream_delivers_event(live_base_url):
    received = {}

    def _push_later():
        time.sleep(0.3)
        events.publish("scan.receiving", {"count": 1})

    t = threading.Thread(target=_push_later, daemon=True)
    t.start()
    with httpx.stream("GET", f"{live_base_url}/v1/events", timeout=10) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("event:"):
                received["event"] = line.split(":", 1)[1].strip()
            if line.startswith("data:"):
                received["data"] = json.loads(line.split(":", 1)[1])
                break
    assert received["event"] == "scan.receiving"
    assert received["data"] == {"count": 1}
