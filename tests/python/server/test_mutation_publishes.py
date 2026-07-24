"""Exhaustiveness guard: every state-mutating /v1 route must publish an SSE
event (so all clients — including remote ones — re-render), OR be explicitly
classified as a non-mutating exception.

The frontend's SOLE inventory re-render path is the `inventory.updated` SSE push
(js/store.js onEvent → scheduleInventoryRefresh → GET /v1/inventory). A mutating
endpoint that forgets to publish leaves every client stale — the same silent
failure class as "a mutation forgot to mark the BOM dirty". Publishing is
funnelled through server.mutations.finish_mutation (which calls events.publish).

This test enumerates every route with a mutating verb and asserts its handler
source contains finish_mutation / events.publish, unless the operation_id is in
EXEMPT (with a documented reason). Add a new mutating route without publishing
and this fails in CI until you either publish or justify the exemption in the
diff — you cannot silently ship a stale-UI mutation.
"""

from __future__ import annotations

import inspect
import types

from fastapi.routing import APIRoute

from server.app import create_app

_MUTATING_VERBS = {"POST", "PUT", "DELETE", "PATCH"}

# Mutating-verb routes that intentionally do NOT change inventory-rendered state,
# each with the reason it needn't publish. Keep this list justified — it doubles
# as documentation of "why this write doesn't refresh the UI".
EXEMPT = {
    # Read-only lookups that happen to use POST (body carries the query).
    "resolve_bom_spec": "read-only spec lookup",
    "extract_spec_from_value": "read-only spec parse",
    "match_part": "read-only import match lookup",
    "detect_columns": "read-only column detection",
    "ocr_overlay": "read-only OCR of an uploaded image",
    "parse_import_source": "read-only file parse",
    # UI-scoped state, not inventory-derived.
    "save_preferences": "UI settings, not inventory data",
    "create_saved_search": "UI-scoped saved searches",
    "delete_saved_search": "UI-scoped saved searches",
    "start_scan_session": "opens a phone-scan session; not a data mutation",
    # Credentials / session — not inventory-rendered state.
    "logout_digikey": "distributor credentials",
    "validate_digikey_session": "distributor credentials",
    "sync_digikey_cookies": "distributor credentials",
    "set_mouser_api_key": "distributor credentials",
    "clear_mouser_api_key": "distributor credentials",
    # Downloads an image to disk; doesn't change inventory rows.
    "fetch_favicon": "fetches a favicon file, no inventory change",
}

# Routes that publish transitively via a shared helper the source-scan can't see.
DELEGATES_OK = {
    "pnp_consume": "delegates to _consume() which publishes",
    "legacy_consume": "delegates to _consume() which publishes",
}

_PUBLISH_MARKERS = ("finish_mutation", "events.publish", "publish(")


def _walk_api_routes(routes):
    """Recurse into FastAPI's lazy `_IncludedRouter` wrappers down to APIRoutes.

    Newer FastAPI (>=0.13x) doesn't flatten `include_router()` into plain
    APIRoutes on `app.routes` — each shows up as an opaque `_IncludedRouter`
    with the real routes under `.original_router.routes`. Without this walk the
    guard finds zero routes and passes vacuously (mirrors test_v1_surface).
    """
    out = []
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            out.extend(_walk_api_routes(original.routes))
        elif isinstance(r, APIRoute):
            out.append(r)
    return out


def _mutating_routes():
    app = create_app(types.SimpleNamespace())
    return [r for r in _walk_api_routes(app.routes) if r.methods & _MUTATING_VERBS]


def test_every_mutating_route_publishes_or_is_exempt():
    offenders = []
    for r in _mutating_routes():
        opid = r.operation_id
        if opid in EXEMPT or opid in DELEGATES_OK:
            continue
        src = inspect.getsource(r.endpoint)
        if not any(m in src for m in _PUBLISH_MARKERS):
            verbs = ",".join(sorted(r.methods & _MUTATING_VERBS))
            offenders.append(f"{verbs} {r.path} ({opid})")
    assert not offenders, (
        "Mutating /v1 route(s) never publish an SSE event — remote clients will "
        "go stale after this mutation. End the route with finish_mutation(...), "
        "or add the operation_id to EXEMPT with a reason:\n  " + "\n  ".join(offenders)
    )


def test_exempt_entries_still_exist_as_routes():
    """Keep EXEMPT/DELEGATES_OK honest — a stale entry (route renamed/removed)
    should be pruned, not silently masking a real gap."""
    live = {r.operation_id for r in _mutating_routes()}
    stale = sorted((set(EXEMPT) | set(DELEGATES_OK)) - live)
    assert not stale, f"EXEMPT/DELEGATES_OK reference routes that no longer exist: {stale}"
