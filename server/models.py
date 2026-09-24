"""Pydantic models derived from domain.schema.INVENTORY_FIELDS.

InventoryItemModel is built at import time via pydantic.create_model so the
/v1 response shape can never drift from the to_js inventory record surface
defined in domain/schema.py — the same source that cache_db.query_inventory
and js/inventory-record.d.ts are generated from.
"""

from __future__ import annotations

from typing import Any

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


# ── JLCPCB credential capture (docs/plans/2026-09-20-extension-credential-capture.md) ──
#
# Write-only by construction: `JlcSessionBody` is the ONLY model here with a
# `cookies` field, and it is a *request* body. Every response model below is a
# projection that cannot carry a credential — FastAPI filters the handler's
# dict down to the declared fields, so a cookie cannot escape even if a future
# facade change starts returning one. Guarded by
# `tests/python/server/test_jlcpcb_routes.py`.


class JlcPairingResponse(BaseModel):
    nonce: str
    ttl: int


class JlcSessionBody(BaseModel):
    nonce: str
    # Nullable: the extension reads the account from `data.list[0].customerCode`
    # and sends `null` when the validation response had no rows (an empty
    # library). It is a HINT either way -- the server files the credential
    # under the account IT resolves.
    account: str | None = None
    label: str | None = None
    # Untyped values rather than a typed cookie model: this is whatever
    # `chrome.cookies.getAll` returned (`secure`/`httpOnly` booleans, an
    # `expirationDate` number), and the server filters it down to the named
    # session cookies itself (`jlc_session.filter_cookies`). Declaring
    # `dict[str, str]` here would 422 the real extension's body.
    cookies: list[dict[str, Any]] = []


class JlcSessionModel(BaseModel):
    account: str
    label: str
    added_at: str
    last_ok: str


class JlcSessionsResponse(BaseModel):
    logged_in: bool
    supported: bool
    message: str
    accounts: list[JlcSessionModel]


class JlcSessionAcceptedResponse(BaseModel):
    account: str
    label: str
    added_at: str
    last_ok: str
    item_count: int
    state: str


class JlcRevokeResponse(BaseModel):
    account: str
    revoked: bool
    accounts: list[JlcSessionModel]


class JlcLibraryResponse(BaseModel):
    account: str
    total: int
    records: list[InventoryItemModel]
