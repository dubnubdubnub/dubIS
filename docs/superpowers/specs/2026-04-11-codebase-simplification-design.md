# Codebase Simplification — 6 Refactoring Proposals

**Date:** 2026-04-11
**Scope:** Reduce indirection, eliminate duplication, modernize JS patterns

## Overview

Six targeted refactors to reduce complexity and improve navigability. Ordered by impact (highest first).

---

## C. Unify Adjustment Application

**Problem:** `cache_db.catch_up()` (lines 270-315) and `inventory_ops.apply_adjustments()` (lines 93-131) both implement adjustment type-switching logic (`set`/`consume`/`add`/`remove`). If one is updated without the other, cache and in-memory state silently diverge.

**Change:**
- Extract a single `apply_adjustment_row(current_qty: int, adj_type: str, adj_qty: int) -> int` function in `inventory_ops.py`
- Both `apply_adjustments()` and `cache_db.catch_up()` call this function
- The function handles the type-switch and returns the new quantity

**Files touched:** `inventory_ops.py`, `cache_db.py`
**Risk:** Low — pure function extraction, behavior-preserving. Existing Python tests cover both code paths.

---

## B. Split inventory-panel.js Into 3 View Modules

**Problem:** `inventory-panel.js` (808 lines) renders three distinct views: normal inventory sections, BOM comparison tables, and generic-part grouped views. All share collapse/expand/filter state but have independent rendering logic. Changing one view requires reading all 808 lines.

**Change:**
- Extract `renderBomComparison()` + helpers (lines 579-808) into `js/inventory/inv-bom-view.js`
- Extract `renderGroupedView()` + `renderFilterRow()` + `applyGroupFilters()` (lines 361-577) into `js/inventory/inv-groups-view.js`
- `inventory-panel.js` retains: `init()`, `render()`, `createPartRow()`, `renderNormalInventory()`, `renderSection()`, `renderHierarchySection()`, `renderSubSection()`, `updateDistFilterUI()`, `updateDistCounts()`, `createReverseLink()`
- Each extracted module exports a single entry-point function that receives needed dependencies (state, store, helpers) as arguments — no new global state

**Files touched:** `js/inventory/inventory-panel.js` (shrinks), `js/inventory/inv-bom-view.js` (new), `js/inventory/inv-groups-view.js` (new)
**Risk:** Low — pure extraction. Vitest unit tests + Playwright E2E tests cover inventory rendering.

---

## D. Kill the App Object in store.js

**Problem:** `store.js` exports two parallel APIs: the `store` getter object and the legacy `App` object. Both expose the same state. Code inconsistently uses `App.links.linkingBomRow` vs `store.links.linkingBomRow`, forcing readers to check which API each caller uses.

**Change:**
- Replace all `App.links.*` references with `store.links.*` across JS modules
- Replace all `App.genericParts` references with `store.genericParts` 
- Remove the `App` export from store.js
- For the one remaining use case (Python `evaluate_js` calling into JS), create a minimal `window.dubIS` shim object in `app-init.js` that proxies to store setters
- Check what Python `evaluate_js` calls actually exist and wire them to the shim

**Files touched:** `js/store.js`, `js/app-init.js`, all JS modules that import `App` (~8-10 files)
**Risk:** Medium — wide-reaching find-and-replace. ESLint + vitest + Playwright will catch missed references. Need to verify all `evaluate_js` calls from Python still work.

---

## A. Merge Three *_api.py Facades Into Domain Modules

**Problem:** `distributor_api.py` (72 lines), `price_api.py` (98 lines), and `generic_parts_api.py` (131 lines) exist solely to be imported by `inventory_api.py`. Each is a thin pass-through that adds a file hop without abstraction.

**Change:**
- Move `DistributorApi` methods into `distributor_manager.py` (rename class or merge methods)
- Move `PriceApi` methods into `price_history.py`
- Move `GenericPartsApi` methods into `generic_parts.py`
- Update `inventory_api.py` imports to point at the domain modules directly
- Delete the three `*_api.py` files

**Files touched:** `distributor_api.py` (deleted), `price_api.py` (deleted), `generic_parts_api.py` (deleted), `distributor_manager.py`, `price_history.py`, `generic_parts.py`, `inventory_api.py`
**Risk:** Low — method bodies move unchanged. Python tests import `inventory_api`, not the facades. Some tests may import facade classes directly — update those imports.

