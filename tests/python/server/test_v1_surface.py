"""Freeze the /v1 route surface (method, path, operation_id) and its OpenAPI snapshot.

`create_app` is built with a stub api object (`types.SimpleNamespace()`) — this
proves route *registration* never touches the api (only request handlers do,
lazily, via `request.app.state.api`). If any route module reached into the api
at import/registration time, building the app here would raise AttributeError
against the stub, catching that class of bug before it reaches a real
deployment.

Mirrors ``tests/python/test_api_surface.py``'s freeze-and-diff style: a
generate-once, paste-and-pin list, asserted with a two-way diff so additions
and removals are both caught and explained.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from fastapi.routing import APIRoute

from server.app import create_app
from tests.python.test_api_surface import FROZEN_SURFACE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Operation IDs that exist on the /v1 surface but have no equivalent in the
# frozen pywebview bridge surface (`test_api_surface.FROZEN_SURFACE`) — either
# because they're new server-only concepts (health/meta/SSE), server-side
# dispatch helpers with no 1:1 InventoryApi method (fetch_distributor_product),
# legacy non-/v1 aliases kept for the OpenPnP Jython script, or renamed to fit
# REST conventions. Enumerated exactly; no wildcard allowance.
_NEW_OPERATIONS = {
    "events_stream",             # GET /v1/events (SSE) — no bridge equivalent
    "fetch_distributor_product", # GET /v1/distributors/{name}/product/{code} — dispatches to fetch_*_product
    "get_digikey_session",       # GET /v1/distributors/digikey/session — merges check_digikey_session + get_digikey_login_status
    "get_po_source",             # GET /v1/purchase-orders/{po_id}/source — streams the file; no bridge equivalent (open_source_file shells out)
    "health",                    # GET /v1/health — server liveness, no bridge equivalent
    "legacy_consume",            # POST /api/consume — non-/v1 OpenPnP alias
    "legacy_health",             # GET /api/health — non-/v1 OpenPnP alias
    "legacy_parts",              # GET /api/parts — non-/v1 OpenPnP alias
    "list_parts",                # GET /v1/parts — server name for InventoryApi's internal _load_organized
    "meta",                      # GET /v1/meta — server liveness/schema metadata, no bridge equivalent
    "ocr_overlay",                # POST /v1/import/ocr — renamed from ocr_overlay_b64
    "parse_import_source",       # POST /v1/import/parse — dispatches to parse_source_file / parse_source_file_b64
    "pnp_consume",                # POST /v1/pnp/consume — /v1 PnP consume route
}

# The complete, frozen (method, path, operation_id) surface of the /v1 app.
# Generated once by walking `create_app(types.SimpleNamespace()).routes`
# (recursing into FastAPI's lazy `_IncludedRouter` wrappers down to the
# concrete `APIRoute`s) and pasted here. Route changes require touching this
# list deliberately — exactly like `test_api_surface.FROZEN_SURFACE`.
FROZEN_V1_SURFACE = [
    ("DELETE", "/v1/adjustments/by-source/{source}", "rollback_source"),
    ("DELETE", "/v1/adjustments/last", "remove_last_adjustments"),
    ("DELETE", "/v1/distributors/digikey/session", "logout_digikey"),
    ("DELETE", "/v1/distributors/mouser/key", "clear_mouser_api_key"),
    ("DELETE", "/v1/generic-parts/{generic_part_id}/members/{part_id}", "remove_generic_member"),
    ("DELETE", "/v1/parts/{part_key}", "delete_part"),
    ("DELETE", "/v1/purchase-orders/last", "delete_last_purchase_order"),
    ("DELETE", "/v1/purchase-orders/{po_id}", "delete_purchase_order"),
    ("DELETE", "/v1/purchases/last", "remove_last_purchases"),
    ("DELETE", "/v1/saved-searches/{search_id}", "delete_saved_search"),
    ("DELETE", "/v1/vendors/{vendor_id}", "delete_vendor"),
    ("GET", "/api/health", "legacy_health"),
    ("GET", "/api/parts", "legacy_parts"),
    ("GET", "/v1/distributors/digikey/session", "get_digikey_session"),
    ("GET", "/v1/distributors/mouser/key", "get_mouser_api_key_status"),
    ("GET", "/v1/distributors/{name}/product/{code}", "fetch_distributor_product"),
    ("GET", "/v1/events", "events_stream"),
    ("GET", "/v1/generic-parts", "list_generic_parts"),
    ("GET", "/v1/generic-parts/{generic_part_id}/saved-searches", "list_saved_searches"),
    ("GET", "/v1/health", "health"),
    ("GET", "/v1/import/ocr/available", "ocr_engine_available"),
    ("GET", "/v1/meta", "meta"),
    ("GET", "/v1/parts", "list_parts"),
    ("GET", "/v1/parts/{part_key}/distributors", "get_sourced_distributors"),
    ("GET", "/v1/parts/{part_key}/groups", "get_generic_group_names"),
    ("GET", "/v1/parts/{part_key}/history", "get_part_history"),
    ("GET", "/v1/parts/{part_key}/last-po-quantity", "get_last_po_quantity"),
    ("GET", "/v1/parts/{part_key}/prices", "get_price_summary"),
    ("GET", "/v1/parts/{part_key}/purchase-history", "has_purchase_history"),
    ("GET", "/v1/parts/{part_key}/spec", "extract_spec"),
    ("GET", "/v1/preferences", "load_preferences"),
    ("GET", "/v1/purchase-orders", "list_purchase_orders"),
    ("GET", "/v1/purchase-orders/{po_id}", "get_po_with_items"),
    ("GET", "/v1/purchase-orders/{po_id}/preview", "get_po_source_preview"),
    ("GET", "/v1/purchase-orders/{po_id}/source", "get_po_source"),
    ("GET", "/v1/vendors", "list_vendors"),
    ("GET", "/v1/warnings", "get_warnings"),
    ("PATCH", "/v1/parts/{part_key}", "update_part_fields"),
    ("PATCH", "/v1/purchase-orders/{po_id}", "update_purchase_order"),
    ("POST", "/api/consume", "legacy_consume"),
    ("POST", "/v1/bom/consume", "consume_bom"),
    ("POST", "/v1/bom/resolve-spec", "resolve_bom_spec"),
    ("POST", "/v1/distributors/digikey/cookies/sync", "sync_digikey_cookies"),
    ("POST", "/v1/distributors/digikey/session/validate", "validate_digikey_session"),
    ("POST", "/v1/generic-parts", "create_generic_part"),
    ("POST", "/v1/generic-parts/{generic_part_id}/members", "add_generic_member"),
    ("POST", "/v1/generic-parts/{generic_part_id}/members/{part_id}/exclude", "exclude_generic_member"),
    ("POST", "/v1/generic-parts/{generic_part_id}/saved-searches", "create_saved_search"),
    ("POST", "/v1/import/detect-columns", "detect_columns"),
    ("POST", "/v1/import/match-part", "match_part"),
    ("POST", "/v1/import/ocr", "ocr_overlay"),
    ("POST", "/v1/import/parse", "parse_import_source"),
    ("POST", "/v1/parts/fetch-missing-descriptions", "fetch_missing_descriptions"),
    ("POST", "/v1/parts/{part_key}/adjust", "adjust_part"),
    ("POST", "/v1/parts/{part_key}/fetched-prices", "record_fetched_prices"),
    ("POST", "/v1/pnp/consume", "pnp_consume"),
    ("POST", "/v1/purchase-orders", "create_purchase_order_with_items"),
    ("POST", "/v1/purchases/import", "import_purchases"),
    ("POST", "/v1/scan/sessions", "start_scan_session"),
    ("POST", "/v1/spec/extract", "extract_spec_from_value"),
    ("POST", "/v1/vendors/favicon", "fetch_favicon"),
    ("POST", "/v1/vendors/merge", "merge_vendors"),
    ("PUT", "/v1/distributors/mouser/key", "set_mouser_api_key"),
    ("PUT", "/v1/generic-parts/{generic_part_id}", "update_generic_part"),
    ("PUT", "/v1/generic-parts/{generic_part_id}/members/{part_id}/preferred", "set_preferred_member"),
    ("PUT", "/v1/parts/{part_key}/price", "update_part_price"),
    ("PUT", "/v1/preferences", "save_preferences"),
    ("PUT", "/v1/vendors", "update_vendor"),
]


def _walk_api_routes(routes) -> list[APIRoute]:
    """Recurse into FastAPI's lazy `_IncludedRouter` wrappers down to `APIRoute`s.

    Newer FastAPI (>=0.13x) doesn't flatten `include_router()` calls into
    plain `APIRoute` objects on `app.routes` eagerly — each `include_router`
    call shows up as an opaque `_IncludedRouter` with the real routes nested
    under `.original_router.routes`. Walk that structure to get the concrete
    routes the app actually serves.
    """
    out: list[APIRoute] = []
    for r in routes:
        original_router = getattr(r, "original_router", None)
        if original_router is not None:
            out.extend(_walk_api_routes(original_router.routes))
        elif isinstance(r, APIRoute):
            out.append(r)
    return out


def _live_v1_surface() -> list[tuple[str, str, str]]:
    """Walk the /v1 app's routes (built with a stub api) into (method, path, operation_id).

    Skips HEAD (auto-added alongside GET) and the auto docs/openapi routes
    (`/v1/openapi.json`, `/v1/docs`, `/docs/oauth2-redirect`, `/redoc`), which
    have no `operation_id` and aren't part of the API contract.
    """
    app = create_app(types.SimpleNamespace())
    rows = []
    for route in _walk_api_routes(app.routes):
        if route.operation_id is None:
            continue
        for method in sorted(route.methods or ()):
            if method == "HEAD":
                continue
            rows.append((method, route.path, route.operation_id))
    return sorted(rows)


def test_stub_api_app_builds():
    """create_app must not touch the api object at route-registration time.

    A `types.SimpleNamespace()` has no attributes at all — if any route
    module reached into `api` during `include_router`/decoration (rather than
    lazily inside a handler via `request.app.state.api`), this would raise
    AttributeError before a single request is ever made.
    """
    app = create_app(types.SimpleNamespace())
    assert app.routes


def test_v1_surface_frozen():
    live = set(_live_v1_surface())
    frozen = set(FROZEN_V1_SURFACE)
    assert live == frozen, (
        "/v1 route surface changed (method, path, operation_id).\n"
        f"  ADDED (not in freeze):   {sorted(live - frozen)}\n"
        f"  REMOVED (gone from app): {sorted(frozen - live)}\n"
        "If intentional, update FROZEN_V1_SURFACE (and _NEW_OPERATIONS below "
        "if the operation_id has no bridge equivalent) — and regenerate "
        "docs/openapi-v1.json via `python scripts/gen-openapi.py`."
    )


def test_v1_operation_ids_map_to_bridge_or_are_explicitly_new():
    op_ids = {op for _method, _path, op in FROZEN_V1_SURFACE}
    bridge_names = set(FROZEN_SURFACE)
    unaccounted = op_ids - bridge_names - _NEW_OPERATIONS
    assert not unaccounted, (
        "operation_id(s) neither match a frozen pywebview bridge method name "
        "nor are enumerated in _NEW_OPERATIONS: "
        f"{sorted(unaccounted)}. Add them to _NEW_OPERATIONS with a comment "
        "explaining why there's no bridge equivalent, or fix the operation_id "
        "to match the intended bridge method."
    )
    # Every _NEW_OPERATIONS entry must actually be used — otherwise the
    # allowlist silently rots as routes are renamed/removed.
    stale = _NEW_OPERATIONS - op_ids
    assert not stale, (
        f"_NEW_OPERATIONS contains entries no longer present on the /v1 "
        f"surface: {sorted(stale)}. Remove them."
    )


def test_openapi_schema_builds_with_stub_api():
    """app.openapi() must succeed with a stub api — proves every response_model
    is schema-able without touching real InventoryApi state."""
    app = create_app(types.SimpleNamespace())
    spec = app.openapi()
    assert spec["paths"]
    assert spec["info"]["title"] == "dubIS"


def test_gen_openapi_check_passes():
    """The committed docs/openapi-v1.json snapshot must be fresh.

    Runs the actual guard script as a subprocess (same invocation
    `scripts/verify.sh` uses) rather than importing it, so this test exercises
    the exact CLI contract CI relies on.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen-openapi.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docs/openapi-v1.json is stale — run `python scripts/gen-openapi.py` "
        f"and commit.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
