# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.py` (webview launcher), `inventory_api.py` (file I/O, inventory CRUD), `categorize.py` (part categorization), `digikey_client.py` (Digikey browser session + scraping), `lcsc_client.py` (LCSC API fetching) |
| **Frontend** | `index.html`, `css/styles.css`, 20 JS ES modules (no build step, no framework) |
| **Data** | CSV files in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`, `preferences.json`, `constants.json` |

## JS Module Dependency Graph

Entry point: `<script type="module" src="js/app-init.js">` in `index.html`.

### Infrastructure modules
- `js/event-bus.js` — pub/sub event system + Events enum
- `js/constants.js` — shared constants loaded from `data/constants.json`
- `js/ui-helpers.js` — DOM utilities (toasts, escaping, modals, dropzones, price inputs)
- `js/api.js` — pywebview bridge + AppLog (← ui-helpers)
- `js/undo-redo.js` — undo/redo stack (← api)
- `js/store.js` — global `App` state, preferences, inventory loading (← event-bus, constants, api)

### Pure-logic modules (no DOM)
- `js/types.js` — JSDoc typedefs for shared data shapes (no runtime code)
- `js/csv-parser.js` — CSV parsing, BOM processing, CSV generation
- `js/part-keys.js` — part key builders, status icons/classes (← csv-parser)
- `js/matching.js` — BOM-to-inventory matching, value/package compatibility (← part-keys)
- `js/bom-row-data.js` — BOM row display data assembly (← part-keys)

### Panel modules (side-effect imports, self-initialise)
- `js/inventory-panel.js` — middle panel: grouped inventory view, search, render dispatch
- `js/inventory-modals.js` — adjustment + price modals with undo/redo
- `js/bom-comparison.js` — BOM comparison table: filtering, matching, confirm/unconfirm
- `js/bom-panel.js` — right panel: BOM file viewer, staging, consume
- `js/import-panel.js` — left panel: purchase CSV import
- `js/resize-panels.js` — drag-to-resize panels
- `js/part-preview.js` — hover tooltips
- `js/preferences-modal.js` — preferences sliders + Digikey login flow

### Entry point
- `js/app-init.js` — wires modals, global shortcuts, loads inventory, imports all panels

## Key Conventions

- **ES modules**: every dependency is an explicit `import` statement at the top of each file
- **Constants**: `UPPER_SNAKE_CASE` at module scope
- **Event names**: centralized in `Events` object in `event-bus.js`, used as `Events.X` everywhere
- **Status maps**: `STATUS_ICONS` and `STATUS_ROW_CLASS` in `part-keys.js`
- **Panels communicate**: via `EventBus.emit(Events.X)` / `EventBus.on(Events.X)`
- **`App` object** in `store.js` owns global state; properties declared upfront with ownership comments
- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Window globals**: `window.closeModal` exposed for Python's `evaluate_js` (set in app-init.js)

## Testing & Linting

```bash
# JavaScript
npm ci
npx eslint js/            # lint
npx tsc --noEmit          # type check (tsconfig.json, @ts-check files only)
npx vitest run            # unit tests
npx playwright test       # E2E tests (needs running app)

# Python
pip install -r requirements-dev.txt
ruff check inventory_api.py app.py digikey_client.py lcsc_client.py categorize.py pnp_server.py
pytest tests/python/ -v
```

## Branch Workflow (for multi-Claude development)

- **Main branch** (`main`): protected, requires PR + passing CI
- **Branch naming**: `claude/<scope>-<description>` (e.g., `claude/refactor-tests`, `claude/feature-bom-export`)
- **Squash-merge** PRs to keep linear history
- **Each Claude instance** works in a separate git worktree
- **Coordination**: via GitHub Issues (labels: `feature`, `refactor`)
- **Before creating a PR**: ensure lint, type check, and tests pass (see Testing & Linting section above)
- **Always use a new branch for new work**: Once a PR is merged, its branch is dead — CI does not run on merged PRs. Before starting new work, check if your current branch's PR was already merged (`gh pr list --head <branch>`). If so, create a fresh branch from `origin/main` (`git fetch origin main && git checkout -b claude/<new-scope> origin/main`). Never push new commits to a branch whose PR was already merged.
