# Data-Down, Events-Up Refactor

**Date:** 2026-03-26
**Approach:** Batch refactor — all changes land as one coordinated PR
**Goal:** Make the codebase structurally optimized for AI-assisted development by eliminating shared mutable state, separating logic from rendering, and splitting monolithic files into focused modules.
**Constraint:** Zero user-visible behavior changes.

---

## Problem Statement

The dubIS codebase has several structural patterns that impede AI coding assistants:

1. **App god object** — 15+ properties directly mutated from 7 modules. No encapsulation, no grep target for mutations, action-at-a-distance bugs.
2. **Monolithic panel modules** — `bom-panel.js` (713 lines), `import-panel.js` (516 lines), `inventory-panel.js` (347 lines) each mix CSV parsing, matching logic, HTML generation, DOM manipulation, event handling, and state mutation.
3. **60% untested code** — The panel modules are untestable because logic is entangled with DOM side effects.
4. **Side-effect imports** — Panel modules self-initialize on import, making isolated testing impossible.
5. **Monolithic Python backend** — `inventory_api.py` (927 lines, 46 methods, 8 responsibilities).
6. **Monolithic CSS** — 1,376 lines in one file.
7. **Scattered DOM selectors** — 145 hardcoded `getElementById` calls across 12 files.

## Design: Data-Down, Events-Up

### Core Principle

State flows down (store → panels), events flow up (user actions → store mutations → re-renders). No module directly mutates shared state. Every mutation goes through a setter that emits a change event.

---

## 1. State Management — `js/store.js`

Replace the `App` god object with a structured store using getter/setter pairs.

### State Slices

| Slice | Type | Setter | Event Emitted |
|-------|------|--------|---------------|
| `inventory` | `Array<Object>` | `setInventory(items)` | `INVENTORY_UPDATED` |
| `bomResults` | `Object \| null` | `setBomResults(results)` | (none — caller emits `BOM_LOADED`) |
| `bomFileName` | `string` | `setBomMeta({fileName, headers, cols})` | (none) |
| `bomHeaders` | `Array<string>` | (via `setBomMeta`) | (none) |
| `bomCols` | `Object` | (via `setBomMeta`) | (none) |
| `bomDirty` | `boolean` | `setBomDirty(dirty)` | (none) |
| `preferences` | `Object` | `setPreferences(prefs)` | `PREFS_CHANGED` |
| `links` | `{manualLinks, confirmedMatches}` | `addManualLink()`, `confirmMatch()`, `unconfirmMatch()`, `loadLinks()`, `clearLinks()` | `LINKS_CHANGED` or `CONFIRMED_CHANGED` |
| `linkingMode` | `{active, invItem, bomRow}` | `setLinkingMode()`, `setReverseLinkingMode()` | `LINKING_MODE` |

### Read Access

```js
export const store = {
  get inventory()   { return inventory; },
  get bomResults()  { return bomResults; },
  // ... all slices exposed as read-only getters
};
```

### Derived State (read-only, computed once from constants)

```js
export const SECTION_HIERARCHY = parseSectionOrder(SECTION_ORDER);
export const FLAT_SECTIONS = flattenSections(SECTION_HIERARCHY);
```

### Convenience Functions (moved from current store.js)

- `loadPreferences()` — async, calls Python API, calls `setPreferences()`
- `savePreferences()` — async, calls Python API
- `getThreshold(section)` — reads `store.preferences.thresholds`
- `setThreshold(section, value)` — calls `setPreferences()`, `savePreferences()`
- `loadInventory()` — async, calls Python API, calls `setInventory()`
- `snapshotLinks()` — returns deep copy for undo/redo

Note: `updateInventoryHeader()` (currently in store.js) moves to `inventory-panel.js` — it reads inventory and writes to DOM, which is rendering, not state management.

### Migration

Every `App.X = y` becomes the corresponding setter call. Every `App.X` read becomes `store.X`. Mechanical find-and-replace.

---

## 2. Panel Architecture — Logic / Renderer / Wiring

Each panel splits into three files:

```
js/<panel>/
  <panel>-logic.js      Pure functions: (inputs) -> outputs
  <panel>-renderer.js   Pure functions: (data) -> HTML string
  <panel>-panel.js      Thin wiring: events <-> logic <-> renderer <-> DOM
```

### Rules

