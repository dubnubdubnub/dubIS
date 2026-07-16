# Phase 1b — Frontend Port to /v1 + Bridge Deletion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desktop app becomes a browser against the loopback /v1 server; pywebview bridge shrinks to a ~9-method client shell; Python→JS pushes move to SSE; `dubis_headless.py`/`tests/e2e-server.py` deleted.

**Architecture:** per `docs/plans/2026-07-16-phase1b-frontend-port-design.md` (binding). Frontend keeps the `api(method, ...args)` convention backed by a GENERATED method→route map (`scripts/gen-api-client.py` reading `docs/openapi-v1.json` → `js/api-map.js`, staleness-guarded). Two-step mutation convention: port with `?include=inventory` first, flip to SSE-driven re-fetch before phase close.

**Tech Stack:** existing (FastAPI/pydantic server side; vanilla JS ESM; Playwright/vitest).

## Global Constraints

- Never weaken `sticky-buttons.spec.mjs`/`resize-visibility.spec.mjs`. Playwright tests use realistic interactions only (no dispatchEvent/force:true).
- `api()`'s failure contract is sacred until the phase-close flip: catch → `AppLog.error(method+": "+msg)` + toast + return `undefined`.
- Every generated artifact gets a `--check` staleness guard wired into `scripts/verify.sh` and regenerated in the producing task.
- After backend changes: `python scripts/generate-test-fixtures.py`. After JS changes: `npx eslint js/ && npx tsc --noEmit && npx vitest run --project core`. Per-task gates as in each task; full `bash scripts/verify.sh` in the final task.
- Worktree D:/gehub/dubIS/.claude/worktrees/platform-phase1b, branch `claude/platform-phase1b-frontend-port`. TDD failing-first wherever a behavior is specified.
- Frozen JS globals that E2E depends on stay: `window.store`, `window.EventBus/Events/processBOM/matchBOM`, `window._scanReceived`/`_scanReceiving` (redefined as SSE-fed, same signature).

---

### Task 1: Spike — static serving + WebView2 loopback verification

**Files:** Modify `server/app.py` (static mount); Create `tests/python/server/test_static_serving.py`; Create `scripts/spike-webview-loopback.py` (manual spike, committed for reproducibility).

**Interfaces:** `create_app(api, static_dir: str | None = None)` — when static_dir given, mounts `StaticFiles(directory=static_dir, html=True)` at `/` AFTER all routers (API precedence). `GET /` returns index.html; `GET /js/api.js` returns the module; `/v1/*` unaffected.

- [ ] Failing tests: static index served with correct content-type; `/v1/health` still wins over static; no static_dir → 404 on `/` (current behavior).
- [ ] Implement the mount (guard: only mount if dir exists; `html=True`).
- [ ] Spike script: starts `start_server(api, static_dir=repo root)` on a free port, opens a pywebview window at the loopback URL with NO js_api, page runs `fetch('/v1/health')` + `new EventSource('/v1/events')` and paints results into the DOM; script asserts via `evaluate_js` that both succeeded, then closes. Run it once on this Windows machine (`python scripts/spike-webview-loopback.py`); paste the output into the commit message. If EventSource fails in WebView2, STOP and report BLOCKED — the SSE decision needs revisiting.
- [ ] `pytest tests/python/server/ -q`, `ruff check .`; commit `feat(server): static frontend serving + webview loopback spike`.

### Task 2: API-client generator + js/api.js HTTP transport

**Files:** Create `scripts/gen-api-client.py`, `js/api-map.js` (generated), `tests/python/test_gen_api_client.py`, `tests/js/api-client.test.js`; Modify `js/api.js`, `scripts/verify.sh` (new `api-client` run_step after `openapi`).

**Interfaces:**
- `scripts/gen-api-client.py`: reads `docs/openapi-v1.json`; for every operation emits an entry `{method: {verb, path, pathParams: [name...], queryParams: [name...], bodyParams: [name...], unwrap: str|null}}` into `js/api-map.js` (`export const API_MAP = {...}` + header comment, sorted keys, `--check` mode mirroring gen-openapi.py). Parameter ORDER comes from a hand-maintained `ARG_ORDER` dict in the generator for operations whose positional bridge order differs from (pathParams + bodyParams alphabetical) — seed it by copying each frozen signature from `tests/python/test_api_surface.py` (the old file, still present until Task 10) — the generator ASSERTS every /v1 operation is either order-derivable or in ARG_ORDER (fail loud on new routes).
- `unwrap` values: `"inventory"` (list_parts + all `?include=inventory` mutations), `"quantity"`, `"groups"`, `"has_purchase_history"`, `"spec"`, `"match"`, `"path"`, null (identity). Special entries: `rebuild_inventory` aliases `list_parts` GET with unwrap inventory; `check_digikey_session`/`get_digikey_login_status` both alias `get_digikey_session`; `fetch_lcsc_product`/`fetch_digikey_product`/`fetch_mouser_product`/`fetch_pololu_product` alias `fetch_distributor_product` with a fixed `name` path param; `parse_source_file`/`parse_source_file_b64` alias `parse_import_source`; `ocr_overlay_b64` aliases `ocr_overlay`.
- `js/api.js` rewrite: `api(method, ...args)` → if `API_MAP[method]` missing → legacy shim path `window.pywebview.api[method](...args)` (dialog/window methods); else build URL (encodeURIComponent path params), query (`include=inventory` automatically appended for ops marked `mutating: true` in the map — a generator flag derived from verb != GET — DURING STEP-1 CONVENTION ONLY, controlled by a single `INCLUDE_INVENTORY = true` const in api.js), JSON body, `fetch`, on `!res.ok` parse `{error}` body and throw, then the existing catch does log+toast+undefined; on ok parse JSON and apply `unwrap`. `whenPywebviewReady()` unchanged for now.
- vitest `tests/js/api-client.test.js`: table-driven — for a representative op of each class (GET path-param, POST body, DELETE query, unwrap scalar, distributor alias, mutation include=inventory) assert exact URL/verb/body assembled (mock global fetch); assert failure contract (non-ok → undefined + AppLog entry).

