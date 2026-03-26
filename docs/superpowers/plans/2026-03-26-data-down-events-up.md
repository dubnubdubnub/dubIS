# Data-Down, Events-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor dubIS into a data-down/events-up architecture — replace App god object with getter/setter store, split panel modules into logic/renderer/wiring layers, split monolithic Python backend and CSS.

**Architecture:** State lives in `js/store.js` with getter/setter pairs. Each panel becomes three files: pure logic, pure renderer (HTML strings), and thin wiring. Python backend splits into focused modules behind the same API facade.

**Tech Stack:** Vanilla JS (ES modules), Python 3, CSS (no build step), vitest, pytest

**Spec:** `docs/superpowers/specs/2026-03-26-data-down-events-up-refactor-design.md`

---

## Phase 1: Foundation (parallelizable — no interdependencies)

### Task 1: Create `js/store.js` (new state management)

**Files:**
- Rewrite: `js/store.js`
- Create: `tests/js/store.test.js`

The current `store.js` exports an `App` object with direct property access. Rewrite it with:
- Private `let` variables for each state slice
- A `store` object with getters (read-only access)
- Named setter functions that emit events via EventBus
- Link methods (`addManualLink`, `confirmMatch`, `unconfirmMatch`, `setLinkingMode`, `setReverseLinkingMode`, `loadLinks`, `clearLinks`, `hasLinks`)
- Keep exporting `App` as a **deprecated alias** that proxies to `store` — this allows Phase 2 migration to happen incrementally
- Move `updateInventoryHeader()` out of store (it touches DOM) — for now, keep it exported but mark it for relocation
- Keep `loadPreferences`, `savePreferences`, `getThreshold`, `setThreshold`, `loadInventory`, `onInventoryUpdated`, `snapshotLinks` as exported functions
- Parse `SECTION_HIERARCHY` and `FLAT_SECTIONS` from `SECTION_ORDER` and export as named constants

Current state slices to extract from the `App` object:
- `inventory: []`
- `bomResults: null`, `bomFileName: ""`, `bomHeaders: []`, `bomCols: {}`, `bomDirty: false`
- `links: { manualLinks: [], confirmedMatches: [] }`
- `linkingMode: { active: false, invItem: null, bomRow: null }`
- `preferences: { thresholds: {} }`
- `SECTION_ORDER`, `SECTION_HIERARCHY`, `FLAT_SECTIONS`

Setters to create:
- `setInventory(items)` — emits `INVENTORY_UPDATED`
- `setBomResults(results)` — no event (caller emits `BOM_LOADED`)
- `setBomMeta({ fileName, headers, cols })` — no event
- `setBomDirty(dirty)` — no event
- `setPreferences(prefs)` — emits `PREFS_CHANGED`
- `addManualLink(bk, ipk)` — emits `LINKS_CHANGED`
- `confirmMatch(bk, ipk)` — emits `CONFIRMED_CHANGED`
- `unconfirmMatch(bk)` — emits `CONFIRMED_CHANGED`
- `setLinkingMode(active, invItem)` — emits `LINKING_MODE`
- `setReverseLinkingMode(active, bomRow)` — emits `LINKING_MODE`
- `loadLinks(savedLinks)` — no event
- `clearLinks()` — no event
- `hasLinks()` — returns boolean

The `App` deprecated alias should be a Proxy that:
- Reads from `store` getters for simple properties
- Delegates `App.links.*` method calls to the new setter functions
- Allows `App.links.manualLinks` reads (for undo/redo snapshot compatibility)
- Allows direct property writes (`App.bomResults = x`) by calling the corresponding setter

Test the store:
- Each setter updates the corresponding getter
- Each setter that emits events actually emits them
- `snapshotLinks()` returns a deep copy
- `getThreshold()` fallback logic works
- `App` proxy compatibility: `App.bomResults = x` calls `setBomResults(x)`

- [ ] Write store.test.js with tests for getters, setters, events, and App proxy
- [ ] Run tests to verify they fail
- [ ] Rewrite store.js with new architecture + App proxy
- [ ] Run tests to verify they pass
- [ ] Run full test suite: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(store): replace App god object with getter/setter store"

---

### Task 2: Create `js/dom-refs.js`

**Files:**
- Create: `js/dom-refs.js`

Extract all stable element IDs from `index.html` into lazy getters. These are elements defined in the HTML that multiple JS modules reference:

