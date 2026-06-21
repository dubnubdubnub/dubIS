# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.pyw` (webview launcher; `.pyw` so Windows uses `pythonw.exe` and skips the console window), `inventory_api.py` (API facade, 74 methods), `inventory_ops.py` (merge/adjust/categorize/sort), `csv_io.py` (CSV read/write/migrate), `cache_db.py` (SQLite materialized view), `categorize.py` (part categorization), `generic_parts.py` (generic part CRUD + BOM resolution), `spec_extractor.py` (component spec parsing), `price_ops.py` + `price_history.py` (pricing), `distributor_manager.py` (client coordination), `base_client.py` (ABC), `digikey_client.py` + `digikey_cdp.py` + `digikey_normalizer.py`, `lcsc_client.py`, `mouser_client.py`, `pololu_client.py`, `html_product_parser.py` (shared HTML extraction), `pnp_server.py` (OpenPnP HTTP API), `file_dialogs.py` (OS file dialogs), `dubis_errors.py` (exception hierarchy); `domain/` (extracted business logic: `inventory.py`, `pricing.py`, `generic_parts.py`) |
| **Frontend** | `index.html`, `css/styles.css`, 73 JS ES modules in `js/` and subdirs (`a11y/`, `bom/`, `group-flyout/`, `import/`, `inventory/`, `vendor/`) — no build step, no framework |
| **Data** | CSV in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`; events in `events/` — `price_observations.csv`, `part_events.csv`; config: `preferences.json`, `constants.json`, `pnp_part_map.json`; SQLite: `cache.db` (deletable, rebuilt from CSVs) |

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
        ├── prices table    (populated by price_history.py from events/price_observations.csv)
        └── generic_parts   (populated by generic_parts.py, stored directly in SQLite)
```

All inventory-mutating API methods (`adjust_part`, `import_purchases`, `consume_bom`) append to CSVs, then rebuild cache and return fresh `list[InventoryItem]`. The cache can be deleted at any time — it rebuilds on next `rebuild_inventory()`.

## Agent Tooling

Two custom MCP servers are configured in `.mcp.json` at the repo root:

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

## EventBus Flow

Events are centralized in `event-bus.js`. Store setters that emit are marked; unlisted setters do NOT emit.

| Event | Emitted by | Listened by |
|-------|-----------|-------------|
| `INVENTORY_LOADED` | store.js (`loadInventory`) | inv-events.js, bom-events.js, app-init.js |
| `INVENTORY_UPDATED` | store.js (`onInventoryUpdated`) | inv-events.js, bom-events.js, app-init.js |
| `BOM_LOADED` | bom-panel.js | inv-events.js, app-init.js |
| `BOM_CLEARED` | bom-events.js | inv-events.js |
| `PREFS_CHANGED` | store.js (`setThreshold`), preferences-modal.js | inv-events.js |
| `CONFIRMED_CHANGED` | store.js (`confirmMatch`, `unconfirmMatch`), app-init.js | bom-events.js |
| `LINKING_MODE` | store.js (`setLinkingMode`, `setReverseLinkingMode`) | bom-events.js, inv-events.js |
| `LINKS_CHANGED` | store.js (`addManualLink`), app-init.js | bom-events.js |
| `SAVE_AND_CLOSE` | app-init.js | bom-events.js |
| `GENERIC_PARTS_LOADED` | store.js (`loadInventory`), generic-parts-modal.js | (none currently) |
| `FLYOUT_OPENED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_CLOSED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_ACTIVE_CHANGED` | group-flyout/flyout-panel.js | inventory/inv-events.js |
| `FLYOUT_SEARCH_CHANGED` | group-flyout/flyout-events.js | inventory/inv-events.js |
| `VENDORS_CHANGED` | store.js (`setVendors`) | inventory/inv-events.js |
| `PO_CHANGED` | store.js (`setPurchaseOrders`) | inventory/inv-events.js |
| `LABEL_MODE` | label-selection.js | inventory/inv-events.js, label-selection.js |
| `LABEL_SELECTION_CHANGED` | label-selection.js | label-selection.js |
| `LABEL_BULK_SELECTION` | label-selection.js | inventory/inv-events.js |

**Non-emitting setters:** `setInventory()`, `setBomResults()`, `setBomMeta()`, `setBomDirty()`, `setPreferences()`, `loadLinks()`, `clearLinks()` — callers handle emission or don't need it.

## Key Policies

- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Test policy**: never use `pytest.skip`, `pytest.importorskip`, or `@pytest.mark.skip` to hide missing dependencies — add them to `requirements-dev.txt` instead. Tests must run, not be skipped.
- **UI clipping tests**: `sticky-buttons.spec.mjs` and `resize-visibility.spec.mjs` verify that action buttons (Adjust, Confirm, Link) are not clipped by panel overflow. Never weaken these tests (e.g., by relaxing tolerances, removing viewport sizes, or switching from individual-button checks to cell-level checks). If a CSS change causes these tests to fail, fix the CSS — the test is catching a real bug.

### Traps

- **WebView2 caches JS:** editing JS and relaunching shows stale code. The app uses a persistent WebView2 profile by default (`app.pyw:189`). Force fresh behavior: `DUBIS_WEBVIEW_PROFILE=ephemeral python app.pyw`.
- **Backend change → regenerate fixtures:** after changing Python inventory/columns/prices logic, run `python scripts/generate-test-fixtures.py` or vitest fails with confusing value mismatches. Easiest: just run `bash scripts/verify.sh` (see Testing & Linting).
- **Don't import `js/constants.js` in test setup:** it has a top-level `await fetch` at line 3 that crashes vitest collection; keep `js/ui-helpers.js` store-free and use lazy imports in tests instead.
- **Visual bugs need pixel-truth tests:** geometry-asserts-itself property tests can pass while the visual bug persists. See `docs/visual-testing.md`.

## Common Workflows

### After modifying Python backend code
```bash
python scripts/generate-test-fixtures.py   # regenerate JS test fixtures
npx vitest run                              # verify JS tests still pass
pytest tests/python/ -v                     # verify Python tests still pass
```
JS tests use pre-generated JSON fixtures derived from Python backend logic. If you change how inventory loads, how columns are detected, or how prices/distributors work, the fixtures go stale and JS tests fail with confusing mismatches.

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
The public fixtures (LCSC + Pololu) self-refresh weekly via the scheduled `refresh-fixtures.yml` workflow, which opens a PR. DigiKey + Mouser are local-only (refresh them via `pytest -m live`); their credentials never run on CI.

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
The headless test server (`dubis_headless.py --test-source <tag> --rollback-on-exit`)
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
