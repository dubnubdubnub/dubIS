# Refactor Sweep 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Two behavior-preserving refactors from the follow-up audit — (#2) delete the dead legacy CSV pipeline in `inventory_ops.py`, and (#3) decompose the 816-line `js/inventory/inv-modals.js`. (Task "#1", retiring the InventoryApi pass-through, was scoped out: the facades need InventoryApi as a shared-state bag, so it would rename ~100 call sites rather than remove a layer.)

**Architecture:** Pure refactor, gated by the existing suites. #2 is Python (delete two dead functions + rework one test). #3 is JS (extract two support modules, then split the two modals, keeping `inv-modals.js` as a thin barrel so the public API and file path are unchanged).

**Tech Stack:** Python 3 (pytest), vanilla JS ES modules (vitest, eslint, tsc, playwright), no build step.

## Global Constraints

- **No behavior change.** Every task leaves the suite green.
- **Error policy:** prefer raising / `AppLog.warn`/`error` over silent catches.
- **Keep in production (do NOT delete)** — these `inventory_ops.py` helpers are used by the real pipeline: `get_part_key`, `read_and_merge`, `apply_adjustments`, `categorize_and_sort`, `compute_adjusted_qty`, `sort_key_for_section`, `append_adjustment`, `rollback_source`, `migrate_to_vendors`, `truncate_csv`, `last_po_quantity`, `load_organized`, and the constant `_FIELD_TO_COL`.
- **#3 public API frozen:** `inv-modals.js` must keep exporting `openAdjustModal`, `openPriceModal`, `init` from the same path — importers (`app-init.js`, `inv-row-build.js`, `inv-mutations.js`, `inv-bom-view.js`) and the test mock (`bom-label-checkbox.test.js`) must not need edits.
- **#3 behavior preservation is E2E-verified:** there is no fine-grained unit harness for the modals; preserve exact `render()` output strings, DOM ids/classes, and `api()` call order. Full `npx playwright test` is the gate.
- **Verify command:** `bash scripts/verify.sh` is the full gate.
- **Git discipline (mandatory for every task):** never run `git checkout <ref>`/`reset`/`stash`/`rebase`/branch-switch. Only `git add` + `git commit` (and `git mv` for renames). Before committing capture `git rev-parse HEAD`; after, confirm `git rev-parse HEAD^` equals it, else STOP + report BLOCKED.
- **Commit trailer:** every commit message ends with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## Task 1 (#2): Delete the dead legacy CSV pipeline in inventory_ops.py

**Files:**
- Modify: `inventory_ops.py` (delete `rebuild` ~:223 and `write_organized` ~:205)
- Modify: `tests/python/test_cache_db.py` (`TestIntegration.test_cache_matches_legacy_rebuild` ~:718, the only caller of `inventory_ops.rebuild` at ~:744)

**Interfaces:**
- Consumes: the KEPT helpers `read_and_merge`, `apply_adjustments`, `categorize_and_sort` (all remain in `inventory_ops.py`).
- Produces: nothing new. `rebuild` and `write_organized` cease to exist.

