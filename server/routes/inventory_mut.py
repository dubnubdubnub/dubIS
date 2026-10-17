"""Inventory-mutating /v1 routes.

Every mutating endpoint below ends with `return finish_mutation(...)`, which
builds the `{"ok", "detail"}` envelope and publishes `inventory.updated` on
the SSE broker. `resolve_bom_spec` and `extract_spec_from_value` are
read-only lookups (no cache mutation, no publish) and return their result
directly.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from server.auth import stamp_source
from server.mutations import finish_mutation

router = APIRouter(prefix="/v1", tags=["parts"])


# ── Body models ─────────────────────────────────────────────────────────────


class AdjustPartBody(BaseModel):
    adj_type: Literal["set", "add", "remove"]
    quantity: int
    note: str = ""
    source: str = ""


class UpdatePartFieldsBody(BaseModel):
    fields: dict[str, str]


class CreatePartBody(BaseModel):
    """Identity for a part the store does not have yet.

    `part_key` is optional and only for completing a part the store already
    knows under that key (one created by an adjustment, say). Leave it empty to
    create the part under the key its own part numbers imply.
    """

    part_key: str = ""
    lcsc: str = ""
    mpn: str = ""
    digikey: str = ""
    mouser: str = ""
    pololu: str = ""
    manufacturer: str = ""
    package: str = ""
    description: str = ""
    unit_price: float | None = None
    source: str = ""


class UpdatePartPriceBody(BaseModel):
    unit_price: float | None = None
    ext_price: float | None = None


class RecordFetchedPricesBody(BaseModel):
    distributor: str
    price_tiers: list[dict]
    # Packaging metadata off the normalized product, so the stored price
    # observation records WHICH packaging's ladder it came from. All
    # optional: a client that does not send them records exactly what it
    # recorded before, with the packaging left unknown.
    packagings: list[dict] | None = None
    reel_qty: int | None = None
    reel_fee: float | None = None


class ImportPurchasesBody(BaseModel):
    rows: list[dict[str, str]]
    # Provenance tag for the whole batch, stamped into each ledger row's
    # `source` column (and with the caller's identity, like every other
    # mutation). Without it the only undo is `remove_last_purchases`, which is
    # order-dependent: anything imported afterwards has to be deleted first.
    source: str = ""


class ConsumeBomBody(BaseModel):
    matches: list[dict]
    board_qty: int
    bom_name: str
    note: str = ""
    source: str = ""


class ResolveBomSpecBody(BaseModel):
    part_type: str
    value: float
    package: str


class ExtractSpecFromValueBody(BaseModel):
    part_type: str
    value_str: str
    package_str: str


# ── Mutating routes ──────────────────────────────────────────────────────────


@router.post("/parts/{part_key}/adjust", operation_id="adjust_part")
def adjust_part(
    request: Request,
    part_key: str,
    body: AdjustPartBody,
) -> dict:
    api = request.app.state.api
    source = stamp_source(request, body.source)
    api.adjust_part(body.adj_type, part_key, body.quantity, body.note, source)
    return finish_mutation(
        reason="adjust",
        detail={"part_key": part_key, "adj_type": body.adj_type, "quantity": body.quantity},
    )


@router.post("/parts", operation_id="create_part")
def create_part(
    request: Request,
    body: CreatePartBody,
) -> dict:
    """Create a part with its identity, without recording a purchase.

    Carries the part numbers, manufacturer, package and description, and leaves
    the part at whatever stock the adjustments already say (usually zero). No
    price observation is written and no purchase order is touched, so the part
    does not end up looking like some distributor sold it to you. Use
    `POST /v1/purchases/import` when a part really was bought.

    Re-running it on a part that exists fills in only the fields you send, so
    completing a part's identity cannot blank what the store already had.
    """
    api = request.app.state.api
    source = stamp_source(request, body.source)
    fields = {
        name: value
        for name, value in (
            ("lcsc", body.lcsc), ("mpn", body.mpn), ("digikey", body.digikey),
            ("mouser", body.mouser), ("pololu", body.pololu),
            ("manufacturer", body.manufacturer), ("package", body.package),
            ("description", body.description),
        )
    }
    detail = api.create_part(body.part_key, fields, body.unit_price)
    detail["source"] = source
    return finish_mutation(reason="create_part", detail=detail)


@router.patch("/parts/{part_key}", operation_id="update_part_fields")
def update_part_fields(
    request: Request,
    part_key: str,
    body: UpdatePartFieldsBody,
) -> dict:
    api = request.app.state.api
    api.update_part_fields(part_key, body.fields)
    return finish_mutation(
        reason="update_fields",
        detail={"part_key": part_key, "fields": body.fields},
    )


@router.put("/parts/{part_key}/price", operation_id="update_part_price")
def update_part_price(
    request: Request,
    part_key: str,
    body: UpdatePartPriceBody,
) -> dict:
    api = request.app.state.api
    api.update_part_price(part_key, body.unit_price, body.ext_price)
    return finish_mutation(
        reason="update_price",
        detail={"part_key": part_key, "unit_price": body.unit_price, "ext_price": body.ext_price},
    )


@router.delete("/parts/{part_key}", operation_id="delete_part")
def delete_part(
    request: Request,
    part_key: str,
) -> dict:
    api = request.app.state.api
    api.delete_part(part_key)
    return finish_mutation(
        reason="delete_part", detail={"part_key": part_key},
    )


@router.post("/parts/fetch-missing-descriptions", operation_id="fetch_missing_descriptions")
def fetch_missing_descriptions(
    request: Request,
) -> dict:
    api = request.app.state.api
    summary = api.fetch_missing_descriptions()
    return finish_mutation(
        reason="fetch_missing_descriptions", detail=summary,
    )


@router.post("/parts/{part_key}/fetched-prices", operation_id="record_fetched_prices")
def record_fetched_prices(
    request: Request,
    part_key: str,
    body: RecordFetchedPricesBody,
) -> dict:
    api = request.app.state.api
    api.record_fetched_prices(
        part_key, body.distributor, body.price_tiers,
        packagings=body.packagings, reel_qty=body.reel_qty, reel_fee=body.reel_fee,
    )
    detail = {"part_key": part_key, "distributor": body.distributor}
    return finish_mutation(reason="prices", detail=detail)


@router.post("/purchases/import", operation_id="import_purchases")
def import_purchases(
    request: Request,
    body: ImportPurchasesBody,
) -> dict:
    api = request.app.state.api
    source = stamp_source(request, body.source)
    api.import_purchases(body.rows, source)
    # `source` is echoed because it is the handle for the undo, and on a remote
    # server it is NOT the string the caller sent (stamp_source appends their
    # identity). Showing what was stored is how the next `rollback-source`
    # names the right tag instead of guessing.
    return finish_mutation(
        reason="import_purchases",
        detail={"count": len(body.rows), "source": source},
    )


@router.delete("/purchases/last", operation_id="remove_last_purchases")
def remove_last_purchases(
    request: Request,
    count: int = Query(..., ge=1),
) -> dict:
    api = request.app.state.api
    api.remove_last_purchases(count)
    return finish_mutation(
        reason="remove_last_purchases", detail={"count": count},
    )


@router.delete("/adjustments/last", operation_id="remove_last_adjustments")
def remove_last_adjustments(
    request: Request,
    count: int = Query(..., ge=1),
) -> dict:
    api = request.app.state.api
    api.remove_last_adjustments(count)
    return finish_mutation(
        reason="remove_last_adjustments", detail={"count": count},
    )


@router.delete("/adjustments/by-source/{source}", operation_id="rollback_source")
def rollback_source(
    request: Request,
    source: str,
) -> dict:
    """Undo every adjustment tagged `source`, suffixed with an identity or not.

    A tag written by a non-local caller is stored as `<tag>@<identity>`, so
    naming the bare tag rolls back every identity that used it — and nothing
    else: `xfer-2026` never touches `xfer-2026-12-31`. A rollback that removed
    nothing is a 404 naming the tags this server holds, never a 200 with an
    empty list: the documented undo path must not answer "done" while the stock
    stays wrong.
    """
    api = request.app.state.api
    removed = api.rollback_source(source, require_match=True)
    detail = {"removed": removed}
    return finish_mutation(reason="rollback_source", detail=detail)


@router.delete("/purchases/by-source/{source}", operation_id="rollback_purchases_source")
def rollback_purchases_source(
    request: Request,
    source: str,
) -> dict:
    """Undo every purchase-ledger row tagged `source` (see `rollback_source`).

    Only rows imported with a tag can be found this way: a ledger written
    before the `source` column existed has no tag to match, and that answers
    404 with the tags that do exist rather than removing nothing quietly. The
    price observations the import recorded are not undone — a price is a
    sighting, and `events/price_observations.csv` is history, not state.
    """
    api = request.app.state.api
    removed = api.rollback_purchases(source, require_match=True)
    detail = {"removed": removed}
    return finish_mutation(reason="rollback_purchases", detail=detail)


@router.post("/bom/consume", operation_id="consume_bom")
def consume_bom(
    request: Request,
    body: ConsumeBomBody,
) -> dict:
    api = request.app.state.api
    source = stamp_source(request, body.source)
    api.consume_bom(body.matches, body.board_qty, body.bom_name, body.note, source)
    return finish_mutation(
        reason="consume_bom",
        detail={"bom_name": body.bom_name, "board_qty": body.board_qty},
    )


# ── Read-only lookups (no finish_mutation, no publish) ──────────────────────


@router.post("/bom/resolve-spec", operation_id="resolve_bom_spec")
def resolve_bom_spec(request: Request, body: ResolveBomSpecBody) -> dict:
    api = request.app.state.api
    result = api.resolve_bom_spec(body.part_type, body.value, body.package)
    return {"match": result}


@router.post("/spec/extract", operation_id="extract_spec_from_value")
def extract_spec_from_value(request: Request, body: ExtractSpecFromValueBody) -> dict:
    api = request.app.state.api
    return api.extract_spec_from_value(body.part_type, body.value_str, body.package_str)