- [ ] TDD: vitest cases first (they import the generated map — generate it in the same task, guard test after).
- [ ] Wire verify.sh step; `npx vitest run --project core`, `npx eslint js/`, `npx tsc --noEmit`, `python -m pytest tests/python/ -q`, `ruff check .`; commit `feat(js): generated /v1 api-map + HTTP transport in api.js (bridge fallback retained)`.

**NOTE:** after this task the app transparently uses HTTP for every mapped method WHEN SERVED FROM THE SERVER, and still works under file://+bridge (fallback) — the fallback is removed in Task 10. E2E mocks still intercept `window.pywebview` and still pass because Playwright serves via `serve-static.mjs` without a /v1 backend… **verify**: with API_MAP present, mocked-bridge specs would break (api() prefers HTTP). Mitigation inside this task: `api()` prefers HTTP only when `window.__DUBIS_HTTP__ !== false` and a module-load probe (`fetch('/v1/health', {method:'GET'}).ok` cached promise) succeeds — under serve-static.mjs there is no /v1, probe fails, transport falls back to bridge, existing mocks keep passing until Task 8 migrates them. The probe result must be awaited inside api() (memoized), NOT at module top level (vitest collection trap: no top-level await fetch).

### Task 3: SSE client module + scan push migration

**Files:** Create `js/sse.js`, `tests/js/sse.test.js`; Modify `js/store.js` (EventSource wiring behind a feature check), `js/import/mfg-direct/mfg-direct-panel.js` (subscribe scan events), `js/app-init.js` (_pnpConsume path).

**Interfaces:** `js/sse.js` exports `connectEvents(baseUrl="")` → opens `EventSource('/v1/events')`, dispatches to registered handlers `onEvent(name, fn)`; auto-reconnect is native. Store wires: `inventory.updated` → debounced (250ms trailing) `loadInventoryQuiet()` (new store fn: `GET /v1/parts` unwrap → `onInventoryUpdated`) — ACTIVE ONLY when `INCLUDE_INVENTORY === false` (phase-close flip) to avoid double render; until then the handler is registered but gated. `scan.receiving`/`scan.received` → call the SAME functions `_scanReceiving`/`_scanReceived` currently assigned to window (keep the window globals assigned — E2E uses them). `inventory.consumed` → toast + (gated) refresh. `connectEvents()` is called from app-init only when the HTTP probe succeeded.
vitest: mock EventSource; assert dispatch, debounce, gating.

- [ ] TDD; JS gates; commit `feat(js): SSE client — scan/consume/inventory events (gated refresh)`.

### Tasks 4–7: Panel-by-panel call-site normalization (mostly semantic diffs; the map does the transport)

Each task: read the listed modules; fix any call whose /v1 shape differs from bridge shape and isn't covered by `unwrap` (add unwrap entries/aliases to the generator where systematic); migrate that panel's mocked-bridge E2E specs to the shared route-mock helper (created in Task 4); run that panel's Playwright specs + vitest + eslint/tsc; commit per task.

