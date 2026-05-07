# Inventory Column Headers + Scope-Cycling Sort — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sticky column-header row above the main inventory view with a per-part Unit Price column, scope-cycling sort/group controls per column, a 3-state Group toggle, and a Reset (↺) button — with state persisted to `preferences.json`.

**Architecture:** A new pure module `js/inventory/inv-sort-group.js` houses the sort-cycle stepper, sort comparators, and vendor-grouping helper. State lives in `js/inventory/inv-state.js` and is mirrored to `data/preferences.json` under a new `inventory_view` key. `inventory-renderer.js` gets a `renderInvColHeader()` function and an updated `renderPartRowHtml()` (adds Unit Price + optional section chip). `inventory-panel.js` consults state to decide which scope unit (subsection / section / global) to render and feeds each unit's parts through `applySort()`. Click wiring lives in `inv-events.js`. BOM mode and the Groups (◆) flyout are unchanged.

**Tech Stack:** Vanilla JS (ES modules, no build), Vitest (unit), Playwright (E2E), CSS variables for sticky offsets. Backend is unchanged — JS-only feature.

---

## File map

**New files:**
- `js/inventory/inv-sort-group.js` — pure sort/group logic (`nextScope`, `sortPartsBy`, `groupByVendor`).
- `tests/js/inv-sort-group.test.js` — unit tests for the pure module.
- `tests/js/e2e/inv-col-header.spec.mjs` — Playwright E2E for the new UI.

**Modified files:**
- `js/inventory/inv-state.js` — add `groupLevel`, `sortColumn`, `sortScope`, `vendorGroupScope` fields.
- `js/inventory/inventory-renderer.js` — new `renderInvColHeader(state)`; update `renderPartRowHtml` to add Unit Price column + optional section chip; remove section-only re-rendering paths if needed.
- `js/inventory/inventory-panel.js` — render the column header row, integrate sort/group into the scope-unit rendering decisions, wire reset handler.
- `js/inventory/inv-events.js` — column header click delegation.
- `js/store.js` — `loadPreferences` and `savePreferences` round-trip a new `inventory_view` slice.
- `js/app-init.js` — pass loaded `inventory_view` into `inv-state.js` after preferences resolve.
- `css/panels/inventory.css` — column header styles, `--inv-col-header-h` custom property, sticky offsets, Unit Price column width, section chip styling, vendor sub-header.

---

## Task 1: `nextScope` cycle stepper (TDD)

The pure function that advances the scope cycle.

**Files:**
- Create: `js/inventory/inv-sort-group.js`
- Test: `tests/js/inv-sort-group.test.js`

- [ ] **Step 1: Write the failing test**

Create `tests/js/inv-sort-group.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { nextScope } from '../../js/inventory/inv-sort-group.js';

describe('nextScope', () => {
  it('cycles subsection → section → global → null at groupLevel=0', () => {
    expect(nextScope(0, null)).toBe('subsection');
    expect(nextScope(0, 'subsection')).toBe('section');
    expect(nextScope(0, 'section')).toBe('global');
    expect(nextScope(0, 'global')).toBe(null);
  });

  it('cycles section → global → null at groupLevel=1', () => {
    expect(nextScope(1, null)).toBe('section');
    expect(nextScope(1, 'section')).toBe('global');
    expect(nextScope(1, 'global')).toBe(null);
  });

  it('cycles global → null at groupLevel=2', () => {
    expect(nextScope(2, null)).toBe('global');
    expect(nextScope(2, 'global')).toBe(null);
  });

  it('coerces invalid current scope back to first scope of the level', () => {
    // If groupLevel changes to 1 while scope was "subsection", treat as null.
    expect(nextScope(1, 'subsection')).toBe('section');
    expect(nextScope(2, 'subsection')).toBe('global');
    expect(nextScope(2, 'section')).toBe('global');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `nextScope`**

Create `js/inventory/inv-sort-group.js`:

```js
// @ts-check
/* inv-sort-group.js — Pure helpers for the inventory column-header
   scope-cycling sort and vendor-grouping. No DOM, no store, no events. */

/**
 * Advance the scope cycle for a column based on the current Group level.
 * Returns the next scope string, or null to mean "off / default".
 *
 * groupLevel=0 (full hierarchy): subsection → section → global → null
 * groupLevel=1 (sections only):           section → global → null
 * groupLevel=2 (flat):                              global → null
 *
 * If the current scope is not reachable at the given level (e.g. user
 * lowered the Group level while a finer-grained scope was active),
 * coerces forward to the first reachable scope.
 *
 * @param {number} groupLevel  0 | 1 | 2
 * @param {string|null} current  null | "subsection" | "section" | "global"
 * @returns {string|null}
 */
