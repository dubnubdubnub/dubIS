"""Vendors + purchase-orders /v1 routes.

Vendor/PO mutations that change inventory-derived state (delete/merge vendor,
create/update/delete PO) end with `finish_mutation(..., reason="vendors"/"...",
detail=...)`, publishing `inventory.updated`. `update_vendor` mutates only
vendor config (not inventory rows), so it publishes with `result=None` (no
`include=inventory` support) via the CFG convention used elsewhere.
`fetch_favicon` is a pure network read with no cache mutation — no publish.

`GET /v1/purchase-orders/{po_id}/source` streams the archived source file
straight from disk (replaces the pywebview-only `open_source_file`, which
shells out to the OS default app and can't work for a remote client). It
reuses `purchase_orders.resolve_source_path`, the same helper
`get_po_source_preview` uses, and raises `KeyError` (mapped to 404) when the
PO or its archived source file doesn't exist.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.mutations import finish_mutation

router = APIRouter(prefix="/v1", tags=["vendors", "purchase-orders"])


# ── Body models ─────────────────────────────────────────────────────────────


class UpdateVendorBody(BaseModel):
    vendor_id: str = ""
    name: str = ""
    url: str = ""
    favicon_path: str = ""


class MergeVendorsBody(BaseModel):
    src_id: str
    dst_id: str


class FetchFaviconBody(BaseModel):
    url: str


class CreatePurchaseOrderBody(BaseModel):
    vendor_id: str
    source_file_b64: str = ""
    source_file_name: str = ""
    purchase_date: str = ""
    notes: str = ""
    line_items: list[dict] = []


class UpdatePurchaseOrderBody(BaseModel):
    vendor_id: str = ""
    purchase_date: str = ""
    notes: str = ""


# ── Vendors ──────────────────────────────────────────────────────────────────


@router.get("/vendors", operation_id="list_vendors")
def list_vendors(request: Request) -> list:
    api = request.app.state.api
    return api.list_vendors()


@router.put("/vendors", operation_id="update_vendor")
def update_vendor(request: Request, body: UpdateVendorBody) -> dict:
    api = request.app.state.api
    result = api.update_vendor(body.vendor_id, body.name, body.url, body.favicon_path)
    return finish_mutation(api, None, None, reason="vendors", detail=result)


@router.delete("/vendors/{vendor_id}", operation_id="delete_vendor")
def delete_vendor(
    request: Request,
    vendor_id: str,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.delete_vendor(vendor_id)
    return finish_mutation(
        api, result, include, reason="vendors", detail={"vendor_id": vendor_id},
    )


@router.post("/vendors/merge", operation_id="merge_vendors")
def merge_vendors(
    request: Request,
    body: MergeVendorsBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.merge_vendors(body.src_id, body.dst_id)
    return finish_mutation(
        api, result, include, reason="vendors",
        detail={"src_id": body.src_id, "dst_id": body.dst_id},
    )


@router.post("/vendors/favicon", operation_id="fetch_favicon")
def fetch_favicon(request: Request, body: FetchFaviconBody) -> dict:
    api = request.app.state.api
    path = api.fetch_favicon(body.url)
    return {"path": path}


# ── Purchase orders ──────────────────────────────────────────────────────────


@router.get("/purchase-orders", operation_id="list_purchase_orders")
def list_purchase_orders(request: Request) -> list:
    api = request.app.state.api
    return api.list_purchase_orders()


@router.post("/purchase-orders", operation_id="create_purchase_order_with_items")
def create_purchase_order_with_items(
    request: Request,
    body: CreatePurchaseOrderBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.create_purchase_order_with_items(
        body.vendor_id, body.source_file_b64, body.source_file_name,
        body.purchase_date, body.notes, body.line_items,
    )
    return finish_mutation(
        api, result, include, reason="create_po",
        detail={"vendor_id": body.vendor_id, "count": len(body.line_items)},
    )


# Registered before `/purchase-orders/{po_id}` so "last" is not swallowed by
# the {po_id} path parameter.
@router.delete("/purchase-orders/last", operation_id="delete_last_purchase_order")
def delete_last_purchase_order(
    request: Request,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.delete_last_purchase_order()
    return finish_mutation(
        api, result, include, reason="delete_last_po", detail={},
    )


@router.get("/purchase-orders/{po_id}", operation_id="get_po_with_items")
def get_po_with_items(request: Request, po_id: str) -> dict:
    api = request.app.state.api
    return api.get_po_with_items(po_id)


@router.patch("/purchase-orders/{po_id}", operation_id="update_purchase_order")
def update_purchase_order(
    request: Request,
    po_id: str,
    body: UpdatePurchaseOrderBody,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.update_purchase_order(po_id, body.vendor_id, body.purchase_date, body.notes)
    return finish_mutation(
        api, result, include, reason="update_po", detail={"po_id": po_id},
    )


@router.delete("/purchase-orders/{po_id}", operation_id="delete_purchase_order")
def delete_purchase_order(
    request: Request,
    po_id: str,
    include: str | None = Query(None),
) -> dict:
    api = request.app.state.api
    result = api.delete_purchase_order(po_id)
    return finish_mutation(
        api, result, include, reason="delete_po", detail={"po_id": po_id},
    )


@router.get("/purchase-orders/{po_id}/preview", operation_id="get_po_source_preview")
def get_po_source_preview(request: Request, po_id: str) -> dict:
    api = request.app.state.api
    return api.get_po_source_preview(po_id)


@router.get("/purchase-orders/{po_id}/source", operation_id="get_po_source")
def get_po_source(request: Request, po_id: str) -> FileResponse:
    api = request.app.state.api
    import purchase_orders

    path = purchase_orders.resolve_source_path(api._sources_dir, po_id, api._po_csv)
    if not path:
        raise KeyError(po_id)
    ext = os.path.splitext(path)[1]
    return FileResponse(path, filename=f"{po_id}{ext}")
