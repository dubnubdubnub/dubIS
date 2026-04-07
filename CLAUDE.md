# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.py` (webview launcher), `inventory_api.py` (API facade, 46 methods), `inventory_ops.py` (merge/adjust/categorize/sort), `csv_io.py` (CSV read/write/migrate), `cache_db.py` (SQLite materialized view), `categorize.py` (part categorization), `generic_parts.py` (generic part CRUD + BOM resolution), `spec_extractor.py` (component spec parsing), `price_ops.py` + `price_history.py` (pricing), `distributor_manager.py` (client coordination), `base_client.py` (ABC), `digikey_client.py` + `digikey_cdp.py` + `digikey_normalizer.py`, `lcsc_client.py`, `mouser_client.py`, `pololu_client.py`, `html_product_parser.py` (shared HTML extraction), `pnp_server.py` (OpenPnP HTTP API), `file_dialogs.py` (OS file dialogs), `dubis_errors.py` (exception hierarchy) |
| **Frontend** | `index.html`, `css/styles.css`, 31 JS ES modules in `js/` (no build step, no framework) |
| **Data** | CSV in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`; events in `events/` — `price_observations.csv`, `part_events.csv`; config: `preferences.json`, `constants.json`, `pnp_part_map.json`; SQLite: `cache.db` (deletable, rebuilt from CSVs) |

## JS Module Dependency Graph

Entry point: `<script type="module" src="js/app-init.js">` in `index.html`.

### Infrastructure modules
- `js/event-bus.js` — pub/sub event system + Events enum
- `js/constants.js` — shared constants from `data/constants.json`
- `js/types.js` — JSDoc typedefs for shared data shapes (no runtime code)
- `js/ui-helpers.js` — DOM utilities (toasts, modals, dropzones, escaping)
- `js/api.js` — pywebview bridge + AppLog (← ui-helpers)
- `js/undo-redo.js` — undo/redo stack (← api)
- `js/dom-refs.js` — lazy DOM element lookups
- `js/store.js` — global state, preferences, inventory loading (← event-bus, constants, api)

### Pure-logic modules (no DOM)
- `js/csv-parser.js` — CSV parsing, BOM processing, CSV generation
- `js/part-keys.js` — part key builders, status icons/classes (← csv-parser)
- `js/matching.js` — BOM-to-inventory 5-step matching engine (← part-keys)
- `js/bom-row-data.js` — BOM row display data assembly (← part-keys)

### Panel modules (state → events → logic → renderer pattern)
Each major panel is split into focused modules:
- **BOM Panel** — `js/bom/bom-state.js` (mutable state), `bom-events.js` (DOM + EventBus listeners), `bom-logic.js` (pure functions), `bom-renderer.js` (HTML generation), `bom-panel.js` (wiring)
- **Inventory Panel** — `js/inventory/inv-state.js`, `inv-events.js`, `inventory-logic.js`, `inventory-renderer.js`, `inventory-panel.js`
- **Import Panel** — `js/import/import-logic.js`, `import-renderer.js`, `import-panel.js`

### Standalone modules
- `js/inventory-modals.js` — adjust + price modals with undo/redo
- `js/generic-parts-modal.js` — generic part CRUD modal
- `js/preferences-modal.js` — preferences sliders + Digikey login flow
- `js/part-preview.js` — hover tooltips for distributor part numbers
- `js/resize-panels.js` — drag-to-resize panels

### Entry point
- `js/app-init.js` — wires modals, registers undo/redo handlers, loads inventory, calls panel init functions

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

**Non-emitting setters:** `setInventory()`, `setBomResults()`, `setBomMeta()`, `setBomDirty()`, `setPreferences()`, `loadLinks()`, `clearLinks()` — callers handle emission or don't need it.

## Python → JS API Surface

`InventoryApi` (46 public methods) is exposed to JS via `window.pywebview.api.*`. Grouped by domain:

**Inventory CRUD:** `rebuild_inventory()`, `adjust_part(adj_type, part_key, qty, note, source)`, `remove_last_purchases(count)`, `remove_last_adjustments(count)`, `import_purchases(rows_json)`, `update_part_fields(part_key, fields_json)`

**BOM:** `consume_bom(matches_json, board_qty, bom_name, note, source)`

**Pricing:** `update_part_price(part_key, unit_price, ext_price)`, `record_fetched_prices(part_key, distributor, price_tiers)`, `get_price_summary(part_key)`

**Distributors:** `fetch_lcsc_product(code)`, `fetch_digikey_product(pn)`, `fetch_pololu_product(sku)`, `fetch_mouser_product(pn)`, `check_digikey_session()`, `start_digikey_login()`, `sync_digikey_cookies()`, `get_digikey_login_status()`, `logout_digikey()`

**Generic Parts:** `create_generic_part(name, type, spec_json, strictness_json)`, `resolve_bom_spec(type, value, package)`, `list_generic_parts()`, `add_generic_member(gp_id, part_id)`, `remove_generic_member(gp_id, part_id)`, `set_preferred_member(gp_id, part_id)`, `update_generic_part(gp_id, name, spec_json, strictness_json)`, `extract_spec(part_key)`

**Preferences & File I/O:** `load_preferences()`, `save_preferences(prefs_json)`, `detect_columns(headers_json)`, `save_file_dialog(content, name, dir, links_json)`, `open_file_dialog(title, dir)`, `load_file(path)`, `convert_xls_to_csv(path)`

**Window lifecycle:** `set_bom_dirty(dirty)`, `confirm_close()`, `rollback_source(source)`

## Key Conventions

- **ES modules**: every dependency is an explicit `import` statement at the top of each file
- **Constants**: `UPPER_SNAKE_CASE` at module scope
- **Event names**: centralized in `Events` object in `event-bus.js`, used as `Events.X` everywhere
- **Status maps**: `STATUS_ICONS` and `STATUS_ROW_CLASS` in `part-keys.js`
- **Panels communicate**: via `EventBus.emit(Events.X)` / `EventBus.on(Events.X)` — see EventBus Flow table above
- **Store pattern**: `store` object has read-only getters; mutations go through exported setter functions (e.g., `setInventory()`, `confirmMatch()`)
- **Panel pattern**: each panel follows state → events → logic → renderer split. State modules hold mutable variables, events wire DOM/EventBus, logic is pure, renderers produce HTML strings
- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Test policy**: never use `pytest.skip`, `pytest.importorskip`, or `@pytest.mark.skip` to hide missing dependencies — add them to `requirements-dev.txt` instead. Tests must run, not be skipped.
- **UI clipping tests**: `sticky-buttons.spec.mjs` and `resize-visibility.spec.mjs` verify that action buttons (Adjust, Confirm, Link) are not clipped by panel overflow. Never weaken these tests (e.g., by relaxing tolerances, removing viewport sizes, or switching from individual-button checks to cell-level checks). If a CSS change causes these tests to fail, fix the CSS — the test is catching a real bug.
- **Window globals**: `window.closeModal` exposed for Python's `evaluate_js` (set in app-init.js)

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

## Testing & Linting

### Local (runs in seconds)

```bash
# JavaScript — Node.js installed on Windows via winget
npx eslint js/            # lint
npx tsc --noEmit          # type check
npx vitest run            # unit tests (~1s)