- **Logic modules:** No imports from `store.js`, `event-bus.js`, or DOM APIs. All data comes via function parameters. All results returned as values. Testable with zero mocking.
- **Renderer modules:** No imports from `store.js` or `event-bus.js`. May import from `ui-helpers.js` (for `escHtml`). Take data as parameters, return HTML strings. Testable by snapshot.
- **Panel modules (wiring):** Import from store, event-bus, logic, and renderer. Subscribe to events, call logic functions, pass results to renderers, write to DOM. Export an `init()` function. Target: under 200 lines each.

### BOM Panel Split

**`js/bom/bom-logic.js`** — extracted from `bom-panel.js`:
- `processLoadedBom(csvText, inventory, links)` — parse CSV, detect columns, run matching. Returns `{rows, headers, cols, results}`.
- `computeStagingRows(bomResults, inventory, links, multiplier, filter)` — compute display data for staging table. Returns row data array.
- `applyBomEdit(bomRawRows, rowIndex, colIndex, newValue)` — apply in-place edit. Returns `{updatedRows, dirty}`.
- `computeBomSummary(results)` — compute OK/short/missing counts. Returns summary object.
- `prepareConsumption(bomResults, inventory, links, multiplier)` — prepare consumption matches for Python API.

**`js/bom/bom-renderer.js`** — extracted from `bom-panel.js`:
- `renderDropZone()` — initial drop zone HTML.
- `renderBomResultPanel()` — outer shell for results view (summary, filter bar, table container).
- `renderStagingTable(rows, linkingMode)` — staging rows HTML (currently the 100-line `renderStagingRows()`).
- `renderBomSummary(summary)` — summary chips HTML.
- `renderEditableTable(headers, rawRows)` — editable BOM table HTML.

**`js/bom/bom-panel.js`** — thin wiring (~150 lines):
- `init()` — render drop zone, register undo/redo, subscribe to events.
- Event handlers that call logic functions, update store via setters, call renderers, write to DOM.
- Drop zone setup, file dialog integration, save/consume button handlers.

### Import Panel Split

**`js/import/import-logic.js`**:
- `detectColumnMapping(headers, knownMappings)` — auto-detect column mapping.
- `transformImportRows(parsedRows, columnMapping, fieldnames)` — transform parsed CSV rows to inventory format.
- `validateImportRows(rows)` — validate before import.

**`js/import/import-renderer.js`**:
- `renderDropZone(templates)` — drop zone with template buttons.
- `renderMapper(headers, rows, columnMapping, fieldnames)` — column mapping UI (currently the 122-line `renderMapper()`).
- `renderStagingTable(rows, columnMapping)` — preview table.

**`js/import/import-panel.js`** — thin wiring.

### Inventory Panel Split

**`js/inventory/inventory-logic.js`** — absorbs logic from `bom-comparison.js`:
- `groupBySection(inventory, sectionHierarchy)` — group inventory items by section.
- `filterByQuery(items, query)` — search filtering.
- `computeBomComparison(bomData, inventory, links, thresholds)` — compute which inventory items are BOM-matched and their status (currently in `bom-comparison.js`).
- `computeMatchedInvKeys(bomData, links)` — return Set of inventory keys that appear in BOM matches.

**`js/inventory/inventory-renderer.js`** — absorbs rendering from `bom-comparison.js`:
- `renderSectionHeader(section, count, collapsed)` — section header HTML.
- `renderPartRow(part, options)` — single part row (currently 66-line `createPartRow()`).
- `renderBomComparisonTable(comparisonData, linkingMode)` — BOM comparison table (currently 84-line `renderBomComparison()`).
- `renderNormalSections(groups, options)` — non-BOM-matched sections.

**`js/inventory/inventory-panel.js`** — thin wiring, absorbs `bom-comparison.js` event subscriptions.

### `bom-comparison.js` Elimination

`bom-comparison.js` currently exports mutable state (`bomData`, `activeFilter`, `expandedAlts`, `rowMap`) and uses callback injection to reach into inventory-panel. After the split:
- Its logic moves to `inventory-logic.js`
- Its rendering moves to `inventory-renderer.js`
- Its mutable state becomes local variables in `inventory-panel.js`
- The callback injection pattern (`initBomComparison({render, openAdjustModal, ...})`) is eliminated — the inventory panel calls its own logic/renderer directly
- The file is deleted

