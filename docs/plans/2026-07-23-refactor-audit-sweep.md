# Refactor Audit Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the refactoring opportunities surfaced by the 2026-07-23 codebase audit — consolidate duplicated logic, decompose god-functions/files, remove dead code, and fix doc/memory drift — without changing behavior.

**Architecture:** Pure refactor. Every task is behavior-preserving and gated by the existing test suite. Work is grouped into waves ordered by risk (doc fixes → safe consolidation → structural splits → large migration). Each wave is independently PR-able; touched files are disjoint across waves to minimize conflict with parallel Claude instances.

**Tech Stack:** Python 3 (FastAPI backend, pytest), vanilla JS ES modules (vitest, eslint, tsc, playwright), no build step.

## Global Constraints

- **No behavior change.** Every task must leave the test suite green. Refactors are structural only.
- **Error policy:** prefer `AppLog.warn`/`AppLog.error` (JS) and raising typed errors over silent catches. Never introduce a silent failure.
- **API surface is frozen** by `tests/python/test_api_surface.py` — do not add/remove/rename public `InventoryApi` methods.
- **Inventory record shape frozen** — if any task touches the `query_inventory` dict, edit `domain/schema.py` first and run `python scripts/gen-inventory-types.py`. (No task here is expected to.)
- **Fixtures:** after any Python change to inventory/columns/prices logic, run `python scripts/generate-test-fixtures.py` before vitest.
- **Verify command:** `bash scripts/verify.sh` is the full gate. Per-change fast loops are noted in each task.
- **Commit style:** `refactor(scope): summary` / `chore(docs): …`. End commit messages with the `Co-Authored-By` trailer per repo convention.
- **JS test-only exports are real** — many exports are consumed only by `tests/js/`; do not delete an export as "unused" without grepping `tests/js/` first.

---

## Wave 1 — Doc & memory drift (no code risk)

### Task 1: Fix stale documentation references

**Files:**
- Modify: `CLAUDE.md` (Data Flow section — `price_history.py` reference)
- Modify: `server/routes/openpnp.py:4` (cites nonexistent `docs/plans/phase3a-openpnp-bridge-design.md`)
- Modify: `docs/plans/2026-04-06-phase2-frontend-completion.md`, `docs/plans/2026-04-06-phase3-feeder-tracking.md` (mark superseded)

**Interfaces:** none (docs only).

- [ ] **Step 1: Fix `price_history.py` reference in CLAUDE.md**
  Find the Data Flow line reading `prices table (populated by price_history.py …)` and change `price_history.py` → `domain/pricing.py`. Grep the whole repo for other `price_history.py` mentions (`grep -rn "price_history" --include=*.md --include=*.py .`) and fix each (the module is `domain/pricing.py`).

- [ ] **Step 2: Fix the stale plan reference in openpnp.py**
  Read `server/routes/openpnp.py:1-10`. The docstring/comment cites `docs/plans/phase3a-openpnp-bridge-design.md`, which does not exist. Verify with `ls docs/plans/ | grep -i phase3a`. Replace the reference with the actual design doc `docs/plans/2026-07-15-platform-architecture-design.md` (Phase 3 section), or drop the file citation and keep the prose description if no doc fits.

- [ ] **Step 3: Mark the superseded 04-06 plans**
  Prepend a one-line banner to the top of each 04-06 plan: `> **SUPERSEDED** by docs/plans/2026-07-15-platform-architecture-design.md (2026-07-15). Retained for history.` Do not delete them.

- [ ] **Step 4: Verify no other broken doc links introduced**
  Run `grep -rn "phase3a-openpnp" . ; grep -rn "price_history.py" .` — expect no code/doc hits remain (test files that legitimately reference a `price_history` test module, if any, are fine — inspect before editing).

- [ ] **Step 5: Commit**
  ```bash
  git add CLAUDE.md server/routes/openpnp.py docs/plans/2026-04-06-*.md
  git commit -m "chore(docs): fix stale references (price_history.py, phase3a plan) + mark superseded plans"
  ```

---

## Wave 2 — Python consolidation (safe, surgical)

### Task 2: Single normalized-product factory

