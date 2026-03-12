# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.py` (webview launcher), `inventory_api.py` (file I/O, inventory CRUD), `categorize.py` (part categorization), `digikey_client.py` (Digikey browser session + scraping), `lcsc_client.py` (LCSC API fetching) |
| **Frontend** | `index.html`, `css/styles.css`, 17 JS ES modules (no build step, no framework) |
| **Data** | CSV files in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`, `preferences.json`, `constants.json` |

## JS Module Dependency Graph

Entry point: `<script type="module" src="js/app-init.js">` in `index.html`.

### Infrastructure modules
- `js/event-bus.js` — `EventBus`, `Events` enum (no dependencies)
- `js/constants.js` — `SECTION_ORDER`, `FIELDNAMES` (loads `data/constants.json` via fetch)
- `js/ui-helpers.js` — `showToast`, `escHtml`, `Modal`, `setupDropZone`, `resetDropZoneInput`, `linkPriceInputs`, `stockValueColor`
- `js/api.js` — `api()` pywebview bridge, `AppLog` (← ui-helpers)
- `js/undo-redo.js` — `UndoRedo` (← api)
- `js/store.js` — `App` state object, `loadPreferences`, `savePreferences`, `getThreshold`, `setThreshold`, `loadInventory`, `onInventoryUpdated`, `snapshotLinks` (← event-bus, constants, api)

### Pure-logic modules (no DOM)
- `js/types.js` — JSDoc `@typedef`s for shared data shapes (`InventoryItem`, `BomMatchResult`, `LinkState`, etc.) — no runtime code
- `js/csv-parser.js` — `parseCSV`, `detectBOMColumns`, `extractLCSC`, `isDnp`, `extractPartIds`, `generateCSV`, `aggregateBomRows`, `processBOM`
- `js/part-keys.js` — `bomKey`, `invPartKey`, `rawRowAggKey`, `bomAggKey`, `STATUS_ICONS`, `STATUS_ROW_CLASS`, `countStatuses` (← csv-parser)
- `js/matching.js` — `matchBOM`, `parseEEValue`, `extractValueFromDesc`, `buildLookupMaps`, value/package compatibility (← part-keys)
- `js/bom-row-data.js` — `bomRowDisplayData` (← part-keys)

### Panel modules (side-effect imports, self-initialise)
- `js/inventory-panel.js` — middle panel: normal inventory view (grouped sections, search), render dispatcher, event listeners (← event-bus, api, ui-helpers, undo-redo, store, part-keys, inventory-modals, bom-comparison)
- `js/inventory-modals.js` — adjustment + price modals with undo/redo handlers, exports `openAdjustModal`, `openPriceModal` (← api, ui-helpers, undo-redo, store, part-keys)
- `js/bom-comparison.js` — BOM comparison table: filter bar, matched rows, alt rows, click delegation, confirm/unconfirm (← api, ui-helpers, undo-redo, store, part-keys, bom-row-data)
- `js/bom-panel.js` — right panel (← event-bus, api, ui-helpers, undo-redo, store, part-keys, csv-parser, matching)
- `js/import-panel.js` — left panel (← api, ui-helpers, undo-redo, store, csv-parser)
- `js/resize-panels.js` — drag-to-resize (no JS imports)
- `js/part-preview.js` — hover tooltips (← api, ui-helpers)
- `js/preferences-modal.js` — preferences sliders + Digikey login/logout flow, exports `openPreferencesModal`, `applyPreferences`, `wireDigikeyButtons` (← api, ui-helpers, event-bus, store)

### Entry point
- `js/app-init.js` — wires modals, global shortcuts, loads inventory, side-effect imports all panels + preferences-modal

## Key Conventions

- **ES modules**: every dependency is an explicit `import` statement at the top of each file
- **Constants**: `UPPER_SNAKE_CASE` at module scope
- **Event names**: centralized in `Events` object in `event-bus.js`, used as `Events.X` everywhere
- **Status maps**: `STATUS_ICONS` and `STATUS_ROW_CLASS` in `part-keys.js`
- **Panels communicate**: via `EventBus.emit(Events.X)` / `EventBus.on(Events.X)`
- **`App` object** in `store.js` owns global state; properties declared upfront with ownership comments
- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Window globals**: `window.closeModal` exposed for Python's `evaluate_js` (set in app-init.js)

## Testing

```bash
# JavaScript (vitest)
npm ci
npx vitest run

# Python (pytest)
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Linting

```bash
# JavaScript
npx eslint js/

# Python
ruff check inventory_api.py app.py categorize.py digikey_client.py lcsc_client.py
```

## Branch Workflow (for multi-Claude development)

- **Main branch** (`main`): protected, requires PR + passing CI
- **Branch naming**: `claude/<scope>-<description>` (e.g., `claude/refactor-tests`, `claude/feature-bom-export`)
- **Squash-merge** PRs to keep linear history
- **Each Claude instance** works in a separate git worktree
- **Coordination**: via GitHub Issues (labels: `feature`, `refactor`)
- **Before creating a PR**: ensure `npm ci && npx eslint js/ && npx vitest run` passes, and `ruff check inventory_api.py app.py categorize.py digikey_client.py lcsc_client.py && pytest tests/ -v` passes
- **Always use a new branch for new work**: Once a PR is merged, its branch is dead — CI does not run on merged PRs. Before starting new work, check if your current branch's PR was already merged (`gh pr list --head <branch>`). If so, create a fresh branch from `origin/main` (`git fetch origin main && git checkout -b claude/<new-scope> origin/main`). Never push new commits to a branch whose PR was already merged.
