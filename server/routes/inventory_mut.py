"""Inventory-mutating /v1 routes.

Every mutating endpoint below ends with `return finish_mutation(...)`, which
builds the `{"ok", "detail", ["inventory"]}` envelope and publishes
`inventory.updated` on the SSE broker. `resolve_bom_spec` and
`extract_spec_from_value` are read-only lookups (no cache mutation, no
publish) and return their result directly.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

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


class UpdatePartPriceBody(BaseModel):
    unit_price: float | None = None
    ext_price: float | None = None


class RecordFetchedPricesBody(BaseModel):
    distributor: str
    price_tiers: list[dict]


class ImportPurchasesBody(BaseModel):
    rows: list[dict[str, str]]


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
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.adjust_part(body.adj_type, part_key, body.quantity, body.note, body.source)
    return finish_mutation(
        api, result, include, reason="adjust",
        detail={"part_key": part_key, "adj_type": body.adj_type, "quantity": body.quantity},
    )


@router.patch("/parts/{part_key}", operation_id="update_part_fields")
def update_part_fields(
    request: Request,
    part_key: str,
    body: UpdatePartFieldsBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.update_part_fields(part_key, body.fields)
    return finish_mutation(
        api, result, include, reason="update_fields",
        detail={"part_key": part_key, "fields": body.fields},
    )


@router.put("/parts/{part_key}/price", operation_id="update_part_price")
def update_part_price(
    request: Request,
    part_key: str,
    body: UpdatePartPriceBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.update_part_price(part_key, body.unit_price, body.ext_price)
    return finish_mutation(
        api, result, include, reason="update_price",
        detail={"part_key": part_key, "unit_price": body.unit_price, "ext_price": body.ext_price},
    )


@router.delete("/parts/{part_key}", operation_id="delete_part")
def delete_part(
    request: Request,
    part_key: str,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.delete_part(part_key)
    return finish_mutation(
        api, result, include, reason="delete_part", detail={"part_key": part_key},
    )


@router.post("/parts/fetch-missing-descriptions", operation_id="fetch_missing_descriptions")
def fetch_missing_descriptions(
    request: Request,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    summary = api.fetch_missing_descriptions()
    result = api._load_organized() if include == "inventory" else summary
    return finish_mutation(
        api, result, include, reason="fetch_missing_descriptions", detail=summary,
    )


@router.post("/parts/{part_key}/fetched-prices", operation_id="record_fetched_prices")
def record_fetched_prices(
    request: Request,
    part_key: str,
    body: RecordFetchedPricesBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    api.record_fetched_prices(part_key, body.distributor, body.price_tiers)
    detail = {"part_key": part_key, "distributor": body.distributor}
    result = api._load_organized() if include == "inventory" else None
    return finish_mutation(api, result, include, reason="prices", detail=detail)


@router.post("/purchases/import", operation_id="import_purchases")
def import_purchases(
    request: Request,
    body: ImportPurchasesBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.import_purchases(body.rows)
    return finish_mutation(
        api, result, include, reason="import_purchases", detail={"count": len(body.rows)},
    )


@router.delete("/purchases/last", operation_id="remove_last_purchases")
def remove_last_purchases(
    request: Request,
    count: int = Query(..., ge=1),
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.remove_last_purchases(count)
    return finish_mutation(
        api, result, include, reason="remove_last_purchases", detail={"count": count},
    )


@router.delete("/adjustments/last", operation_id="remove_last_adjustments")
def remove_last_adjustments(
    request: Request,
    count: int = Query(..., ge=1),
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.remove_last_adjustments(count)
    return finish_mutation(
        api, result, include, reason="remove_last_adjustments", detail={"count": count},
    )


@router.delete("/adjustments/by-source/{source}", operation_id="rollback_source")
def rollback_source(
    request: Request,
    source: str,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    removed = api.rollback_source(source)
    detail = {"removed": removed}
    result = api._load_organized() if include == "inventory" else None
    return finish_mutation(api, result, include, reason="rollback_source", detail=detail)


@router.post("/bom/consume", operation_id="consume_bom")
def consume_bom(
    request: Request,
    body: ConsumeBomBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.consume_bom(body.matches, body.board_qty, body.bom_name, body.note, body.source)
    return finish_mutation(
        api, result, include, reason="consume_bom",
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
