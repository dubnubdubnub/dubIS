# Codebase Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce indirection, eliminate duplication, and modernize JS patterns across 6 targeted refactors.

**Architecture:** Behavior-preserving refactors only. Extract shared logic, merge unnecessary facades, split an 808-line monolith, modernize var/IIFE patterns, unify state APIs, and auto-generate test fixtures. Existing tests are the safety net.

**Tech Stack:** Python 3 (pytest), vanilla JS ES modules (vitest, Playwright), SQLite

---

### Task 1: Pre-refactor — Row-handler mapping Playwright test

**Files:**
- Create: `tests/js/e2e/row-handler-mapping.spec.mjs`

This test clicks adjust buttons on 3 different inventory rows and verifies each modal shows the correct part data. Catches closure variable capture bugs.

- [ ] **Step 1: Write the test**

```javascript
// tests/js/e2e/row-handler-mapping.spec.mjs
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const INVENTORY = [
  {
    section: 'Connectors', lcsc: 'C429942', mpn: 'DF40C-30DP',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'HRS', package: 'SMD', description: 'connector',
    qty: 30, unit_price: 0.29, ext_price: 8.57,
  },
  {
    section: 'Connectors', lcsc: 'C2040', mpn: 'USB-C-SMD',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'XKB', package: 'SMD', description: 'usb connector',
    qty: 10, unit_price: 0.50, ext_price: 5.00,
  },
  {
    section: 'Connectors', lcsc: 'C99999', mpn: 'FPC-20P',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'BOOMBIT', package: 'SMD', description: 'fpc connector',
    qty: 5, unit_price: 0.10, ext_price: 0.50,
  },
];

test.describe('Row handler mapping', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('clicking adjust on row N opens modal for row N', async ({ page }) => {
    const rows = page.locator('.inv-part-row');
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(3);

    for (let i = 0; i < 3; i++) {
      const row = rows.nth(i);
      const expectedLcsc = await row.locator('.part-id-lcsc').getAttribute('data-lcsc');

      await row.locator('.adj-btn').click();

      const modal = page.locator('#adjust-modal:not(.hidden)');
      await expect(modal).toBeVisible();

      const lcscInput = modal.locator('.modal-field-input[data-field="lcsc"]');
      await expect(lcscInput).toBeVisible();
      expect(await lcscInput.inputValue()).toBe(expectedLcsc);

      await modal.locator('#adj-cancel').click();
      await expect(modal).not.toBeVisible();
    }
  });
});
```

- [ ] **Step 2: Run test to verify it passes (baseline)**