---

## E. Modernize JS: let/const and Drop IIFEs

**Problem:** `inventory-panel.js` uses `var` throughout and IIFE closures like `(function(gpId) { ... })(gp.generic_part_id)` for loop variable capture. This is pre-ES6 boilerplate.

**Change:**
- Replace `var` with `let` or `const` as appropriate across inventory panel files
- Replace all IIFE closure patterns with `let` in for-loops (which provides block scoping)
- Apply the same modernization to the two new view modules from proposal B

**Scope:** `js/inventory/` directory only (don't boil the ocean — other files can be modernized incrementally)
**Files touched:** `js/inventory/inventory-panel.js`, `js/inventory/inv-bom-view.js`, `js/inventory/inv-groups-view.js`
**Risk:** Very low — mechanical transformation. `let` in for-loops is semantically equivalent to the IIFE pattern. ESLint + vitest catch any mistakes.

---

## F. Inline Fixture Generation Into Vitest Setup

**Problem:** Changing Python backend logic (e.g., categorization) silently breaks JS tests via stale `tests/fixtures/generated/*.json`. The failure message doesn't mention the root cause. CI catches it, but developers waste cycles.

**Change:**
- Create a `tests/js/setup-fixtures.js` that shells out to `python scripts/generate-test-fixtures.py --check` before tests run
- If `--check` fails (fixtures stale), automatically regenerate by running `python scripts/generate-test-fixtures.py`
- Wire this into `vitest.config.js` as a `globalSetup` script
- Keep `generate-test-fixtures.py` as-is (it's still useful standalone)
- Update CLAUDE.md to note that fixture generation now happens automatically in vitest

**Files touched:** `tests/js/setup-fixtures.js` (new), `vitest.config.js`
**Risk:** Medium — adds a Python dependency to the JS test pipeline. If Python isn't available (unlikely in this project), vitest setup fails loudly. Need to handle the case where `--check` passes (skip regeneration for speed).

---

## Pre-Refactor Tests (Coverage Gaps)

Three tests to add before proposals B/E, closing identified coverage gaps in inventory rendering and row-handler mapping:

### T1. BOM comparison smoke test (vitest)

Load inventory + BOM fixture data, call the BOM comparison render path, assert:
- Expected number of BOM rows appear
- Status CSS classes (ok/warn/missing) are present on correct rows
- Filter bar renders with correct status counts

~30 lines in `tests/js/inventory-rendering.test.js`.

### T2. Generic-parts grouped view smoke test (vitest)

Render an inventory section in groups mode with mock generic-part data, assert:
- Group headers appear with correct names
- Member rows appear under expanded groups
- Collapsed groups hide member rows

~30 lines in the same test file.

### T3. Row-handler mapping test (Playwright)

Click adjust buttons on 3 different inventory rows sequentially, verify each modal shows the correct part (MPN or LCSC code matches the row that was clicked). Catches closure variable capture bugs where clicking row N triggers row M's handler.

~20 lines in `tests/js/e2e/row-handler-mapping.spec.mjs`.

---

## Execution Order

1. **T1-T3** (pre-refactor tests) — establish baseline before changes
2. **C** (unify adjustments) — no dependencies, smallest blast radius
3. **B** (split inventory-panel) — independent of Python changes
4. **E** (modernize JS) — applies to files from B, do immediately after
5. **D** (kill App object) — touches many JS files, do after B/E stabilize
6. **A** (merge Python facades) — independent of JS changes
7. **F** (inline fixtures) — touches test infra, do last

Steps 2 and 6 are Python-only. Steps 1, 3-5, 7 are JS-only. They can be parallelized across two tracks:
- **Python track:** C → A
- **JS track:** T1-T3 → B → E → D → F

---

## Testing Strategy

After each proposal:
- Run `pytest tests/python/ -v` (for Python changes)
- Run `npx vitest run` + `npx eslint js/` + `npx tsc --noEmit` (for JS changes)
- Run `npx playwright test` after T3, B, D, E (layout/interaction changes)

All 6 proposals are behavior-preserving refactors. No new features, no API changes. The existing test suite + the 3 new pre-refactor tests are the verification.
