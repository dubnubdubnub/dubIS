"""GET /v1/parts — inventory listing and per-part read routes.

Each endpoint is a thin wrapper: fetch `api` off `request.app.state`, call the
frozen-surface InventoryApi method by its exact name, wrap scalar returns in
the documented envelope dict.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.models import (
    GroupsResponse,
    InventoryEnvelope,
    PurchaseHistoryResponse,
    QuantityResponse,
)

router = APIRouter(prefix="/v1", tags=["parts"])


@router.get("/parts", response_model=InventoryEnvelope, operation_id="list_parts")
def list_parts(request: Request) -> dict:
    api = request.app.state.api
    return {"inventory": api._load_organized()}


@router.get("/parts/{part_key}/history", operation_id="get_part_history")
def get_part_history(request: Request, part_key: str) -> list:
    api = request.app.state.api
    return api.get_part_history(part_key)


@router.get("/parts/{part_key}/prices", operation_id="get_price_summary")
def get_price_summary(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    return api.get_price_summary(part_key)


@router.get("/parts/{part_key}/distributors", operation_id="get_sourced_distributors")
def get_sourced_distributors(request: Request, part_key: str) -> list:
    api = request.app.state.api
    return api.get_sourced_distributors(part_key)


@router.get(
    "/parts/{part_key}/last-po-quantity",
    operation_id="get_last_po_quantity",
    response_model=QuantityResponse,
)
def get_last_po_quantity(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    return {"quantity": api.get_last_po_quantity(part_key)}


@router.get(
    "/parts/{part_key}/purchase-history",
    operation_id="has_purchase_history",
    response_model=PurchaseHistoryResponse,
)
def has_purchase_history(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    return {"has_purchase_history": api.has_purchase_history(part_key)}


@router.get(
    "/parts/{part_key}/groups",
    operation_id="get_generic_group_names",
    response_model=GroupsResponse,
)
def get_generic_group_names(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    return {"groups": api.get_generic_group_names(part_key)}


@router.get("/parts/{part_key}/spec", operation_id="extract_spec")
def extract_spec(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    return {"spec": api.extract_spec(part_key)}


@router.get("/warnings", operation_id="get_warnings")
def get_warnings(request: Request) -> dict:
    api = request.app.state.api
    return api.get_warnings()
