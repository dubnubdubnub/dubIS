"""Cart /v1 routes — CRUD, items, active-cart tracking, split/consolidate/export.

Carts are not part of the inventory materialized view (see
domain/api_cart.py), so mutations publish a dedicated `carts.updated` SSE
event instead of `inventory.updated` — the frontend refetches carts only,
never rebuilding inventory for a cart edit. GET and the export route do NOT
publish (read-only).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from server import events

router = APIRouter(prefix="/v1", tags=["carts"])


# ── Body models ─────────────────────────────────────────────────────────────


class CreateCartBody(BaseModel):
    name: str | None = None


class RenameCartBody(BaseModel):
    name: str


class BoardCountBody(BaseModel):
    """How many boards this cart builds. Positive: a cart for zero boards
    would zero every quantity derived from it."""

    model_config = ConfigDict(extra="forbid")
    board_count: int = Field(ge=1)


class AddItemBody(BaseModel):
    part_id: str | None = None
    raw: dict | None = None
    qty: int | None = None
    target_distributor: str | None = None
    shortfall: int | None = None
    target_packaging: str | None = None
    preset: str | None = None
    per_board_qty: int | None = None


class UpdateItemBody(BaseModel):
    # `extra="forbid"`: every field here is None-means-leave-alone, so a
    # misspelled field name would otherwise be dropped in silence and the route
    # would answer 200 for an edit that never happened.
    model_config = ConfigDict(extra="forbid")
    qty: int | None = None
    target_distributor: str | None = None
    target_packaging: str | None = None
    preset: str | None = None
    per_board_qty: int | None = None


class AddBomMissingBody(BaseModel):
    missing: list[dict]


class SplitBody(BaseModel):
    distributor: str
    new_name: str
    remove_from_source: bool = False


class ConsolidateBody(BaseModel):
    distributor: str


def _identity(request: Request) -> str:
    return getattr(request.state, "identity", None) or "local"


def _publish(cart_id: str) -> None:
    events.publish("carts.updated", {"cart_id": cart_id})


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("/carts", operation_id="list_carts")
def list_carts(request: Request) -> dict:
    api = request.app.state.api
    identity = _identity(request)
    return {
        "carts": api.list_carts(),
        "active_cart_id": api.get_active_cart(identity),
    }


@router.post("/carts", operation_id="create_cart")
def create_cart(request: Request, body: CreateCartBody) -> dict:
    api = request.app.state.api
    result = api.create_cart(body.name)
    _publish(result["id"])
    return {"ok": True, "detail": result}


@router.get("/carts/{cart_id}", operation_id="get_cart")
def get_cart(request: Request, cart_id: str) -> dict:
    api = request.app.state.api
    return api.get_cart(cart_id)


@router.put("/carts/{cart_id}", operation_id="rename_cart")
def rename_cart(request: Request, cart_id: str, body: RenameCartBody) -> dict:
    api = request.app.state.api
    result = api.rename_cart(cart_id, body.name)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.put("/carts/{cart_id}/board-count", operation_id="set_cart_board_count")
def set_cart_board_count(request: Request, cart_id: str, body: BoardCountBody) -> dict:
    api = request.app.state.api
    result = api.set_cart_board_count(cart_id, body.board_count)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.get("/carts/{cart_id}/plan", operation_id="plan_cart")
def plan_cart(
    request: Request,
    cart_id: str,
    preset: str = Query("min"),
    reel_ceiling: float | None = Query(None, ge=0),
) -> dict:
    """Recommend what to buy for every line, with the options that lost.

    Read-only, and deliberately not a mutation: it does not write the
    quantities it suggests. Committing a recommendation is the ordinary item
    update, so re-planning after a price refresh cannot silently rewrite a
    decision the user already made -- which is also why this route publishes no
    `carts.updated` event.
    """
    api = request.app.state.api
    try:
        return api.plan_cart(cart_id, preset=preset, reel_ceiling=reel_ceiling)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/carts/{cart_id}", operation_id="delete_cart")
def delete_cart(request: Request, cart_id: str) -> dict:
    api = request.app.state.api
    api.delete_cart(cart_id)
    _publish(cart_id)
    return {"ok": True, "detail": {"cart_id": cart_id}}


# ── active cart ──────────────────────────────────────────────────────────────


@router.post("/carts/{cart_id}/active", operation_id="set_active_cart")
def set_active_cart(request: Request, cart_id: str) -> dict:
    api = request.app.state.api
    identity = _identity(request)
    result = api.set_active_cart(identity, cart_id)
    _publish(cart_id)
    return {"ok": True, "detail": result}


# ── items ────────────────────────────────────────────────────────────────────


@router.post("/carts/{cart_id}/items", operation_id="add_cart_item")
def add_cart_item(request: Request, cart_id: str, body: AddItemBody) -> dict:
    api = request.app.state.api
    result = api.add_cart_item(
        cart_id, part_id=body.part_id, raw=body.raw, qty=body.qty,
        target_distributor=body.target_distributor, shortfall=body.shortfall,
        target_packaging=body.target_packaging, preset=body.preset,
        per_board_qty=body.per_board_qty,
    )
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.patch("/carts/{cart_id}/items/{ref}", operation_id="update_cart_item")
def update_cart_item(request: Request, cart_id: str, ref: str, body: UpdateItemBody) -> dict:
    api = request.app.state.api
    result = api.update_cart_item(
        cart_id, ref, qty=body.qty, target_distributor=body.target_distributor,
        target_packaging=body.target_packaging, preset=body.preset,
        per_board_qty=body.per_board_qty,
    )
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.delete("/carts/{cart_id}/items/{ref}", operation_id="remove_cart_item")
def remove_cart_item(request: Request, cart_id: str, ref: str) -> dict:
    api = request.app.state.api
    result = api.remove_cart_item(cart_id, ref)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.post("/carts/{cart_id}/clear", operation_id="clear_cart")
def clear_cart(request: Request, cart_id: str) -> dict:
    api = request.app.state.api
    result = api.clear_cart(cart_id)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.post("/carts/{cart_id}/add-bom-missing", operation_id="add_bom_missing_to_cart")
def add_bom_missing_to_cart(request: Request, cart_id: str, body: AddBomMissingBody) -> dict:
    api = request.app.state.api
    result = api.add_bom_missing_to_cart(cart_id, body.missing)
    _publish(cart_id)
    return {"ok": True, "detail": result}


# ── split / consolidate / export ─────────────────────────────────────────────


@router.post("/carts/{cart_id}/split", operation_id="split_cart")
def split_cart(request: Request, cart_id: str, body: SplitBody) -> dict:
    api = request.app.state.api
    result = api.split_cart(cart_id, body.distributor, body.new_name, body.remove_from_source)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.post("/carts/{cart_id}/consolidate", operation_id="consolidate_cart")
def consolidate_cart(request: Request, cart_id: str, body: ConsolidateBody) -> dict:
    api = request.app.state.api
    result = api.consolidate_cart(cart_id, body.distributor)
    _publish(cart_id)
    return {"ok": True, "detail": result}


@router.get("/carts/{cart_id}/export", operation_id="export_cart")
def export_cart(
    request: Request, cart_id: str, distributor: str, fmt: str = Query("csv", alias="format"),
) -> dict:
    api = request.app.state.api
    return api.export_cart(cart_id, distributor, fmt)