export function nextScope(groupLevel, current) {
  var cycle;
  if (groupLevel === 0) cycle = ['subsection', 'section', 'global'];
  else if (groupLevel === 1) cycle = ['section', 'global'];
  else cycle = ['global'];

  if (current === null) return cycle[0];

  var idx = cycle.indexOf(current);
  // Unreachable scope at this level → treat as null.
  if (idx === -1) return cycle[0];
  // Last scope → cycle back to null.
  if (idx === cycle.length - 1) return null;
  return cycle[idx + 1];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: PASS — 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-sort-group.js tests/js/inv-sort-group.test.js
git commit -m "feat(inv): add nextScope cycle stepper for column-header sort"
```

---

## Task 2: `sortPartsBy` pure comparator (TDD)

Sorts a flat list of inventory parts by a column with the column's natural direction.

**Files:**
- Modify: `js/inventory/inv-sort-group.js`
- Modify: `tests/js/inv-sort-group.test.js`

- [ ] **Step 1: Add failing tests**

Append to `tests/js/inv-sort-group.test.js`:

```js
import { sortPartsBy } from '../../js/inventory/inv-sort-group.js';

const SAMPLE = [
  { mpn: 'BBB', qty: 5,  unit_price: 0.10, description: 'Beta',  lcsc: 'C2' },
  { mpn: 'AAA', qty: 20, unit_price: 0.50, description: 'Alpha', lcsc: 'C1' },
  { mpn: 'CCC', qty: 1,  unit_price: 5.00, description: 'Gamma', lcsc: 'C3' },
];

describe('sortPartsBy', () => {
  it('returns input unchanged when column is null', () => {
    expect(sortPartsBy(SAMPLE, null)).toEqual(SAMPLE);
  });

  it('sorts mpn ascending (A→Z)', () => {
    const out = sortPartsBy(SAMPLE, 'mpn');
    expect(out.map(p => p.mpn)).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('sorts description ascending', () => {
    const out = sortPartsBy(SAMPLE, 'description');
    expect(out.map(p => p.description)).toEqual(['Alpha', 'Beta', 'Gamma']);
  });

  it('sorts qty descending', () => {
    const out = sortPartsBy(SAMPLE, 'qty');
    expect(out.map(p => p.qty)).toEqual([20, 5, 1]);
  });

  it('sorts unit_price descending', () => {
    const out = sortPartsBy(SAMPLE, 'unit_price');
    expect(out.map(p => p.unit_price)).toEqual([5.00, 0.50, 0.10]);
  });

  it('sorts total value (qty * unit_price) descending', () => {
    const out = sortPartsBy(SAMPLE, 'value');
    // values: 5*0.10=0.5, 20*0.50=10, 1*5=5
    expect(out.map(p => p.mpn)).toEqual(['AAA', 'CCC', 'BBB']);
  });

  it('does not mutate the input array', () => {
    const copy = SAMPLE.slice();
    sortPartsBy(SAMPLE, 'qty');
    expect(SAMPLE).toEqual(copy);
  });

  it('treats missing numeric fields as 0 and sorts last in desc', () => {
    const parts = [
      { mpn: 'A', qty: 5 },
      { mpn: 'B' },
      { mpn: 'C', qty: 10 },
    ];
    const out = sortPartsBy(parts, 'qty');
    expect(out.map(p => p.mpn)).toEqual(['C', 'A', 'B']);
  });

  it('treats missing/empty strings as last in asc', () => {
    const parts = [
      { mpn: '' },
      { mpn: 'B' },
      { mpn: 'A' },
    ];
    const out = sortPartsBy(parts, 'mpn');
    expect(out.map(p => p.mpn)).toEqual(['A', 'B', '']);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: FAIL — `sortPartsBy is not a function`.

- [ ] **Step 3: Implement `sortPartsBy`**

Append to `js/inventory/inv-sort-group.js`:

```js
/**
 * Comparator config: { field, type, dir }
 *   type: "num" | "str"
 *   dir: 1 (asc) | -1 (desc)
 *   field: property accessor; "value" is computed (qty * unit_price)
 */
var SORT_CONFIG = {
  mpn:         { field: 'mpn',         type: 'str', dir: 1 },
  description: { field: 'description', type: 'str', dir: 1 },
  qty:         { field: 'qty',         type: 'num', dir: -1 },
  unit_price:  { field: 'unit_price',  type: 'num', dir: -1 },
  value:       { field: '__value',     type: 'num', dir: -1 },
};

function getNumeric(item, field) {
  if (field === '__value') {
    return (Number(item.qty) || 0) * (Number(item.unit_price) || 0);
  }
  return Number(item[field]) || 0;
}

function getString(item, field) {
  return String(item[field] || '');
}

/**
 * Return a new array of parts sorted by the given column.
 * Each column has a fixed natural direction (numeric=desc, string=asc).
 * @param {Array<Object>} parts
 * @param {string|null} column  null | "mpn" | "description" | "qty" | "unit_price" | "value"
 * @returns {Array<Object>}
 */
export function sortPartsBy(parts, column) {
  if (column === null || !SORT_CONFIG[column]) return parts;
  var cfg = SORT_CONFIG[column];
  var copy = parts.slice();
  if (cfg.type === 'num') {
    copy.sort(function (a, b) {
      var av = getNumeric(a, cfg.field);
      var bv = getNumeric(b, cfg.field);
      return (av - bv) * cfg.dir;
    });
  } else {
    copy.sort(function (a, b) {
      var av = getString(a, cfg.field);
      var bv = getString(b, cfg.field);
      // Empty strings sort last regardless of direction.
      if (av === '' && bv !== '') return 1;
      if (bv === '' && av !== '') return -1;
      var cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return cmp * cfg.dir;
    });
  }
  return copy;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: PASS — all tests pass.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-sort-group.js tests/js/inv-sort-group.test.js
git commit -m "feat(inv): add sortPartsBy with natural direction per column"
```

---

## Task 3: `groupByVendor` pure helper (TDD)

Splits a flat list of parts into vendor piles in canonical order: LCSC → Digikey → Mouser → Pololu → Other.

**Files:**
- Modify: `js/inventory/inv-sort-group.js`
- Modify: `tests/js/inv-sort-group.test.js`

- [ ] **Step 1: Add failing tests**

Append to `tests/js/inv-sort-group.test.js`:

```js
import { groupByVendor } from '../../js/inventory/inv-sort-group.js';

describe('groupByVendor', () => {
  it('returns piles in canonical vendor order, omitting empty piles', () => {
    const parts = [
      { mpn: 'A', lcsc: 'C1' },
      { mpn: 'B', digikey: 'DK-2' },
      { mpn: 'C', mouser: 'M-3' },
      { mpn: 'D' }, // no distributor → "other"
      { mpn: 'E', pololu: 'P-5' },
      { mpn: 'F', lcsc: 'C6' },
    ];
    const piles = groupByVendor(parts);
    expect(piles.map(p => p.vendor)).toEqual(['lcsc', 'digikey', 'mouser', 'pololu', 'other']);
    expect(piles[0].parts.map(p => p.mpn)).toEqual(['A', 'F']);
    expect(piles[1].parts.map(p => p.mpn)).toEqual(['B']);
    expect(piles[2].parts.map(p => p.mpn)).toEqual(['C']);
    expect(piles[3].parts.map(p => p.mpn)).toEqual(['E']);
    expect(piles[4].parts.map(p => p.mpn)).toEqual(['D']);
  });

  it('classifies multi-distributor parts under the highest-priority vendor', () => {
    // A part with both lcsc and digikey is bucketed as lcsc.
    const parts = [{ mpn: 'X', lcsc: 'C1', digikey: 'DK-1' }];
    const piles = groupByVendor(parts);
    expect(piles).toHaveLength(1);
    expect(piles[0].vendor).toBe('lcsc');
  });

  it('returns empty array when input is empty', () => {
    expect(groupByVendor([])).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: FAIL — `groupByVendor is not a function`.

- [ ] **Step 3: Implement `groupByVendor`**

Append to `js/inventory/inv-sort-group.js`:

```js
var VENDOR_ORDER = ['lcsc', 'digikey', 'mouser', 'pololu', 'other'];

function classifyVendor(item) {
  if (item.lcsc) return 'lcsc';
  if (item.digikey) return 'digikey';
  if (item.mouser) return 'mouser';
  if (item.pololu) return 'pololu';
  return 'other';
}

/**
 * Split parts into vendor piles in canonical order.
 * Empty piles are omitted from the returned array.
 * Parts retain their original relative order within each pile.
 * @param {Array<Object>} parts
 * @returns {Array<{vendor: string, parts: Array<Object>}>}
 */
export function groupByVendor(parts) {
  var buckets = { lcsc: [], digikey: [], mouser: [], pololu: [], other: [] };
  for (var i = 0; i < parts.length; i++) {
    buckets[classifyVendor(parts[i])].push(parts[i]);
  }
  var out = [];
  for (var j = 0; j < VENDOR_ORDER.length; j++) {
    var v = VENDOR_ORDER[j];
    if (buckets[v].length > 0) out.push({ vendor: v, parts: buckets[v] });
  }
  return out;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/js/inv-sort-group.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-sort-group.js tests/js/inv-sort-group.test.js
git commit -m "feat(inv): add groupByVendor pile builder"
```

---

## Task 4: Add view state fields to `inv-state.js`

State for the new column-header controls.

**Files:**
- Modify: `js/inventory/inv-state.js`

- [ ] **Step 1: Add fields**

Edit `js/inventory/inv-state.js` — add inside the `state` object literal (place these new fields just below `nearMissMap`):

```js
  // ── Column-header controls (sort + group) ──
  groupLevel: 0,           // 0 = full hierarchy (default), 1 = sections only, 2 = flat
  sortColumn: null,        // null | "mpn" | "description" | "qty" | "unit_price" | "value"
  sortScope: null,         // null | "subsection" | "section" | "global"
  vendorGroupScope: null,  // null | "subsection" | "section" | "global"
```

The full state object after edit will have:

```js
var state = {
  body: null,
  searchInput: null,
  clearFilterBtn: null,
  distFilterBar: null,

  collapsedSections: new Set(),
  bomData: null,
  activeFilter: "all",
  activeDistributors: new Set(),
  expandedAlts: new Set(),
  expandedMembers: new Set(),
  rowMap: new Map(),

  groupsSections: new Set(),
  expandedGroups: new Set(),
  groupFilters: {},

  activeFlyoutId: null,
  linkedSearchText: "",
  flyoutDragActive: false,

  DESC_HIDE_WIDTH: 680,
  hideDescs: true,

  nearMissMap: null,

  // ── Column-header controls (sort + group) ──
  groupLevel: 0,
  sortColumn: null,
  sortScope: null,
  vendorGroupScope: null,
};
```

- [ ] **Step 2: Verify it loads (no test changes yet — this is just a state addition)**

Run: `npx tsc --noEmit`
Expected: PASS (no type errors).

- [ ] **Step 3: Commit**

```bash
git add js/inventory/inv-state.js
git commit -m "feat(inv): add column-header sort/group state fields"
```

---

## Task 5: Persist `inventory_view` in store.js

Round-trip the new state slice through `data/preferences.json`.

**Files:**
- Modify: `js/store.js`

- [ ] **Step 1: Extend `loadPreferences` and add a default**

Edit `js/store.js`. Find line 17:

```js
let preferences = { thresholds: {} };
```

Change to:

```js
let preferences = {
  thresholds: {},
  inventory_view: { group_level: 0, sort_column: null, sort_scope: null, vendor_group_scope: null },
};
```

Then find `loadPreferences` (around line 184) and update the body to include the new key:

```js
export async function loadPreferences() {
  const stored = await api("load_preferences");
  if (stored && typeof stored === "object") {
    if (stored.thresholds) preferences.thresholds = stored.thresholds;
    if (stored.lastBomDir) preferences.lastBomDir = stored.lastBomDir;
    if (stored.lastImportDir) preferences.lastImportDir = stored.lastImportDir;
    if (stored.lastBomFile) preferences.lastBomFile = stored.lastBomFile;
    if (stored.inventory_view && typeof stored.inventory_view === "object") {
      preferences.inventory_view = {
        group_level: Number.isInteger(stored.inventory_view.group_level) ? stored.inventory_view.group_level : 0,
        sort_column: stored.inventory_view.sort_column || null,
        sort_scope: stored.inventory_view.sort_scope || null,
        vendor_group_scope: stored.inventory_view.vendor_group_scope || null,
      };
    }
  }
}
```

- [ ] **Step 2: Add a `saveInventoryView` setter**

After `setThreshold` (around line 213), append:

```js
export function saveInventoryView(view) {
  preferences.inventory_view = {
    group_level: view.groupLevel,
    sort_column: view.sortColumn,
    sort_scope: view.sortScope,
    vendor_group_scope: view.vendorGroupScope,
  };
  savePreferences();
}
```

- [ ] **Step 3: Type-check**

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add js/store.js
git commit -m "feat(store): persist inventory_view state in preferences.json"
```

---

## Task 6: Apply loaded preferences to `inv-state.js` on startup

After `loadPreferences()` resolves in `app-init.js`, copy the `inventory_view` slice into `inv-state.js` before `initInventoryPanel()` renders.

**Files:**
- Modify: `js/app-init.js`
- Modify: `js/inventory/inv-state.js`

- [ ] **Step 1: Add a hydrate function to `inv-state.js`**

Append to `js/inventory/inv-state.js` (after the `export default state;` — keep the default export, add a named export):

```js
export function hydrateFromPreferences(view) {
  if (!view || typeof view !== "object") return;
  if (Number.isInteger(view.group_level) && view.group_level >= 0 && view.group_level <= 2) {
    state.groupLevel = view.group_level;
  }
  state.sortColumn       = view.sort_column || null;
  state.sortScope        = view.sort_scope  || null;
  state.vendorGroupScope = view.vendor_group_scope || null;
}
```

- [ ] **Step 2: Call it from `app-init.js` after preferences load**

Edit `js/app-init.js`. Find the line where `loadPreferences()` is awaited (around line 169) and add the hydrate call immediately after:

```js
await loadPreferences();
const { default: invState, hydrateFromPreferences: hydrateInvView } =
  await import('./inventory/inv-state.js');
hydrateInvView(store.preferences.inventory_view);
```

(Use a dynamic import to avoid circular imports — `inv-state.js` is normally imported by `inventory-panel.js`, not at the top level of `app-init.js`.)

- [ ] **Step 3: Type-check**

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add js/app-init.js js/inventory/inv-state.js
git commit -m "feat(inv): hydrate column-header state from preferences on startup"
```

---

## Task 7: Add Unit Price column to part rows

Update `renderPartRowHtml` to emit a new `.part-unit-price` cell immediately before `.part-value`. Also adds optional section chip in column 1 when Group level = 2.

**Files:**
- Modify: `js/inventory/inventory-renderer.js`
- Modify: `tests/js/inventory-rendering.test.js`

- [ ] **Step 1: Add a failing test for the unit-price cell**

Find existing render tests in `tests/js/inventory-rendering.test.js` and append:

```js
import { renderPartRowHtml } from '../../js/inventory/inventory-renderer.js';

describe('renderPartRowHtml — unit price column', () => {
  const baseOpts = { hideDescs: false, isBomMode: false, isLinkSource: false, isReverseTarget: false, sectionKey: 'Resistors', threshold: 50, genericParts: null };

  it('renders $X.XX for prices ≥ $0.01', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10, unit_price: 0.05 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">\$0\.05<\/span>/);
  });

  it('renders $X.XXXX for sub-cent prices', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10, unit_price: 0.0034 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">\$0\.0034<\/span>/);
  });

  it('renders em-dash for missing/zero unit price', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">—<\/span>/);
  });

  it('renders a section chip when sectionChip option is provided', () => {
    const html = renderPartRowHtml(
      { mpn: 'A', qty: 10, unit_price: 0.05 },
      { ...baseOpts, sectionChip: 'Resistors' }
    );
    expect(html).toMatch(/<span class="inv-section-chip">Resistors<\/span>/);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/js/inventory-rendering.test.js`
Expected: FAIL — no `.part-unit-price` in output.

- [ ] **Step 3: Update `renderPartRowHtml`**

Edit `js/inventory/inventory-renderer.js`. Inside `renderPartRowHtml`, after the `valueStr` declaration (around line 97), add:

```js
  var unitPrice = Number(item.unit_price) || 0;
  var unitPriceStr;
  if (unitPrice >= 0.01) unitPriceStr = '$' + unitPrice.toFixed(2);
  else if (unitPrice > 0) unitPriceStr = '$' + unitPrice.toFixed(4);
  else unitPriceStr = '—';

  var sectionChipHtml = options.sectionChip
    ? '<span class="inv-section-chip">' + escHtml(options.sectionChip) + '</span>'
    : '';
```

Then in the `html` assembly (around line 107), insert the section chip just after the drag handle and the unit-price span just before `part-value`:

```js
  var html =
    '<span class="inv-drag-handle" title="Drag to add to group">&#x2261;</span>' +
    sectionChipHtml +
    partIdsHtml +
    nearMissBadgeHtml +
    '<span class="part-mpn" title="' + escHtml(displayMpn) + '">' + escHtml(displayMpn) + '</span>' +
    '<span class="part-unit-price">' + unitPriceStr + '</span>' +
    '<span class="part-value">' + valueStr + '</span>' +
    '<span class="part-qty" style="color:' + qtyColor + '">' + (showPriceWarn ? '<button class="price-warn-btn" title="No price data — click to set">⚠</button>' : '') + item.qty + '</span>' +
    (options.hideDescs ? '' : '<span class="part-desc"><span class="part-desc-inner" title="' + escHtml(displayDesc) + '">' + escHtml(displayDesc) + '</span></span>') +
    '<span class="part-actions">' + groupBtnStr + '<button class="btn-sm adj-btn" title="Adjust qty">Adjust</button>' +
    linkBtnStr + '</span>';
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/js/inventory-rendering.test.js`
Expected: PASS — the four new tests pass and existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inventory-renderer.js tests/js/inventory-rendering.test.js
git commit -m "feat(inv): add Unit Price column and optional section chip to part rows"
```

---

## Task 8: Add `renderInvColHeader` function

A pure function that emits the column-header HTML.

**Files:**
- Modify: `js/inventory/inventory-renderer.js`

- [ ] **Step 1: Append the function**

Append to `js/inventory/inventory-renderer.js`:

```js
// ── Column header ────────────────────────────────────────

/**
 * Build the inventory column-header HTML.
 * @param {object} viewState
 * @param {number} viewState.groupLevel        0 | 1 | 2
 * @param {string|null} viewState.sortColumn   null | "mpn" | "description" | "qty" | "unit_price" | "value"
 * @param {string|null} viewState.sortScope    null | "subsection" | "section" | "global"
 * @param {string|null} viewState.vendorGroupScope null | "subsection" | "section" | "global"
 * @param {boolean} viewState.hideDescs
 * @returns {string}
 */
export function renderInvColHeader(viewState) {
  function scopeDots(scope) {
    if (scope === 'subsection') return '·';
    if (scope === 'section')    return '··';
    if (scope === 'global')     return '···';
    return '';
  }
  function sortIndicator(col) {
    if (viewState.sortColumn !== col) return '';
    var isText = (col === 'mpn' || col === 'description');
    var arrow = isText ? '▲' : '▼';
    return '<span class="inv-col-sort-active">' + arrow + scopeDots(viewState.sortScope) + '</span>';
  }
  function vendorIndicator() {
    if (!viewState.vendorGroupScope) return '';
    return '<span class="inv-col-sort-active">⧉' + scopeDots(viewState.vendorGroupScope) + '</span>';
  }
  function groupDots() {
    if (viewState.groupLevel === 0) return '●●';   // ●●  full hierarchy
    if (viewState.groupLevel === 1) return '●○';   // ●○  sections only
    return '○○';                                    // ○○  flat
  }

  var descCellHtml = viewState.hideDescs ? '' :
    '<button class="inv-col-cell inv-col-desc" data-col="description">Description ' + sortIndicator('description') + '</button>';

  return '<div class="inv-col-header">' +
    '<button class="inv-col-cell inv-col-group" data-col="group" title="Cycle grouping: full → sections → flat">' +
      '<span class="inv-col-group-dots">' + groupDots() + '</span> Group' +
    '</button>' +
    '<button class="inv-col-cell inv-col-partid" data-col="partid" title="Group by vendor">Part # ' + vendorIndicator() + '</button>' +
    '<button class="inv-col-cell inv-col-mpn" data-col="mpn">MPN ' + sortIndicator('mpn') + '</button>' +
    '<button class="inv-col-cell inv-col-unit"  data-col="unit_price">Unit $ ' + sortIndicator('unit_price') + '</button>' +
    '<button class="inv-col-cell inv-col-value" data-col="value">Total $ ' + sortIndicator('value') + '</button>' +
    '<button class="inv-col-cell inv-col-qty"   data-col="qty">Qty ' + sortIndicator('qty') + '</button>' +
    descCellHtml +
    '<button class="inv-col-cell inv-col-reset" data-col="reset" title="Reset sort/group">↺</button>' +
    '</div>';
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add js/inventory/inventory-renderer.js
git commit -m "feat(inv): add renderInvColHeader function"
```

---

## Task 9: Inventory CSS — column header, sticky offsets, unit-price column, section chip, vendor sub-header

**Files:**
- Modify: `css/panels/inventory.css`

- [ ] **Step 1: Add CSS custom property and column header styles**

Edit `css/panels/inventory.css`. At the very top of the file (after the `.panel-inventory` rule, around line 3), insert:

```css
/* CSS custom property: column-header row height (single line of small text). */
.panel-inventory { --inv-col-header-h: 26px; }

/* ── Column Header Row ────────────────────────────────── */
.inv-col-header {
  display: flex;
  align-items: center;
  padding: 4px 12px 4px 24px;
  gap: 8px;
  position: sticky;
  top: 0;
  z-index: 6;
  height: var(--inv-col-header-h);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-default);
  user-select: none;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}
.inv-col-cell {
  background: transparent;
  border: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  padding: 0;
  cursor: pointer;
  white-space: nowrap;
  text-align: left;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.inv-col-cell:hover { color: var(--text-primary); }
.inv-col-sort-active { color: var(--color-blue); font-weight: 700; }

/* Group toggle dots */
.inv-col-group-dots { font-size: 8px; letter-spacing: 1px; color: var(--text-muted); }

/* Column widths must mirror the .inv-part-row column widths exactly. */
.inv-col-group   { width: 50px; flex-shrink: 0; }   /* drag-handle / section-chip slot */
.inv-part-row .inv-drag-handle { width: 42px; flex-shrink: 0; display: inline-flex; align-items: center; }
.inv-col-partid  { width: 100px; flex-shrink: 0; }
.inv-col-mpn     { width: 160px; flex-shrink: 0; }
.inv-col-unit    { width: 70px; flex-shrink: 0; text-align: right; justify-content: flex-end; }
.inv-col-value   { width: 70px; flex-shrink: 0; text-align: right; justify-content: flex-end; }
.inv-col-qty     { width: 60px; flex-shrink: 0; text-align: right; justify-content: flex-end; }
.inv-col-desc    { flex: 1; min-width: 0; }
.inv-col-reset   { margin-left: auto; flex-shrink: 0; font-size: 14px; }
```

- [ ] **Step 2: Add `.part-unit-price` row cell to mirror `.inv-col-unit`**

In the existing `.inv-part-row` block (around line 116–132 in the current file), add a rule for the unit-price cell. After the `.part-value` rule (currently line 129), add:

```css
.inv-part-row .part-unit-price { width: 70px; min-width: 20px; text-align: right; font-size: 11px; font-family: "SFMono-Regular", Consolas, monospace; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 3: Add section chip styling**

After the `.inv-col-reset` rule, append:

```css
/* Section chip shown on rows when Group level = 2 (flat mode) */
.inv-section-chip {
  display: inline-block;
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 8px;
  background: var(--bg-raised);
  color: var(--text-secondary);
  border: 1px solid var(--border-default);
  white-space: nowrap;
  flex-shrink: 0;
  margin-right: 4px;
}

/* Vendor sub-header (when Part # group is active) */
.inv-vendor-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px 4px 36px;
  background: var(--bg-base);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--bg-hover);
}
.inv-vendor-header .vendor-icon { width: 14px; height: 14px; }
```

- [ ] **Step 4: Update sticky offsets for parent and subsection headers**

Find the existing `.inv-section-header` rule (around line 34) — change `top: 0;` to `top: var(--inv-col-header-h);`. Find `.inv-parent-header` (around line 59) — change `top: 0;` to `top: var(--inv-col-header-h);`. Find `.inv-subsection-header` (around line 84) — change `top: 29px;` to `top: calc(var(--inv-col-header-h) + 29px);`.

The three rules become:

```css
.inv-section-header {
  /* …existing properties… */
  position: sticky;
  top: var(--inv-col-header-h);
  z-index: 3;
  user-select: none;
}

.inv-parent-header {
  /* …existing properties… */
  position: sticky;
  top: var(--inv-col-header-h);
  z-index: 4;
  user-select: none;
  border-bottom: 1px solid var(--border-default);
}

.inv-subsection-header {
  /* …existing properties… */
  position: sticky;
  top: calc(var(--inv-col-header-h) + 29px);
  z-index: 3;
  user-select: none;
}
```

(Only the `top:` and surrounding lines change; preserve every other property.)

- [ ] **Step 5: Commit**

```bash
git add css/panels/inventory.css
git commit -m "feat(inv): CSS for column header, unit-price column, section chip, sticky offsets"
```

---

## Task 10: Render the column header in `inventory-panel.js`

Insert the column header at the top of the inventory body when in normal (non-BOM) mode.

**Files:**
- Modify: `js/inventory/inventory-panel.js`

- [ ] **Step 1: Add the import**

In `js/inventory/inventory-panel.js`, find the renderer imports block (around line 28–35) and add `renderInvColHeader` to the list:

```js
import {
  renderPartRowHtml,
  createBomRowElement,
  renderAltRows,
  renderMemberRows,
  renderFilterBarHtml,
  renderBomTableHeader,
  renderInvColHeader,
} from './inventory-renderer.js';
```

- [ ] **Step 2: Render the column header at the top of normal mode**

Find the `render()` function (around line 96). Update so that the column header is rendered first when not in BOM mode:

```js
function render() {
  state.body.innerHTML = "";
  updateDistCounts();
  // Sticky offset for parent/subsection headers depends on whether the
  // column header is present (non-BOM mode only).
  state.body.style.setProperty("--inv-col-header-h", state.bomData ? "0px" : "26px");
  if (state.bomData) {
    var matchedInvKeys = renderBomComparison();
    renderRemainingInventory(matchedInvKeys, (state.searchInput.value || "").toLowerCase());
  } else {
    var headerWrap = document.createElement("div");
    headerWrap.innerHTML = renderInvColHeader({
      groupLevel: state.groupLevel,
      sortColumn: state.sortColumn,
      sortScope: state.sortScope,
      vendorGroupScope: state.vendorGroupScope,
      hideDescs: state.hideDescs,
    });
    while (headerWrap.firstChild) state.body.appendChild(headerWrap.firstChild);
    renderNormalInventory();
  }
}
```

- [ ] **Step 3: Visual smoke check**

Run: `npx eslint js/inventory/inventory-panel.js`
Expected: PASS.

Run: `npx tsc --noEmit`
Expected: PASS.

(Visual check by launching the app is optional here — wiring comes in the next task.)

- [ ] **Step 4: Commit**

```bash
git add js/inventory/inventory-panel.js
git commit -m "feat(inv): render column header at top of normal-mode inventory body"
```

---

## Task 11: Wire column header click handlers in `inv-events.js`

Click delegation: each column cycles through scopes; mutual exclusion between sort and vendor-group; reset clears everything; group toggle cycles 0→1→2.

**Files:**
- Modify: `js/inventory/inv-events.js`

- [ ] **Step 1: Add the handler**

Edit `js/inventory/inv-events.js`. Add this import at the top:

```js
import { nextScope } from './inv-sort-group.js';
import { saveInventoryView } from '../store.js';
```

Inside `setupEvents`, after the existing distributor-filter click handler (after line 71 in the current file), append:

```js
  // ── Column header clicks ──
  state.body.addEventListener("click", function (e) {
    var cell = e.target.closest(".inv-col-cell");
    if (!cell) return;
    var col = cell.dataset.col;

    if (col === "group") {
      state.groupLevel = (state.groupLevel + 1) % 3;
      // If new level can no longer support the active scope, drop active sort/vendor-group.
      if (state.groupLevel === 2 && state.sortScope && state.sortScope !== "global") {
        state.sortColumn = null; state.sortScope = null;
      }
      if (state.groupLevel === 2 && state.vendorGroupScope && state.vendorGroupScope !== "global") {
        state.vendorGroupScope = null;
      }
      if (state.groupLevel === 1 && state.sortScope === "subsection") {
        state.sortColumn = null; state.sortScope = null;
      }
      if (state.groupLevel === 1 && state.vendorGroupScope === "subsection") {
        state.vendorGroupScope = null;
      }
      persistAndRender();
      return;
    }
    if (col === "reset") {
      state.groupLevel = 0;
      state.sortColumn = null;
      state.sortScope = null;
      state.vendorGroupScope = null;
      persistAndRender();
      return;
    }
    if (col === "partid") {
      // Vendor-group cycle. Activating clears any active sort.
      state.vendorGroupScope = nextScope(state.groupLevel, state.vendorGroupScope);
      if (state.vendorGroupScope) { state.sortColumn = null; state.sortScope = null; }
      persistAndRender();
      return;
    }
    // Sort columns: mpn, unit_price, value, qty, description.
    if (col === "mpn" || col === "unit_price" || col === "value" || col === "qty" || col === "description") {
      if (state.sortColumn !== col) {
        // Switching column resets to first scope.
        state.sortColumn = col;
        state.sortScope = nextScope(state.groupLevel, null);
      } else {
        state.sortScope = nextScope(state.groupLevel, state.sortScope);
        if (state.sortScope === null) state.sortColumn = null;
      }
      // Activating sort clears vendor-group.
      if (state.sortColumn) state.vendorGroupScope = null;
      persistAndRender();
      return;
    }
  });

  function persistAndRender() {
    saveInventoryView({
      groupLevel: state.groupLevel,
      sortColumn: state.sortColumn,
      sortScope: state.sortScope,
      vendorGroupScope: state.vendorGroupScope,
    });
    render();
  }
```

- [ ] **Step 2: Lint and type-check**

Run: `npx eslint js/inventory/inv-events.js`
Expected: PASS.

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add js/inventory/inv-events.js
git commit -m "feat(inv): wire column-header click handlers (sort, group, vendor, reset)"
```

---

## Task 12: Apply sort and Group-level inside `renderNormalInventory`

Now that state is wired, integrate it into the rendering decisions. This is the biggest behavioral task.

**Files:**
- Modify: `js/inventory/inventory-panel.js`

- [ ] **Step 1: Add imports**

In `js/inventory/inventory-panel.js`, add at the top of the imports:

```js
import { sortPartsBy, groupByVendor } from './inv-sort-group.js';
```

- [ ] **Step 2: Replace `renderNormalInventory` and helpers**

Replace the existing `renderNormalInventory` function (around line 109) with a scope-aware version. The whole new block (replacing both `renderNormalInventory` and the existing `renderHierarchySection` and `renderSubSection`):

```js
function renderNormalInventory() {
  var query = (state.searchInput.value || "").toLowerCase();
  var sections = groupBySection(store.inventory);

  // ── Global scope: flatten + sort/group everything, render with no section/subsection headers ──
  if (state.sortScope === "global" || state.vendorGroupScope === "global" || state.groupLevel === 2) {
    renderGlobalScope(sections, query);
    return;
  }

  // ── Section/subsection rendering ──
  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributors);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}

function renderGlobalScope(sections, query) {
  // Flatten all parts in the order of FLAT_SECTIONS, applying filters.
  var allParts = [];
  for (var i = 0; i < FLAT_SECTIONS.length; i++) {
    var name = FLAT_SECTIONS[i];
    var bucket = sections[name] || [];
    var filtered = filterByDistributor(filterByQuery(bucket, query), state.activeDistributors);
    for (var j = 0; j < filtered.length; j++) {
      // Tag each part with its section name for the section chip (only used in flat mode).
      filtered[j].__sectionName = sectionDisplayName(name);
      allParts.push(filtered[j]);
    }
  }
  if (allParts.length === 0) return;

  if (state.vendorGroupScope === "global") {
    renderVendorPiles(allParts, /*scopeKey*/ "global");
  } else {
    var sorted = sortPartsBy(allParts, state.sortColumn);
    appendFlatRows(sorted, "global");
  }
}

function sectionDisplayName(fullKey) {
  var sep = fullKey.indexOf(" > ");
  return sep === -1 ? fullKey : fullKey.substring(sep + 3);
}

function appendFlatRows(parts, scopeKey) {
  for (var k = 0; k < parts.length; k++) {
    var row = createPartRow(parts[k], scopeKey);
    // In Group=2, surface the section chip via the existing renderer option.
    if (state.groupLevel === 2 && parts[k].__sectionName) {
      var chip = document.createElement("span");
      chip.className = "inv-section-chip";
      chip.textContent = parts[k].__sectionName;
      row.insertBefore(chip, row.querySelector(".part-ids") || row.firstChild.nextSibling);
    }
    state.body.appendChild(row);
  }
}

function renderVendorPiles(parts, scopeKey) {
  var piles = groupByVendor(parts);
  for (var p = 0; p < piles.length; p++) {
    var hdr = document.createElement("div");
    hdr.className = "inv-vendor-header";
    hdr.textContent = piles[p].vendor.charAt(0).toUpperCase() + piles[p].vendor.slice(1) + " (" + piles[p].parts.length + ")";
    state.body.appendChild(hdr);
    var pileSorted = state.sortColumn ? sortPartsBy(piles[p].parts, state.sortColumn) : piles[p].parts;
    appendFlatRows(pileSorted, scopeKey + ":" + piles[p].vendor);
  }
}

function renderHierarchySection(entry, sections, query) {
  var parentParts = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributors);
  var childData = [];
  var totalCount = parentParts.length;
  for (var i = 0; i < entry.children.length; i++) {
    var fullKey = entry.name + " > " + entry.children[i];
    var filtered = filterByDistributor(filterByQuery(sections[fullKey] || [], query), state.activeDistributors);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  var container = document.createElement("div");
  container.className = "inv-section";

  var isParentCollapsed = state.collapsedSections.has(entry.name);
  var header = document.createElement("div");
  header.className = "inv-parent-header" + (isParentCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (state.collapsedSections.has(entry.name)) state.collapsedSections.delete(entry.name);
    else state.collapsedSections.add(entry.name);
    render();
  });
  container.appendChild(header);

  if (isParentCollapsed) { state.body.appendChild(container); return; }

  // ── Section-scope sort/vendor-group: merge subsections, sort/group within section ──
  if (state.sortScope === "section" || state.vendorGroupScope === "section") {
    var merged = parentParts.slice();
    for (var c = 0; c < childData.length; c++) merged = merged.concat(childData[c].parts);
    if (state.vendorGroupScope === "section") {
      var pileWrap = document.createElement("div");
      pileWrap.className = "inv-section-vendor-piles";
      var pilesHere = groupByVendor(merged);
      for (var pp = 0; pp < pilesHere.length; pp++) {
        var phdr = document.createElement("div");
        phdr.className = "inv-vendor-header";
        phdr.textContent = pilesHere[pp].vendor.charAt(0).toUpperCase() + pilesHere[pp].vendor.slice(1) + " (" + pilesHere[pp].parts.length + ")";
        pileWrap.appendChild(phdr);
        var ps = state.sortColumn ? sortPartsBy(pilesHere[pp].parts, state.sortColumn) : pilesHere[pp].parts;
        for (var pi = 0; pi < ps.length; pi++) pileWrap.appendChild(createPartRow(ps[pi], entry.name));
      }
      container.appendChild(pileWrap);
    } else {
      var sortedSec = sortPartsBy(merged, state.sortColumn);
      for (var s = 0; s < sortedSec.length; s++) container.appendChild(createPartRow(sortedSec[s], entry.name));
    }
    state.body.appendChild(container);
    return;
  }

  // ── Default rendering: subsections visible ──
  if (state.groupLevel === 1) {
    // Sections-only mode: no subsection headers, but parts still grouped under parent.
    var allChildParts = parentParts.slice();
    for (var cc = 0; cc < childData.length; cc++) allChildParts = allChildParts.concat(childData[cc].parts);
    var maybeSorted = state.sortColumn && state.sortScope ? sortPartsBy(allChildParts, state.sortColumn) : allChildParts;
    for (var x = 0; x < maybeSorted.length; x++) container.appendChild(createPartRow(maybeSorted[x], entry.name));
  } else {
    if (parentParts.length > 0) renderSubSection(container, "Ungrouped", entry.name, parentParts);
    for (var j = 0; j < childData.length; j++) {
      if (childData[j].parts.length > 0) renderSubSection(container, childData[j].name, childData[j].fullKey, childData[j].parts);
    }
  }
  state.body.appendChild(container);
}

function renderSubSection(container, displayName, fullKey, parts) {
  var sub = document.createElement("div");
  sub.className = "inv-subsection";

  var isCollapsed = state.collapsedSections.has(fullKey);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(fullKey);

  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">◆ Groups</button>' : '');

  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(fullKey)) state.collapsedSections.delete(fullKey);
    else state.collapsedSections.add(fullKey);
    render();
  });
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(fullKey)) state.groupsSections.delete(fullKey);
      else state.groupsSections.add(fullKey);
      render();
    });
  }
  sub.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(sub, fullKey, parts);
    } else {
      // Subsection-scope sort/vendor-group is handled here.
      if (state.vendorGroupScope === "subsection") {
        var subPiles = groupByVendor(parts);
        for (var vp = 0; vp < subPiles.length; vp++) {
          var vhdr = document.createElement("div");
          vhdr.className = "inv-vendor-header";
          vhdr.textContent = subPiles[vp].vendor.charAt(0).toUpperCase() + subPiles[vp].vendor.slice(1) + " (" + subPiles[vp].parts.length + ")";
          sub.appendChild(vhdr);
          var pileParts = state.sortColumn ? sortPartsBy(subPiles[vp].parts, state.sortColumn) : subPiles[vp].parts;
          for (var pq = 0; pq < pileParts.length; pq++) sub.appendChild(createPartRow(pileParts[pq], fullKey));
        }
      } else {
        var subSorted = state.sortScope === "subsection" && state.sortColumn ? sortPartsBy(parts, state.sortColumn) : parts;
        for (var k = 0; k < subSorted.length; k++) sub.appendChild(createPartRow(subSorted[k], fullKey));
      }
    }
  }
  container.appendChild(sub);
}
```

- [ ] **Step 3: Update `renderSection` (the flat-section branch) for sort + vendor-group**

Replace the existing `renderSection` (around line 345) with:

```js
function renderSection(name, parts) {
  var section = document.createElement("div");
  section.className = "inv-section";

  var isCollapsed = state.collapsedSections.has(name);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(name);

  var header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">◆ Groups</button>' : '');

  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(name)) state.collapsedSections.delete(name);
    else state.collapsedSections.add(name);
    render();
  });
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(name)) state.groupsSections.delete(name);
      else state.groupsSections.add(name);
      render();
    });
  }
  section.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(section, name, parts);
    } else if (state.vendorGroupScope === "section" || state.vendorGroupScope === "subsection") {
      // For flat sections (no subsections), subsection-scope == section-scope.
      var piles = groupByVendor(parts);
      for (var vp = 0; vp < piles.length; vp++) {
        var vhdr = document.createElement("div");
        vhdr.className = "inv-vendor-header";
        vhdr.textContent = piles[vp].vendor.charAt(0).toUpperCase() + piles[vp].vendor.slice(1) + " (" + piles[vp].parts.length + ")";
        section.appendChild(vhdr);
        var pileParts = state.sortColumn ? sortPartsBy(piles[vp].parts, state.sortColumn) : piles[vp].parts;
        for (var pi = 0; pi < pileParts.length; pi++) section.appendChild(createPartRow(pileParts[pi], name));
      }
    } else {
      var sorted = (state.sortScope === "section" || state.sortScope === "subsection") && state.sortColumn
        ? sortPartsBy(parts, state.sortColumn)
        : parts;
      for (var k = 0; k < sorted.length; k++) section.appendChild(createPartRow(sorted[k], name));
    }
  }
  state.body.appendChild(section);
}
```

- [ ] **Step 4: Run the existing JS test suite to verify no regressions**

Run: `npx vitest run`
Expected: PASS — all existing tests still pass; new sort-group tests pass.

Run: `npx eslint js/`
Expected: PASS.

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inventory-panel.js
git commit -m "feat(inv): apply sort/group state to normal-mode rendering"
```

---

## Task 13: Manual smoke test — launch the app

Verify the feature works end-to-end before writing the E2E test.

- [ ] **Step 1: Launch the app**

Run: `python app.pyw`

- [ ] **Step 2: Verify visually**

Confirm:
- Column header row visible at top of inventory panel.
- Column header stays visible when scrolling.
- Each part row shows a Unit $ cell next to Total $.
- Click on Group cell cycles through dot states `●●` → `●○` → `○○` → `●●`. In `○○` mode, parts appear flat with section chips.
- Click on MPN cycles `▲·` → `▲··` → `▲···` → none. Sorted accordingly.
- Click on Qty cycles `▼·` → `▼··` → `▼···` → none. Sorted accordingly.
- Click on Part # produces vendor sub-headers (LCSC, Digikey, …).
- Click on ↺ Reset clears everything, restores grouping.
- Reload the app — last state is restored from `data/preferences.json`.

If anything is broken, fix and re-commit before proceeding.

- [ ] **Step 3: Commit any fixes** (if needed)

```bash
git add -A
git commit -m "fix(inv): smoke-test fixes"
```

---

## Task 14: Playwright E2E test

**Files:**
- Create: `tests/js/e2e/inv-col-header.spec.mjs`

- [ ] **Step 1: Inspect existing E2E spec for patterns**

Run: `cat tests/js/e2e/distributor-filter.spec.mjs | head -40`

You'll see the standard fixture-based setup using `page.goto()` and `expect(...).toBeVisible()`.

- [ ] **Step 2: Write the spec**

Create `tests/js/e2e/inv-col-header.spec.mjs`:

```js
// @ts-check
import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers.mjs';

test.describe('inventory column header + sort/group', () => {
  test('column header is visible and sticky', async ({ page }) => {
    await gotoApp(page);
    const header = page.locator('.inv-col-header');
    await expect(header).toBeVisible();

    // Scroll the inventory body and confirm the header is still in viewport.
    await page.evaluate(() => {
      document.getElementById('inventory-body').scrollTop = 800;
    });
    await expect(header).toBeVisible();
    const box = await header.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(0);
  });

  test('Unit $ column is rendered on each part row', async ({ page }) => {
    await gotoApp(page);
    const firstUnit = page.locator('.inv-part-row .part-unit-price').first();
    await expect(firstUnit).toBeVisible();
    const text = await firstUnit.innerText();
    expect(text).toMatch(/^\$|^—$/);
  });

  test('Group toggle cycles 0 → 1 → 2 → 0 and persists', async ({ page }) => {
    await gotoApp(page);
    const groupBtn = page.locator('.inv-col-cell.inv-col-group');

    // Click 1: groupLevel 1 (sections-only — subsection headers should disappear).
    await groupBtn.click();
    await expect(page.locator('.inv-subsection-header')).toHaveCount(0);

    // Click 2: groupLevel 2 (flat — no section/subsection headers, chips appear).
    await groupBtn.click();
    await expect(page.locator('.inv-section-header')).toHaveCount(0);
    await expect(page.locator('.inv-section-chip').first()).toBeVisible();

    // Click 3: back to groupLevel 0.
    await groupBtn.click();
    await expect(page.locator('.inv-subsection-header').first()).toBeVisible();

    // Persist: set to 2, reload, verify state restored.
    await groupBtn.click(); // -> 1
    await groupBtn.click(); // -> 2
    await page.reload();
    await expect(page.locator('.inv-section-chip').first()).toBeVisible();

    // Cleanup: reset for other tests.
    await page.locator('.inv-col-cell.inv-col-reset').click();
  });

  test('Qty column sort cycle reorders rows', async ({ page }) => {
    await gotoApp(page);
    await page.locator('.inv-col-cell.inv-col-reset').click();

    const qtyBtn = page.locator('.inv-col-cell.inv-col-qty');

    async function getFirstSubsectionQtys() {
      return page.evaluate(() => {
        const sub = document.querySelector('.inv-subsection');
        if (!sub) return [];
        return Array.from(sub.querySelectorAll('.part-qty')).map(el => parseInt(el.textContent || '0', 10));
      });
    }

    const before = await getFirstSubsectionQtys();
    await qtyBtn.click();   // subsection scope, qty desc
    const after = await getFirstSubsectionQtys();
    // After sorting by qty desc, first should be ≥ last.
    if (after.length >= 2) expect(after[0]).toBeGreaterThanOrEqual(after[after.length - 1]);
    // And the order should differ from before (unless the section happened to already be desc).
    expect(after).not.toEqual(before.slice().reverse().reverse() === before ? [] : before);

    // Cycle to global, then off.
    await qtyBtn.click();   // section
    await qtyBtn.click();   // global
    await expect(page.locator('.inv-section-header')).toHaveCount(0);
    await qtyBtn.click();   // off
    await expect(page.locator('.inv-subsection-header').first()).toBeVisible();
  });

  test('Part # creates vendor sub-headers', async ({ page }) => {
    await gotoApp(page);
    await page.locator('.inv-col-cell.inv-col-reset').click();
    await page.locator('.inv-col-cell.inv-col-partid').click();
    const vendorHeaders = page.locator('.inv-vendor-header');
    await expect(vendorHeaders.first()).toBeVisible();
    await page.locator('.inv-col-cell.inv-col-reset').click();
  });

  test('Reset button clears sort, vendor-group, and group level', async ({ page }) => {
    await gotoApp(page);
    await page.locator('.inv-col-cell.inv-col-group').click(); // -> 1
    await page.locator('.inv-col-cell.inv-col-qty').click();   // section sort
    await page.locator('.inv-col-cell.inv-col-reset').click();
    await expect(page.locator('.inv-subsection-header').first()).toBeVisible();
    await expect(page.locator('.inv-col-sort-active')).toHaveCount(0);
  });
});
```

If `tests/js/e2e/helpers.mjs` does not export `gotoApp`, inspect it:

```bash
cat tests/js/e2e/helpers.mjs | head -40
```

…and use the existing helper that loads the app page (commonly `setupAppPage` or similar). Adapt the import accordingly.

- [ ] **Step 3: Run the new spec**

Run: `npx playwright test tests/js/e2e/inv-col-header.spec.mjs`
Expected: All tests pass. If any fail, fix the implementation (not the test).

- [ ] **Step 4: Commit**

```bash
git add tests/js/e2e/inv-col-header.spec.mjs
git commit -m "test(inv): add E2E spec for column header sort/group"
```

---

## Task 15: Final verification — full suite + UI clipping regression check

**Files:** none

- [ ] **Step 1: Lint, type-check, unit tests, full E2E**

Run each in sequence; do not proceed if any fails:

```bash
npx eslint js/
npx tsc --noEmit
npx vitest run
npx playwright test
```

Expected: all pass.

- [ ] **Step 2: Verify the existing UI-clipping tests still pass without modification**

Run: `npx playwright test sticky-buttons resize-visibility`
Expected: PASS — these were called out in CLAUDE.md as load-bearing; the new column header must not clip the action buttons or violate the sticky guarantees.

- [ ] **Step 3: If anything fails**

Diagnose and fix in a focused commit. Per project policy (CLAUDE.md): never weaken the clipping tests; fix the CSS instead.

- [ ] **Step 4: Push and open a PR via the project's helper script**

```bash
bash scripts/push-pr.sh --title "feat(inv): column headers, per-part price, and scope-cycling sort"
```

Then watch CI:

```bash
gh pr checks $(gh pr view --json number -q .number)
```

If CI fails, diagnose, fix, and push again. Do not abandon a PR with failing CI.