---

## 3. DOM References — `js/dom-refs.js`

Single source of truth for stable element references.

```js
export const dom = {
  get bomBody()       { return document.getElementById("bom-body"); },
  get inventoryBody() { return document.getElementById("inventory-body"); },
  get importBody()    { return document.getElementById("import-body"); },
  get invSearch()     { return document.getElementById("inv-search"); },
  // ... ~50 lazy getters for all stable element IDs
};
```

### Scope

- **Moves here:** All `getElementById` calls for elements defined in `index.html` (stable, long-lived).
- **Stays inline:** `querySelector` calls on dynamically-created elements (scoped to renderer output).

---

## 4. Explicit Initialization

Every panel and component module exports an `init()` function. `app-init.js` calls them in explicit order:

```js
import { init as initResizePanels } from './resize-panels.js';
import { init as initInventoryPanel } from './inventory/inventory-panel.js';
import { init as initBomPanel } from './bom/bom-panel.js';
import { init as initImportPanel } from './import/import-panel.js';
import { init as initPartPreview } from './part-preview.js';

async function initApp() {
  initResizePanels();
  initInventoryPanel();
  initBomPanel();
  initImportPanel();
  initPartPreview();
  wireGlobalShortcuts();
  wirePreferencesModal();
  await loadInventory();
}
```

No side-effect imports. Import order is irrelevant. Initialization order is explicit and documented.

---

## 5. Python Backend Split

### New Modules

**`csv_io.py`** — extracted from `inventory_api.py`:
- `append_csv_rows(path, fieldnames, rows)` — append with schema migration
- `migrate_csv_header(path, expected_fieldnames)` — header migration
- `fix_double_utf8(text)` — encoding fix
- `read_text(path)` — UTF-16/UTF-8 auto-detection
- `convert_xls_to_csv(path)` — XLS conversion

**`inventory_ops.py`** — extracted from `inventory_api.py`:
- `get_part_key(row)` — unique identifier extraction
- `read_and_merge(purchase_csv, fieldnames)` — read + merge duplicates
- `apply_adjustments(merged, adjustments_csv, adj_fieldnames)` — apply adjustments
- `categorize_and_sort(parts)` — categorize + sort (delegates to `categorize.py`)
- `write_organized(categorized, output_csv, fieldnames)` — write with section headers
- `load_organized(output_csv)` — load as JSON-serializable dicts
- `rebuild(purchase_csv, adjustments_csv, output_csv, fieldnames, adj_fieldnames)` — full pipeline
- `append_adjustment(adjustments_csv, adj_fieldnames, adj_type, part_key, quantity, ...)` — append one adjustment row

**`price_ops.py`** — extracted from `inventory_api.py`:
- `parse_qty(value, default=0)` — quantity parsing
- `parse_price(value, default=0.0)` — price parsing
- `update_price_in_csv(purchase_csv, fieldnames, part_key, unit_price, ext_price)` — price update with auto-calculation
- `derive_missing_prices(row)` — compute unit from ext or vice versa

**`file_dialogs.py`** — extracted from `inventory_api.py`:
- `open_file_dialog(title, default_dir)` — native open dialog
- `save_file_dialog(content, default_name, default_dir, links_json)` — native save dialog
- `load_file(path)` — load file by path with encoding detection

### Facade

`inventory_api.py` (~150 lines) keeps the same public API. Every public method becomes a 3-5 line delegation:

```python
def adjust_part(self, adj_type, part_key, quantity, note="", source=""):
    qty = parse_qty(quantity)
    with self._lock:
        append_adjustment(self.adjustments_csv, self.ADJ_FIELDNAMES,
                         adj_type, part_key, qty, note=note, source=source)
        return self._rebuild()
```

**JS frontend changes: zero.** The pywebview API surface is identical.

---

## 6. CSS Split

Split `css/styles.css` along existing section comment boundaries:

```
css/
  variables.css                :root custom properties (~40 lines)
  layout.css                   Header, panel grid, resize handles (~100 lines)
  buttons.css                  All button variants (~120 lines)
  tables.css                   Shared table styles, row colors, qty states (~250 lines)
  modals.css                   All modal dialogs (~150 lines)
  panels/
    import.css                 Import panel specific (~150 lines)
    inventory.css              Inventory panel specific (~150 lines)
    bom.css                    BOM panel specific (~150 lines)
  components/
    toast.css                  Toast notifications (~30 lines)
    tooltip.css                Part preview card (~100 lines)
    badges.css                 Chips, status badges (~50 lines)
    console.css                Console log (~30 lines)
    linking.css                Linking mode styles (~50 lines)
```