Run: `npx playwright test row-handler-mapping --project functional`
Expected: PASS (this is a baseline test, not TDD — we're verifying the current behavior works before refactoring)

- [ ] **Step 3: Commit**

```bash
git add tests/js/e2e/row-handler-mapping.spec.mjs
git commit -m "test: add row-handler mapping Playwright test

Verifies that clicking adjust button on row N opens the modal for
row N (not row M). Catches closure variable capture regressions."
```

---

### Task 2: Pre-refactor — Inventory rendering smoke tests

**Files:**
- Create: `tests/js/inventory-rendering.test.js`

Smoke tests for BOM comparison rendering and generic-parts grouped view. These use jsdom via vitest to verify that the renderer functions produce expected HTML structure.

- [ ] **Step 1: Write the BOM comparison smoke test**

```javascript
// tests/js/inventory-rendering.test.js
import { describe, it, expect } from 'vitest';
import {
  renderPartRowHtml,
  renderFilterBarHtml,
} from '../../js/inventory/inventory-renderer.js';
import { countStatuses } from '../../js/part-keys.js';

describe('renderPartRowHtml', () => {
  it('includes data attributes matching the inventory item', () => {
    const item = {
      lcsc: 'C2040', mpn: 'USB-C-SMD', digikey: '', pololu: '', mouser: '',
      manufacturer: 'XKB', package: 'SMD', description: 'usb connector',
      qty: 10, unit_price: 0.50, ext_price: 5.00, section: 'Connectors',
    };
    const html = renderPartRowHtml(item, {
      hideDescs: false, isBomMode: false, isLinkSource: false,
      isReverseTarget: false, sectionKey: 'Connectors', threshold: 0,
      genericParts: [],
    });

    expect(html).toContain('data-lcsc="C2040"');
    expect(html).toContain('adj-btn');
    expect(html).toContain('USB-C-SMD');
  });

  it('renders multiple items with distinct data attributes', () => {
    const items = [
      { lcsc: 'C1111', mpn: 'R1', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 5, unit_price: 0.01, ext_price: 0.05, section: 'Passives' },
      { lcsc: 'C2222', mpn: 'R2', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 10, unit_price: 0.02, ext_price: 0.20, section: 'Passives' },
      { lcsc: 'C3333', mpn: 'C1', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 3, unit_price: 0.03, ext_price: 0.09, section: 'Passives' },
    ];
    const htmls = items.map(item => renderPartRowHtml(item, {
      hideDescs: false, isBomMode: false, isLinkSource: false,
      isReverseTarget: false, sectionKey: 'Passives', threshold: 0,
      genericParts: [],
    }));

    expect(htmls[0]).toContain('data-lcsc="C1111"');
    expect(htmls[1]).toContain('data-lcsc="C2222"');
    expect(htmls[2]).toContain('data-lcsc="C3333"');
    expect(htmls[0]).not.toContain('C2222');
    expect(htmls[1]).not.toContain('C1111');
  });
});

describe('renderFilterBarHtml', () => {
  it('renders filter buttons with correct counts', () => {
    const counts = { ok: 5, short: 2, missing: 1, possible: 0, confirmed: 3 };
    const html = renderFilterBarHtml(counts, 'all');
    expect(html).toContain('filter-btn');
    expect(html).toContain('5');
    expect(html).toContain('2');
    expect(html).toContain('1');
  });
});
```

- [ ] **Step 2: Run test to verify it passes**

Run: `npx vitest run tests/js/inventory-rendering.test.js`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/js/inventory-rendering.test.js
git commit -m "test: add inventory rendering smoke tests

Verifies renderPartRowHtml produces correct data attributes and
renderFilterBarHtml includes expected counts. Baseline for
inventory-panel split refactor."
```

---

### Task 3: Unify adjustment application (Proposal C)

**Files:**
- Modify: `inventory_ops.py:123-130`
- Modify: `cache_db.py:307-310`

Extract the type-switching logic into a shared pure function. Both `apply_adjustments()` and `cache_db.catch_up()` will call it.

- [ ] **Step 1: Add `compute_adjusted_qty` to inventory_ops.py**

Add this function above `apply_adjustments()` (before line 93):

```python
def compute_adjusted_qty(current: int, adj_type: str, qty: int) -> int | None:
    """Compute new quantity after applying an adjustment.

    Returns the new quantity, or None if adj_type is unrecognized.
    """
    if adj_type == "set":
        return max(0, qty)
    elif adj_type in ("consume", "add", "remove"):
        return max(0, current + qty)
    return None
```

- [ ] **Step 2: Refactor `apply_adjustments()` to use `compute_adjusted_qty`**

Replace the type-switch block in `apply_adjustments()` (lines 124-130) with:

```python
            new_qty = compute_adjusted_qty(current, adj_type, qty)
            if new_qty is None:
                continue
            merged[pn]["Quantity"] = str(new_qty)
```

- [ ] **Step 3: Run Python tests to verify no regression**

Run: `pytest tests/python/test_inventory_api.py -v -k "adjustment"`
Expected: All 4 adjustment tests PASS (test_set_adjustment, test_add_adjustment, test_consume_adjustment, test_malformed_qty_skipped)

- [ ] **Step 4: Refactor `cache_db.catch_up()` to use `compute_adjusted_qty`**

In `cache_db.py`, update the import (line 16):
```python
from inventory_ops import apply_adjustments, compute_adjusted_qty, get_part_key, read_and_merge, sort_key_for_section
```

Replace the type-switch block in `catch_up()` (lines 307-310) with:

```python
                new_qty = compute_adjusted_qty(0, adj_type, qty)
                if new_qty is None:
                    continue
                if adj_type == "set":
                    set_stock_quantity(conn, pn, new_qty)
                else:
                    apply_stock_delta(conn, pn, qty)
```

Note: For `catch_up()`, we still need to call `set_stock_quantity` vs `apply_stock_delta` because the cache uses SQL operations. The `compute_adjusted_qty` call validates the adj_type and computes the value for `set`; for delta types, we pass `qty` directly to `apply_stock_delta` which does `MAX(0, quantity + ?)` in SQL.

- [ ] **Step 5: Run cache_db tests**

Run: `pytest tests/python/test_cache_db.py -v -k "catch_up"`
Expected: All 4 catch_up tests PASS

- [ ] **Step 6: Run full Python test suite**

Run: `pytest tests/python/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add inventory_ops.py cache_db.py
git commit -m "refactor: unify adjustment logic into compute_adjusted_qty

Extract type-switching (set/consume/add/remove) into a single
pure function. Both apply_adjustments() and cache_db.catch_up()
now use it, eliminating the risk of logic divergence."
```

---

### Task 4: Split inventory-panel.js — Extract BOM view (Proposal B, part 1)

**Files:**
- Create: `js/inventory/inv-bom-view.js`
- Modify: `js/inventory/inventory-panel.js`

Extract `renderBomComparison()`, `handleBomTableClick()`, `confirmMatch()`, `unconfirmMatch()`, `confirmAltMatch()` into a new module.

- [ ] **Step 1: Create inv-bom-view.js**

```javascript
// js/inventory/inv-bom-view.js — BOM comparison rendering for inventory panel.

import { AppLog } from '../api.js';
import { showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { App, store, snapshotLinks } from '../store.js';
import { bomKey, invPartKey, countStatuses } from '../part-keys.js';
import { openAdjustModal } from '../inventory-modals.js';
import { openCreate as openGenericCreate } from '../generic-parts-modal.js';

import {
  sortBomRows,
  bomRowDisplayData,
  computeMatchedInvKeys,
} from './inventory-logic.js';

import {
  createBomRowElement,
  renderAltRows,
  renderMemberRows,
  renderFilterBarHtml,
  renderBomTableHeader,
} from './inventory-renderer.js';

import state from './inv-state.js';
```

Then copy the following functions from `inventory-panel.js` into this file, **exactly as they appear** (lines 551-778):
- `renderBomComparison()` (lines 583-641 of current file)
- `handleBomTableClick()` (lines 646-737)
- `confirmMatch()` (lines 751-758)
- `unconfirmMatch()` (lines 761-767)
- `confirmAltMatch()` (lines 770-778)

Add this parameter to `renderBomComparison` so it can call back to the parent for reverse links:

```javascript
export function renderBomComparison(render, createReverseLink) {
```

At the top of `renderBomComparison`, where it currently reads `var query = (state.searchInput.value || "").toLowerCase()`, this stays the same — it reads `state` which is shared.

In `handleBomTableClick`, where it references `render()` (lines 654, 665 in original), it should call the passed-in `render` parameter. Wrap `handleBomTableClick` to accept `render` and `createReverseLink`:

```javascript
function makeHandleBomTableClick(render, createReverseLink) {
  return function handleBomTableClick(e) {
    // ... existing body, unchanged ...
    // Where it calls render(), it uses the closure parameter
  };
}
```

And update `renderBomComparison` to use `makeHandleBomTableClick(render, createReverseLink)` when attaching the event listener.

Export only `renderBomComparison`:
```javascript
export { renderBomComparison };
```

- [ ] **Step 2: Update inventory-panel.js to import from inv-bom-view.js**

Remove the 5 functions (renderBomComparison, handleBomTableClick, confirmMatch, unconfirmMatch, confirmAltMatch) from `inventory-panel.js`.

Add import at top:
```javascript
import { renderBomComparison } from './inv-bom-view.js';
```

Update the `render()` function call (line ~98):
```javascript
var matchedInvKeys = renderBomComparison(render, createReverseLink);
```

Remove the `openCreate as openGenericCreate` import from `inventory-panel.js` (only used by BOM view's create-generic button).

- [ ] **Step 3: Run all JS tests**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: All PASS

- [ ] **Step 4: Run Playwright E2E**

Run: `npx playwright test --project functional`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-bom-view.js js/inventory/inventory-panel.js
git commit -m "refactor: extract BOM comparison view from inventory-panel

Move renderBomComparison, handleBomTableClick, and confirm/unconfirm
functions into inv-bom-view.js. inventory-panel.js shrinks by ~200 lines."
```

---

### Task 5: Split inventory-panel.js — Extract groups view (Proposal B, part 2)

**Files:**
- Create: `js/inventory/inv-groups-view.js`
- Modify: `js/inventory/inventory-panel.js`

Extract `renderGroupedView()`, `renderFilterRow()`, `applyGroupFilters()`.

- [ ] **Step 1: Create inv-groups-view.js**

```javascript
// js/inventory/inv-groups-view.js — Generic-parts grouped view rendering.

import { escHtml } from '../ui-helpers.js';
import { App } from '../store.js';
import { openEdit as openGenericEdit } from '../generic-parts-modal.js';

import {
  groupPartsByGeneric,
  computeFilterDimensions,
  filterMembersByChips,
} from './inventory-logic.js';

import state from './inv-state.js';
```

Then copy these three functions from `inventory-panel.js` exactly as they appear:
- `renderGroupedView(container, sectionKey, parts, createPartRow, render)` — add `createPartRow` and `render` as parameters since these come from the parent
- `renderFilterRow(gp, parts, render)` — add `render` parameter
- `applyGroupFilters(gpId, parts, gp)` — no changes needed

Export `renderGroupedView`:
```javascript
export { renderGroupedView };
```

- [ ] **Step 2: Update inventory-panel.js**

Remove the 3 functions from `inventory-panel.js`.

Add import:
```javascript
import { renderGroupedView } from './inv-groups-view.js';
```

Update the two call sites in `renderSubSection()` and `renderSection()` where `renderGroupedView` is called:
```javascript
renderGroupedView(sub, fullKey, parts, createPartRow, render);
// and
renderGroupedView(section, name, parts, createPartRow, render);
```

Remove the `openEdit as openGenericEdit` import from `inventory-panel.js` if it's only used by groups view and createPartRow. Check — `createPartRow` also uses `openGenericEdit` for the group badge click, so keep the import in inventory-panel.js.

- [ ] **Step 3: Run all JS tests**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: All PASS

- [ ] **Step 4: Run Playwright E2E**

Run: `npx playwright test --project functional`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-groups-view.js js/inventory/inventory-panel.js
git commit -m "refactor: extract generic-parts groups view from inventory-panel

Move renderGroupedView, renderFilterRow, applyGroupFilters into
inv-groups-view.js. inventory-panel.js now ~350 lines (was 808)."
```

---

### Task 6: Modernize JS — let/const and drop IIFEs (Proposal E)

**Files:**
- Modify: `js/inventory/inventory-panel.js`
- Modify: `js/inventory/inv-bom-view.js`
- Modify: `js/inventory/inv-groups-view.js`

Replace `var` with `let`/`const` and replace IIFE closure patterns with `let` block scoping.

- [ ] **Step 1: Modernize inventory-panel.js**

Across the file:
- Replace `var` with `const` for values that are never reassigned (DOM lookups, function returns, computed values)
- Replace `var` with `let` for loop counters and reassigned values
- `var SECTION_HIERARCHY = ...` → `const SECTION_HIERARCHY = ...`
- `var FLAT_SECTIONS = ...` → `const FLAT_SECTIONS = ...`
- All `for (var i = ...)` → `for (let i = ...)`

- [ ] **Step 2: Modernize inv-bom-view.js**

Same transformation. Additionally, replace any IIFE patterns like:
```javascript
(function(gpId) {
  el.addEventListener("click", function() { doThing(gpId); });
})(gp.generic_part_id);
```
with:
```javascript
const gpId = gp.generic_part_id;
el.addEventListener("click", function() { doThing(gpId); });
```
Or simply rely on `let` in the enclosing for-loop.

- [ ] **Step 3: Modernize inv-groups-view.js**

Same transformation. This file has the most IIFE patterns (in `renderGroupedView` and `renderFilterRow`). For each:

Before:
```javascript
for (var i = 0; i < result.groups.length; i++) {
  var group = result.groups[i];
  // ...
  (function (gpId) {
    headerDiv.addEventListener("click", function (e) {
      // ...uses gpId...
    });
  })(gp.generic_part_id);
}
```

After:
```javascript
for (let i = 0; i < result.groups.length; i++) {
  const group = result.groups[i];
  // ...
  const gpId = gp.generic_part_id;
  headerDiv.addEventListener("click", function (e) {
    // ...uses gpId...
  });
}
```

- [ ] **Step 4: Run ESLint to catch any issues**

Run: `npx eslint js/inventory/`
Expected: PASS (no new errors — `prefer-const` rule should be satisfied)

- [ ] **Step 5: Run all JS tests + E2E**

Run: `npx vitest run && npx playwright test --project functional`
Expected: All PASS

- [ ] **Step 6: Run row-handler mapping test specifically**

Run: `npx playwright test row-handler-mapping --project functional`
Expected: PASS (this validates the IIFE→let conversion didn't break closure variable capture)

- [ ] **Step 7: Commit**

```bash
git add js/inventory/
git commit -m "refactor: modernize inventory panel JS to let/const

Replace var with let/const and eliminate IIFE closure patterns
in favor of block-scoped let bindings."
```

---

### Task 7: Kill App object in store.js (Proposal D)

**Files:**
- Modify: `js/store.js` — expand `store` object, remove `App` export
- Modify: `js/app-init.js` — add `window.dubIS` shim, update imports
- Modify: `js/inventory/inventory-panel.js` — replace `App.*` → `store.*`
- Modify: `js/inventory/inv-bom-view.js` — replace `App.*` → `store.*`
- Modify: `js/inventory/inv-groups-view.js` — replace `App.*` → `store.*`
- Modify: `js/inventory/inv-events.js` — replace `App.*` → `store.*`
- Modify: `js/bom/bom-panel.js` — replace `App.*` → `store.*`
- Modify: `js/bom/bom-events.js` — replace `App.*` → `store.*`
- Modify: `js/generic-parts-modal.js` — replace `App.*` → `store.*`

This is the widest-reaching change. Approach: first expand `store` to include everything `App` has, then do find-and-replace across all files, then remove `App`.

- [ ] **Step 1: Expand store object in store.js**

Add missing getters/setters to the `store` object (around line 51). The `store` object already has `inventory`, `bomResults`, `bomFileName`, `bomHeaders`, `bomCols`, `bomDirty`, `preferences`, and `links`. Add the missing ones:

```javascript
export const store = {
  get inventory() { return inventory; },
  get bomResults() { return bomResults; },
  get bomFileName() { return bomFileName; },
  get bomHeaders() { return bomHeaders; },
  get bomCols() { return bomCols; },
  get bomDirty() { return bomDirty; },
  get preferences() { return preferences; },
  get genericParts() { return genericParts; },
  set genericParts(v) { genericParts = v; },
  get links() {
    return _linksProxy;
  },
  SECTION_ORDER,
  SECTION_HIERARCHY,
  FLAT_SECTIONS,
};
```

Key change: `store.links` now returns `_linksProxy` directly (instead of an inline object with nested getters). This makes `store.links` identical to `App.links`. Also added `genericParts` getter/setter.

- [ ] **Step 2: Replace App references across all JS files**

In each file that imports `App` from `store.js`:

Replace all `App.links.` with `store.links.` (property reads and method calls).
Replace all `App.genericParts` with `store.genericParts`.
Replace all `App.inventory` with `store.inventory`.
Remove `App` from the import statement. Add `store` if not already imported.

**Files and changes:**

`js/inventory/inventory-panel.js`:
- Import: `{ App, store, snapshotLinks, getThreshold }` → `{ store, snapshotLinks, getThreshold }`
- `App.links.*` → `store.links.*` (all occurrences)
- `App.genericParts` → `store.genericParts`

`js/inventory/inv-bom-view.js`:
- Import: `{ App, store, snapshotLinks }` → `{ store, snapshotLinks }`
- `App.links.*` → `store.links.*`
- `App.inventory` → `store.inventory`

`js/inventory/inv-groups-view.js`:
- Import: `{ App }` → `{ store }`
- `App.links.*` → `store.links.*`
- `App.genericParts` → `store.genericParts`

`js/inventory/inv-events.js`:
- Import: `{ App }` → `{ store }`
- `App.links.*` → `store.links.*`

`js/bom/bom-panel.js`:
- Import: `{ App, store, ... }` → `{ store, ... }`
- `App.links.*` → `store.links.*`
- `App.genericParts` → `store.genericParts`

`js/bom/bom-events.js`:
- Import: `{ App, store, ... }` → `{ store, ... }`
- `App.links.*` → `store.links.*`

`js/generic-parts-modal.js`:
- Import: `{ App }` → `{ store }`
- `App.genericParts` → `store.genericParts`

`js/app-init.js`:
- Import: `{ App, loadPreferences, ... }` → `{ store, loadPreferences, ... }`
- `App.links.*` → `store.links.*`

- [ ] **Step 3: Add window.dubIS shim for Python evaluate_js**

In `js/app-init.js`, near the top (after imports), add:

```javascript
// Shim for Python evaluate_js calls that reference window globals
// closeModal is already set as window.closeModal below
// _pnpConsume is set by pnp integration
```

Check the Python `evaluate_js` calls:
- `app.py`: `closeModal.open()` — uses `window.closeModal`, not `App`. Safe.
- `pnp_server.py`: `window._pnpConsume(...)` — uses `window._pnpConsume`, not `App`. Safe.
- `digikey_client.py`: raw DOM scraping, no `App` reference. Safe.

No shim needed. None of the `evaluate_js` calls reference `App`.

- [ ] **Step 4: Remove App export from store.js**

Delete the `App` const and `_linksProxy` const definitions (lines 155-188). Wait — `_linksProxy` is still needed since `store.links` returns it. Keep `_linksProxy`, delete only the `App` export.

Remove: `export const App = { ... };` (lines 174-188).
Remove the `App` from exports but keep `_linksProxy` as a module-private variable.

- [ ] **Step 5: Run ESLint to catch any missed App references**

Run: `npx eslint js/`
Expected: PASS. If any file still references `App`, ESLint `no-undef` will catch it.

- [ ] **Step 6: Run full test suite**

Run: `npx vitest run && npx playwright test --project functional`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add js/
git commit -m "refactor: remove App object, use store exclusively

Replace all App.links/App.genericParts/App.inventory references
with store equivalents. No Python evaluate_js calls referenced App,
so no shim needed. store is now the single state API."
```

---

### Task 8: Merge distributor_api.py into distributor_manager.py (Proposal A, part 1)

**Files:**
- Modify: `distributor_manager.py`
- Modify: `inventory_api.py:87-90, 515-540`
- Modify: `tests/python/test_distributor_api.py`
- Delete: `distributor_api.py`

- [ ] **Step 1: Add fetch methods to DistributorManager**

In `distributor_manager.py`, add the import and methods from `distributor_api.py`:

Add import at top:
```python
from base_client import BaseProductClient
```

Add these methods to the `DistributorManager` class:

```python
    def _fetch_product(self, client: BaseProductClient, identifier: str,
                       *, debug: bool = False) -> dict[str, Any] | None:
        """Fetch a product via the given client, stripping _debug in non-debug mode."""
        result = client.fetch_product(identifier)
        if result and not debug:
            result.pop("_debug", None)
        return result

    def fetch_lcsc_product(self, product_code: str, *, debug: bool = False) -> dict[str, Any] | None:
        return self._fetch_product(self._lcsc, product_code, debug=debug)

    def fetch_digikey_product(self, part_number: str, *, debug: bool = False) -> dict[str, Any] | None:
        return self._fetch_product(self._digikey, part_number, debug=debug)

    def fetch_pololu_product(self, sku: str, *, debug: bool = False) -> dict[str, Any] | None:
        return self._fetch_product(self._pololu, sku, debug=debug)

    def fetch_mouser_product(self, part_number: str, *, debug: bool = False) -> dict[str, Any] | None:
        return self._fetch_product(self._mouser, part_number, debug=debug)
```

Add `from typing import Any` to imports if not present.

- [ ] **Step 2: Update inventory_api.py**

Replace the import and initialization (lines 19, 87-90):

```python
# Remove: from distributor_api import DistributorApi
from distributor_manager import DistributorManager
```

In `__init__`:
```python
        self._distributors = DistributorManager(self.base_dir, self._get_cache)
```

Update the delegation methods (lines 515-540). Replace `self._dist_api.` with `self._distributors.` and pass `debug=self._debug` to fetch methods:

```python
    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        return self._distributors.fetch_lcsc_product(product_code, debug=self._debug)

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        return self._distributors.fetch_digikey_product(part_number, debug=self._debug)

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        return self._distributors.fetch_pololu_product(sku, debug=self._debug)

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        return self._distributors.fetch_mouser_product(part_number, debug=self._debug)

    def check_digikey_session(self) -> dict[str, Any]:
        return self._distributors.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        return self._distributors.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        return self._distributors.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        return self._distributors.get_digikey_login_status()

    def logout_digikey(self) -> dict[str, str]:
        return self._distributors.logout_digikey()
```

- [ ] **Step 3: Update test imports**

In `tests/python/test_distributor_api.py`, replace:
```python
from distributor_api import DistributorApi
```
with:
```python
from distributor_manager import DistributorManager
```

Update the fixture:
```python
@pytest.fixture
def dist_api(tmp_path):
    return DistributorManager(str(tmp_path), lambda: None)
```

Update any test that called `DistributorApi(...)` to use `DistributorManager(...)`. The fetch method signatures now take `debug=` kwarg, so update test calls if they test debug stripping:
```python
result = dist_api.fetch_lcsc_product("C2040", debug=True)
```

- [ ] **Step 4: Delete distributor_api.py**

```bash
git rm distributor_api.py
```

- [ ] **Step 5: Run Python tests**

Run: `pytest tests/python/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add distributor_manager.py inventory_api.py tests/python/test_distributor_api.py
git commit -m "refactor: merge distributor_api.py into distributor_manager.py

Move product fetch methods and debug-stripping into DistributorManager.
Eliminates one indirection layer. InventoryApi now delegates directly
to DistributorManager."
```

---

### Task 9: Merge price_api.py into price_history.py (Proposal A, part 2)

**Files:**
- Modify: `price_history.py`
- Modify: `inventory_api.py:94-96, 504-511`
- Modify: `tests/python/test_price_api.py`
- Delete: `price_api.py`

- [ ] **Step 1: Add PriceApi methods to price_history.py**

Add the `_resolve_part_key` function and the two API methods to `price_history.py` as module-level functions:

```python
def resolve_part_key(conn: Any, key: str) -> str | None:
    """Resolve a distributor-specific PN to the inventory part_id.

    Checks for a direct match first, then searches distributor columns
    (lcsc, mpn, digikey, pololu, mouser) in the parts table.
    """
    try:
        if conn.execute("SELECT 1 FROM parts WHERE part_id = ?", (key,)).fetchone():
            return key
        for col in ("lcsc", "mpn", "digikey", "pololu", "mouser"):
            row = conn.execute(
                f"SELECT part_id FROM parts WHERE {col} = ?", (key,)
            ).fetchone()
            if row:
                return row["part_id"]
    except (sqlite3.OperationalError, sqlite3.InterfaceError):
        logger.debug("resolve_part_key: cache busy, falling back to raw key")
        return key
    return None


def record_fetched_prices(conn: Any, events_dir: str, part_key: str,
                          distributor: str, price_tiers: list[dict[str, Any]]) -> None:
    """Record prices fetched from a distributor API/scraper."""
    resolved_key = resolve_part_key(conn, part_key)
    if not resolved_key:
        logger.warning("record_fetched_prices: no inventory part for %r", part_key)
        return
    os.makedirs(events_dir, exist_ok=True)
    observations = []
    for tier in price_tiers:
        price = float(tier.get("price", 0))
        if price <= 0:
            continue
        observations.append({
            "part_id": resolved_key,
            "distributor": distributor,
            "unit_price": price,
            "source": "live_fetch",
            "moq": tier.get("qty", ""),
        })
    if observations:
        record_observations(events_dir, observations)
        populate_prices_cache(conn, events_dir)


def get_price_summary(conn: Any, events_dir: str, part_key: str) -> dict[str, dict[str, Any]]:
    """Get aggregated pricing per distributor for a part."""
    resolved_key = resolve_part_key(conn, part_key) or part_key
    try:
        if not conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone():
            if os.path.exists(events_dir):
                populate_prices_cache(conn, events_dir)
        rows = conn.execute(
            "SELECT * FROM prices WHERE part_id = ?", (resolved_key,)
        ).fetchall()
    except (sqlite3.OperationalError, sqlite3.InterfaceError):
        logger.debug("get_price_summary: cache busy for %r", part_key)
        return {}
    result = {}
    for row in rows:
        result[row["distributor"]] = {
            "latest_unit_price": row["latest_unit_price"],
            "avg_unit_price": row["avg_unit_price"],
            "price_count": row["price_count"],
            "last_observed": row["last_observed"],
            "moq": row["moq"],
            "source": row["source"],
        }
    return result
```

Add missing imports to `price_history.py`:
```python
import os
import sqlite3
from typing import Any
```

- [ ] **Step 2: Update inventory_api.py**

Remove `from price_api import PriceApi` import and `self._price_api` initialization.

Add import:
```python
import price_history
```

Replace delegation methods:
```python
    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        return price_history.record_fetched_prices(
            self._get_cache(), self.events_dir, part_key, distributor, price_tiers)

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        return price_history.get_price_summary(
            self._get_cache(), self.events_dir, part_key)
```

- [ ] **Step 3: Update test imports**

In `tests/python/test_price_api.py`, replace:
```python
from price_api import PriceApi
```
with:
```python
import price_history
```

Update the fixture to pass `conn` and `events_dir` directly. Update test calls:
```python
# Before: price_api.record_fetched_prices(key, dist, tiers)
# After:  price_history.record_fetched_prices(db, events_dir, key, dist, tiers)
```

Each test method needs its `db` and `events_dir` parameters updated.

- [ ] **Step 4: Delete price_api.py**

```bash
git rm price_api.py
```

- [ ] **Step 5: Run Python tests**

Run: `pytest tests/python/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add price_history.py inventory_api.py tests/python/test_price_api.py
git commit -m "refactor: merge price_api.py into price_history.py

Move resolve_part_key, record_fetched_prices, get_price_summary
into the price_history module. Eliminates facade indirection."
```

---

### Task 10: Merge generic_parts_api.py into generic_parts.py (Proposal A, part 3)

**Files:**
- Modify: `generic_parts.py`
- Modify: `inventory_api.py:91-93, 544-569`
- Modify: `tests/python/test_generic_parts_api.py`
- Delete: `generic_parts_api.py`

This is the largest facade (195 lines). The key logic to absorb: JSON string→dict parsing, events_dir creation, and member fetching.

- [ ] **Step 1: Add JSON-accepting wrappers to generic_parts.py**

Add helper functions that accept both `str` and `dict` for JSON arguments, then delegate to existing functions:

```python
def _parse_json(value: str | dict) -> dict:
    """Accept both JSON strings and dicts."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def create_generic_part_from_api(
    conn, events_dir: str, name: str, part_type: str,
    spec_json: str | dict, strictness_json: str | dict,
) -> dict[str, Any]:
    """API entry point — parses JSON args, creates part, auto-matches, returns with members."""
    spec = _parse_json(spec_json)
    strictness = _parse_json(strictness_json)
    os.makedirs(events_dir, exist_ok=True)
    gp = create_generic_part(conn, events_dir, name, part_type, spec, strictness)
    _auto_match(conn, events_dir, gp["generic_part_id"], spec, strictness)
    gp["members"] = fetch_members(conn, gp["generic_part_id"])
    return gp


def update_generic_part_from_api(
    conn, events_dir: str, generic_part_id: str, name: str,
    spec_json: str | dict, strictness_json: str | dict,
) -> dict[str, Any]:
    """API entry point — parses JSON, updates part, re-auto-matches, returns with members."""
    spec = _parse_json(spec_json)
    strictness = _parse_json(strictness_json)
    os.makedirs(events_dir, exist_ok=True)
    # ... existing update + auto-match logic from generic_parts_api.py
```

Add a public `fetch_members` function (was `_fetch_members` in the facade):

```python
def fetch_members(conn, generic_part_id: str) -> list[dict[str, Any]]:
    """Fetch members with their specs for a generic part."""
    rows = conn.execute(
        """SELECT gpm.part_id, gpm.is_preferred,
                  p.lcsc, p.mpn, p.description, p.package, p.manufacturer
           FROM generic_part_members gpm
           JOIN parts p ON p.part_id = gpm.part_id
           WHERE gpm.generic_part_id = ?
           ORDER BY gpm.is_preferred DESC, p.part_id""",
        (generic_part_id,),
    ).fetchall()
    members = []
    for r in rows:
        member = dict(r)
        desc = member.get("description", "") or ""
        pkg = member.get("package", "") or ""
        member["spec"] = spec_extractor.extract_spec(desc, pkg)
        members.append(member)
    return members
```

Add missing import: `import json` if not already present.

- [ ] **Step 2: Migrate remaining facade methods**

For each method in `generic_parts_api.py` that isn't already in `generic_parts.py`, create a corresponding `*_from_api` function or update the existing function signature to accept JSON strings. The key ones:

- `extract_spec(part_key)` — needs cache lookup → add `extract_spec_for_part(conn, part_key)` 
- `list_generic_parts()` — already exists as `list_generic_parts_with_member_specs(conn)`
- `add_generic_member` / `remove_generic_member` / `set_preferred_member` — existing functions, just need to return member list
- `resolve_bom_spec` — already exists
- `preview_generic_members` — already exists as `preview_members`

- [ ] **Step 3: Update inventory_api.py**

Remove `from generic_parts_api import GenericPartsApi` and `self._gp_api`.

Add:
```python
import generic_parts
```

Update delegation (already has `import generic_parts` indirectly via cache_db, but add explicit import). Replace each `self._gp_api.method(...)` call with `generic_parts.method_from_api(self._get_cache(), self.events_dir, ...)`.

- [ ] **Step 4: Update test imports**

In `tests/python/test_generic_parts_api.py`, replace `GenericPartsApi` fixture with direct calls to `generic_parts.*_from_api()` functions.

- [ ] **Step 5: Delete generic_parts_api.py**

```bash
git rm generic_parts_api.py
```

- [ ] **Step 6: Run Python tests**

Run: `pytest tests/python/ -v`
Expected: All PASS

- [ ] **Step 7: Run ruff lint**

Run: `ruff check .`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add generic_parts.py inventory_api.py tests/python/test_generic_parts_api.py
git commit -m "refactor: merge generic_parts_api.py into generic_parts.py

Move JSON parsing, member fetching, and API entry points into the
domain module. Eliminates the facade layer."
```

---

### Task 11: Inline fixture generation into vitest (Proposal F)

**Files:**
- Create: `tests/vitest-global-setup.js`
- Modify: `vitest.config.js`

- [ ] **Step 1: Create the globalSetup script**

```javascript
// tests/vitest-global-setup.js
// Auto-regenerate Python-generated fixtures if stale.
import { execSync } from 'node:child_process';

export async function setup() {
  try {
    execSync('python scripts/generate-test-fixtures.py --check', {
      stdio: 'pipe',
      timeout: 30_000,
    });
    // Fixtures are up-to-date, nothing to do
  } catch {
    // --check failed (exit code 1) → fixtures are stale, regenerate
    console.log('[vitest-global-setup] Fixtures stale, regenerating...');
    try {
      execSync('python scripts/generate-test-fixtures.py', {
        stdio: 'inherit',
        timeout: 60_000,
      });
      console.log('[vitest-global-setup] Fixtures regenerated.');
    } catch (e) {
      throw new Error(
        'Failed to regenerate test fixtures. Is Python available?\n' + e.message
      );
    }
  }
}
```

- [ ] **Step 2: Wire into vitest.config.js**

Add `globalSetup` to the test config:

```javascript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globalSetup: ['./tests/vitest-global-setup.js'],
    projects: [
      {
        test: {
          name: 'core',
          include: ['tests/js/**/*.test.js'],
          exclude: [
            'tests/js/contrast.test.js',
            'tests/js/style-audit.test.js',
            'tests/js/ui-helpers.test.js',
          ],
        },
      },
      {
        test: {
          name: 'quality',
          include: [
            'tests/js/contrast.test.js',
            'tests/js/style-audit.test.js',
            'tests/js/ui-helpers.test.js',
          ],
        },
      },
    ],
  },
});
```

- [ ] **Step 3: Test with current fixtures (should skip regeneration)**

Run: `npx vitest run --project core`
Expected: PASS. Console should NOT show "Fixtures stale" message.

- [ ] **Step 4: Test with stale fixtures (should auto-regenerate)**

Manually touch a fixture to make it stale:
```bash
echo "// stale" >> tests/fixtures/generated/inventory.json
npx vitest run --project core
git checkout tests/fixtures/generated/inventory.json
```
Expected: Console shows "Fixtures stale, regenerating..." then tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/vitest-global-setup.js vitest.config.js
git commit -m "feat: auto-regenerate stale test fixtures in vitest

Add globalSetup that runs generate-test-fixtures.py --check before
tests. If fixtures are stale, regenerates automatically. Eliminates
the 'stale fixture' failure mode."
```

---

### Task 12: Final verification

- [ ] **Step 1: Run full Python test suite**

Run: `pytest tests/python/ -v`
Expected: All PASS

- [ ] **Step 2: Run full JS test suite**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: All PASS

- [ ] **Step 3: Run full Playwright E2E**

Run: `npx playwright test`
Expected: All PASS

- [ ] **Step 4: Run ruff**

Run: `ruff check .`
Expected: PASS

- [ ] **Step 5: Verify deleted files are gone**

```bash
test ! -f distributor_api.py && test ! -f price_api.py && test ! -f generic_parts_api.py && echo "All facades deleted"
```

- [ ] **Step 6: Verify inventory-panel.js is smaller**

```bash
wc -l js/inventory/inventory-panel.js js/inventory/inv-bom-view.js js/inventory/inv-groups-view.js
```
Expected: inventory-panel.js ~350 lines, total across 3 files ~808 lines
