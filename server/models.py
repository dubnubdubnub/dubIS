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


class SourceStatusModel(BaseModel):
    """One roster entry as `GET /v1/sources` reports it.

    The token is NEVER echoed — only `has_token`. It is a credential for another
    server that this hub holds on the user's behalf; a GET that returned it
    would hand it to anything that can read the roster.
    """

    id: str
    name: str
    url: str
    enabled: bool
    has_token: bool
    # Reachable FROM THIS HUB, which is finally the honest question now that the
    # hub is what does the fetching (the old dot in Preferences could only ask
    # "reachable from this browser window").
    reachable: bool
    # Why not, in a few words — so a red dot can say something rather than just
    # being red. Empty when `reachable`.
    detail: str = ""
    # Whether this hub can get past that server's auth: "ok" | "required" |
    # "rejected" | "unknown". See `server/sources.ProbeResult`.
    #
    # `reachable` cannot answer this and must not be read as if it does.
    # `/v1/health` is exempt from `AuthMiddleware`, so a server running
    # `DUBIS_AUTH_MODE=on` that we hold no token for is fully "reachable" while
    # answering 401 to every request that carries data — a green dot on a
    # server that will not serve a single row.
    auth: str = "unknown"


class SourcesResponse(BaseModel):
    # What a request with no `X-Dubis-Source` header falls back to. NOT "the
    # server this hub is on" — the hub holds no such state; see
    # server/dispatch.py.
    default: str
    # The same value under the name js/server-tabs-logic.js already reads.
    active: str
    sources: list[SourceStatusModel]


class SetActiveSourceBody(BaseModel):
    # "local" | "<source id>" | "merged"
    source: str


class CreateSourceBody(BaseModel):
    url: str
    id: str = ""
    name: str = ""
    token: str = ""
    enabled: bool = True


class UpdateSourceBody(BaseModel):
    """Every field optional: `None` means "leave this one alone"."""

    name: str | None = None
    url: str | None = None
    token: str | None = None
    enabled: bool | None = None
