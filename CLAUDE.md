# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.py` (webview launcher), `inventory_api.py` (file I/O, inventory CRUD), `digikey_client.py` (Digikey browser session + scraping), `lcsc_client.py` (LCSC API fetching) |
| **Frontend** | `index.html`, `css/styles.css`, 7 JS files (no build step, no modules) |
| **Data** | CSV files in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`, `preferences.json` |

## JS File Dependency Graph (script load order in index.html)

1. `js/main.js` — `EventBus`, `UndoRedo`, `App` (global state), `AppLog`, `showToast()`, `Events` constants, preferences, inventory loading, UI helpers (`Modal`, `setupDropZone`, etc.)
2. `js/csv-parser.js` — `parseCSV`, `detectBOMColumns`, `extractLCSC`, `isDnp`, `extractPartIds`, `generateCSV`, `aggregateBomRows`, `processBOM`
3. `js/part-keys.js` — `bomKey`, `invPartKey`, `rawRowAggKey`, `bomAggKey`, `STATUS_ICONS`, `STATUS_ROW_CLASS`, `countStatuses`
4. `js/matching.js` — `matchBOM`, `parseEEValue`, `extractValueFromDesc`, `buildLookupMaps`, value/package compatibility
5. `js/inventory-panel.js` — IIFE, middle panel
6. `js/bom-panel.js` — IIFE, right panel
7. `js/import-panel.js` — IIFE, left panel
8. `js/resize-panels.js` — IIFE, drag-to-resize

## Key Conventions

- **Global constants**: `UPPER_SNAKE_CASE` at file top
- **Event names**: centralized in `Events` object in `main.js`, used as `Events.X` everywhere
- **Status maps**: `STATUS_ICONS` and `STATUS_ROW_CLASS` in `part-keys.js`
- **Panels communicate**: via `EventBus.emit(Events.X)` / `EventBus.on(Events.X)`
- **`App` object** owns global state; properties declared upfront with ownership comments
- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.

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
ruff check inventory_api.py app.py digikey_client.py lcsc_client.py
```

## Branch Workflow (for multi-Claude development)

- **Main branch** (`main`): protected, requires PR + passing CI
- **Branch naming**: `claude/<scope>-<description>` (e.g., `claude/refactor-tests`, `claude/feature-bom-export`)
- **Squash-merge** PRs to keep linear history
- **Each Claude instance** works in a separate git worktree
- **Coordination**: via GitHub Issues (labels: `feature`, `refactor`)
- **Before creating a PR**: ensure `npm ci && npx eslint js/ && npx vitest run` passes, and `ruff check inventory_api.py app.py digikey_client.py lcsc_client.py && pytest tests/ -v` passes
- **Always use a new branch for new work**: Once a PR is merged, its branch is dead — CI does not run on merged PRs. Before starting new work, check if your current branch's PR was already merged (`gh pr list --head <branch>`). If so, create a fresh branch from `origin/main` (`git fetch origin main && git checkout -b claude/<new-scope> origin/main`). Never push new commits to a branch whose PR was already merged.