```
bomBody, inventoryBody, importBody, invSearch, invCount, invTotalValue,
bomResults, bomSummary, bomMultiplierBar, bomQtyMult, bomSaveBtn, bomConsumeBtn,
bomClearBtn, bomPriceInfo, bomStagingToolbar, bomStagingTitle, bomThead, bomTbody,
bomDropZone, bomFileInput,
consoleLog, consoleClear, consoleEntries,
adjustModal, modalTitle, modalDetailTable, adjType, adjQty, adjNote,
adjUnitPrice, adjExtPrice, adjCancel, adjApply,
consumeModal, consumeTitle, consumeSubtitle, consumeNote, consumeCancel, consumeConfirm,
prefsModal, prefsSliders, prefsCancel, prefsSave, dkStatus, dkLogin, dkLogout,
priceModal, priceModalTitle, priceModalSubtitle, priceUnit, priceExt, priceCancel, priceApply,
closeModal, closeCancel, closeDiscard, closeSave,
toast, prefsBtn, rebuildInv, globalUndo, globalRedo
```

Each is a lazy getter: `get bomBody() { return document.getElementById("bom-body"); }`

No tests needed — this is a pure mechanical extraction. Verification happens when panels are rewritten to use it.

- [ ] Create `js/dom-refs.js` with all lazy getters
- [ ] Run: `npx eslint js/dom-refs.js && npx tsc --noEmit`
- [ ] Commit: "refactor: add dom-refs.js with centralized element lookups"

---

### Task 3: Split `css/styles.css`

**Files:**
- Read: `css/styles.css` (1,376 lines)
- Create: 13 new CSS files
- Modify: `index.html` (replace single `<link>` with multiple)
- Delete: `css/styles.css`

Split along existing section comment boundaries. The CSS is well-organized with `/* ── Section ── */` markers. Map each section to its target file:

```
css/variables.css         — :root block (~lines 1-40)
css/layout.css            — header, panels grid, scrollbar (~lines 41-120)
css/buttons.css           — .btn, .header-btn, save/consume/clear/link buttons
css/tables.css            — shared table styles, row colors, qty states, columns, designators, alternatives
css/modals.css            — .modal-overlay, .modal, .modal-title, etc.
css/panels/import.css     — #panel-import, .col-mapper, import staging
css/panels/inventory.css  — .inv-section, .inv-part-row, inventory search
css/panels/bom.css        — .bom-table-wrap, .drop-zone, .bom-staging, .summary, .multiplier-bar
css/components/toast.css  — .toast
css/components/tooltip.css — .part-preview-card and children
css/components/badges.css — .chip, status badges
css/components/console.css — .console-header, .console-entries
css/components/linking.css — .linking-banner, .link-target, @keyframes link-pulse
```

Update `index.html`:
```html
<link rel="stylesheet" href="css/variables.css">
<link rel="stylesheet" href="css/layout.css">
<link rel="stylesheet" href="css/buttons.css">
<link rel="stylesheet" href="css/tables.css">
<link rel="stylesheet" href="css/modals.css">
<link rel="stylesheet" href="css/panels/import.css">
<link rel="stylesheet" href="css/panels/inventory.css">
<link rel="stylesheet" href="css/panels/bom.css">
<link rel="stylesheet" href="css/components/toast.css">
<link rel="stylesheet" href="css/components/tooltip.css">
<link rel="stylesheet" href="css/components/badges.css">
<link rel="stylesheet" href="css/components/console.css">
<link rel="stylesheet" href="css/components/linking.css">
```

Verification: visual inspection (app looks identical), ESLint, tsc.

