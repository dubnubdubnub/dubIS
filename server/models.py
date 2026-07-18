"""Pydantic models derived from domain.schema.INVENTORY_FIELDS.

InventoryItemModel is built at import time via pydantic.create_model so the
/v1 response shape can never drift from the to_js inventory record surface
defined in domain/schema.py — the same source that cache_db.query_inventory
and js/inventory-record.d.ts are generated from.
"""

from __future__ import annotations

from pydantic import BaseModel, create_model

from domain.schema import INVENTORY_FIELDS

_TS_TYPE_MAP = {
    "string": str,
    "string[]": list[str],
}


def _field_type(field_def) -> type:
    if field_def.ts_type == "number":
        return float if isinstance(field_def.default, float) else int
    return _TS_TYPE_MAP[field_def.ts_type]


InventoryItemModel = create_model(
    "InventoryItemModel",
    **{
        f.py_key: (_field_type(f), ...)
        for f in INVENTORY_FIELDS
        if f.to_js
    },
)


class InventoryEnvelope(BaseModel):
    inventory: list[InventoryItemModel]


class QuantityResponse(BaseModel):
    quantity: int | None


class PurchaseHistoryResponse(BaseModel):
    has_purchase_history: bool


class GroupsResponse(BaseModel):
    groups: list[str]


# ── KiCad HTTP Library (/v1/kicad/*) ────────────────────────────────────────
#
# Every leaf scalar is `str`-typed per the protocol's string-encoding
# requirement (design doc `docs/plans/2026-07-17-phase4-kicad-design.md`
# §1 -- KiCad rejects native ints/bools). Route handlers in
# `server/routes/kicad.py` build these from `domain/kicad_view.py`, which
# does the `str(...)`/"True"/"False" coercion explicitly -- these models are
# a second, schema-level guard: if a handler ever slips a native bool/int
# into a payload, pydantic's `str` field type rejects it at response-model
# validation time rather than silently emitting a JSON number.


class KicadRootResponse(BaseModel):
    categories: str
    parts: str


class KicadCategory(BaseModel):
    id: str
    name: str
    description: str


class KicadPartSummary(BaseModel):
    id: str
    name: str
    description: str
    keywords: str
    footprint_filters: list[str]


class KicadFieldValue(BaseModel):
    value: str
    visible: str


class KicadPartDetail(BaseModel):
    id: str
    name: str
    symbolIdStr: str
    description: str
    keywords: str
    exclude_from_bom: str
    exclude_from_board: str
    exclude_from_sim: str
    footprint_filters: list[str]
    fields: dict[str, KicadFieldValue]