**Files:**
- Create: `domain/product.py` (new `NormalizedProduct` dataclass + `build_product(...)` factory)
- Modify: `lcsc_client.py:~83`, `mouser_client.py:~236` & `~356`, `pololu_client.py:~116`, `digikey_normalizer.py:~247` & `~353` & `~378`
- Test: `tests/python/test_product_factory.py` (new) + existing `tests/python/test_normalizers.py`, `tests/python/test_clients_*.py`

**Interfaces:**
- Produces: `build_product(*, product_code, title, manufacturer, mpn, package, description, stock, prices, image_url=None, pdf_url=None, url=None, category=None, subcategory=None, attributes=None, provider, debug=None) -> dict` returning the exact dict shape all clients emit today (keys: `productCode, title, manufacturer, mpn, package, description, stock, prices, imageUrl, pdfUrl, <providerUrl>, category, subcategory, attributes, provider, _debug`). The per-provider URL key name (`lcscUrl`/`digikeyUrl`/`mouserUrl`/`pololuUrl`) is derived from `provider` or passed explicitly.

- [ ] **Step 1: Capture the current shape as the test oracle**
  Read all 7 call sites listed above and record the exact dict each emits (key names, which keys each includes/omits, default values). This is the contract the factory must reproduce byte-for-byte. Note the drift: LCSC/DigiKey-jsonld include `category`/`subcategory`; the DigiKey fallback/nextdata variants omit them — the factory defaults them to `None` so all sites converge without changing what a caller that DID set them produces.

- [ ] **Step 2: Write the failing test**
  In `tests/python/test_product_factory.py`, assert `build_product(...)` with a representative field set returns a dict equal to a hand-written expected dict matching today's LCSC output, and that the provider-URL key is named correctly per provider. Include a case with `category=None` to lock the default.
  ```python
  def test_build_product_lcsc_shape():
      p = build_product(product_code="C123", title="t", manufacturer="m", mpn="MPN",
                        package="0402", description="d", stock=10, prices=[{"qty":1,"price":0.1}],
                        provider="lcsc", url="https://lcsc.com/x")
      assert p["provider"] == "lcsc"
      assert p["lcscUrl"] == "https://lcsc.com/x"
      assert p["category"] is None and p["subcategory"] is None
      assert set(p) >= {"productCode","title","manufacturer","mpn","package","description",
                        "stock","prices","imageUrl","pdfUrl","category","subcategory",
                        "attributes","provider","_debug"}
  ```

- [ ] **Step 3: Run test to verify it fails**
  `pytest tests/python/test_product_factory.py -v` → FAIL (module not found).

