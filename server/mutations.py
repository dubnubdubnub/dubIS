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


def finish_mutation(reason: str, detail: dict) -> dict:
    body: dict[str, Any] = {"ok": True, "detail": detail}
    events.publish("inventory.updated", {"reason": reason, "detail": detail})
    return body
