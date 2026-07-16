"""GET /v1/events — server-sent events stream over the in-process broker.

Sync generator is correct here: FastAPI iterates sync generators in its
worker thread pool, so this blocks one worker thread per connected client
for the lifetime of the connection. Acceptable at desktop scale (a handful
of concurrent local/Tailscale clients), not intended to scale beyond that.
"""

from __future__ import annotations

import queue
from collections.abc import Generator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from server import events

router = APIRouter(prefix="/v1", tags=["events"])


def _stream() -> Generator[str, None, None]:
    q = events.subscribe()
    try:
        while True:
            try:
                name, data = q.get(timeout=events.HEARTBEAT_SECONDS)
                yield events.format_frame(name, data)
            except queue.Empty:
                yield ": heartbeat\n\n"
    finally:
        events.unsubscribe(q)


@router.get("/events", operation_id="events_stream")
def events_stream() -> StreamingResponse:
    return StreamingResponse(_stream(), media_type="text/event-stream")
