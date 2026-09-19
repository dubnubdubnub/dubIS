"""Shared "finish a mutating route" helper.

Every mutating /v1 endpoint ends with `return finish_mutation(...)`. It builds
the `{"ok", "detail"}` response envelope and — LAST, after everything else —
publishes `inventory.updated` on the SSE broker (server/events.py). Mutation
responses never carry inventory data; the frontend's sole re-render path is
the `inventory.updated` SSE push (or a direct post-mutation call sharing the
same debounce), which triggers a fresh `GET /v1/inventory` fetch.

Publishing after the facade call has already returned is inherently safe:
InventoryApi facades acquire/release their internal lock entirely within the
method call and have released it by the time control returns here, so this
helper never publishes while the facade lock is held.
"""

from __future__ import annotations

from typing import Any

from server import events
from server.sources import LOCAL_ID


def finish_mutation(reason: str, detail: dict, source: str = LOCAL_ID) -> dict:
    """Build the `{ok, detail}` envelope and announce the change on the SSE bus.

    `source` tags the event with the dubIS server the change happened on. It
    defaults to `local` because a route handler only ever runs for a request the
    hub served itself — a request bound for another source is forwarded by
    `server/dispatch.py` long before it reaches here, and that module publishes
    the forwarded write's event with its own source id.

    The tag exists so clients can filter. With several windows open on different
    servers, one shared stream has to say *whose* data moved; without the tag a
    window on the bench would re-fetch every time someone wrote to the shop.
    """
    body: dict[str, Any] = {"ok": True, "detail": detail}
    events.publish("inventory.updated", {"reason": reason, "detail": detail, "source": source})
    return body
