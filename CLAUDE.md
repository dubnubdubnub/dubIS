# dubIS — Codebase Guide

## Architecture

Desktop app: Python backend + vanilla JS/HTML/CSS frontend, served over HTTP. `app.pyw` spawns the `/v1` FastAPI server (`server/`) on a loopback port, then opens a pywebview window pointed at `splash.html`, which self-navigates to the app once the server answers `/v1/health` — the webview window is just a browser; the app is a client of its own `/v1` API (dogfooding), not a JS-bridge app. The old ~76-method pywebview bridge shrank to a ~9-method client shell (`client_shell.py`) for OS-only concerns (file dialogs, close-confirm, BOM-dirty tracking) that have no HTTP-y shape; everything else — data, mutations, push updates (SSE) — goes over `/v1`.

| Layer | Files |
|-------|-------|
| **Backend** | `app.pyw` (webview launcher; boots `server/` then opens the window at `splash.html`; `.pyw` so Windows uses `pythonw.exe` and skips the console window), `splash.html` (first paint; polls `/v1/health` then self-navigates to `index.html`), `client_shell.py` (the ~9-method pywebview bridge: file dialogs, close-confirm, BOM-dirty — the only surface still exposed to `window.pywebview.api`), `server/` (FastAPI `/v1` app: `server/app.py` composition root, `server/routes/` resource routes, `server/mutations.py` command routes + SSE dual-write publish points, `server/events.py` SSE pub/sub, `server/models.py` Pydantic request/response models), `inventory_api.py` (business-logic facade the server calls into; composition root; surface frozen by `tests/python/test_api_surface.py`), `inventory_ops.py` (merge/adjust/categorize/sort), `csv_io.py` (CSV read/write/migrate), `cache_db.py` (SQLite materialized view), `categorize.py` (part categorization), `spec_extractor.py` (component spec parsing), `distributor_manager.py` (client coordination), `base_client.py` (ABC), `digikey_client.py` + `digikey_cdp.py` + `digikey_normalizer.py`, `lcsc_client.py`, `mouser_client.py`, `pololu_client.py`, `html_product_parser.py` (shared HTML extraction), `pnp_server.py` (OpenPnP HTTP API; pushes to the frontend via SSE only — no more direct `evaluate_js` injection), `file_dialogs.py` (OS file dialogs), `dubis_errors.py` (exception hierarchy); `domain/` (extracted business logic: `domain/inventory.py`, `domain/pricing.py`, `domain/generic_parts.py`, `domain/part_registry.py`, `domain/packages.py` (controlled package/land-pattern vocabulary — does a substitute physically fit), `domain/packaging.py` (carrier vocabulary — reel/tray/tube/cut tape, how it arrives), `domain/attributes.py` + `domain/attribute_parse.py` (distributor parametrics per part × attribute × distributor), `domain/predicates.py` (evaluate substitution requirements against those parametrics — unknown is never a pass), `domain/purchase_candidates.py` (enumerate + rank every purchasable distributor x packaging x price-break for one requirement; a quantity the distributor never quoted has no price), `domain/cart_plan.py` (per-line purchase plan for a cart: `per_board_qty x board_count - on_hand`, then the presets)) |
| **Frontend** | `index.html`, `css/` (split stylesheets: `css/tokens/`, `css/components/`, `css/panels/`, `css/buttons.css`, `css/tables.css`, `css/modals.css`), JS ES modules in `js/` and subdirs (`js/a11y/`, `js/bom/`, `js/group-flyout/`, `js/import/`, `js/inventory/`, `js/vendor/`) — no build step, no framework. `js/api.js` talks to `/v1` over `fetch` (`js/api-map.js`, generated from `docs/openapi-v1.json`, drives URL/verb/body building) for every method it maps; only the handful of client-shell methods still go through `window.pywebview.api`. `js/sse.js` + `js/store.js`'s `scheduleInventoryRefresh()` are the sole inventory re-render path: an `inventory.updated` SSE push (or a direct post-mutation call, sharing the same debounce) triggers a debounced re-fetch — mutation responses no longer carry inventory data. Global UI zoom lives in `js/ui-zoom-logic.js` (pure ladder arithmetic + `scaleRect`), `js/ui-zoom.js` (the `--ui-zoom` property, persistence, and the `innerRect`/`zoomedViewport`/`toInnerPx` coordinate-space seam — see Traps) and `js/ui-zoom-control.js` (the header slider); collapsible panels in `js/panel-collapse-logic.js` (region table + trigger→reopen mapping) and `js/panel-collapse.js` (DOM binding). The cart's purchase plan is server-computed and fetched, never re-ranked in JS: `js/cart/cart-plan-store.js` (one debounced, batched `plan_cart` per cart, with a monotonic sequence guard so a slow earlier response cannot overwrite a newer one) and `js/cart/cart-plan-logic.js` (pure formatting + the row note). A plan is advisory — the stored `qty` stays authoritative because `cart_export.py` reads it, so accepting a recommendation is an explicit write. |
| **Data** | CSV in `data/` — `data/inventory.csv`, `data/purchase_ledger.csv`, `data/adjustments.csv`; events in `events/` — `events/price_observations.csv`, `events/part_events.csv`; config: `data/preferences.json`, `data/constants.json`, `data/pnp_part_map.json`, `data/part_registry.json`, `data/generic_parts.json`, `data/saved_searches.json`, `data/part_attributes.csv` (distributor parametrics per part × attribute × distributor — see `domain/attributes.py`); runtime: `data/.v1_port` (bound `/v1` port, written on server startup, removed on clean shutdown — lets `tools/dubis-cli` discover a running server); SQLite: `cache.db` (deletable, rebuilt from CSVs); entity persistence rules: docs/entity-store.md |

