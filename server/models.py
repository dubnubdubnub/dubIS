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


class FeederLoadedModel(BaseModel):
    part_key: str
    qty: int
    tape_width_mm: float | None = None
    loaded_at: str


class FeederModel(BaseModel):
    tag_id: str
    family: str
    feeder_type: str
    loaded: FeederLoadedModel | None = None


class FeederListResponse(BaseModel):
    feeders: list[FeederModel]
