# Phase 1a — Service Extraction + /v1 API + SSE — Design

**Date:** 2026-07-15
**Parent:** `docs/plans/2026-07-15-platform-architecture-design.md` (Phase 1a section)
**Status:** Approved decisions from owner: **FastAPI + uvicorn**, **SSE** push channel, pydantic schema SSOT, JS client throws on failure. Veto-by-exception granted for spec-level decisions.

## Scope

Build the `/v1` HTTP layer **alongside** the existing pywebview bridge. Nothing user-visible changes in 1a:
- New `server/` package: FastAPI app wrapping the existing `InventoryApi` instance (same object, same `_lock`).
- Full `/v1` endpoint set (disposition table below), structured error contract, `/v1/events` SSE channel.
- `domain/schema.py` migrates to pydantic models (SSOT: OpenAPI comes from FastAPI natively; `gen-inventory-types.py` keeps generating `js/inventory-record.d.ts` from the same models).
- Optional launch: `DUBIS_SERVER_PORT=<port> python app.pyw` (or `python -m server`) starts uvicorn in a daemon thread. Default desktop behavior unchanged until 1b.

**Out of scope (1b):** frontend port, bridge deletion, prefs split enforcement, spawning the server by default, startup-budget gate. **(1c):** auth middleware, docker, tailnet exposure, OpenPnP script cutover, mirror retirement.

## Dependencies

`requirements.txt` += `fastapi`, `uvicorn[standard]`, `pydantic` (v2). `requirements-dev.txt` += the same **plus** `httpx` (FastAPI TestClient) — per the CI trap, every new runtime dep is added to requirements-dev.txt too.

## Architecture

```
app.pyw ──creates──▶ InventoryApi (unchanged, holds _lock, cache, domain facades)
                        ▲                       ▲
        pywebview bridge│(unchanged in 1a)      │ server/  (NEW)
                        │                       │  app.py      create_app(api) → FastAPI
    js/api.js (unchanged│in 1a)                 │  routes/     one module per domain area
                                                │  models.py   request/response pydantic models
                                                │  errors.py   DubISError → HTTP mapping
                                                │  events.py   SSE broker (asyncio queue fan-out)
                                                │  run.py      uvicorn entry (thread or standalone)
```

- Endpoints are **sync `def` functions** (FastAPI runs them in its thread pool) calling the same facade methods the bridge calls; the existing `InventoryApi._lock` serializes as today. No async in domain code.
- The SSE broker bridges sync→async: facades/pnp push events via a thread-safe `events.publish(event, data)`; the `/v1/events` endpoint fans out to connected clients with heartbeat comments every 15s.
- `pnp_server.py` push points (`_scanReceiving`, `_scanReceived`, `_pnpConsume`) additionally call `events.publish` in 1a (evaluate_js stays until 1b): events `scan.receiving`, `scan.received`, `inventory.consumed`. All inventory-mutating facade paths publish `inventory.updated` (payload: `{reason, detail}` — NOT the full inventory; clients re-GET `/v1/parts`).

## Error contract

`server/errors.py` exception handlers map: `ValueError` → 400, `KeyError`/not-found sentinels → 404, `PartRegistryCollisionError` → 409, `DistributorAuthError` → 401(-ish, `code:"distributor_auth"`), `DistributorTimeout` → 504, `DistributorError` → 502, `CacheError` → 500, unhandled → 500. Body always `{"error": <message>, "code": <machine slug>, "detail": <optional dict>}`. No silent catches: handlers log at warning+ and re-serialize the real message.

## Mutation return contract

