"""Shared "finish a mutating route" helper.

Every mutating /v1 endpoint ends with `return finish_mutation(...)`. It builds
the response envelope and — LAST, after everything else — publishes
`inventory.updated` on the SSE broker (server/events.py).

Publishing after the facade call has already returned is inherently safe:
InventoryApi facades acquire/release their internal lock entirely within the
method call and have released it by the time control returns here, so this
helper never publishes while the facade lock is held.
"""

from __future__ import annotations

from typing import Any

from server import events


def finish_mutation(
    api: Any,
    result: Any,
    include: str | None,
    reason: str,
    detail: dict,
) -> dict:
    body: dict[str, Any] = {"ok": True, "detail": detail}
    if include == "inventory" and isinstance(result, list):
        body["inventory"] = result
    events.publish("inventory.updated", {"reason": reason, "detail": detail})
    return body