- [ ] Read styles.css and identify exact section boundaries
- [ ] Create all 13 CSS files with correct content
- [ ] Update index.html with new link tags
- [ ] Delete css/styles.css
- [ ] Run: `npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(css): split monolithic styles.css into component files"

---

### Task 4: Split `inventory_api.py`

**Files:**
- Create: `csv_io.py`, `inventory_ops.py`, `price_ops.py`, `file_dialogs.py`
- Rewrite: `inventory_api.py` (thin facade)
- Modify: `tests/python/test_inventory_api.py` (update imports if needed)

Extract functions from `inventory_api.py` into focused modules:

**`csv_io.py`** — from inventory_api.py:
- `append_csv_rows()` (lines 110-128)
- `migrate_csv_header()` (lines 130-146)
- `fix_double_utf8()` (lines 148-156)
- `read_text()` (lines 923-930)
- `convert_xls_to_csv()` (lines 784-846)

**`inventory_ops.py`** — from inventory_api.py:
- `get_part_key()` (lines 158-176)
- `_read_raw_inventory()` → `read_and_merge()` (lines 180-230)
- `_apply_adjustments()` → `apply_adjustments()` (lines 232-268)
- `_categorize_and_sort()` → `categorize_and_sort()` (lines 270-285)
- `_write_organized()` → `write_organized()` (lines 287-300)
- `_rebuild()` → `rebuild()` (lines 302-311)
- `_load_organized()` → `load_organized()` (lines 313-345)
- `_append_adjustment()` → `append_adjustment()` (lines 349-362)
- `rollback_source()` (lines 364-398)
- `_truncate_csv()` → `truncate_csv()` (lines 507-533)

**`price_ops.py`** — from inventory_api.py:
- `_parse_qty()` → `parse_qty()` (lines 89-95)
- `_parse_price()` → `parse_price()` (lines 97-103)
- `_ensure_parsed()` → `ensure_parsed()` (lines 105-108)
- Price update logic from `update_part_price()` (lines 570-624)
- Price update logic from `update_part_fields()` (lines 638-677)

**`file_dialogs.py`** — from inventory_api.py:
- `open_file_dialog()` (lines 848-885)
- `save_file_dialog()` (lines 753-782)
- `load_file()` (lines 887-905)
- `detect_columns()` (lines 679-735)

**`inventory_api.py`** becomes ~150 lines:
- Class definition, `__init__`, constants, lock
- Each public method delegates to the appropriate module
- Vendor client delegates stay as one-liners

Critical: the public API surface (method names, signatures, return types) must be identical. JS frontend doesn't change at all.

- [ ] Create `csv_io.py` with extracted functions
- [ ] Create `inventory_ops.py` with extracted functions
- [ ] Create `price_ops.py` with extracted functions
- [ ] Create `file_dialogs.py` with extracted functions
- [ ] Rewrite `inventory_api.py` as thin facade
- [ ] Run: `pytest tests/python/ -v` (all 196+ tests must pass)
- [ ] Run: `ruff check inventory_api.py csv_io.py inventory_ops.py price_ops.py file_dialogs.py`
- [ ] Commit: "refactor(python): split inventory_api.py into focused modules"

---

## Phase 2: Extract Logic & Renderers (depends on Task 1 store.js)

### Task 5: Extract `js/bom/bom-logic.js` and `js/bom/bom-renderer.js`

**Files:**
- Create: `js/bom/bom-logic.js`, `js/bom/bom-renderer.js`
- Rewrite: `js/bom-panel.js` → `js/bom/bom-panel.js`
- Delete: `js/bom-panel.js` (old location)
- Create: `tests/js/bom-logic.test.js`, `tests/js/bom-renderer.test.js`

**`js/bom/bom-logic.js`** — extract from bom-panel.js:
- `classifyBomRow(row, bomCols)` — row classification (lines 30-50). Add `bomCols` as parameter instead of reading module-scoped var.
- `countBomWarnings(bomRawRows, bomCols)` — warning counter (lines 52-59). Add params.
- `computeRows(results, multiplier, links)` — the `computeRows()` function (lines 253-279). Takes explicit params instead of reading `lastResults`, `App.links.*`.
- `buildStatusMap(rows)` — extract from `renderBomPanel()` lines 467-471.
- `buildMissingKeys(rows, linkingMode)` — extract from `renderBomPanel()` lines 473-481.
- `prepareConsumption(results, multiplier)` — extract from consume handler lines 597-603.
- `computeBomPriceInfo(rows, multiplier)` — extract from `renderBomPriceInfo()` lines 307-320, return data instead of writing DOM.

**`js/bom/bom-renderer.js`** — extract from bom-panel.js:
- `renderDropZone()` — the initial drop zone HTML (lines 80-106 of init()).
- `renderLoadedDropZone(fileName)` — the loaded state drop zone (lines 224-228).
- `renderBomSummary(counts, fileName, multiplier)` — summary chips (lines 290-305).
- `renderBomPriceInfo(pricePerBoard, totalPrice)` — price info string.
- `renderLinkingBanner(linkingMode)` — linking banner HTML (lines 322-347). Return string instead of manipulating DOM.
- `renderStagingHead(headers)` — thead HTML (lines 351-353).
- `renderStagingRow(row, ri, bomCols, statusMap, missingKeys, linkingMode, headers)` — single row HTML. Extract from renderStagingRows loop (lines 359-448). Return HTML string, no event listeners.
- `renderBomResultPanel()` — the outer shell (bom-results container).

**`js/bom/bom-panel.js`** — thin wiring (~150-200 lines):
- `init()` exported (not called at module scope)
- Subscribes to events, calls logic, calls renderers, writes to DOM
- Event listener attachment happens here (not in renderers)
- Uses `store.*` for reads, setter functions for writes
- Uses `dom.*` for element references where available

Event listener attachment pattern for staging rows: since renderers return HTML strings, the panel uses event delegation on the tbody element rather than per-row listeners. This is actually cleaner than the current per-row approach.

- [ ] Create `js/bom/bom-logic.js` with extracted pure functions
- [ ] Create `tests/js/bom-logic.test.js` testing classifyBomRow, computeRows, buildStatusMap, prepareConsumption
- [ ] Run: `npx vitest run tests/js/bom-logic.test.js`
- [ ] Create `js/bom/bom-renderer.js` with extracted render functions
- [ ] Create `tests/js/bom-renderer.test.js` testing renderBomSummary, renderStagingRow output
- [ ] Run: `npx vitest run tests/js/bom-renderer.test.js`
- [ ] Create `js/bom/bom-panel.js` (thin wiring with init())
- [ ] Delete old `js/bom-panel.js`
- [ ] Update `js/app-init.js` to import from `js/bom/bom-panel.js`
- [ ] Run: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(bom): split bom-panel into logic/renderer/wiring"

---

### Task 6: Extract `js/import/import-logic.js` and `js/import/import-renderer.js`

**Files:**
- Create: `js/import/import-logic.js`, `js/import/import-renderer.js`
- Rewrite: `js/import-panel.js` → `js/import/import-panel.js`
- Delete: `js/import-panel.js`
- Create: `tests/js/import-logic.test.js`

**`js/import/import-logic.js`** — extract from import-panel.js:
- `TARGET_FIELDS` constant array
- `PART_ID_FIELDS` constant array
- `PO_TEMPLATES` constant object
- `autoDetectMapping(headers, targetFields)` — column auto-detection logic
- `transformImportRows(parsedRows, columnMapping, targetFields)` — row transformation
- `validateImportRows(rows, partIdFields)` — validation before import

**`js/import/import-renderer.js`** — extract from import-panel.js:
- `renderDropZone(templates)` — drop zone with template buttons
- `renderMapper(headers, rows, columnMapping, targetFields, fileName)` — the 122-line renderMapper(), return HTML string
- `renderStagingPreview(rows, columnMapping, targetFields)` — staging table preview

**`js/import/import-panel.js`** — thin wiring with `init()`.

- [ ] Create `js/import/import-logic.js`
- [ ] Create `tests/js/import-logic.test.js` testing autoDetectMapping, transformImportRows
- [ ] Run: `npx vitest run tests/js/import-logic.test.js`
- [ ] Create `js/import/import-renderer.js`
- [ ] Create `js/import/import-panel.js` (thin wiring with init())
- [ ] Delete old `js/import-panel.js`
- [ ] Update `js/app-init.js` to import from `js/import/import-panel.js`
- [ ] Run: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(import): split import-panel into logic/renderer/wiring"

---

### Task 7: Extract `js/inventory/inventory-logic.js` and `js/inventory/inventory-renderer.js`

**Files:**
- Create: `js/inventory/inventory-logic.js`, `js/inventory/inventory-renderer.js`
- Rewrite: `js/inventory-panel.js` → `js/inventory/inventory-panel.js`
- Absorb + delete: `js/bom-comparison.js`
- Delete: `js/inventory-panel.js`, `js/bom-row-data.js`
- Create: `tests/js/inventory-logic.test.js`

**`js/inventory/inventory-logic.js`** — extract from inventory-panel.js + bom-comparison.js + bom-row-data.js:
- `groupBySection(inventory, sectionHierarchy, flatSections)` — group inventory by section
- `filterByQuery(items, query)` — search filtering
- `computeBomComparison(bomData, inventory, links, thresholds)` — from bom-comparison.js: compute matched/unmatched sets
- `computeMatchedInvKeys(bomData, links)` — return Set of matched inventory keys
- `bomRowDisplayData(row, inventory, thresholds, ...)` — from bom-row-data.js

**`js/inventory/inventory-renderer.js`** — extract from inventory-panel.js + bom-comparison.js:
- `renderSectionHeader(section, count, collapsed, hideDescs)` — section header
- `renderPartRow(part, options)` — from createPartRow() (66 lines)
- `renderBomComparisonTable(comparisonRows, linkingMode, ...)` — from renderBomComparison() (84 lines)
- `renderNormalSections(groups, options)` — non-BOM inventory sections

**`js/inventory/inventory-panel.js`** — thin wiring:
- `init()` exported
- Absorbs bom-comparison.js wiring (setBomData, clearBomState, event subscriptions)
- Callback injection pattern (`initBomComparison({...})`) eliminated
- Uses event delegation for click handlers

- [ ] Create `js/inventory/inventory-logic.js`
- [ ] Create `tests/js/inventory-logic.test.js` testing groupBySection, filterByQuery, computeMatchedInvKeys
- [ ] Run: `npx vitest run tests/js/inventory-logic.test.js`
- [ ] Create `js/inventory/inventory-renderer.js`
- [ ] Create `js/inventory/inventory-panel.js` (thin wiring with init())
- [ ] Delete old `js/inventory-panel.js`, `js/bom-comparison.js`, `js/bom-row-data.js`
- [ ] Update `js/app-init.js` and any other imports
- [ ] Run: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(inventory): split inventory-panel into logic/renderer/wiring"

---

## Phase 3: Wire Up & Clean Up

### Task 8: Rewrite `js/app-init.js` with explicit initialization

**Files:**
- Rewrite: `js/app-init.js`
- Modify: `js/part-preview.js` (add `init()` export)
- Modify: `js/resize-panels.js` (add `init()` export)
- Modify: `js/inventory-modals.js` (use store/dom-refs)
- Modify: `js/preferences-modal.js` (use store/dom-refs)

Replace side-effect imports with explicit `init()` calls:

```js
import { init as initBomPanel } from './bom/bom-panel.js';
import { init as initImportPanel } from './import/import-panel.js';
import { init as initInventoryPanel } from './inventory/inventory-panel.js';
import { init as initPartPreview } from './part-preview.js';
import { init as initResizePanels } from './resize-panels.js';
```

Add `init()` exports to `part-preview.js` and `resize-panels.js` — wrap their current module-scope initialization code in an exported function.

Update `inventory-modals.js` and `preferences-modal.js` to use `store.*` for reads and setter functions for writes instead of `App.*`.

- [ ] Add `init()` export to `js/part-preview.js`
- [ ] Add `init()` export to `js/resize-panels.js`
- [ ] Update `js/inventory-modals.js` to use store/setters
- [ ] Update `js/preferences-modal.js` to use store/setters
- [ ] Rewrite `js/app-init.js` with explicit init() calls
- [ ] Run: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor(init): replace side-effect imports with explicit initialization"

---

### Task 9: Replace all remaining `App.*` references and `getElementById` calls

**Files:**
- Modify: all JS files still using `App.*` or raw `getElementById`

Grep for remaining `App\.` references across all JS files. Replace:
- `App.inventory` → `store.inventory`
- `App.bomResults` → `store.bomResults`
- `App.bomResults = x` → `setBomResults(x)`
- `App.links.addManualLink(...)` → `addManualLink(...)`
- etc.

Grep for remaining `document.getElementById` in panel/wiring files. Replace with `dom.*` imports where the element is in dom-refs.js. Keep inline `getElementById` for dynamically-created elements.

Remove the `App` deprecated proxy from store.js once all references are gone.

- [ ] Grep for `App\.` across js/ and replace all occurrences
- [ ] Grep for `document.getElementById` and replace stable refs with `dom.*`
- [ ] Remove App proxy from store.js
- [ ] Run: `npx vitest run && npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "refactor: remove App god object and centralize DOM refs"

