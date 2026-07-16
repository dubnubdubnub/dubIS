# Phase 1a — /v1 Service Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FastAPI `/v1` HTTP layer + SSE event channel wrapping the existing `InventoryApi`, running alongside the untouched pywebview bridge, env-gated off by default.

**Architecture:** New `server/` package — `app.py` factory taking an `InventoryApi`, one route module per domain area calling the same facade methods the bridge calls (same `_lock`), `errors.py` exception→HTTP mapping, `events.py` thread-safe SSE broker, pydantic models derived from `domain/schema.py::INVENTORY_FIELDS`. See the design doc for the endpoint disposition table: `docs/plans/2026-07-15-phase1a-service-v1-design.md` (**the plan's route tables below are binding; the design doc is context**).

**Tech Stack:** FastAPI, uvicorn[standard], pydantic v2, httpx (tests only).

## Global Constraints

- The pywebview bridge, `js/api.js`, `pnp_server.py` routes, and `test_api_surface.py` are UNCHANGED in this phase (exception: `pnp_server.py` gains three `events.publish` calls alongside its existing `evaluate_js` pushes — dual-write, nothing removed).
- Every new runtime dep goes in BOTH `requirements.txt` and `requirements-dev.txt` (CI trap).
- Endpoints are sync `def` (FastAPI thread pool); never `async def` for anything touching `InventoryApi`. Never call `events.publish` while holding `api._lock` — publish after the facade call returns.
- Error bodies are always `{"error": str, "code": str, "detail": dict|None}`; throw-don't-swallow; handlers log via `logging`.
- Mutating endpoints publish `inventory.updated` and support `?include=inventory`.
- Route operation_ids are the snake_case frozen-surface method names they wrap (e.g. `adjust_part`) — this is what the /v1 contract test freezes and Phase 2 MCP generation consumes.
- Tests live under `tests/python/server/`; shared fixtures in `tests/python/server/conftest.py`. No pytest.skip ever.
- Before each commit: focused tests, then `python -m pytest tests/python/ -q` and `ruff check .`. Fixture regen only if inventory logic changed (it shouldn't in this phase).
- Work in D:/gehub/dubIS/.claude/worktrees/platform-phase1a, branch `claude/platform-phase1a-service-v1`.

---

### Task 1: Dependencies + server skeleton (app factory, errors, health/meta)

**Files:**
- Modify: `requirements.txt`, `requirements-dev.txt`
- Create: `server/__init__.py`, `server/app.py`, `server/errors.py`, `server/routes/__init__.py`, `server/routes/meta.py`
- Test: `tests/python/server/conftest.py`, `tests/python/server/test_app_skeleton.py`

**Interfaces:**
- Produces: `server.app.create_app(api) -> FastAPI` (api: `InventoryApi`; stored as `app.state.api`); `server.errors.register_handlers(app)`; routes `GET /v1/health` → `{"ok": true}`, `GET /v1/meta` → `{"schema_version", "section_order", "flat_section_order"}`.
- Conftest produces the fixture every later task reuses: `client` — a `fastapi.testclient.TestClient` over `create_app(api)` where `api = InventoryApi(base_dir=tmp_path_data)` seeded with a minimal ledger (reuse the seeding helpers from `tests/python/test_inventory_api_loading.py` / existing conftest patterns — read them first and follow the established fixture style).

- [ ] **Step 1: Add deps**

`requirements.txt` append:
```
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
```
`requirements-dev.txt` append the same three lines plus:
```
httpx>=0.27
```

- [ ] **Step 2: Write failing skeleton test**

`tests/python/server/test_app_skeleton.py`:
```python
"""/v1 app factory skeleton: health, meta, error contract."""


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_meta_exposes_section_order(client):
    r = client.get("/v1/meta")
    body = r.json()
    assert r.status_code == 200
    assert isinstance(body["section_order"], dict) or isinstance(body["section_order"], list)
    assert body["flat_section_order"]


def test_unknown_route_is_structured_404(client):
    r = client.get("/v1/nope")
    assert r.status_code == 404


def test_value_error_maps_to_400(client):
    # adjust with invalid type triggers ValueError in facade — route added in Task 4;
    # here we register a throwaway route to pin the handler mapping itself.
    from dubis_errors import CacheError
    app = client.app

    @app.get("/v1/_test/valueerror")
    def _raise_ve():
        raise ValueError("bad input")

    @app.get("/v1/_test/cacheerror")
    def _raise_ce():
        raise CacheError("cache broken")

    r = client.get("/v1/_test/valueerror")
    assert r.status_code == 400
    assert r.json()["error"] == "bad input"
    assert r.json()["code"] == "value_error"

    r = client.get("/v1/_test/cacheerror")
    assert r.status_code == 500
    assert r.json()["code"] == "cache_error"
```

- [ ] **Step 3: Run to verify failure** — `python -m pytest tests/python/server/ -v` → import errors (no server package / no conftest).

- [ ] **Step 4: Implement**

`server/errors.py`:
```python
"""DubISError → HTTP mapping. Body contract: {"error", "code", "detail"}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dubis_errors import (
    CacheError,
    DistributorAuthError,
    DistributorError,
    DistributorTimeout,
    DubISError,
    PartRegistryCollisionError,
)

logger = logging.getLogger(__name__)

_MAPPING: list[tuple[type[Exception], int, str]] = [
    # order matters: subclasses before bases
    (PartRegistryCollisionError, 409, "part_registry_collision"),
    (DistributorAuthError, 401, "distributor_auth"),
    (DistributorTimeout, 504, "distributor_timeout"),
    (DistributorError, 502, "distributor_error"),
    (CacheError, 500, "cache_error"),
    (DubISError, 500, "dubis_error"),
    (KeyError, 404, "not_found"),
    (ValueError, 400, "value_error"),
]


def _body(exc: Exception, code: str) -> dict:
    return {"error": str(exc) or exc.__class__.__name__, "code": code, "detail": None}


def register_handlers(app: FastAPI) -> None:
    for exc_type, status, code in _MAPPING:
        def handler(request: Request, exc: Exception,
                    _status=status, _code=code):
            logger.warning("/v1 %s -> %s: %s", request.url.path, _code, exc)
            return JSONResponse(status_code=_status, content=_body(exc, _code))
        app.add_exception_handler(exc_type, handler)
```

`server/app.py`:
```python
"""FastAPI app factory for the /v1 service layer.

The app wraps an existing InventoryApi instance (same object the pywebview
bridge uses); endpoints are sync functions so FastAPI's thread pool +
InventoryApi._lock serialize exactly like the bridge and PnP threads today.
"""

from __future__ import annotations

from fastapi import FastAPI

from server.errors import register_handlers


def create_app(api) -> FastAPI:
    app = FastAPI(title="dubIS", version="1", docs_url="/v1/docs",
                  openapi_url="/v1/openapi.json")
    app.state.api = api
    register_handlers(app)

    from server.routes import meta
    app.include_router(meta.router)
    return app
```

`server/routes/meta.py`:
```python
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1", tags=["meta"])


@router.get("/health", operation_id="health")
def health() -> dict:
    return {"ok": True}


@router.get("/meta", operation_id="meta")
def meta(request: Request) -> dict:
    api = request.app.state.api
    import cache_db  # noqa: PLC0415

    return {
        "schema_version": cache_db.SCHEMA_VERSION,
        "section_order": api.SECTION_ORDER,
        "flat_section_order": api.FLAT_SECTION_ORDER,
    }
```

`tests/python/server/conftest.py` — build the `client` fixture: construct `InventoryApi` against a tmp data dir with a small seeded ledger. READ `tests/python/test_inventory_api_loading.py` (and any conftest it uses) first and reuse its construction/seeding helpers — extract into this conftest rather than duplicating; the fixture must yield `TestClient(create_app(api))` and close the api (`api.shutdown()`) on teardown.

- [ ] **Step 5: Run tests** — `python -m pytest tests/python/server/ -v` → PASS; also `ruff check .`.

- [ ] **Step 6: Commit** — `feat(server): /v1 skeleton — app factory, error contract, health/meta`

---

### Task 2: SSE broker + /v1/events

**Files:**
- Create: `server/events.py`, `server/routes/events.py`
- Test: `tests/python/server/test_events.py`

**Interfaces:**
- Produces: `server.events.publish(event: str, data: dict) -> None` (thread-safe, callable from any facade/pnp thread, no-op if no subscribers); `server.events.subscribe() -> queue.Queue` / `unsubscribe(q)`; route `GET /v1/events` streaming `text/event-stream` frames `event: <name>\ndata: <json>\n\n` with `: heartbeat` comment lines every 15s (heartbeat interval module-constant `HEARTBEAT_SECONDS = 15`).
- Event names used later: `inventory.updated`, `inventory.consumed`, `scan.receiving`, `scan.received`.

- [ ] **Step 1: Failing test**

`tests/python/server/test_events.py`:
```python
import json
import threading
import time

from server import events


def test_publish_reaches_subscriber_queue():
    q = events.subscribe()
    try:
        events.publish("inventory.updated", {"reason": "test"})
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data == {"reason": "test"}
    finally:
        events.unsubscribe(q)


def test_publish_without_subscribers_is_noop():
    events.publish("inventory.updated", {"reason": "nobody-listening"})  # must not raise


def test_sse_stream_delivers_event(client):
    received = {}

    def _push_later():
        time.sleep(0.3)
        events.publish("scan.receiving", {"count": 1})

    t = threading.Thread(target=_push_later, daemon=True)
    t.start()
    with client.stream("GET", "/v1/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("event:"):
                received["event"] = line.split(":", 1)[1].strip()
            if line.startswith("data:"):
                received["data"] = json.loads(line.split(":", 1)[1])
                break
    assert received["event"] == "scan.receiving"
    assert received["data"] == {"count": 1}
```

- [ ] **Step 2: Verify failure**, then implement:

`server/events.py`:
```python
"""Thread-safe SSE broker: sync producers (facade/pnp threads) → async consumers.

publish() is safe to call from any thread and MUST be called only after the
facade releases InventoryApi._lock (never while holding it).
"""

from __future__ import annotations

import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15

_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=256)
    with _sub_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(q)


def publish(event: str, data: dict) -> None:
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait((event, data))
        except queue.Full:
            logger.warning("SSE subscriber queue full; dropping %s", event)


def format_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
```

`server/routes/events.py` — `GET /v1/events` (operation_id `events_stream`) returning `StreamingResponse` over a generator that `subscribe()`s, yields `format_frame(*q.get(timeout=HEARTBEAT_SECONDS))`, yields `": heartbeat\n\n"` on `queue.Empty`, and `unsubscribe`s in a `finally`. Register the router in `create_app`. (Sync generator is correct here — FastAPI iterates it in a thread; one thread per connected client, acceptable at desktop scale, noted in the module docstring.)

- [ ] **Step 3: Tests pass; ruff; commit** — `feat(server): SSE event broker + /v1/events stream`

---

### Task 3: Derived pydantic models + parts read routes

**Files:**
- Create: `server/models.py`, `server/routes/parts_read.py`
- Modify: `server/app.py` (include router)
- Test: `tests/python/server/test_models.py`, `tests/python/server/test_parts_read.py`

**Interfaces:**
- Produces: `server.models.InventoryItemModel` (pydantic, derived from `domain.schema.INVENTORY_FIELDS` via `create_model`: ts_type "string"→`str`, "number"→`float` if `isinstance(default, float)` else `int`, "string[]"→`list[str]`; skip `to_js=False`); `server.models.InventoryEnvelope` (`{"inventory": list[InventoryItemModel]}`).
- Routes (all GET, all `response_model` where shape is stable):
  - `/v1/parts` → `{"inventory": api._load_organized()}` — wraps `rebuild_inventory` semantics via the read path; operation_id `list_parts`
  - `/v1/parts/{part_key}/history` → `get_part_history` (op id same)
  - `/v1/parts/{part_key}/prices` → `get_price_summary`
  - `/v1/parts/{part_key}/distributors` → `get_sourced_distributors`
  - `/v1/parts/{part_key}/last-po-quantity` → `get_last_po_quantity` → `{"quantity": int|None}`
  - `/v1/parts/{part_key}/purchase-history` → `has_purchase_history` → `{"has_purchase_history": bool}`
  - `/v1/parts/{part_key}/groups` → `get_generic_group_names` → `{"groups": [str]}`
  - `/v1/parts/{part_key}/spec` → `extract_spec` → `{"spec": dict}`
  - `/v1/warnings` → `get_warnings`
- Route-module pattern (canonical for ALL later route tasks): module defines `router = APIRouter(prefix="/v1", tags=[...])`; each endpoint takes `request: Request`, gets `api = request.app.state.api`, calls the frozen-surface method by its exact name, wraps scalar returns in the documented dict.

- [ ] **Step 1: Failing tests.** `test_models.py`: InventoryItemModel field set == the `to_js` py_keys of INVENTORY_FIELDS (assert `set(InventoryItemModel.model_fields) == {f.py_key for f in INVENTORY_FIELDS if f.to_js}`); qty is int, unit_price float, po_history list[str]. `test_parts_read.py`: GET `/v1/parts` returns 200 with `inventory` list matching the seeded fixture parts; `/history`, `/purchase-history`, `/groups` for a seeded part return expected shapes; unknown part on `/last-po-quantity` returns `{"quantity": None}`.
- [ ] **Step 2: Implement** `models.py` (create_model loop as specified) and `parts_read.py` (pattern above; every endpoint is 3-5 lines).
- [ ] **Step 3: Tests pass; ruff; commit** — `feat(server): derived pydantic models + parts read routes`

---

### Task 4: Inventory mutation routes

**Files:**
- Create: `server/routes/inventory_mut.py`, `server/mutations.py`
- Modify: `server/app.py`
- Test: `tests/python/server/test_inventory_mut.py`

**Interfaces:**
- Produces `server/mutations.py::finish_mutation(api, result, include: str | None, reason: str, detail: dict) -> dict`: builds `{"ok": True, "detail": detail}`, adds `"inventory": result` when `include == "inventory"` and result is the fresh inventory list (facades return it already), then calls `events.publish("inventory.updated", {"reason": reason, "detail": detail})` LAST. Every mutating route in this and later tasks ends with `return finish_mutation(...)`.
- Routes (bodies are pydantic models defined in the same module; JSON-string bridge args become real typed fields):

| Route | op / wraps | Body model fields |
|---|---|---|
| POST `/v1/parts/{part_key}/adjust` | `adjust_part` | `adj_type: Literal["set","add","remove"]`, `quantity: int`, `note: str = ""`, `source: str = ""` |
| PATCH `/v1/parts/{part_key}` | `update_part_fields` | `fields: dict[str, str]` |
| PUT `/v1/parts/{part_key}/price` | `update_part_price` | `unit_price: float | None = None`, `ext_price: float | None = None` |
| DELETE `/v1/parts/{part_key}` | `delete_part` | — |
| POST `/v1/parts/fetch-missing-descriptions` | `fetch_missing_descriptions` | — (returns its summary dict in `detail`) |
| POST `/v1/parts/{part_key}/fetched-prices` | `record_fetched_prices` | `distributor: str`, `price_tiers: list[dict]` |
| POST `/v1/purchases/import` | `import_purchases` | `rows: list[dict[str, str]]` |
| DELETE `/v1/purchases/last?count=` | `remove_last_purchases` | — |
| DELETE `/v1/adjustments/last?count=` | `remove_last_adjustments` | — |
| DELETE `/v1/adjustments/by-source/{source}` | `rollback_source` | — (returns removed rows in `detail.removed`) |
| POST `/v1/bom/consume` | `consume_bom` | `matches: list[dict]`, `board_qty: int`, `bom_name: str`, `note: str = ""`, `source: str = ""` |
| POST `/v1/bom/resolve-spec` | `resolve_bom_spec` | `part_type: str`, `value: float`, `package: str` |
| POST `/v1/spec/extract` | `extract_spec_from_value` | `part_type: str`, `value_str: str`, `package_str: str` |

- [ ] **Step 1: Failing tests** covering at minimum: adjust set/add/remove happy path (assert qty change via follow-up GET `/v1/parts`), adjust publishes `inventory.updated` (subscribe with `events.subscribe()` before the POST, assert queue receives the event after), `?include=inventory` returns the fresh list, PATCH fields, DELETE part with history → 400 (facade raises ValueError), consume_bom end-to-end with a seeded match, rollback_source removes tagged adjustments.
- [ ] **Step 2: Implement**; note `resolve_bom_spec`/`extract_spec_from_value` are read-ops that live here only if convenient — put them in `parts_read.py` instead if cleaner, they do NOT call finish_mutation.
- [ ] **Step 3: Tests pass; full pytest; ruff; commit** — `feat(server): inventory mutation routes + inventory.updated events`

---

### Task 5: Generic parts + saved searches routes

**Files:** Create `server/routes/generic_parts.py`; modify `server/app.py`; test `tests/python/server/test_generic_parts_routes.py`.

Route table (all wrap the identically-named frozen methods; CFG mutations publish `inventory.updated` with `reason:"generic-parts"` — they change flyout state derived data — via `finish_mutation` without `include` support where the facade returns members instead of inventory; return the facade's actual return in `detail`):

| Route | wraps |
|---|---|
| GET `/v1/generic-parts` | `list_generic_parts` |
| POST `/v1/generic-parts` | `create_generic_part` (body: `name, part_type, spec: dict, strictness: dict`) |
| PUT `/v1/generic-parts/{generic_part_id}` | `update_generic_part` |
| POST `/v1/generic-parts/{generic_part_id}/members` | `add_generic_member` (body: `part_id`) |
| DELETE `/v1/generic-parts/{generic_part_id}/members/{part_id}` | `remove_generic_member` |
| POST `/v1/generic-parts/{generic_part_id}/members/{part_id}/exclude` | `exclude_generic_member` |
| PUT `/v1/generic-parts/{generic_part_id}/members/{part_id}/preferred` | `set_preferred_member` |
| GET `/v1/generic-parts/{generic_part_id}/saved-searches` | `list_saved_searches` |
| POST `/v1/generic-parts/{generic_part_id}/saved-searches` | `create_saved_search` (body: `name, tag_state: dict, search_text, frozen_members: list`) |
| DELETE `/v1/saved-searches/{search_id}` | `delete_saved_search` |

TDD steps as in Task 3/4 pattern (failing tests → implement → pass → commit `feat(server): generic-parts + saved-searches routes`). Tests must cover create→add member→exclude→preferred→list roundtrip against the seeded fixture.

---

### Task 6: Vendors + purchase orders routes

**Files:** Create `server/routes/vendors_pos.py`; modify `server/app.py`; test `tests/python/server/test_vendors_pos_routes.py`.

| Route | wraps | notes |
|---|---|---|
| GET `/v1/vendors` | `list_vendors` | |
| PUT `/v1/vendors` | `update_vendor` (body: `vendor_id="", name="", url="", favicon_path=""`) | CFG |
| DELETE `/v1/vendors/{vendor_id}` | `delete_vendor` | INV mutation → finish_mutation |
| POST `/v1/vendors/merge` | `merge_vendors` (body: `src_id, dst_id`) | INV mutation |
| POST `/v1/vendors/favicon` | `fetch_favicon` (body: `url`) | network |
| GET `/v1/purchase-orders` | `list_purchase_orders` | |
| POST `/v1/purchase-orders` | `create_purchase_order_with_items` (body: `vendor_id, source_file_b64="", source_file_name="", purchase_date="", notes="", line_items: list[dict]`) | INV mutation |
| GET `/v1/purchase-orders/{po_id}` | `get_po_with_items` | |
| PATCH `/v1/purchase-orders/{po_id}` | `update_purchase_order` | INV mutation |
| DELETE `/v1/purchase-orders/{po_id}` | `delete_purchase_order` | INV mutation |
| DELETE `/v1/purchase-orders/last` | `delete_last_purchase_order` | INV mutation; register BEFORE `/{po_id}` route (path precedence) |
| GET `/v1/purchase-orders/{po_id}/preview` | `get_po_source_preview` | |
| GET `/v1/purchase-orders/{po_id}/source` | NEW: streams the archived PO source file (`FileResponse`; 404 if none) — replaces client-side `open_source_file` for remote clients | read the archive-path logic from `domain/api_purchase_orders.py` and reuse the same resolution helper |

TDD as before; commit `feat(server): vendors + purchase-orders routes`.

---

### Task 7: Import/OCR, scan absorption, distributors, PnP consume + legacy aliases

**Files:** Create `server/routes/import_scan.py`, `server/routes/distributors.py`, `server/routes/pnp.py`; modify `server/app.py`, `pnp_server.py` (add `events.publish` dual-writes only); test `tests/python/server/test_import_scan_routes.py`, `tests/python/server/test_distributors_routes.py`, `tests/python/server/test_pnp_routes.py`.

**import_scan.py:**
| Route | wraps |
|---|---|
| POST `/v1/import/parse` | `parse_source_file_b64` (body: `file_b64, file_name, template="generic"`; optional `path` field → `parse_source_file` for server-local files) |
| POST `/v1/import/ocr` | `ocr_overlay_b64` |
| GET `/v1/import/ocr/available` | `ocr_engine_available` → `{"available": bool}` |
| POST `/v1/import/match-part` | `match_part` (body: `mpn, manufacturer=""`) |
| POST `/v1/import/detect-columns` | `detect_columns` (body: `headers: list[str]`) |
| POST `/v1/scan/sessions` | `start_scan_session` (body: `template="generic"`) — requires the pnp server running; if `api._pnp_server` is None return 409 `{"code":"pnp_server_unavailable"}` |

**distributors.py:**
| Route | wraps |
|---|---|
| GET `/v1/distributors/{name}/product/{code}` | dispatch: `{"lcsc": fetch_lcsc_product, "digikey": fetch_digikey_product, "mouser": fetch_mouser_product, "pololu": fetch_pololu_product}`; unknown name → 404; None result → 404 `{"code":"product_not_found"}` |
| GET `/v1/distributors/digikey/session` | merged dict of `check_digikey_session` + `get_digikey_login_status` |
| DELETE `/v1/distributors/digikey/session` | `logout_digikey` |
| POST `/v1/distributors/digikey/session/validate` | `validate_digikey_session` |
| POST `/v1/distributors/digikey/cookies/sync` | `sync_digikey_cookies` |
| GET/PUT/DELETE `/v1/distributors/mouser/key` | `get_mouser_api_key_status` / `set_mouser_api_key` (body: `key`) / `clear_mouser_api_key` |

Distributor tests use monkeypatched DistributorManager methods (do NOT hit network; follow the mocking style in existing `tests/python/test_distributor_manager.py` — read it first).

**pnp.py:**
| Route | behavior |
|---|---|
| POST `/v1/pnp/consume` | body `{part_id, qty}` — same resolution+adjust flow as `pnp_server.do_POST` `/api/consume` (reuse `pnp_part_map` resolution helper, call `api.adjust_part("remove", key, qty, "OpenPnP placement", source="openpnp")`), publish `inventory.consumed` `{part_id, part_key, qty, new_qty}` + `inventory.updated`, return `{ok, part_key, new_qty}` |
| GET `/api/parts`, `/api/health`, POST `/api/consume` | legacy aliases (no `/v1` prefix) mounted on the same app returning pnp_server-shaped payloads, so OpenPnP's Jython script can point at this server unchanged at 1c cutover |

**pnp_server.py dual-write:** immediately after each of the three `evaluate_js` push points (`_scanReceiving`, `_scanReceived`, `_pnpConsume`), add `server.events.publish(...)` with events `scan.receiving` / `scan.received` / `inventory.consumed` and the payloads from the design doc. Import guarded at module top (`from server import events as sse_events`) — server package has no heavy imports at module level besides fastapi; if import cost is a concern move the import inside the handler. Existing pnp_server tests must stay green.

TDD as before; commit `feat(server): import/scan/distributor/pnp routes + legacy aliases + event dual-write`.

---

### Task 8: Preferences routes + server lifecycle (thread start, standalone entry, app.pyw gate)

**Files:** Create `server/routes/preferences.py`, `server/run.py`, `server/__main__.py`; modify `server/app.py`, `app.pyw`; test `tests/python/server/test_lifecycle.py`, `tests/python/server/test_preferences_routes.py`.

- `preferences.py`: GET `/v1/preferences` → `load_preferences`; PUT `/v1/preferences` (body: free-form dict) → `save_preferences`.
- `run.py`:
```python
"""Run the /v1 server: in-process daemon thread (desktop) or standalone."""

from __future__ import annotations

import threading

import uvicorn

from server.app import create_app


def start_server(api, host: str = "127.0.0.1", port: int = 7891) -> "uvicorn.Server":
    config = uvicorn.Config(create_app(api), host=host, port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dubis-v1-server", daemon=True)
    thread.start()
    return server


def stop_server(server: "uvicorn.Server") -> None:
    server.should_exit = True
```
  (uvicorn's `Server.run` installs no signal handlers when not on the main thread — verify on Windows in the test; if it raises, pass `config` with `install_signal_handlers=False` equivalent for the installed uvicorn version and note it.)
- `__main__.py`: argparse `--data-dir`, `--host`, `--port`; constructs `InventoryApi` headlessly (no webview) exactly the way `dubis_headless.py` does (read it and reuse its construction call), runs uvicorn in the foreground.
- `app.pyw`: after `api = InventoryApi(debug=debug)`, add:
```python
    v1_port = os.environ.get("DUBIS_SERVER_PORT")
    v1_server = None
    if v1_port:
        from server.run import start_server
        v1_server = start_server(api, port=int(v1_port))
```
  and in the teardown path (near `stop_pnp_server`), `if v1_server: from server.run import stop_server; stop_server(v1_server)`.
- `test_lifecycle.py`: start_server on port 0 is not supported by this pattern — instead pick a free port via `socket`, start, poll `GET /v1/health` with httpx until 200 (timeout 10s), assert response, stop_server, assert port closes. This is the one test using a real uvicorn thread (all other tests use TestClient).

TDD; commit `feat(server): preferences routes + lifecycle (thread mode, standalone entry, app.pyw env gate)`.

---

### Task 9: /v1 contract test + OpenAPI snapshot guard

**Files:** Create `tests/python/server/test_v1_surface.py`, `scripts/gen-openapi.py`, `docs/openapi-v1.json` (generated); modify `scripts/verify.sh`.

- `test_v1_surface.py`: builds `create_app` with a minimal api, walks `app.routes`, and asserts the frozen set of `(method, path, operation_id)` triples — write the frozen list explicitly in the test (generate it once by running the walker and pasting; the point is that route changes require touching the freeze, exactly like `test_api_surface.py`). Also assert: every mutating route's operation_id matches a name in `test_api_surface.FROZEN_SURFACE` (import it) OR is in the explicit `_NEW_OPERATIONS` allowlist (`{"list_parts", "events_stream", "health", "meta", "pnp_consume", "po_source", "scan_upload", "scan_page", ...}` — enumerate exactly what exists).
- `scripts/gen-openapi.py`: builds the app with a stub api object (`create_app` must not touch the api at import/route-registration time — only inside handlers; use `types.SimpleNamespace()` as the stub), dumps `app.openapi()` JSON (sorted keys, indent 2) to `docs/openapi-v1.json`; `--check` mode diffs and exits 1 with regen instructions (mirror `gen-inventory-types.py --check` exactly).
- `verify.sh`: add after the `claude-md` step:
```bash
# 4c. openapi
run_step "openapi" "$PY" scripts/gen-openapi.py --check
```

TDD; commit `feat(server): freeze /v1 surface + OpenAPI snapshot guard`.

---

### Task 10: Full verification + PR

- [ ] `bash scripts/verify.sh` → all gates green (now includes `openapi`).
- [ ] `bash scripts/push-pr.sh --title "feat(server): /v1 service layer — FastAPI + SSE alongside the bridge (Phase 1a)" --body "<summarize: scope per docs/plans/2026-07-15-phase1a-service-v1-design.md; bridge untouched; env-gated off by default>"` (append the standard generated-with footer).
- [ ] `gh pr checks <n>` to green; fix and iterate; merge per repo convention.