- **Task 4:** `tests/js/e2e/route-mocks.mjs` shared helper (page.route('**/v1/**') fixture server mirroring addMockSetup's data; `window.__apiCalls` recording preserved; shim methods remain pywebview mocks) + port preferences-modal.js, vendors-modal.js, part-preview.js, label-selection.js, label-export-modal.js (+ their specs). Commit `feat(js): port preferences/vendors/labels/part-preview panels to /v1 mocks`.
- **Task 5:** import-panel.js, mfg-direct-panel.js (parse/ocr/match/scan session + get_po_with_items), group-flyout (flyout-events/drag/panel saved-search + member ops) + specs. NOTE `get_saved_search` at flyout-events.js:258 — verify this method exists on /v1 (survey shows only list/create/delete; if the bridge has `get_saved_search` the 1a surface may have missed it — check `test_api_surface.py`; if missing, add the /v1 route + regen openapi/api-map in this task). Commit `feat(js): port import + group-flyout panels`.
- **Task 6:** BOM panel (bom-events.js, bom-panel.js): consume_bom/remove_last_adjustments via HTTP; dialogs (`open_file_dialog`/`save_file_dialog`/`load_file`/`set_bom_dirty`/`confirm_close`) stay on the shim path (they're not in API_MAP — confirm fallback works) + specs. Commit `feat(js): port BOM panel`.
- **Task 7:** inventory cluster: inventory-modals.js (incl. the raw-bridge distributor fetch at :336 → raw `fetch('/v1/distributors/...')` preserving no-toast semantics), inv-inline-edit.js, inv-mutations.js, fetch-descriptions-command.js, vendor-flyout.js + specs (adjust/price/delete flows). Commit `feat(js): port inventory cluster`.

### Task 8: Bootstrap port + client shell + app.pyw loopback

**Files:** Create `client_shell.py`, `tests/python/test_client_shell.py`; Modify `app.pyw`, `js/app-init.js`, `js/store.js`, `js/api.js` (`whenPywebviewReady` probes shim), `tests/python/test_api_surface.py` (refreeze to shim surface — the OLD frozen list moves into a comment tombstone referencing /v1's contract test).

**Interfaces:** `ClientShell(window_getter, api)` exposing exactly: `open_file_dialog`, `save_file_dialog`, `load_file`, `confirm_close`, `set_bom_dirty`, `start_digikey_login`, `open_source_file`, `install_tesseract`, `bench_mark` — each delegating to the existing implementations (`file_dialogs.py`, InventoryApi methods). `app.pyw`: always `start_server(api, static_dir=APP_DIR, port=free_or_env)`, poll started, `create_window(url=http://127.0.0.1:{port}/, js_api=shell)`; on_closing reads `shell._bom_dirty`/`shell._force_close`. app-init: `connectEvents()` after readiness; bench marks via shim. Bench before/after: run the DUBIS_BENCH_OUT harness on this machine, paste numbers in the commit message; acceptance = skeleton/window timing within noise of baseline (responsiveness criterion; readiness may lag with the existing splash).

- [ ] TDD python-side (shell surface freeze + delegation tests); JS gates; manual bench run; commit `feat(app): desktop = browser on loopback /v1; bridge shrinks to 9-method client shell`.

### Task 9: Live E2E + PnP-E2E migration; delete e2e-server.py and dubis_headless.py

**Files:** Modify `server/__main__.py` (add `--test-source`, `--rollback-on-exit` — port semantics from dubis_headless.py before deleting it), `tests/js/e2e/live/global-setup.mjs`, `tests/js/e2e/live/setup-page.mjs` (delete the Proxy; live tests hit /v1 directly; MOCKED dialog list stays as pywebview mocks), `tests/pnp-e2e/*` (spawn `python -m server` instead of dubis_headless; legacy /api aliases keep the OpenPnP scripts working), `tests/python/test_scan_session.py` (assert SSE publish instead of evaluate_js string); Delete `tests/e2e-server.py`, `tests/pnp-e2e/dubis_headless.py`.

- [ ] Port flags with tests (rollback-on-exit: tagged adjustments removed on SIGTERM/atexit — reuse rollback_source); migrate configs; run `npx playwright test --project live` and the pnp-e2e suite locally where possible (pnp-e2e cross-compute is CI-only — ensure the python-side unit tests cover the new spawn path); commit `feat(test): live/PnP E2E on python -m server; delete e2e-server.py + dubis_headless.py`.

### Task 10: The flip + deletions + full verification

**Files:** Modify `js/api.js` (`INCLUDE_INVENTORY = false`, delete bridge fallback for mapped methods + HTTP probe — HTTP is now unconditional), `js/store.js` (mutation call sites stop consuming returns; SSE refresh ungated), `server/mutations.py` (remove `?include=inventory`), `pnp_server.py` (delete the three evaluate_js pushes; SSE only), `inventory_api.py` (remove `convert_xls_to_csv`), regenerate openapi + api-map + fixtures + code-map; CLAUDE.md updates (architecture table: bridge → client shell + /v1; traps: WebView2 JS caching note now also covers the loopback origin).

- [ ] Flip, delete, regen; ALL panel specs + `npx playwright test` (functional; sticky-buttons/resize-visibility explicitly); `bash scripts/verify.sh` full PASS.
- [ ] `bash scripts/push-pr.sh --title "feat(app): frontend on /v1 — bridge deleted, desktop = loopback browser (Phase 1b)"` with a body summarizing per the spec; watch CI to green; merge per repo convention.

## Self-review checklist appendix (for the executing controller)

- Task 2's HTTP-probe fallback is the load-bearing compatibility trick — if any panel task (4-7) finds specs failing because the probe passed under serve-static (it shouldn't — no /v1 there), stop and fix the probe, don't migrate that spec early.
- `get_saved_search` (Task 5 note) is a suspected 1a surface gap — resolve explicitly.
- Watch cumulative api-map drift: every generator change regenerates `js/api-map.js` AND `docs/openapi-v1.json` stays untouched (map derives from it, never the reverse).