---

### Task 10: Add JSDoc event payloads and update tsconfig

**Files:**
- Modify: `js/event-bus.js`
- Modify: `js/types.js`

Add JSDoc comments to each event in the Events object describing payload shape:

```js
/** Inventory reloaded from disk. Payload: inventory array. */
INVENTORY_LOADED: "inventory-loaded",
```

Update `js/types.js` with any new type definitions for store state shapes.

- [ ] Add JSDoc payload comments to Events object
- [ ] Update types.js if needed
- [ ] Run: `npx eslint js/ && npx tsc --noEmit`
- [ ] Commit: "docs: add event payload documentation and type definitions"

---

## Phase 4: Verify

### Task 11: Full verification

- [ ] Run: `npx vitest run` — all tests pass
- [ ] Run: `npx eslint js/` — no errors
- [ ] Run: `npx tsc --noEmit` — no type errors
- [ ] Run: `pytest tests/python/ -v` — all tests pass
- [ ] Run: `ruff check inventory_api.py csv_io.py inventory_ops.py price_ops.py file_dialogs.py`
- [ ] Verify no remaining `App\.` references (except in store.js proxy if still needed for window.App E2E compat)
- [ ] Verify old files deleted: `js/bom-panel.js`, `js/import-panel.js`, `js/inventory-panel.js`, `js/bom-comparison.js`, `js/bom-row-data.js`, `css/styles.css`