**Background (verified by scout):** `inventory_ops.rebuild` (→ `write_organized` → `load_organized`) is the legacy CSV→organized pipeline. It has ZERO production callers — the real runtime path is `domain/inventory.py` → `cache_db.populate_full`/`query_inventory`. `rebuild` is called only by the agreement test; `write_organized` only by `rebuild`. `load_organized` is KEPT (used by `scripts/generate-test-fixtures.py` and guarded by `test_cache_db.py:142`'s shape-parity test — do NOT touch it).

- [ ] **Step 1: Confirm zero other callers**
  Run `grep -rn "write_organized\|inventory_ops.rebuild\|\.rebuild(" --include=*.py . | grep -v "domain/inventory\|def rebuild"`. Confirm the only reference to `inventory_ops.rebuild` / `write_organized` outside their definitions is `tests/python/test_cache_db.py`. (Note: `domain.inventory.rebuild` is a DIFFERENT function — do not touch it. `cache_db`/`domain` callers of `read_and_merge`/`apply_adjustments`/`categorize_and_sort` are the KEPT helpers — leave them.)

- [ ] **Step 2: Rework the agreement test to drop the legacy `rebuild` leg**
  In `test_cache_matches_legacy_rebuild` (`tests/python/test_cache_db.py:718`), the "legacy" leg currently calls `inventory_ops.rebuild(...)` (~:744) and the "cache" leg builds via `read_and_merge`→`apply_adjustments`→`categorize_and_sort`→`populate_full`→`query_inventory`. Replace the legacy `rebuild(...)` leg with the equivalent using the KEPT helpers directly (the same calls `rebuild` made internally, minus the dead `write_organized`/`load_organized` CSV round-trip):
  ```python
  categorized = categorize_and_sort(
      apply_adjustments(read_and_merge(purchase_csv, FIELDNAMES), adjustments_csv, FIELDNAMES)
  )
  ```
  (Use the exact arg names/fieldnames the surrounding test already uses — read the test to match.) Then keep the existing assertions (line/key parity, per-key `qty`/`section`/`unit_price`) comparing `query_inventory(...)` against `categorized`. This preserves the real behavioral pin (SQLite cache path == in-memory categorized helpers) without the dead functions. Keep the docstring accurate (it no longer compares to `load_organized`'s CSV round-trip). Do NOT weaken any assertion.

- [ ] **Step 3: Delete the dead functions**
  Remove `write_organized` (~:205) and `rebuild` (~:223) from `inventory_ops.py`. Leave every other function and `_FIELD_TO_COL` intact. If either has an import that becomes unused after removal (e.g. a csv/os import only they used), remove it only if truly unused (confirm with grep), else leave.

- [ ] **Step 4: Run gates**
  ```bash
  python -m pytest tests/python/test_cache_db.py tests/python/test_inventory_ops.py -v
  ruff check inventory_ops.py tests/python/test_cache_db.py
  ```
  Expected: PASS. Then confirm nothing else broke: `python -m pytest tests/python/ -q` (ignore the pre-existing pydantic-artifact `test_gen_openapi_check_passes` if it appears; everything else must pass).

- [ ] **Step 5: Commit**
  ```bash
  git add inventory_ops.py tests/python/test_cache_db.py
  git commit -m "refactor(inventory_ops): delete dead legacy CSV pipeline (rebuild + write_organized)"
  ```

---

## Task 2 (#3a): Extract distributor-fetch.js + fetch-controller.js from inv-modals.js

**Files:**
- Create: `js/inventory/distributor-fetch.js` (move `fetchDistributorProduct` ~:164-180 and the `FETCH_SUPPLIERS` table ~:23-28)
- Create: `js/inventory/fetch-controller.js` (move `createFetchController` ~:191-466 unchanged in signature)
- Modify: `js/inventory/inv-modals.js` (import the two moved units instead of defining them)
- Test: `npx vitest run`, `npx playwright test`

**Interfaces:**
- Produces: `distributor-fetch.js` exports `fetchDistributorProduct(...)` and `FETCH_SUPPLIERS` (match current signatures exactly). `fetch-controller.js` exports `createFetchController({ panelEl, unitInput, onPartUpdated }) -> { configure, deleteEligibility, hasSourcedRows, bestDescription }` — signature and return shape UNCHANGED.
- Consumes: `fetch-controller.js` imports `fetchDistributorProduct`/`FETCH_SUPPLIERS` from `distributor-fetch.js`, and `rowPrice`/`cheapestRow` from `pricing-utils.js`, `pickBestDescription` from `pick-description.js`, plus `api`/`AppLog`/`store`/`scheduleInventoryRefresh`/`invPartKey`/`showToast`/`escHtml`/`formatMoney` from their current modules.

- [ ] **Step 1: Map the moves.** Read `inv-modals.js`. Confirm current line ranges of `FETCH_SUPPLIERS`, `fetchDistributorProduct`, and `createFetchController` (they may have shifted). Note every symbol each references so imports can be reconstructed at the new location.

- [ ] **Step 2: Create `distributor-fetch.js`** with `fetchDistributorProduct` + `FETCH_SUPPLIERS` moved verbatim (only add the imports they need). In `inv-modals.js`, delete those definitions and import them from the new module. Run `npx vitest run` (should stay green — no behavior change).

- [ ] **Step 3: Create `fetch-controller.js`** with `createFetchController` moved verbatim (add its imports; import `fetchDistributorProduct`/`FETCH_SUPPLIERS` from `distributor-fetch.js`). Keep `applyFix`↔`configure` together in this module (they are mutually recursive — do NOT split them). In `inv-modals.js`, delete the definition and import `createFetchController` from the new module.

- [ ] **Step 4: Verify (behavior-preserving move).**
  ```bash
  npx eslint js/ && npx tsc --noEmit && npx vitest run && npx playwright test
  ```
  Full playwright is required — `adjust-fetch-price.spec.mjs` (15 tests) is the controller's real contract. All must pass. (If ONLY the known `pololu-integration`/parallel flake fails, re-run it in isolation to confirm; anything else is a regression.)

- [ ] **Step 5: Commit**
  ```bash
  git add js/inventory/distributor-fetch.js js/inventory/fetch-controller.js js/inventory/inv-modals.js
  git commit -m "refactor(js): extract distributor-fetch + fetch-controller from inv-modals"
  ```

---

## Task 3 (#3b): Split inv-modals.js into adjust-modal + price-modal + thin barrel

**Files:**
- Create: `js/inventory/adjust-modal.js` (`openAdjustModal` + the Adjust half of `init` + `EDITABLE_FIELDS`/`buildFieldInput`/`getChangedFields`/`populateDetailFields` + `lastAdjustMeta` + the `"adjust"` `UndoRedo.register`)
- Create: `js/inventory/price-modal.js` (`openPriceModal` + the Price half of `init` (the `defineFormModal` block + panel injection + patched `open`) + `_priceCtx` + `lastPriceMeta` + the `"price"` `UndoRedo.register`)
- Modify: `js/inventory/inv-modals.js` → becomes a thin barrel: `export { openAdjustModal } from './adjust-modal.js'; export { openPriceModal } from './price-modal.js';` and an `init()` that calls both modules' init functions in the current order.
- Modify: `tests/python/test_dev_tools_mcp.py` (~:33,:44 — the assertion that a devtools grep finds `api("adjust_part")` in `inv-modals.js`; after the split that call lives in `adjust-modal.js`)
- Test: `npx vitest run`, `npx playwright test`, `pytest tests/python/test_dev_tools_mcp.py`

**Interfaces:**
- Consumes: both new modules import `createFetchController` from `fetch-controller.js` (Task 2). Adjust constructs `createFetchController({..., onPartUpdated})` and uses `deleteEligibility`/`bestDescription`/`hasSourcedRows`; Price constructs its own controller and uses only `configure`.
- Produces: `inv-modals.js` re-exports `openAdjustModal`, `openPriceModal`, `init` (unchanged public API + path). Each modal owns its own undo `*Meta` global and `UndoRedo.register` block.

- [ ] **Step 1: Map the split.** Read `inv-modals.js` (post-Task-2). Identify the Adjust-only vs Price-only regions of `init()` and the two `UndoRedo.register` blocks. Confirm `_priceCtx`, `lastAdjustMeta`, `lastPriceMeta` each belong to exactly one side. Note DOM lookups each half needs (whether `init` does `getElementById` centrally or each module does its own — pick one and keep it consistent).

- [ ] **Step 2: Create `adjust-modal.js`.** Move the Adjust modal (`openAdjustModal`, its helpers, the Adjust half of `init` incl. the description-fetch handler and delete-part flow and adjust-apply handler that calls `api("adjust_part")`, `lastAdjustMeta`, the `"adjust"` undo register). Export `openAdjustModal` and an `initAdjustModal()`.

- [ ] **Step 3: Create `price-modal.js`.** Move the Price modal (`openPriceModal`, the `defineFormModal` block, panel injection, patched `open`, `_priceCtx`, `lastPriceMeta`, the `"price"` undo register). Export `openPriceModal` and an `initPriceModal()`.

- [ ] **Step 4: Make `inv-modals.js` a thin barrel.** Re-export `openAdjustModal`/`openPriceModal`; define `init()` = call `initAdjustModal()` then `initPriceModal()` in the SAME order the old `init()` did its work. Keep the file at `js/inventory/inv-modals.js`. Confirm the 4 production importers and the `bom-label-checkbox.test.js` mock target still resolve unchanged.

- [ ] **Step 5: Update the devtools MCP test.** In `tests/python/test_dev_tools_mcp.py` (~:33,:44), the assertion expects `api("adjust_part")` to be found in `inv-modals.js`. After the split it lives in `adjust-modal.js`. Update the expected file path to `adjust-modal.js` (verify what the assertion actually checks by reading it; keep the assertion meaningful, don't weaken it).

- [ ] **Step 6: Verify.**
  ```bash
  npx eslint js/ && npx tsc --noEmit && npx vitest run && npx playwright test
  pytest tests/python/test_dev_tools_mcp.py -v
  ```
  Full playwright required (adjust-fetch-price 15 tests, fetch-descriptions, row-handler-mapping, live adjust-modal). All green (bar the known isolated flake).

- [ ] **Step 7: Commit**
  ```bash
  git add js/inventory/adjust-modal.js js/inventory/price-modal.js js/inventory/inv-modals.js tests/python/test_dev_tools_mcp.py
  git commit -m "refactor(js): split inv-modals into adjust-modal + price-modal, keep thin barrel"
  ```

---

## Post-tasks (controller)
- Regenerate `docs/code-map.md` (`python scripts/gen-code-map.py`) after the JS module changes so the `gen-code-map --check` guard in verify.sh passes; commit as a `chore(code-map)`.
- Final gate: `bash scripts/verify.sh` + full `npx playwright test`, then whole-branch review, then PR.

## Self-Review
- **Coverage:** #2 → Task 1 (delete rebuild+write_organized, rework agreement test). #3 → Task 2 (extract fetch support modules) + Task 3 (split modals + barrel + devtools-test fix). #1 intentionally omitted (user scoped out).
- **Kept-vs-deleted is explicit** (Global Constraints lists every helper to keep; only `rebuild`+`write_organized` deleted).
- **Public API + file path preserved** for inv-modals.js (barrel); the one path-coupled test (`test_dev_tools_mcp.py`) is explicitly updated in Task 3 Step 5.
- **No placeholders:** each step has concrete files, line anchors, the transformation, and exact verify commands.