- [ ] **Step 4: Implement `domain/product.py`**
  Implement `build_product` reproducing the recorded shape exactly. Map `provider` → `<provider>Url` key. Default `attributes` to `{}` (or match today's default — check step 1), `_debug` to the passed `debug`.

- [ ] **Step 5: Run new test to verify it passes**
  `pytest tests/python/test_product_factory.py -v` → PASS.

- [ ] **Step 6: Migrate the 7 call sites**
  Replace each hand-built dict with a `build_product(...)` call. Keep each client's field-extraction logic; only the final dict assembly changes.

- [ ] **Step 7: Regenerate fixtures + run full normalizer/client tests**
  ```bash
  python scripts/generate-test-fixtures.py
  pytest tests/python/test_normalizers.py tests/python/test_clients_lcsc.py tests/python/test_clients_mouser.py tests/python/test_clients_pololu.py tests/python/test_clients_digikey.py tests/python/test_product_factory.py -v
  ```
  Expected: all PASS with no value mismatches. If a fixture value shifts, the factory drifted from a call site — fix the factory, not the fixture.

- [ ] **Step 8: Commit**
  ```bash
  git add domain/product.py tests/python/test_product_factory.py lcsc_client.py mouser_client.py pololu_client.py digikey_normalizer.py tests/fixtures/
  git commit -m "refactor(distributors): single build_product factory replaces 7 hand-built product dicts"
  ```

### Task 3: Deduplicate the parts-row INSERT tuple in cache_db

**Files:**
- Modify: `cache_db.py` (`populate_full` ~:184, `upsert_part` ~:311)
- Test: existing `tests/python/test_cache_db.py`

**Interfaces:**
- Produces: `_part_row_values(row: dict) -> tuple` — returns the ordered column values for a parts-table INSERT, applying the shared `(row.get("...") or "").strip()` mapping.

- [ ] **Step 1: Confirm the two sites are identical**
  Read `cache_db.py` around `:184` and `:311`. Confirm the column order and per-field `.get(...).strip()` logic match. Note any divergence (if they differ, the helper must be parameterized or this task is deferred with a note).

- [ ] **Step 2: Extract `_part_row_values(row)`**
  Add a module-level helper returning the tuple. Replace both call sites to build their INSERT values via the helper.

- [ ] **Step 3: Run cache_db tests**
  `pytest tests/python/test_cache_db.py -v` → PASS (including the `populate_full`/`inventory_ops` agreement test).

- [ ] **Step 4: Commit**
  ```bash
  git add cache_db.py
  git commit -m "refactor(cache_db): extract _part_row_values to dedup parts INSERT tuple"
  ```

### Task 4: RebuildContext dataclass for the rebuild kwarg bundle

**Files:**
- Modify: `domain/inventory.py` (functions threading `base_dir, input_csv, adjustments_csv, events_dir, fieldnames, adj_fieldnames, conn`: `update_part_price`, `update_part_fields`, `truncate_and_rebuild`, `adjust_part`, and the `rebuild(...)` call sites)
- Test: existing `tests/python/` inventory tests + `test_api_surface.py`

**Interfaces:**
- Produces: `@dataclass RebuildContext` (frozen) with fields `base_dir, input_csv, adjustments_csv, events_dir, fieldnames, adj_fieldnames, conn`. Internal-only — must NOT change any public `inventory_api.py` signature.

- [ ] **Step 1: Confirm the shared bundle**
  Read the functions in `domain/inventory.py` that take these 7 args. Confirm they always travel together into `rebuild(...)`.

- [ ] **Step 2: Introduce `RebuildContext` internally**
  Define the dataclass. Refactor the internal `rebuild(...)` helper and its callers to pass a single `ctx: RebuildContext`. **Do not** change the outward-facing `InventoryApi`/facade signatures — the context is constructed at the domain boundary from the existing args.

- [ ] **Step 3: Run inventory + surface tests**
  ```bash
  pytest tests/python/ -k "inventory or api_surface" -v
  ```
  Expected: PASS. `test_api_surface.py` must still pass (public surface unchanged).

- [ ] **Step 4: Regenerate fixtures + vitest sanity**
  ```bash
  python scripts/generate-test-fixtures.py && npx vitest run
  ```

- [ ] **Step 5: Commit**
  ```bash
  git add domain/inventory.py tests/fixtures/
  git commit -m "refactor(domain/inventory): RebuildContext dataclass collapses the 7-arg rebuild bundle"
  ```

### Task 5: Remove redundant inline imports

**Files:**
- Modify: `cache_db.py:~275` and `:~589` (`import json as _json` — `json` already imported at top), `digikey_client.py:~280`/`~319` (only if the move in Task 6 doesn't already remove them)

**Interfaces:** none.

- [ ] **Step 1: Fix the redundant json imports**
  In `cache_db.py`, `json` is imported at the top (~:12). Replace the two inline `import json as _json` + `_json.` uses with the top-level `json`.

- [ ] **Step 2: Run cache_db tests**
  `pytest tests/python/test_cache_db.py -v` → PASS.

- [ ] **Step 3: Commit**
  ```bash
  git add cache_db.py
  git commit -m "refactor(cache_db): drop redundant inline json import"
  ```

---

## Wave 3 — DigiKey client cleanup

### Task 6: Finish the DigiKey session extraction

**Files:**
- Modify: `digikey_client.py` (move session/CDP methods out), `digikey_session.py` (receive them)
- Test: existing `tests/python/test_clients_digikey.py`, `tests/python/test_digikey_session.py`

**Interfaces:**
- Produces: session methods (`check_session`, `start_login`, `validate_session_http`, `_probe_session`) relocated to `digikey_session.py`; `DigikeyClient` retains thin delegating wrappers only where an external/test caller needs them. Public behavior identical.

- [ ] **Step 1: Map callers**
  Grep for each method (`grep -rn "check_session\|start_login\|validate_session_http\|_probe_session" --include=*.py .`) including tests and `distributor_manager.py`. Record who calls what and via which object.

- [ ] **Step 2: Move the CF-challenge poll loop into one helper first**
  The near-identical "poll `document.title` until not 'Just a moment' for 25s" loop appears in `_probe_session` (~:444) and `_fetch_raw` (~:538). Extract `_await_cf_clearance(window, timeout=25)` in `digikey_session.py` (or a shared util) and call it from both. Run `pytest tests/python/test_clients_digikey.py tests/python/test_digikey_session.py -v` → PASS.

- [ ] **Step 3: Relocate the session methods**
  Move `check_session`, `start_login`, `validate_session_http`, `_probe_session` to `digikey_session.py`. Where a caller reaches them through `DigikeyClient`, keep a one-line delegating wrapper so the surface is unchanged. Commit-sized: keep this behavior-preserving.

- [ ] **Step 4: Run DigiKey tests + live-marked smoke (skipped by default)**
  ```bash
  pytest tests/python/test_clients_digikey.py tests/python/test_digikey_session.py tests/python/test_normalizers.py -v
  ```
  Expected: PASS. (Do not run `-m live` — no credentials assumed.)

- [ ] **Step 5: Commit**
  ```bash
  git add digikey_client.py digikey_session.py
  git commit -m "refactor(digikey): relocate session/CDP methods to digikey_session; dedup CF-clearance poll"
  ```

### Task 7: Delete dead DigiKey compat shims

**Files:**
- Modify: `digikey_client.py:~92-162` (remove `_find_default_browser_exe`, `_check_cookies_logged_in`, `_normalize_result`, `_cdp_get_cookies`, `_save_cookies`, `_load_cookies`, `_inject_cookies_to_window`)
- Modify: `tests/python/test_clients_digikey.py`, `tests/python/test_digikey_session.py`, `tests/python/test_normalizers.py` (repoint any tests that exercised the shims at the extracted modules)

**Interfaces:** removes internal helpers with only test callers.

- [ ] **Step 1: Confirm no production callers**
  For each of the 7 functions run `grep -rn "<name>" --include=*.py .`. Confirm every non-definition hit is under `tests/`. If any production module (`distributor_manager.py`, `digikey_cdp.py`, `digikey_normalizer.py`) still calls one, exclude that one from deletion and note it.

- [ ] **Step 2: Repoint the tests**
  For each test that calls a shim, rewrite it to call the real extracted function in `digikey_session.py`/`digikey_cdp.py`/`digikey_normalizer.py`. If a shim was only tested to test the shim itself (no behavior now), delete that test.

- [ ] **Step 3: Delete the shims**
  Remove the confirmed-dead functions from `digikey_client.py`.

- [ ] **Step 4: Run DigiKey test suite**
  `pytest tests/python/test_clients_digikey.py tests/python/test_digikey_session.py tests/python/test_normalizers.py -v` → PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add digikey_client.py tests/python/test_clients_digikey.py tests/python/test_digikey_session.py tests/python/test_normalizers.py
  git commit -m "refactor(digikey): delete dead compat shims, repoint tests at extracted modules"
  ```

---

## Wave 4 — JS safety & consolidation

### Task 8: Consolidate escHtml / escapeHtml

**Files:**
- Modify: `js/ui-helpers.js:24` (`escHtml`), the 29 importers of `escHtml`, `js/dom/html.js:22` (`escapeHtml`)
- Test: existing `tests/js/` + new attribute-escaping assertion

**Interfaces:**
- Produces: one canonical escape (`escapeHtml` in `js/dom/html.js`, regex-based, escapes `& < > " '`). `escHtml` becomes a re-export alias from `js/ui-helpers.js` (`export { escapeHtml as escHtml } from './dom/html.js'`) so the 29 call sites keep working, OR all call sites are migrated to import `escapeHtml`. Prefer the alias to keep the diff small and safe.

- [ ] **Step 1: Write the failing test**
  In `tests/js/` add a test asserting the canonical escape handles attribute-unsafe chars:
  ```js
  import { escHtml } from '../js/ui-helpers.js';
  test('escHtml escapes quotes for attribute safety', () => {
    expect(escHtml(`"x" 'y' <z>&`)).toBe('&quot;x&quot; &#39;y&#39; &lt;z&gt;&amp;');
  });
  ```
  (Match the exact entity spelling `js/dom/html.js` emits — read it first and align the expectation.)

- [ ] **Step 2: Run to verify it fails**
  `npx vitest run tests/js/<file>` → FAIL (old `escHtml` leaves quotes unescaped).

- [ ] **Step 3: Make `escHtml` an alias of `escapeHtml`**
  In `js/ui-helpers.js`, delete the old `textContent`-based body and re-export: `export { escapeHtml as escHtml } from './dom/html.js';`. Keep the named export `escHtml` so importers are untouched.

- [ ] **Step 4: Run to verify it passes + full vitest**
  `npx vitest run` → PASS. Then `npx eslint js/ && npx tsc --noEmit` clean.

- [ ] **Step 5: Playwright smoke (escaping is render-path)**
  `npx playwright test sticky-buttons resize-visibility` → PASS (sanity that render didn't break).

- [ ] **Step 6: Commit**
  ```bash
  git add js/ui-helpers.js tests/js/
  git commit -m "refactor(js): unify escHtml on attribute-safe escapeHtml, closing quote-escaping gap"
  ```

### Task 9: Shared formatMoney() helper

**Files:**
- Modify: `js/ui-helpers.js` (add `formatMoney`), then `js/bom/bom-renderer.js:85-86`, `js/inventory/inventory-renderer.js:113,117`, `js/inventory-modals.js:136-137,260`, `js/part-preview.js:370`, `js/store.js:321`, `js/ui-helpers.js:104`
- Test: new `tests/js/` unit test for `formatMoney`

**Interfaces:**
- Produces: `formatMoney(n, {fallback='—'} = {}) -> string` returning `"$" + n.toFixed(2)` for finite numbers, `fallback` for null/undefined/NaN. Must reproduce today's output exactly (check whether existing sites use `—` em-dash fallback vs empty).

- [ ] **Step 1: Read all 7 sites, record exact current behavior**
  Note fallback handling per site (some may render nothing for null, others `—`). The helper must match; where sites differ, keep `formatMoney(n)` (no fallback) vs `formatMoney(n, {fallback:'—'})` variants so each site's output is byte-identical.

- [ ] **Step 2: Write failing test**
  ```js
  test('formatMoney', () => {
    expect(formatMoney(1.5)).toBe('$1.50');
    expect(formatMoney(null, {fallback:'—'})).toBe('—');
  });
  ```
  `npx vitest run` → FAIL (not defined).

- [ ] **Step 3: Implement in `js/ui-helpers.js`** (store-free — no `js/constants.js` import).

- [ ] **Step 4: Migrate the 7 sites** to call `formatMoney`, preserving each site's fallback exactly.

- [ ] **Step 5: Verify**
  `npx vitest run && npx eslint js/ && npx tsc --noEmit` → all clean.

- [ ] **Step 6: Commit**
  ```bash
  git add js/ui-helpers.js js/bom/bom-renderer.js js/inventory/inventory-renderer.js js/inventory-modals.js js/part-preview.js js/store.js tests/js/
  git commit -m "refactor(js): shared formatMoney() replaces scattered '$'+toFixed(2)"
  ```

### Task 10: Remove dead GENERIC_PARTS_LOADED event

**Files:**
- Modify: `js/inventory/inv-mutations.js:137`, `js/store.js:335`, `js/event-bus.js:32`, `CLAUDE.md` (EventBus table row)
- Test: existing `tests/js/`

**Interfaces:** removes an event with zero listeners.

- [ ] **Step 1: Re-confirm zero listeners**
  `mcp__devtools__event_trace("GENERIC_PARTS_LOADED")` (or `grep -rn "GENERIC_PARTS_LOADED" js/`). Confirm only the two emits + the declaration exist, no `.on(`/subscribe. If a listener exists, STOP and reassess (wire it instead).

- [ ] **Step 2: Remove the two emits and the declaration**
  Delete the `emit(GENERIC_PARTS_LOADED …)` calls at `inv-mutations.js:137` and `store.js:335`, and the enum entry at `event-bus.js:32`. Remove the `GENERIC_PARTS_LOADED` row from the CLAUDE.md EventBus table.

- [ ] **Step 3: Verify**
  `npx eslint js/ && npx tsc --noEmit && npx vitest run` → clean.

- [ ] **Step 4: Commit**
  ```bash
  git add js/inventory/inv-mutations.js js/store.js js/event-bus.js CLAUDE.md
  git commit -m "refactor(js): remove dead GENERIC_PARTS_LOADED event (no listeners)"
  ```

---

## Wave 5 — JS structural decomposition

> Higher-risk; each task is behavior-preserving and gated by vitest + playwright. Do these one at a time, verifying between.

### Task 11: Split initApp()

**Files:**
- Modify: `js/app-init.js:48-579` (the single `initApp()` mega-function)
- Test: `npx playwright test` (startup path), `npx vitest run`

**Interfaces:** `initApp()` remains the exported entry point; internals extracted into named phase functions in the same module (`wireEvents()`, `mountPanels()`, `restorePreferences()`, etc.) called in order from `initApp()`.

- [ ] **Step 1: Read the full function and identify phase boundaries**
  Map the 62 nested handlers into logical phases (event wiring, panel mount, prefs restore, SSE/store hookup). Preserve ordering and closure-captured state — extracted functions take explicit params instead of closure capture.

- [ ] **Step 2: Extract phases incrementally**
  Pull one phase into a named function, keep `initApp()` calling it in the same place. Run `npx vitest run` after each extraction. Do NOT reorder side effects.

- [ ] **Step 3: Full verify + startup E2E**
  `npx eslint js/ && npx tsc --noEmit && npx vitest run && npx playwright test` → PASS. Manually confirm no init-order regression (the startup race notes in CLAUDE.md — splash self-nav — are unaffected since this is post-load app init).

- [ ] **Step 4: Commit**
  ```bash
  git add js/app-init.js
  git commit -m "refactor(js): decompose 530-line initApp() into named phase functions"
  ```

### Task 12: Split & relocate inventory-modals.js

**Files:**
- Create: `js/inventory/inv-modals.js` (Adjust + Price modals), `js/inventory/pricing-utils.js` (`pickTier`, `rowPrice`, `cheapestRow`)
- Remove: `js/inventory-modals.js` (852 lines, at wrong dir level)
- Modify: all importers of `js/inventory-modals.js`
- Test: `npx vitest run`, `npx playwright test`

**Interfaces:** exports preserved by name; only module paths change. `pickTier/rowPrice/cheapestRow` move to `pricing-utils.js`; modal openers (`openAdjustModal`, `openPriceModal`, `createFetchController`) move to `inv-modals.js`.

- [ ] **Step 1: Grep importers** `grep -rn "inventory-modals" js/ tests/js/`. Record every import path.
- [ ] **Step 2: Extract pricing-utils.js** (pure functions) + point importers at it. `npx vitest run`.
- [ ] **Step 3: Move modal code to `js/inventory/inv-modals.js`**, update importers, delete the old file. Use `mcp__devtools__file_ops` mv where helpful.
- [ ] **Step 4: Verify** `npx eslint js/ && npx tsc --noEmit && npx vitest run && npx playwright test sticky-buttons resize-visibility` → PASS.
- [ ] **Step 5: Commit**
  ```bash
  git add -A js/inventory/ js/
  git commit -m "refactor(js): split inventory-modals into inv-modals + pricing-utils under js/inventory/"
  ```

### Task 13: Disambiguate inv-render / inventory-renderer names

**Files:**
- Rename: `js/inventory/inv-render.js` → `js/inventory/inv-tree-render.js`; `js/inventory/inventory-renderer.js` → `js/inventory/inv-html-builders.js`
- Modify: all importers
- Test: `npx vitest run`, `npx playwright test`

**Interfaces:** exports unchanged; only filenames + import paths change. Update `js/inventory/_README.md` to reflect new names.

- [ ] **Step 1: Grep importers of both files.** Record paths (include `tests/js/`).
- [ ] **Step 2: Rename via `mcp__devtools__file_ops mv`, update every import path.**
- [ ] **Step 3: Update `js/inventory/_README.md`.**
- [ ] **Step 4: Verify** `npx eslint js/ && npx tsc --noEmit && npx vitest run` → clean.
- [ ] **Step 5: Commit**
  ```bash
  git add -A js/inventory/
  git commit -m "refactor(js): rename inv-render/inventory-renderer to intent-revealing names"
  ```

### Task 14: Split mfg-direct-panel.js

**Files:**
- Modify: `js/import/mfg-direct/mfg-direct-panel.js` (743 lines) → extract scan-session lifecycle + phone-scan socket handlers + import-queue into sibling modules
- Test: `npx vitest run`, `npx playwright test`

**Interfaces:** panel's public entry points unchanged; internals split into e.g. `mfg-direct-scan-session.js`, `mfg-direct-import-queue.js`. Extracted functions take explicit params.

- [ ] **Step 1: Map the ≥3 units** (`scanReceived`/`scanReceiving` socket handlers ~:521/575; `startImportQueue`/`_openNextInQueue` ~:621/627; scan-session lifecycle ~:438-467). Record shared state each needs.
- [ ] **Step 2: Extract one unit at a time**, verify `npx vitest run` between each.
- [ ] **Step 3: Verify** full JS gate + `npx playwright test` (scan/import flow E2E if present).
- [ ] **Step 4: Commit**
  ```bash
  git add -A js/import/mfg-direct/
  git commit -m "refactor(js): split mfg-direct-panel into scan-session + import-queue modules"
  ```

### Task 15: Drop the @deprecated single-file shim

**Files:**
- Modify: `js/import/mfg-direct/mfg-direct-panel.js:267` (`@deprecated` shim routing through `beginScanImport`)

- [ ] **Step 1: Grep callers** of the deprecated function. If any remain, migrate them to `beginScanImport` first.
- [ ] **Step 2: Delete the shim.**
- [ ] **Step 3: Verify** `npx vitest run && npx playwright test` → PASS.
- [ ] **Step 4: Commit**
  ```bash
  git add js/import/mfg-direct/mfg-direct-panel.js
  git commit -m "refactor(js): remove deprecated single-file scan-import shim"
  ```

---

## Wave 6 — app.pyw main() decomposition

### Task 16: Decompose app.pyw main()

**Files:**
- Modify: `app.pyw:134-498` (`main()`, ~364 lines, 8 nested closures)
- Test: `pytest tests/python/` (any app-launch smoke), manual launch sanity

**Interfaces:** `main()` stays the entry point. Extract the closures (`_boot_server`, `_on_webview_ready`, `_cleanup`, `_do_exit`, `on_closing`, `on_closed`, `on_ready`) into module-level functions or a small `Launcher` class holding shared state explicitly instead of via closure capture.

- [ ] **Step 1: Map shared state** the closures capture (server thread handle, window ref, cleanup flags). Decide: `Launcher` class with instance attrs is cleanest for the mutable shared state.
- [ ] **Step 2: Introduce `Launcher`**, move closures to methods, keep `main()` as a thin `Launcher().run()`. Preserve exact ordering — the pywebview second-origin race and close-deadlock notes in CLAUDE.md mean lifecycle ordering is load-bearing. Do NOT change when/where the window is created or navigated.
- [ ] **Step 3: Verify** `pytest tests/python/ -v` → PASS. Then a real launch sanity check: `DUBIS_WEBVIEW_PROFILE=ephemeral python app.pyw` boots to the app window and closes cleanly (report any hang — suspect the close-deadlock note).
- [ ] **Step 4: Commit**
  ```bash
  git add app.pyw
  git commit -m "refactor(app): extract main() closures into Launcher class"
  ```

---

## Wave 7 — innerHTML → dom/html.js migration (large)

### Task 17: Migrate worst-offender innerHTML sites to the dom/html layer

**Files:**
- Modify (priority order): `js/label-selection.js` (7), `js/bom/bom-panel.js` (6), `js/label-export-modal.js`, `js/part-preview.js`, `js/import/import-panel.js`, `js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js` (5 each). Route hand-rolled modals (`mfg-direct-renderer.js:88`, `ocr-overlay-renderer.js:18,41`, `scan-grouping.js:115`) through `js/components/form-modal.js` where they match its shape.
- Test: `npx vitest run`, `npx playwright test` (these files own visible UI)

**Interfaces:** use `html`/`el` tagged-template helpers from `js/dom/html.js`; escaping is automatic. No behavior change.

> **Scope note:** This is the largest, highest-touch task. Migrate the listed high-count offenders and any modal that cleanly fits `form-modal.js`. Do NOT force-fit sites where the template helper would obscure logic — log those as remaining in the commit body rather than contorting them. Report the final migrated/remaining counts.

- [ ] **Step 1: Baseline** `grep -rn "innerHTML\s*=" js/ | wc -l` and record. Playwright green before starting.
- [ ] **Step 2: Migrate one file at a time**, converting raw `innerHTML =` string assembly to `html`/`el` helpers, verifying `npx vitest run` after each file and `npx playwright test sticky-buttons resize-visibility` after UI-structural files.
- [ ] **Step 3: Route matching modals through form-modal.js.**
- [ ] **Step 4: Full gate** `bash scripts/verify.sh` → PASS. Record new `innerHTML` count.
- [ ] **Step 5: Commit** (may be several commits, one per file group)
  ```bash
  git add -A js/
  git commit -m "refactor(js): migrate high-count innerHTML sites to dom/html helpers"
  ```

---

## Cleanup (local-only, NOT a subagent PR task)

These are local-disk / gitignored and must be done by hand with care — deleting another Claude instance's worktree loses their work:
- Prune orphaned `.claude/worktrees/` (only `git worktree list`-registered ones are live) and `.claire/worktrees/` stubs — **operator does this manually**, not in this plan.
- Local `data/*.bak.*`, `signal-*.jpeg`, `045327_*_LCSC_PO_downloads.html*` are untracked; safe to delete locally, no repo impact.
- Untracked `scripts/one_off_import_mouser_cart.py`, spent `scripts/spike-webview-loopback.py` — archive/delete at operator discretion.
- Two `if: false` CI jobs (`.github/workflows/ci.yml:534-567`) — leave until cross-compute PnP path is fixed; deleting them loses the wiring. Out of scope.

## Memory correction (operator, not a PR)

Update project memory `project_ocr_scanning_skeleton.md`: the OCR/photo-scan flow is NOT an unbuilt skeleton — it is fully wired (`pnp_server.py`, `server/routes/import_scan.py`, `scan_image.py` → `ocr_layout.py` → `vlm_extract.py` Ollama path, `domain/api_scan.py`). Correct the stale "shipped-skeleton-but-unbuilt" note.

---

## Self-Review

- **Spec coverage:** every audit finding maps to a task — product factory (T2), god-functions (T11 initApp, T16 main, T12 inventory-modals, T14 mfg-direct), name traps (T8 escHtml, T13 renames), dead code (T7 shims, T10 event, T15 shim), consolidation (T3 row helper, T4 RebuildContext, T9 formatMoney, T17 innerHTML), doc/memory drift (T1 + operator memory note). DigiKey god-object → T6.
- **Type consistency:** `build_product` signature (T2) and `RebuildContext` fields (T4) are the only new cross-task types; both are self-contained within one task. `escHtml`/`escapeHtml` naming reconciled in T8.
- **Placeholders:** none — every code step shows the transformation or the exact grep/verify command.
- **Behavior-preserving:** every task gated by existing tests; the only new tests assert current behavior (oracle tests) or close the documented escaping gap (T8).
