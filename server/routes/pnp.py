"""PnP consume /v1 route + legacy (non-`/v1`) aliases.

`_consume` replicates `pnp_server.PnPHandler.do_POST`'s `/api/consume`
resolution+adjust flow exactly (same `pnp_part_map` helpers, same
`adjust_part("remove", ..., source="openpnp")` call), so both the `/v1` route
and the legacy `/api/consume` alias share one implementation. Unlike a plain
inventory mutation, a PnP consume publishes two events: `inventory.consumed`
(the placement-specific detail OpenPnP/other consumers care about) followed
by `inventory.updated` (so `/v1/events` subscribers watching for generic
inventory changes — e.g. a second desktop client — also get notified,
matching every other mutating route's convention).

The legacy aliases (`GET /api/health`, `GET /api/parts`, `POST /api/consume`)
are mounted without the `/v1` prefix on the same app so OpenPnP's Jython
script can point at this server unchanged through the 1c cutover — the shapes
here exactly mirror `pnp_server.py`'s HTTP responses.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from pnp_part_map import _load_part_map, _resolve_part_id
from server import events

router = APIRouter(tags=["pnp"])


class ConsumeBody(BaseModel):
    part_id: str
    qty: int = 1


def _consume(api, part_id: str, qty: int) -> dict:
    part_id = (part_id or "").strip()
    if not part_id:
        raise ValueError("part_id is required")
    if qty <= 0:
        raise ValueError("qty must be positive")

    part_map = _load_part_map(api.base_dir)
    inventory = api._load_organized()
    part_key = _resolve_part_id(part_id, part_map, inventory)
    if not part_key:
        raise KeyError(f"Unknown part ID: {part_id}")

    fresh = api.adjust_part("remove", part_key, qty, "OpenPnP placement", source="openpnp")

    new_qty = None
    for item in fresh:
        item_key = item.get("lcsc") or item.get("mpn") or item.get("digikey")
        if item_key == part_key:
            new_qty = item.get("qty")
            break

    events.publish(
        "inventory.consumed",
        {"part_id": part_id, "part_key": part_key, "qty": qty, "new_qty": new_qty},
    )
    events.publish(
        "inventory.updated",
        {"reason": "pnp-consume", "detail": {"part_key": part_key, "new_qty": new_qty}},
    )
    return {"ok": True, "part_key": part_key, "new_qty": new_qty}


@router.post("/v1/pnp/consume", operation_id="pnp_consume")
def pnp_consume(request: Request, body: ConsumeBody) -> dict:
    api = request.app.state.api
    return _consume(api, body.part_id, body.qty)


# ── Legacy aliases (no /v1 prefix) ──────────────────────────────────────────
# Note: legacy /api/consume error responses use the /v1 {error, code, detail}
# contract (via the shared FastAPI exception handlers), diverging from
# pnp_server.py's {"ok": False, "error": ...} shape. This is happy-path parity
# only, by design — error-shape parity is deferred to the 1c OpenPnP cutover.


@router.get("/api/health", operation_id="legacy_health")
def legacy_health() -> dict:
    return {"ok": True}


@router.get("/api/parts", operation_id="legacy_parts")
def legacy_parts(request: Request) -> dict:
    api = request.app.state.api
    return {"ok": True, "parts": api._load_organized()}


@router.post("/api/consume", operation_id="legacy_consume")
def legacy_consume(request: Request, body: ConsumeBody) -> dict:
    api = request.app.state.api
    return _consume(api, body.part_id, body.qty)