## Data Flow

CSV files are the source of truth. SQLite cache is a derived, deletable materialized view.

```
purchase_ledger.csv + adjustments.csv
        │
        ▼
  inventory_ops.py          (merge dupes → apply adjustments → categorize → sort)
        │
        ▼
  cache_db.py               (populate SQLite: parts + stock tables)
        │                   catch_up() replays only NEW adjustment rows since last checkpoint
        │                   full rebuild if purchase_ledger changes or cache missing
        ▼
  cache.db (SQLite)         (query_inventory() → list[InventoryItem] sent to JS)
        │
        ├── prices table    (populated by domain/pricing.py from events/price_observations.csv)
        ├── part_attributes (populated by domain/attributes.py from data/part_attributes.csv)
        └── generic_parts   (manual state durable in data/generic_parts.json; auto groups regenerated)
```

All inventory-mutating API methods (`adjust_part`, `import_purchases`, `consume_bom`) append to CSVs, then rebuild cache and return fresh `list[InventoryItem]`. The cache can be deleted at any time — it rebuilds on next `rebuild_inventory()`.

## Agent Tooling

Two custom MCP servers are configured in `.mcp.json` at the repo root, plus the inventory CLI. Inventory work goes through the CLI, not an MCP server — see the dubis CLI section below.

### devtools MCP (`tools/dev-tools-mcp/server.py`)

Efficiency tools that reduce token usage by eliminating redundant reads and providing smarter search:

| Tool | What it does |
|------|-------------|
| `symbol_search` | Jumps straight to a function/class/variable definition and returns its full body — no grep-then-read round trip |
| `block_grep` | Like grep but returns the entire enclosing function/block, not just the matching line — also skips the grep-then-read round trip |
| `event_trace` | Finds all emitters and listeners for a named EventBus event across the whole `js/` tree |
| `api_callers` | Finds every JS call site for a Python backend method, handling all calling conventions (string-keyed `api("name", ...)`, legacy `api.name()`, `pywebview.api.name()`) |
| `file_ops` | `mkdir`, `mv`, `cp`, `rm` — batch file operations within the project root |
| `line_edit` | Replace a line range by number (no old-text matching required) — use when you already have line numbers from Grep/Read |
| `multi_edit` | Apply multiple line-range replacements to one file in a single call (applied in reverse order so line numbers stay valid) |

**Worked examples for this repo's most common reverse-mapping tasks:**

```
# Every JS call site of a backend method (string-keyed bridge convention):
api_callers("adjust_part")

# Every emitter + listener for an event (replaces hand-reading the EventBus table):
event_trace("INVENTORY_UPDATED")
```

