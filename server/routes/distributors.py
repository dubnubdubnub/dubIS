"""Distributor product-preview + credential-management /v1 routes.

`fetch_distributor_product` dispatches to whichever `DistributorManager`
fetch method matches the path's `{name}`; an unrecognized name or a `None`
result (product not found upstream) each get a dedicated 404 `code` — neither
maps cleanly onto the generic `dubis_error`/`not_found` codes in
`server/errors.py`, so these two branches build the JSON body directly rather
than raising.

None of the remaining routes mutate inventory-derived state (they manage
DigiKey session cookies / the Mouser API key / JLC session cookies), so none of
them call `finish_mutation`/publish — same rationale as `fetch_favicon` in
`vendors_pos.py`.

The two JLCPCB *credential intake* routes are the only ones here that are
loopback-gated. A browser extension pushes a live JLC session cookie at
`POST /v1/distributors/jlcpcb/session`, and a hub that forwarded that upstream
would be handing a user's credential to a different machine — so both it and
the pairing route that authorizes it call `auth.require_loopback` AND appear in
`proxy.LOCAL_ONLY_PATHS`. Two mechanisms because they answer different
questions: the allowlist stops the hub *forwarding* the request, the guard
stops a remote caller *reaching* the handler. The extension talking to a remote
dubIS is phase 2+ and needs its own deliberate, tested exception. Design:
`docs/plans/2026-09-20-extension-credential-capture.md`.

Those same two paths carry this package's only per-origin CORS grant, in two
halves: `_preflight` answers their OPTIONS, and `BridgeCorsMiddleware` — an ASGI
middleware that lives here, beside the id it pins, and is registered outermost
by `server/app.py` — stamps the grant onto every response they produce,
including ones raised rather than returned.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders

from server.auth import require_loopback
from server.models import (
    JlcLibraryResponse,
    JlcPairingResponse,
    JlcRevokeResponse,
    JlcSessionAcceptedResponse,
    JlcSessionBody,
    JlcSessionsResponse,
)

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


# ── JLCPCB session (browser-extension credential capture) ────────────────────

# The one browser origin allowed to reach the two intake routes cross-origin.
#
# Why this exists at all: the extension's service worker POSTs
# `Content-Type: application/json`, which is not a CORS-simple request, and the
# dubIS origin is deliberately NOT in its `host_permissions` — so Chrome sends a
# preflight, and a `/v1` that answers OPTIONS with 405 and no `Access-Control-*`
# headers fails it. Verified live on 2026-09-20: the extension reported
# "Failed to fetch" and dubIS never saw the request, on loopback as much as on
# the tailnet.
#
# Why scoped to one origin rather than widening the extension: Chrome match
# patterns cannot name a port, so `http://127.0.0.1/*` in `host_permissions`
# would permanently grant fetch + cookie-read for EVERY service on loopback.
# Naming the extension here is only possible because `manifest.json` pins `key`,
# and `test_bridge_origin_matches_the_manifest_key` fails if the two ever drift.
#
# Why this is not a hole: CORS restrains browsers, not clients — it grants no
# access a local process did not already have. `require_loopback` and the
# single-use nonce remain the actual gate, and both still apply to the POST.
BRIDGE_EXTENSION_ID = "fboadceadnhfhdkdmfjlhbicocbhbbpc"
BRIDGE_EXTENSION_ORIGIN = f"chrome-extension://{BRIDGE_EXTENSION_ID}"

_INTAKE_PREFLIGHT_HEADERS = {
    "Access-Control-Allow-Origin": BRIDGE_EXTENSION_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "600",
    # Chrome's Private Network Access check: a request from any other context to
    # a loopback address needs this on the preflight or it is blocked before the
    # CORS result is even consulted.
    "Access-Control-Allow-Private-Network": "true",
    "Vary": "Origin",
}


def _preflight(request: Request) -> Response:
    """Answer a preflight for the pinned extension; refuse every other origin.

    A non-matching Origin gets a bare 403 with no `Access-Control-*` headers at
    all, which is what makes this an allowlist rather than an opening: the
    browser then blocks the request exactly as it does today.
    """
    if request.headers.get("origin") != BRIDGE_EXTENSION_ORIGIN:
        return Response(status_code=403)
    return Response(status_code=204, headers=_INTAKE_PREFLIGHT_HEADERS)


# The two paths the extension POSTs to, and the only two `/v1` paths that may
# answer it cross-origin at all. `BridgeCorsMiddleware` matches the exact path
# string, so these must stay byte-identical to the route decorators below (and
# to the `proxy.LOCAL_ONLY_PATHS` entries naming the same two routes).
INTAKE_PATHS = frozenset({
    "/v1/distributors/jlcpcb/pairing",
    "/v1/distributors/jlcpcb/session",
})


class BridgeCorsMiddleware:
    """Stamp the allow-origin onto EVERY response from the two intake paths.

    A passed preflight only authorizes the request; without the header on the
    *actual* response the browser withholds the body, so the extension cannot
    tell an accepted session from a rejected one.

    **Why a middleware and not a header written in the handler.** The
    handler-level version (an `_allow_bridge_origin` helper, removed
    2026-09-20) wrote onto FastAPI's injected `Response`, which is merged only
    into a value the handler *returns*. A raise — the common one being
    `DistributorAuthError` for an expired nonce — is rendered by
    `server/errors.py` into a fresh `JSONResponse` that never saw that header,
    and a 422 from Pydantic never reaches the handler at all. So the browser
    withheld the 401 body and the extension reported a generic "Failed to
    fetch" for the error dubIS had written specifically for it to display —
    with a short nonce TTL and a human-paced flow, the error users hit most.
    Registered outermost (`server/app.py`), this sees the final response
    whatever produced it.

    **Error bodies on these two paths are readable by the pinned extension, on
    purpose.** They carry an error string, a `code` and an optional structured
    `detail` — no credential, no inventory data — and only the one pinned
    origin ever receives the header. CORS restrains browsers, not clients, so
    this grants no access a local process did not already have;
    `require_loopback` and the single-use nonce remain the entire gate.

    That includes the refusals: the `403 loopback_only` from `require_loopback`
    and, in `DUBIS_AUTH_MODE=on`, a `401` from `AuthMiddleware` — which this
    middleware wraps, being outermost — are both stamped when the Origin
    matches. Intentional, not an accident of ordering (the handler-level
    version made the 403 opaque by accident, because it ran after the guard).
    Both bodies are fixed constants, only the pinned extension origin can read
    them, and CORS changes *readability*, not *reachability*: the request
    happened either way, and whoever controlled that extension would have far
    better options than reading a 401. A browser that provokes one is better
    off seeing the real reason than a generic failure. Only the header changes:
    the status is still 403, and the request is still refused
    (`test_the_right_origin_does_not_let_a_remote_peer_in`).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") not in INTAKE_PATHS:
            await self.app(scope, receive, send)
            return
        if Headers(scope=scope).get("origin") != BRIDGE_EXTENSION_ORIGIN:
            # Echoed per-request, never set unconditionally: any other origin
            # (or none at all) gets a response with no `Access-Control-*` on it.
            await self.app(scope, receive, send)
            return

        async def stamp(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # Assigned, not appended: `_preflight` already sets both on the
                # 204, and two allow-origin values is a CORS failure.
                headers["Access-Control-Allow-Origin"] = BRIDGE_EXTENSION_ORIGIN
                headers["Vary"] = "Origin"
            await send(message)

        await self.app(scope, receive, stamp)


@router.options("/distributors/jlcpcb/pairing", include_in_schema=False)
def preflight_jlc_pairing(request: Request) -> Response:
    return _preflight(request)


@router.options("/distributors/jlcpcb/session", include_in_schema=False)
def preflight_jlc_session(request: Request) -> Response:
    return _preflight(request)


@router.post(
    "/distributors/jlcpcb/pairing",
    response_model=JlcPairingResponse,
    operation_id="create_jlc_pairing",
)
def create_jlc_pairing(request: Request) -> dict:
    """Mint the single-use nonce the extension must present. Loopback only."""
    # No CORS header written here, deliberately: `BridgeCorsMiddleware` stamps
    # it for both intake routes, so a raise carries it too. See its docstring.
    # Kept as a comment rather than added to the docstring above on purpose —
    # a handler docstring on a `/v1` route is PUBLIC API text: it becomes the
    # OpenAPI `description` and, through `scripts/gen-cli.py`, the CLI's help.
    require_loopback(request)
    return request.app.state.api.create_jlc_pairing()


@router.post(
    "/distributors/jlcpcb/session",
    response_model=JlcSessionAcceptedResponse,
    operation_id="receive_jlc_session",
)
def receive_jlc_session(request: Request, body: JlcSessionBody) -> dict:
    """Accept a pushed JLC session: consume the nonce, validate, store. Loopback only.

    Answers with the account it resolved, its label and the library item count
    — never the credential. `body.account` is a hint the extension read out of
    the validation response; the stored key is whatever THIS server's own
    validation call resolved.
    """
    require_loopback(request)
    api = request.app.state.api
    return api.receive_jlc_session(
        body.nonce, body.account or "", body.cookies, body.label or ""
    )


@router.get(
    "/distributors/jlcpcb/sessions",
    response_model=JlcSessionsResponse,
    operation_id="list_jlc_sessions",
)
def list_jlc_sessions(request: Request) -> dict:
    """Which accounts are paired, and when each last worked. Never a cookie."""
    return request.app.state.api.list_jlc_sessions()


@router.delete(
    "/distributors/jlcpcb/sessions/{account}",
    response_model=JlcRevokeResponse,
    operation_id="revoke_jlc_session",
)
def revoke_jlc_session(request: Request, account: str) -> dict:
    """Forget one account's stored credential."""
    return request.app.state.api.revoke_jlc_session(account)


@router.get(
    "/distributors/jlcpcb/library",
    response_model=JlcLibraryResponse,
    operation_id="fetch_jlc_library",
)
def fetch_jlc_library(request: Request, account: str = "") -> dict:
    """One account's private JLC parts library as dubIS-shaped records.

    Read-only: nothing here writes to inventory. Merging JLC stock into
    inventory is phase 3 and carries its own decision (JLC parts are
    PCBA-only, hence non-fungible with bench stock).
    """
    return request.app.state.api.fetch_jlc_library(account)
