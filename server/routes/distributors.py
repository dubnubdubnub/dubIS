"""Distributor product-preview + credential-management /v1 routes.

`fetch_distributor_product` dispatches to whichever `DistributorManager`
fetch method matches the path's `{name}`; an unrecognized name or a `None`
result (product not found upstream) each get a dedicated 404 `code` — neither
maps cleanly onto the generic `dubis_error`/`not_found` codes in
`server/errors.py`, so these two branches build the JSON body directly rather
than raising.

None of the remaining routes mutate inventory-derived state (they manage
DigiKey session cookies / the Mouser API key), so none of them call
`finish_mutation`/publish — same rationale as `fetch_favicon` in
`vendors_pos.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/v1", tags=["distributors"])


_PRODUCT_FETCHERS = {
    "lcsc": "fetch_lcsc_product",
    "digikey": "fetch_digikey_product",
    "mouser": "fetch_mouser_product",
    "pololu": "fetch_pololu_product",
}


class SetMouserKeyBody(BaseModel):
    key: str


# ── Product preview ──────────────────────────────────────────────────────────


@router.get("/distributors/{name}/product/{code}", operation_id="fetch_distributor_product")
def fetch_distributor_product(request: Request, name: str, code: str) -> dict:
    api = request.app.state.api
    method_name = _PRODUCT_FETCHERS.get(name)
    if method_name is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Unknown distributor: {name}",
                "code": "unknown_distributor",
                "detail": None,
            },
        )
    result = getattr(api, method_name)(code)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Product not found: {name}/{code}",
                "code": "product_not_found",
                "detail": None,
            },
        )
    return result


# ── DigiKey session ──────────────────────────────────────────────────────────


@router.get("/distributors/digikey/session", operation_id="get_digikey_session")
def get_digikey_session(request: Request) -> dict:
    api = request.app.state.api
    return {**api.check_digikey_session(), **api.get_digikey_login_status()}


@router.delete("/distributors/digikey/session", operation_id="logout_digikey")
def logout_digikey(request: Request) -> dict:
    api = request.app.state.api
    return api.logout_digikey()


@router.post("/distributors/digikey/session/validate", operation_id="validate_digikey_session")
def validate_digikey_session(request: Request) -> dict:
    api = request.app.state.api
    return api.validate_digikey_session()


@router.post("/distributors/digikey/cookies/sync", operation_id="sync_digikey_cookies")
def sync_digikey_cookies(request: Request) -> dict:
    api = request.app.state.api
    return api.sync_digikey_cookies()


# ── Mouser API key ───────────────────────────────────────────────────────────


@router.get("/distributors/mouser/key", operation_id="get_mouser_api_key_status")
def get_mouser_api_key_status(request: Request) -> dict:
    api = request.app.state.api
    return api.get_mouser_api_key_status()


@router.put("/distributors/mouser/key", operation_id="set_mouser_api_key")
def set_mouser_api_key(request: Request, body: SetMouserKeyBody) -> dict:
    api = request.app.state.api
    return api.set_mouser_api_key(body.key)


@router.delete("/distributors/mouser/key", operation_id="clear_mouser_api_key")
def clear_mouser_api_key(request: Request) -> dict:
    api = request.app.state.api
    return api.clear_mouser_api_key()