Loaded via `<link>` tags in `index.html`. `variables.css` first, then layout, then shared, then panels/components. No build step.

---

## 7. Test Strategy

### Logic Module Tests (new)

Two fixture strategies, depending on whether Python can compute expected outputs:

**Python-generated fixtures** (Python is source of truth):

| Fixture | Source | Tests |
|---------|--------|-------|
| `generated/inventory.json` | Already exists (PR #156) | Input for all JS logic tests |
| `generated/column-detections.json` | Already exists (PR #156) | `import-logic.test.js` |
| `generated/import-transforms.json` | Run column detection + row transform on PO CSVs | `import-logic.test.js` |

**Golden-file snapshots** (JS logic is source of truth — matching, staging, BOM processing are JS-only):

| Fixture | Generated by | Tests |
|---------|-------------|-------|
| `generated/bom-match-scenarios.json` | One-time JS script using `matchBOM()` against known BOM + inventory fixtures | `bom-logic.test.js` |
| `generated/staging-rows.json` | One-time JS script using `computeStagingRows()` | `bom-logic.test.js` |

Golden files are committed and updated via `node scripts/update-js-snapshots.js`. CI verifies they match current logic output (same pattern as `generate-test-fixtures.py --check`).

### Renderer Tests (new)

Snapshot-style assertions on HTML output:
- Call renderer with known data
- Assert presence/absence of expected CSS classes, content, structure
- No DOM required (they return strings)

### Existing Tests

- **252 vitest unit tests** — all must continue passing (matching.js, csv-parser.js, etc. are unchanged)
- **196 pytest tests** — all must continue passing (Python facade has same API)
- **Playwright E2E tests** — cover the full wiring path (init → render → interact)
- **CI fixture freshness check** — `generate-test-fixtures.py --check` validates fixtures match Python logic

### Coverage Target After Refactor

| Layer | Current | Target |
|-------|---------|--------|
| Logic modules | ~40% tested | ~95% tested |
| Renderer modules | 0% tested | ~80% tested |
| Panel wiring | 0% unit tested | 0% unit tested (covered by E2E) |
| Python backend | ~85% tested | ~90% tested |

---

## 8. Event Bus

No structural changes. Two additions:

1. **Payload documentation** — JSDoc comments on each event in the `Events` object describing the payload shape.
2. **String literal guard** — ESLint rule or tsc check that flags `EventBus.emit("raw-string")` / `EventBus.on("raw-string")` in favor of `Events.X`.

---

## 9. Migration Plan

### Phase 1: Foundation (no behavior change)
1. Create `js/dom-refs.js` with lazy getters for all stable element IDs
2. Rewrite `js/store.js` with getter/setter pairs; keep `App` as deprecated alias during migration
3. Split `css/styles.css` into component files; update `index.html` link tags
4. Split `inventory_api.py` into `csv_io.py`, `inventory_ops.py`, `price_ops.py`, `file_dialogs.py`

### Phase 2: Extract logic & renderers (no behavior change)
5. Extract `js/bom/bom-logic.js` and `js/bom/bom-renderer.js` from `bom-panel.js`
6. Extract `js/import/import-logic.js` and `js/import/import-renderer.js` from `import-panel.js`
7. Extract `js/inventory/inventory-logic.js` and `js/inventory/inventory-renderer.js` from `inventory-panel.js` + `bom-comparison.js`
8. Slim panel modules to thin wiring with explicit `init()` exports

### Phase 3: Wire up & test
9. Rewrite `app-init.js` to use explicit `init()` calls
10. Replace all `App.*` references with `store.*` / setter calls; remove `App` alias
11. Replace all `getElementById` calls in panel/wiring code with `dom.*` imports
12. Extend `generate-test-fixtures.py` with BOM match and import transform scenarios
13. Add tests for all logic and renderer modules

### Phase 4: Verify
14. All existing tests pass (vitest + pytest)
15. New logic + renderer tests pass
16. ESLint + tsc clean
17. Playwright E2E tests pass
18. CI green

---

## File Inventory After Refactor

### JavaScript (new/changed)
```
js/
  store.js                     REWRITTEN — getter/setter state management
  dom-refs.js                  NEW — lazy element references
  event-bus.js                 MINOR — JSDoc payload comments
  constants.js                 UNCHANGED
  api.js                       UNCHANGED
  undo-redo.js                 UNCHANGED
  ui-helpers.js                UNCHANGED
  types.js                     UNCHANGED
  matching.js                  UNCHANGED
  csv-parser.js                UNCHANGED
  part-keys.js                 UNCHANGED
  bom-row-data.js              DELETED — logic absorbed into bom/bom-logic.js
  bom-comparison.js            DELETED — split into inventory/inventory-logic.js + inventory-renderer.js
  app-init.js                  REWRITTEN — explicit init() calls, no side-effect imports
  bom/
    bom-logic.js               NEW — pure BOM processing functions
    bom-renderer.js            NEW — pure BOM HTML generation
    bom-panel.js               REWRITTEN — thin wiring (~150 lines)
  import/
    import-logic.js            NEW — pure import processing functions
    import-renderer.js         NEW — pure import HTML generation
    import-panel.js            REWRITTEN — thin wiring (~150 lines)
  inventory/
    inventory-logic.js         NEW — pure inventory + BOM comparison logic
    inventory-renderer.js      NEW — pure inventory HTML generation
    inventory-panel.js         REWRITTEN — thin wiring (~150 lines)
  inventory-modals.js          UPDATED — use store.* and dom.* imports
  preferences-modal.js         UPDATED — use store.* and dom.* imports
  part-preview.js              UPDATED — add init(), use store.* and dom.*
  resize-panels.js             UPDATED — add init()
```

### CSS (new)
```
css/
  variables.css                NEW (from styles.css lines 1-37)
  layout.css                   NEW (from styles.css)
  buttons.css                  NEW (from styles.css)
  tables.css                   NEW (from styles.css)
  modals.css                   NEW (from styles.css)
  panels/
    import.css                 NEW (from styles.css)
    inventory.css              NEW (from styles.css)
    bom.css                    NEW (from styles.css)
  components/
    toast.css                  NEW (from styles.css)
    tooltip.css                NEW (from styles.css)
    badges.css                 NEW (from styles.css)
    console.css                NEW (from styles.css)
    linking.css                NEW (from styles.css)
  styles.css                   DELETED
```

### Python (new/changed)
```
inventory_api.py               REWRITTEN — thin facade (~150 lines)
csv_io.py                      NEW — CSV operations
inventory_ops.py               NEW — rebuild pipeline
price_ops.py                   NEW — price calculations
file_dialogs.py                NEW — OS dialog operations
categorize.py                  UNCHANGED
app.py                         UNCHANGED
*_client.py                    UNCHANGED
```

### Tests (new)
```
tests/js/
  bom-logic.test.js            NEW — logic tests against fixtures
  bom-renderer.test.js         NEW — snapshot renderer tests
  import-logic.test.js         NEW — logic tests against fixtures
  import-renderer.test.js      NEW — snapshot renderer tests
  inventory-logic.test.js      NEW — logic tests against fixtures
  inventory-renderer.test.js   NEW — snapshot renderer tests
  store.test.js                NEW — getter/setter/event tests

tests/fixtures/generated/
  bom-match-scenarios.json     NEW — JS golden-file snapshot
  staging-rows.json            NEW — JS golden-file snapshot
  import-transforms.json       NEW — Python-generated

scripts/
  update-js-snapshots.js       NEW — generates JS golden-file fixtures

tests/python/
  test_csv_io.py               NEW (or extracted from test_inventory_api.py)
  test_inventory_ops.py        NEW (or extracted from test_inventory_api.py)
  test_price_ops.py            NEW (or extracted from test_inventory_api.py)
```

---

## What Does NOT Change

- `index.html` structure (same element IDs, same panels, same modals)
- Python public API surface (JS calls identical method names with identical signatures)
- `categorize.py` (already well-structured)
- `matching.js` (already pure, already tested)
- `csv-parser.js`, `part-keys.js`, `event-bus.js`, `undo-redo.js` (already clean)
- User-visible behavior (zero UX changes)
- Data files (`data/*.csv`, `data/*.json`)