### ssh MCP (`tools/ssh-mcp/server.py`)

Remote access to the PnP machine and test runner over Tailscale — see `memory/reference_pnp_machine.md` and `memory/reference_ux430_testbox.md` for connection details.

### dubis CLI (`tools/dubis-cli/dubis.py`, launcher `scripts/dubis`)

Every `/v1` route as `dubis <resource> <verb>`, generated from `docs/openapi-v1.json` by `scripts/gen-cli.py` — an HTTP client of `/v1`, never a direct CSV/SQLite reader. Discovers a running server via `DUBIS_URL` → `<data_dir>/.v1_port` (health-checked). It does **not** start one: `dubis serve` does that explicitly.

```bash
scripts/dubis serve                                              # start /v1 (everything else needs it)
scripts/dubis parts list --json                                  # compact single-line JSON
scripts/dubis parts adjust C1000 --adj-type add --quantity 50    # --source defaults to "cli"
scripts/dubis parts adjust C1000 --adj-type add --quantity 50 --dry-run
scripts/dubis schema --json                                      # all 87 commands + their params
```

Path params are positional, everything else is a flag; global flags work before or after the subcommand. Exit codes: `2` bad usage, `3` server or precheck error, `4` no server found. Mutations are tagged `--source` so `dubis adjustments rollback-source <name>` undoes a session.

`parts adjust --adj-type add|remove` against a part that does not exist exits 3 rather than letting `/v1` silently no-op it; `set` creates parts on purpose and is exempt. `--dry-run` never contacts the server, so it will not catch an unknown key. Full detail lives in the generated `.claude/skills/dubis-cli/SKILL.md`.

## EventBus Flow

Events are centralized in `js/event-bus.js`. Store setters that emit are marked; unlisted setters do NOT emit.

| Event | Emitted by | Listened by |
|-------|-----------|-------------|
| `INVENTORY_LOADED` | store.js (`loadInventory`) | inv-events.js, bom-events.js, app-init.js |
| `INVENTORY_UPDATED` | store.js (`onInventoryUpdated`) | inv-events.js, bom-events.js, app-init.js, label-selection.js |
| `BOM_LOADED` | bom-panel.js | inv-events.js, app-init.js |
| `BOM_CLEARED` | bom-events.js | inv-events.js |
| `CONFIRMED_CHANGED` | store.js (`confirmMatch`, `unconfirmMatch`), app-init.js | bom-events.js |
| `LINKING_MODE` | store.js (`setLinkingMode`, `setReverseLinkingMode`) | bom-events.js, inv-events.js |
| `LINKS_CHANGED` | store.js (`addManualLink`), app-init.js | bom-events.js |
| `SAVE_AND_CLOSE` | app-init.js | bom-events.js |
| `FLYOUT_OPENED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_CLOSED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_ACTIVE_CHANGED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_SEARCH_CHANGED` | group-flyout/flyout-events.js | inventory/inv-events.js |
| `VENDORS_CHANGED` | store.js (`setVendors`) | inventory/inv-events.js |
| `PO_CHANGED` | store.js (`setPurchaseOrders`) | inventory/inv-events.js |
| `LABEL_MODE` | label-selection.js | inventory/inv-events.js, label-selection.js |
| `LABEL_SELECTION_CHANGED` | label-selection.js | label-selection.js |
| `LABEL_BULK_SELECTION` | label-selection.js | inventory/inv-events.js |

**Non-emitting setters:** `setInventory()`, `setBomResults()`, `setBomMeta()`, `setBomFootprintNearMisses()`, `setBomDirty()`, `setPreferences()`, `saveInventoryView()`, `loadLinks()`, `clearLinks()` — callers handle emission or don't need it. (`setThreshold()`/`setShortcutPrefs()` propagate via `preferencesSignal`, not EventBus — see the Signals rule below.)