# Python
ruff check .              # lint all Python files
pytest tests/python/ -v   # unit tests (~30s)
```

### CI commit-message tags

Control which CI tiers run by adding a tag to your commit message:

    fix(api): handle empty CSV

    [ci: python]

| Tag | What runs | Wall clock |
|-----|-----------|------------|
| `[ci: all]` or no tag | Everything (safe default) | ~3 min |
| `[ci: lint]` | ESLint + tsc + ruff only (no tests) | ~8s |
| `[ci: js]` | Full JS: lint + types + vitest core + Playwright E2E | ~55s |
| `[ci: python]` | Full Python: ruff + fixture check + pytest | ~17s |
| `[ci: pnp-e2e]` | PnP same-machine E2E (both runners) | ~28s |
| `[ci: pnp-cross]` | PnP cross-compute E2E (depends on pnp-e2e) | ~56s |
| `[ci: quality]` | Visual/a11y: contrast, style-audit, accessibility E2E (warns, never blocks) | ~49s |

When unsure, omit the tag — all suites run. Use `[ci: lint]` only for docs/comments/CLAUDE.md changes.

### CI troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Branch already merged" error on push | Pushing to a branch whose PR was squash-merged | Run `bash scripts/push-pr.sh` — it auto-creates a new branch |
| JS test fixture mismatch | Backend changed but fixtures not regenerated | `python scripts/generate-test-fixtures.py && git add tests/fixtures/generated/` |
| Playwright tests fail only on ubuntu | Playwright browsers only installed on ubuntu runner | Check if test needs `install-pw: true` in CI matrix |
| Quality suite "fails" | Quality tier uses `continue-on-error: true` | These are warnings, not blockers — quality failures don't block merge |
| ruff or eslint fails after refactor | New file not covered by existing config | Check `pyproject.toml` excludes and `eslint.config.mjs` includes |

### CI (via workflow_dispatch for selective runs)

```bash
gh workflow run ci.yml --ref <branch>                          # all suites
gh workflow run ci.yml --ref <branch> -f suite=lint            # lint only, no tests
gh workflow run ci.yml --ref <branch> -f suite=js
gh workflow run ci.yml --ref <branch> -f suite=python
gh workflow run ci.yml --ref <branch> -f suite=pnp-e2e
gh workflow run ci.yml --ref <branch> -f suite=pnp-cross
gh workflow run ci.yml --ref <branch> -f suite=quality
```

### PnP E2E Tests

Same-machine (CI default): `python tests/pnp-e2e/run_test.py`
Cross-compute: `python tests/pnp-e2e/run_test.py --remote-openpnp ux430`

The `--remote-openpnp` flag starts dubIS locally and launches OpenPnP on a remote
host via SSH. Uses `~/.ssh/config` for connection settings. Cross-compute tests
verify the real network path (dubIS ↔ OpenPnP over Tailscale).

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
