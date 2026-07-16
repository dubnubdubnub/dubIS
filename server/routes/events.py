"""GET /v1/events — server-sent events stream over the in-process broker.

Sync generator is correct here: FastAPI iterates sync generators in its
worker thread pool, so this blocks one worker thread per connected client
for the lifetime of the connection. Acceptable at desktop scale (a handful
of concurrent local/Tailscale clients), not intended to scale beyond that.

Starlette iterates a *sync* generator by handing each `next()` call to a
worker thread (`anyio.to_thread.run_sync`, one call per yielded item). A
pending cancellation (server shutdown, client disconnect) can only take
effect once the in-flight `next()` call returns — so a generator that
blocks for the full HEARTBEAT_SECONDS inside a single call is stuck for
that whole window no matter how the cancellation is triggered. To keep
each `next()` call short, the generator polls the subscriber queue in
short (POLL_SECONDS) slices and *yields after every poll*, not just after
an event or a heartbeat: on an empty poll it yields "" (an empty body
chunk, invisible on the wire — chunked-encoding writers emit zero bytes
for it) purely so control returns to the caller every ~POLL_SECONDS. The
real `": heartbeat\n\n"` comment is still emitted only once the full
HEARTBEAT_SECONDS have elapsed without an event, so client-visible
semantics (events flush immediately; heartbeat cadence ~HEARTBEAT_SECONDS)
are unchanged.

Disconnect-detection window: uvicorn's ASGI receive-channel reports
"http.disconnect" as soon as the transport notices the peer closed the
socket, independent of our writes — but that cancellation still can't
interrupt this generator any faster than the ~POLL_SECONDS granularity
above. Between polls, a disconnected subscriber's queue keeps accumulating
events and its thread stays parked for up to ~POLL_SECONDS (worst case
longer on silent network partitions where the transport never notices the
peer is gone). Acceptable at desktop scale; revisit if the number of
concurrent clients grows.

Production note: uvicorn's h11/httptools protocols only flip an in-flight
streaming response's `keep_alive` flag on shutdown — they never force a
still-connected response to complete. Without a bound, `Server.shutdown()`
awaits response completion with no timeout and will hang forever against
a client that never disconnects. Whoever constructs the production
`uvicorn.Config` for this app MUST set `timeout_graceful_shutdown` (a few
seconds) so uvicorn cancels the handler task once it expires — this
generator's ~POLL_SECONDS yield cadence is what lets that cancellation
actually take effect promptly instead of stalling for the remainder of a
blocked call. See `tests/python/server/test_events.py::_start_live_server`.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Generator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from server import events

router = APIRouter(prefix="/v1", tags=["events"])

POLL_SECONDS = 1.0


def _stream() -> Generator[str, None, None]:
    q = events.subscribe()
    try:
        deadline = time.monotonic() + events.HEARTBEAT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield ": heartbeat\n\n"
                deadline = time.monotonic() + events.HEARTBEAT_SECONDS
                continue
            try:
                name, data = q.get(timeout=min(POLL_SECONDS, remaining))
                yield events.format_frame(name, data)
                deadline = time.monotonic() + events.HEARTBEAT_SECONDS
            except queue.Empty:
                # No-op yield: returns control to the caller (a checkpoint
                # where a pending cancellation can take effect) without
                # writing anything visible to the client.
                yield ""
    finally:
        events.unsubscribe(q)


@router.get("/events", operation_id="events_stream")
def events_stream() -> StreamingResponse:
    return StreamingResponse(_stream(), media_type="text/event-stream")