**Change-only emitters:** `setVendors()`/`setPurchaseOrders()` emit `VENDORS_CHANGED`/`PO_CHANGED` **only when the data actually differs**. `onInventoryUpdated()` re-fetches both after *every* inventory mutation — including ones that cannot touch them, e.g. the `record_fetched_prices` write a hover tooltip performs — so an unconditional emit turns a passive hover into a "the user changed a PO" signal, and `js/panel-collapse.js` answers that by force-reopening the collapsed Purchase Import panel. Guarded by `tests/js/panel-reopen-noop-refresh.test.js` (which also forces every new `REOPEN_TRIGGERS` entry to be classified refresh-fed vs one-shot) and `tests/js/e2e/panel-collapse-passive.spec.mjs`. Any future refresh-fed `*_CHANGED` event needs the same treatment.

**Signals vs EventBus:** preferences propagate via `preferencesSignal` in store.js (see `js/signals.js`), not EventBus. Rule: new cross-panel *state* uses signals; EventBus remains for discrete UI *events*.

## Key Policies

- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Test policy**: never use `pytest.skip`, `pytest.importorskip`, or `@pytest.mark.skip` to hide missing dependencies — add them to `requirements-dev.txt` instead. Tests must run, not be skipped.
- **UI clipping tests**: `tests/js/e2e/sticky-buttons.spec.mjs` and `tests/js/e2e/resize-visibility.spec.mjs` verify that action buttons (Adjust, Confirm, Link) are not clipped by panel overflow. Never weaken these tests (e.g., by relaxing tolerances, removing viewport sizes, or switching from individual-button checks to cell-level checks). If a CSS change causes these tests to fail, fix the CSS — the test is catching a real bug.

### Traps

