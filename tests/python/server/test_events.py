import json
import threading
import time

import httpx
import pytest

from server import events
from tests.python.server.conftest import start_live_server


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

    `timeout_graceful_shutdown` is set to a small value: uvicorn's HTTP
    protocol implementations (h11/httptools) only mark an in-flight
    streaming response `keep_alive = False` on shutdown — they never force
    a still-connected response to complete. Without a bound, `Server.
    shutdown()` awaits response completion with no timeout at all and would
    hang forever against a client that never disconnects. With the bound,
    uvicorn cancels the handler task once it expires; our generator's
    ~POLL_SECONDS yield cadence (see server/routes/events.py) then lets that
    cancellation actually take effect promptly instead of stalling for the
    remainder of a blocked call.
    """
    server, thread, base_url = start_live_server(api, timeout_graceful_shutdown=3)
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "uvicorn server thread failed to stop"


def test_sse_stream_delivers_event(live_base_url):
    received = {}

    def _push_later():
        # Wait until the SSE handler has actually subscribed before publishing —
        # a fixed sleep races under full-suite load (publish-before-subscribe
        # drops the event and the stream read times out).
        deadline = time.monotonic() + 5
        while not events.has_subscribers() and time.monotonic() < deadline:
            time.sleep(0.01)
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


def test_shutdown_is_prompt_with_open_sse_connection(api):
    """Server shutdown must not be gated by the 15s heartbeat wait.

    Opens an SSE connection (parking a worker thread inside the generator's
    poll loop), then triggers shutdown and measures how long it takes the
    server thread to actually stop. Must be well under HEARTBEAT_SECONDS —
    proves the granular poll (Finding 1) actually shortens shutdown latency
    rather than just changing the code shape.
    """
    server, thread, base_url = start_live_server(api)
    with httpx.stream("GET", f"{base_url}/v1/events", timeout=10) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        # Headers are only sent once the generator's first next() call has
        # run, so by the time we're past the `with` statement the worker
        # thread is already parked in its poll loop. Give it one full poll
        # cycle to settle before timing shutdown.
        time.sleep(1.5)

        start = time.monotonic()
        server.should_exit = True
        thread.join(timeout=10)
        elapsed = time.monotonic() - start

    assert not thread.is_alive(), "uvicorn server thread failed to stop"
    assert elapsed < 8, f"shutdown took {elapsed:.2f}s, expected well under HEARTBEAT_SECONDS"