Mutating endpoints return a small result (`{ok, detail}` or the created/changed entity) and publish `inventory.updated`. To ease the 1b port, any inventory-mutating endpoint accepts `?include=inventory` to also return `"inventory": [...]` (matching today's bridge return). This query param is a transition affordance and is removed at the end of Phase 1b.

## /v1 endpoint disposition (all 76 frozen methods accounted for)

### Carried to /v1 (grouped; exact facade delegation, JSON-string args become typed pydantic bodies)

| Route | Method(s) | Wraps |
|---|---|---|
| `/v1/parts` | GET | `rebuild_inventory`/`_load_organized` (query inventory) |
| `/v1/parts/{part_key}` | PATCH, DELETE | `update_part_fields`, `delete_part` |
| `/v1/parts/{part_key}/price` | PUT | `update_part_price` |
| `/v1/parts/{part_key}/adjust` | POST | `adjust_part` |
| `/v1/parts/{part_key}/history` | GET | `get_part_history` |
| `/v1/parts/{part_key}/prices` | GET | `get_price_summary` (+`get_sourced_distributors`, `get_last_po_quantity` as sub-paths `/distributors`, `/last-po-quantity`) |
| `/v1/parts/{part_key}/purchase-history` | GET | `has_purchase_history` |
| `/v1/parts/{part_key}/groups` | GET | `get_generic_group_names` |
| `/v1/parts/{part_key}/spec` | GET | `extract_spec` |
| `/v1/parts/{part_key}/fetched-prices` | POST | `record_fetched_prices` |
| `/v1/parts/fetch-missing-descriptions` | POST | `fetch_missing_descriptions` |
| `/v1/adjustments/last` | DELETE (`?count=`) | `remove_last_adjustments` |
| `/v1/adjustments/by-source/{source}` | DELETE | `rollback_source` (test infra + future PnP rollback) |
| `/v1/purchases/last` | DELETE (`?count=`) | `remove_last_purchases` |
| `/v1/purchases/import` | POST | `import_purchases` |
| `/v1/bom/consume` | POST | `consume_bom` |
| `/v1/bom/resolve-spec` | POST | `resolve_bom_spec` |
| `/v1/spec/extract` | POST | `extract_spec_from_value` |
| `/v1/generic-parts` | GET, POST | `list_generic_parts`, `create_generic_part` |
| `/v1/generic-parts/{id}` | PUT | `update_generic_part` |
| `/v1/generic-parts/{id}/members` | POST, DELETE | `add_generic_member`, `remove_generic_member` |
| `/v1/generic-parts/{id}/members/{part_id}/exclude` | POST | `exclude_generic_member` |
| `/v1/generic-parts/{id}/members/{part_id}/preferred` | PUT | `set_preferred_member` |
| `/v1/generic-parts/{id}/saved-searches` | GET, POST | `list_saved_searches`, `create_saved_search` |
| `/v1/saved-searches/{search_id}` | DELETE | `delete_saved_search` |
| `/v1/vendors` | GET, POST/PUT | `list_vendors`, `update_vendor` |
| `/v1/vendors/{id}` | DELETE | `delete_vendor` |
| `/v1/vendors/merge` | POST | `merge_vendors` |
| `/v1/vendors/favicon` | POST | `fetch_favicon` |
| `/v1/purchase-orders` | GET, POST | `list_purchase_orders`, `create_purchase_order_with_items` |
| `/v1/purchase-orders/{po_id}` | GET, PATCH, DELETE | `get_po_with_items`, `update_purchase_order`, `delete_purchase_order` |
| `/v1/purchase-orders/last` | DELETE | `delete_last_purchase_order` |
| `/v1/purchase-orders/{po_id}/preview` | GET | `get_po_source_preview` |
| `/v1/warnings` | GET | `get_warnings` |
| `/v1/import/parse` | POST | `parse_source_file_b64` (path-based `parse_source_file` folded in via optional `path` field, server-local only) |
| `/v1/import/ocr` | POST | `ocr_overlay_b64` |
| `/v1/import/ocr/available` | GET | `ocr_engine_available` |
| `/v1/import/match-part` | POST | `match_part` |
| `/v1/import/detect-columns` | POST | `detect_columns` |
| `/v1/scan/sessions` | POST | `start_scan_session` |
| `/v1/scan/upload` | POST (`?s=`) | absorbs pnp_server `_handle_scan_upload` |
| `/v1/scan/page` | GET (`?s=`) | absorbs pnp_server `_handle_scan_page` (mobile HTML) |
| `/v1/distributors/{name}/product/{code}` | GET | `fetch_lcsc_product`/`fetch_digikey_product`/`fetch_mouser_product`/`fetch_pololu_product` (name-dispatched) |
| `/v1/distributors/digikey/session` | GET, DELETE | `check_digikey_session`+`get_digikey_login_status`+`validate_digikey_session` (GET, merged status dict), `logout_digikey` |
| `/v1/distributors/digikey/cookies/sync` | POST | `sync_digikey_cookies` |
| `/v1/distributors/mouser/key` | GET, PUT, DELETE | `get_mouser_api_key_status`, `set_mouser_api_key`, `clear_mouser_api_key` |
| `/v1/pnp/consume` | POST | absorbs pnp_server `/api/consume` (same body `{part_id, qty}`; keep `/api/consume` + `/api/parts` + `/api/health` as legacy aliases on this server so the OpenPnP Jython script works unchanged until 1c cutover) |
| `/v1/preferences` | GET, PUT | `load_preferences`, `save_preferences` (whole-document in 1a; server/client split enforced in 1b) |
| `/v1/health`, `/v1/meta` | GET | liveness; app version + schema version + section order constants (`SECTION_ORDER` etc.) |
| `/v1/events` | GET (SSE) | event stream: `inventory.updated`, `inventory.consumed`, `scan.receiving`, `scan.received` |

### NOT carried to /v1 (client-shell / desktop-only; stay on the bridge until 1b decides their fate)

`open_file_dialog`, `save_file_dialog`, `load_file`, `convert_xls_to_csv` (0 JS callers — fold into load_file at 1b), `set_bom_dirty`, `confirm_close`, `shutdown`, `bench_mark`, `install_tesseract` (runs winget/UAC on the client machine), `start_digikey_login` (opens a local browser — inherently desktop; status/logout ARE in /v1), `open_source_file` (OS open on client machine — 1b: client shell fetches the file via a new `/v1/purchase-orders/{po_id}/source` download route; that route IS added in 1a), `enable_inventory_mirror`/`disable_inventory_mirror`/`get_inventory_mirror_info` (manages the local daemon; revisit at 1c when the mirror retires).

### Naming-collision resolution

The legacy `/api/*` aliases (`/api/consume`, `/api/parts`, `/api/health`, `/scan`) are mounted for OpenPnP/phone compatibility and return the pnp_server-shaped payloads. The mirror's `/api/inventory` shape is NOT replicated — the mirror daemon remains standalone until 1c. `/v1/parts` is the one canonical inventory read going forward.

## Schema SSOT migration

`domain/schema.py` fields become pydantic v2 models (`InventoryItem` et al.). `scripts/gen-inventory-types.py` is updated to read pydantic model fields (same generated `js/inventory-record.d.ts` output — byte-identical target so `--check` guards keep working). FastAPI response models reference the same classes, so `/openapi.json` is generated, and a new guard `scripts/gen-openapi.py --check` snapshots it to `docs/openapi-v1.json` (staleness-guarded like code-map). MCP tool schemas (Phase 2) will be generated from this snapshot.

## Server lifecycle (1a)

- `server/run.py`: `start_server(api, host="127.0.0.1", port)` runs uvicorn in a daemon thread (mirrors `start_pnp_server` pattern); `python -m server --data-dir …` runs standalone (headless: constructs `InventoryApi` without webview — this is the seed of `dubis_headless`'s replacement, but `dubis_headless.py` itself is untouched until 1b).
- `app.pyw`: if `DUBIS_SERVER_PORT` env var set, start the server thread after API construction. Default: off.

## Testing

- FastAPI `TestClient` (httpx) suite per route module under `tests/python/server/`: happy path + error-contract per endpoint class; SSE test via TestClient streaming (receives a published event + heartbeat).
- Contract test `tests/python/server/test_v1_surface.py`: freezes the /v1 route table (path, method, operation id) — the successor to `test_api_surface.py`, which continues to guard the bridge until 1b deletes it.
- Existing suites untouched and must stay green; `verify.sh` unchanged (pytest picks up the new tests automatically). Fixtures regen not expected (no inventory-logic changes) but run per policy.

## Risks

- **Import weight:** fastapi+pydantic import cost is off the default startup path in 1a (env-gated); measured properly in 1b.
- **Threaded facades under uvicorn:** sync endpoints share `InventoryApi._lock` — same serialization as the pnp_server threads today. SSE broker must never publish while holding the lock (publish after facade returns).
- **Windows + uvicorn:** use `loop="asyncio"` default; no signal handlers in thread mode (uvicorn `Server(config).run()` in thread with `install_signal_handlers=False`).