- **WebView2 caches JS:** editing JS and relaunching shows stale code. The app uses a persistent WebView2 profile by default (gated by the `DUBIS_WEBVIEW_PROFILE` check in `app.pyw`). Force fresh behavior: `DUBIS_WEBVIEW_PROFILE=ephemeral python app.pyw`. The origin is now `http://127.0.0.1:<port>` (the `/v1` server's loopback port), not `file://` — the profile cache keys on origin, so this trap and its fix are unchanged, just served over HTTP instead of the filesystem.
- **pywebview second-origin navigation race:** don't navigate a freshly-created pywebview window to a second origin too early (e.g. from Python, right after window creation, before the target server is confirmed up) — it corrupts the JS-bridge callback wiring (symptom: `JavascriptException: <callback> is not a function` on later `evaluate_js`/bridge calls, even though the page itself loads fine). Fixed by having `splash.html` self-navigate (JS-side `location.href` change) only after its own poll confirms `/v1/health`, with a bisected minimum first-poll delay of ≥500ms before that first poll — bisection data lives in `.superpowers/sdd/task-8-report.md`. If you ever see bridge callbacks silently stop working after a window navigation, suspect this race before anything else.
- **Backend change → regenerate fixtures:** after changing Python inventory/columns/prices logic, run `python scripts/generate-test-fixtures.py` or vitest fails with confusing value mismatches. Easiest: just run `bash scripts/verify.sh` (see Testing & Linting).
- **Inventory record field change:** if you add, remove, or rename a field in the inventory record (the dict that `cache_db.query_inventory` sends to JS), edit `domain/schema.py` first, then run `python scripts/gen-inventory-types.py` to update `js/inventory-record.d.ts`. If you forget, `npx tsc --noEmit` (run by CI and verify.sh) will fail with "inventory-record.d.ts is stale".
- **Don't import `js/constants.js` in test setup:** it has a top-level `await fetch` at line 3 that crashes vitest collection; keep `js/ui-helpers.js` store-free and use lazy imports in tests instead.
- **Visual bugs need pixel-truth tests:** geometry-asserts-itself property tests can pass while the visual bug persists. See `docs/visual-testing.md`.
- **Root zoom splits the page into two px spaces:** `html { zoom: var(--ui-zoom) }` (js/ui-zoom.js) means `getBoundingClientRect()`, `event.clientX` and `window.innerWidth` report *post-zoom* px, while `offsetWidth`, `documentElement.clientWidth` and any px you *write* to a style are *authored* px. Mixing them displaces an element by exactly the zoom factor — and looks flawless at 100%, where the two spaces coincide, so it survives any test written without zoom in mind. Positioning code must work entirely in authored px: rects from `innerRect()`, window bounds from `zoomedViewport()`, pointer deltas through `toInnerPx()`. `tests/js/zoom-geometry-guard.test.js` enforces this across all of `js/`.
- **Never author bare `vh`/`vw`:** viewport units resolve against the viewport and are *then* scaled by the root zoom, so `max-height: 80vh` becomes 160% of the window at 200% zoom (modal spills off-screen, its buttons unreachable). Use `calc(80 * var(--vh))` / `var(--vw)` from `css/tokens/scale.css`, which divide the zoom back out, and `height: 100%` for full-height layout.
- **Header additions can push the panels off-screen:** `.header` is `flex-wrap: wrap`, so a new control adds a whole ~28px row at narrow widths and shifts every panel down — enough to shove the Import button off an 800×600 viewport. `tests/js/e2e/resize-visibility.spec.mjs` catches it; the fix is a `max-width: 1199px` media query hiding the control below the app's documented 1200×700 minimum (see `css/components/ui-zoom.css`), not loosening that spec.

## Common Workflows

### After modifying Python backend code
```bash
python scripts/generate-test-fixtures.py   # regenerate JS test fixtures
npx vitest run                              # verify JS tests still pass
pytest tests/python/ -v                     # verify Python tests still pass
```
JS tests use pre-generated JSON fixtures derived from Python backend logic. If you change how inventory loads, how columns are detected, or how prices/distributors work, the fixtures go stale and JS tests fail with confusing mismatches.

If you change the inventory record shape (add/remove/rename a field in `cache_db.query_inventory`):
```bash
# 1. Edit domain/schema.py to match the new field
python scripts/gen-inventory-types.py      # regenerate js/inventory-record.d.ts
npx tsc --noEmit                           # confirm tsc catches any JS read-site mismatches
```
The gen-inventory-types.py --check guard in verify.sh and CI will fail if `js/inventory-record.d.ts` is stale.

### After modifying JS frontend code
```bash
npx eslint js/                              # lint
npx tsc --noEmit                            # type check
npx vitest run                              # unit tests
```
If changing E2E-tested behavior, also run: `npx playwright test`

### After modifying CSS
Run the E2E tests — especially if touching panel layout, overflow, or button positioning:
```bash
npx playwright test sticky-buttons resize-visibility
```

### After modifying distributor clients or normalizers
These tests are opt-in and deselected by default (plain `pytest` and CI never run them). Run them locally before merging changes to `digikey_client.py`, `lcsc_client.py`, `mouser_client.py`, `pololu_client.py`, the normalizers, or `scripts/capture-distributor-fixtures.py`:
```bash
pytest -m live           # hits real endpoints; requires network + local credentials
                         # (cached DigiKey cookies in data/digikey_cookies.json,
                         #  Mouser API key in data/mouser_credentials.json)
```
Missing credentials cause a test failure with an actionable message — they do not skip. If live runs reveal upstream API drift, refresh the committed fixtures and commit the result:
```bash
python scripts/capture-distributor-fixtures.py
git add tests/fixtures/generated/distributor-scrapes.json
```
The public fixtures (LCSC + Pololu) self-refresh weekly via the scheduled `.github/workflows/refresh-fixtures.yml` workflow, which opens a PR. DigiKey + Mouser are local-only (refresh them via `pytest -m live`); their credentials never run on CI.

## Remote deployment (Phase 1c)

`dubis-server` can also run as an always-on remote instance on the k3s cluster (tailnet), instead of (or alongside) the desktop app spawning it locally.

- **Auth** (`server/auth.py`): off by default (`DUBIS_AUTH_MODE=off`, today's loopback behavior, byte-identical). `on` mode resolves identity per request: loopback peer → `local`; `Authorization: Bearer <token>` or `Authorization: Token <token>` (both resolve identically) matching `DUBIS_TOKENS` (`name:token,...`); the `POST /v1/auth/session` cookie; `Tailscale-User-Login` header when `DUBIS_TRUST_TAILSCALE_HEADER=1`, the request's peer IP is in `DUBIS_TRUSTED_PROXY_IPS` (the tailscale operator proxy's pod IP/CIDR — anything else, e.g. another in-cluster pod hitting the ClusterIP directly, can't forge this header), and the login is in `DUBIS_TAILNET_ALLOWLIST`. Fail-safe: trust=1 with `DUBIS_TRUSTED_PROXY_IPS` unset/empty ignores the header entirely (one warning logged, no crash). Non-local identity gets suffixed into mutation `source` (e.g. `mcp@ci`). `/v1/import/parse` (reads server-local files) is loopback-only regardless of auth mode.
- **Deploy**: container image is `Dockerfile` (repo root) + `deploy/` (kustomize: namespace/deployment/service/ingress/pvc/argocd-application). CI (`.github/workflows/build-image.yml`) builds+pushes `ghcr.io/dubnubdubnub/dubis-server:<full-sha>` on merges to `main` (build+push only — no git write-back, since `main` is PR-protected; the built sha is printed to the job summary and pinned manually in `deploy/kustomization.yaml`, or via Argo Image Updater later). Full step-by-step: `docs/deploy-runbook.md`.
- **Remote desktop mode**: set `DUBIS_URL` env or `data/preferences.json`'s `server_url` to point the desktop app at an already-deployed `dubis-server` instead of spawning one locally — `app.pyw` skips the local server boot thread and the webview navigates straight to the remote URL. `tools/dubis-cli` picks up `DUBIS_TOKEN` for its bearer header the same way.
- **Container feature gaps**: the container image is desktop-feature-limited by design — no DigiKey WebView2/CDP scraping, no OS file dialogs, no OCR (tesseract not installed). Those API methods already fail with typed errors when their dependency is unavailable; this is documented, not a bug to fix in-container.

## Testing & Linting

**Before any PR, run the single catch-all command:**
```bash
bash scripts/verify.sh   # or: npm run verify
```
This runs all four staleness guards (fixtures, code-map, manifests, layout-tokens) plus ruff, pytest, eslint, tsc, and vitest, and prints a pass/fail summary. Use the per-change snippets below for faster iteration during development.

```bash
# JavaScript
npx eslint js/ && npx tsc --noEmit && npx vitest run

# Python
ruff check . && pytest tests/python/ -v
```

CI details (suite selection, override tags, troubleshooting, PnP E2E): see `docs/ci-reference.md`.

### Inventory test safety

Adjustments have a `source` field (`"openpnp"`, `"test:<session_id>"`, etc.).
The headless test server (`python -m server --test-source <tag> --rollback-on-exit`)
tags all test adjustments and rolls them back on shutdown.

## Plan Execution

When executing implementation plans, always use subagent-driven dispatch (superpowers:subagent-driven-development). Do not ask which execution approach to use.

## Branch Workflow (for multi-Claude development)

- **Main branch** (`main`): protected, requires PR + passing CI
- **Branch naming**: `claude/<scope>-<description>` (e.g., `claude/refactor-tests`, `claude/feature-bom-export`)
- **Squash-merge** PRs to keep linear history
- **Each Claude instance** works in a separate git worktree
- **Coordination**: via GitHub Issues (labels: `feature`, `refactor`)
- **Before creating a PR**: ensure lint, type check, and tests pass (see Testing & Linting section above)
- **Push via `scripts/push-pr.sh`**: Always use this script to push and create PRs. It automatically detects if your branch's PR was already merged and creates a new branch if needed.
  ```bash
  bash scripts/push-pr.sh                          # PR title = last commit subject
  bash scripts/push-pr.sh --title "fix: the thing" # explicit title
  bash scripts/push-pr.sh --body "Fixes #123"      # explicit body
  ```
  If you forget and push to a merged branch directly, CI will fail with an error telling you to use this script.
- **Verify your worktree matches your task**: Before starting work, check that your current worktree/branch is relevant to the task at hand. If you're on an unrelated branch (e.g., leftover from a previous task), create a new worktree and branch for your current work instead of reusing it.
- **PR your work and watch CI**: When your work is complete, push your branch and create a PR. Then monitor CI (`gh pr checks <number>`) — if any checks fail, diagnose and fix the issues, push again, and keep iterating until all checks pass and the PR is ready to merge. Do not abandon a PR with failing CI.
