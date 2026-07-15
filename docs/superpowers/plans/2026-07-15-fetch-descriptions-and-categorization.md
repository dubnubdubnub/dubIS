# Fetch Descriptions + Categorization Gaps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user fetch missing part descriptions (per-part from the Adjust modal and in bulk from the command palette) using data the distributor clients already return, then extend the categorizer so described parts leave the "Other" bucket.

**Architecture:** Reuse the existing `fetch_*_product` chain (every client already returns `description`). Backend gets one new batch method that fetches + writes descriptions in a single atomic pass and rebuilds once (which re-runs categorization). Frontend gets a per-part button in the Adjust modal (reads already-fetched panel state) and a command-palette bulk action. Categorizer gets new keyword rules plus a new "Development Boards, Kits, Programmers" category.

**Tech Stack:** Python 3 (pywebview backend, pytest), vanilla JS ES modules (vitest, jsdom), Playwright E2E. No new dependencies.

## Global Constraints

- Source of truth is CSV (`data/purchase_ledger.csv`); `cache.db` is a derived, deletable view. All inventory-mutating methods append/write CSV then rebuild and return fresh `list[InventoryItem]`.
- Error policy: prefer `AppLog.warn`/`AppLog.error` (JS) / Python `logging` over silent catches; throw rather than silently fail. Per-item batch failures are caught, logged, and counted — never abort the whole batch on one bad lookup.
- JS→backend calls use the string-keyed bridge: `api("method_name", ...args)` (`js/api.js`), which toasts+logs on error and returns `undefined`.
- Distributor→CSV column map (both `inventory_ops._FIELD_TO_COL` and `domain.pricing._LEDGER_PN_COLS`): `lcsc`→`LCSC Part Number`, `digikey`→`Digikey Part Number`, `mouser`→`Mouser Part Number`, `pololu`→`Pololu Part Number`; `description`→`Description`. Distributor order: `("lcsc","digikey","mouser","pololu")`.
- New backend bridge methods must be registered in `tests/python/test_api_surface.py` (the pywebview surface is frozen by that test) or it fails.
- After backend changes run `python scripts/generate-test-fixtures.py`; before PR run `bash scripts/verify.sh`.
- Categorizer category names must match `data/constants.json` → `SECTION_ORDER` entries.

---

## Task 1: Backend `fetch_missing_descriptions` batch method

**Files:**
- Modify: `domain/inventory.py` (add `fetch_missing_descriptions` domain function near `update_part_fields`, ~line 442)
- Modify: `domain/api_inventory.py` (add facade method near `update_part_fields`, ~line 137)
- Modify: `inventory_api.py` (expose bridge method near other inventory methods, ~line 255; wire distributors)
- Modify: `tests/python/test_api_surface.py` (register `fetch_missing_descriptions`)
- Test: `tests/python/domain/test_inventory_fetch_descriptions.py` (create)

**Interfaces:**
- Consumes: `domain.pricing.get_sourced_distributors(conn, purchase_csv, part_key) -> list[{"distributor","part_number"}]`; `inventory_ops.get_part_key(row)`; `atomic_write_rows`; `rebuild(...)`; distributor fetch callables.
- Produces: `domain.inventory.fetch_missing_descriptions(*, fetchers: dict[str, Callable[[str], dict|None]], input_csv, adjustments_csv, adj_fieldnames, base_dir, fieldnames, events_dir, conn) -> dict` returning `{"inventory": list[InventoryItem], "summary": {"updated": int, "failed": int, "skipped": int}}`. Bridge method `InventoryApi.fetch_missing_descriptions() -> dict` (no args).

**Design notes for the implementer:**
- A part "needs a description" when its ledger row's `Description` cell is empty or equals `nan`/`none` (case-insensitive), AND the row has at least one distributor PN column populated.
- `fetchers` maps distributor key → a callable taking a PN and returning the normalized product dict (or `None`). In the facade, build it from `self._api._distributors`: `{"lcsc": distmgr.fetch_lcsc_product, "digikey": distmgr.fetch_digikey_product, "mouser": distmgr.fetch_mouser_product, "pololu": distmgr.fetch_pololu_product}`. The domain function stays client-agnostic (easy to mock).
- For each needy row, try distributors in `("lcsc","digikey","mouser","pololu")` order using the row's own PN columns; first fetch returning a non-empty `description` wins. Wrap each fetch in try/except → on exception log + treat as no result.
- Collect `{row_index: description}` for all successes, apply to `rows` in memory, then **one** `atomic_write_rows` + **one** `rebuild`. Count: `updated` = rows written, `failed` = needy rows where every distributor errored or returned empty, `skipped` = rows that already had a description (not counted as needy — only report if useful; keeping it in summary is fine even if 0).
- Determine `file_fieldnames` from the ledger like `update_part_fields` does (read `reader.fieldnames`). Write to the `Description` column directly.
- If there are zero needy rows, still return current inventory (call `rebuild` or reuse an existing read) with `summary={"updated":0,"failed":0,"skipped":N}` — do not raise.

- [ ] **Step 1: Write the failing test**

Create `tests/python/domain/test_inventory_fetch_descriptions.py`. Mirror fixture style from `tests/python/domain/test_pricing.py` (it builds a ledger CSV + sqlite `conn`). Use fake fetchers so no network is touched.

```python
import csv
import domain.inventory as inv


def _fetchers(mapping):
    # mapping: pn -> product dict (or None); raises for pns in `errors`
    def make(dist):
        def fetch(pn):
            if pn in mapping.get("_errors", set()):
                raise RuntimeError("boom")
            return mapping.get(pn)
        return fetch
    return {d: make(d) for d in ("lcsc", "digikey", "mouser", "pololu")}


def test_fills_missing_description_from_lcsc(fetch_desc_env):
    env = fetch_desc_env  # provides paths + conn + a ledger with 3 rows
    fetchers = _fetchers({"C111": {"description": "Cap 47uF"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["updated"] == 1
    # the written CSV now has the description
    rows = list(csv.DictReader(open(env.ledger, encoding="utf-8-sig")))
    got = [r for r in rows if r["LCSC Part Number"] == "C111"][0]
    assert got["Description"] == "Cap 47uF"


def test_skips_rows_that_already_have_description(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({})  # nothing to fetch
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["updated"] == 0


def test_counts_failure_when_all_distributors_error(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({"_errors": {"C111"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["failed"] == 1
    assert out["summary"]["updated"] == 0


def test_returns_fresh_inventory_list(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({"C111": {"description": "Cap 47uF"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert isinstance(out["inventory"], list)
```

Add a `fetch_desc_env` fixture at the top of the file that writes a ledger with: one row with LCSC PN `C111` and empty Description (needy), one row with a PN and a real Description (skip), one row with no PN at all (never needy). Build `conn` via the same helper `test_pricing.py` uses (`cache_db`/populate). Expose `env.kwargs` = the dict of keyword args `fetch_missing_descriptions` needs (`input_csv`, `adjustments_csv`, `adj_fieldnames`, `base_dir`, `fieldnames`, `events_dir`, `conn`) and `env.ledger` = ledger path. Read `test_pricing.py` fixtures first and copy their construction exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .claude/worktrees/fetch-descriptions && python -m pytest tests/python/domain/test_inventory_fetch_descriptions.py -v`
Expected: FAIL with `AttributeError: module 'domain.inventory' has no attribute 'fetch_missing_descriptions'`.

- [ ] **Step 3: Implement the domain function**

Add to `domain/inventory.py` (after `update_part_fields`):

```python
def fetch_missing_descriptions(
    *,
    fetchers: "dict[str, Callable[[str], dict | None]]",
    input_csv: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    fieldnames: list[str],
    events_dir: str,
    conn: sqlite3.Connection,
) -> dict:
    """Fetch descriptions for ledger rows that have a distributor PN but no
    description. Writes all results in one pass, rebuilds once. Caller holds lock.
    """
    _PN_COLS = {
        "lcsc": "LCSC Part Number",
        "digikey": "Digikey Part Number",
        "mouser": "Mouser Part Number",
        "pololu": "Pololu Part Number",
    }
    _ORDER = ("lcsc", "digikey", "mouser", "pololu")

    def _blank(v: str) -> bool:
        return (v or "").strip().lower() in ("", "nan", "none")

    if not os.path.exists(input_csv):
        raise ValueError("No purchase ledger found")

    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        file_fieldnames = reader.fieldnames
        rows = list(reader)

    updated = failed = skipped = 0
    for row in rows:
        if not _blank(row.get("Description", "")):
            skipped += 1
            continue
        pns = [(d, (row.get(_PN_COLS[d]) or "").strip()) for d in _ORDER]
        pns = [(d, pn) for d, pn in pns if pn]
        if not pns:
            continue  # no PN → not fetchable, not counted
        desc = ""
        for dist, pn in pns:
            fetch = fetchers.get(dist)
            if not fetch:
                continue
            try:
                product = fetch(pn)
            except Exception as e:  # noqa: BLE001 - one bad lookup must not abort batch
                logger.warning("fetch_missing_descriptions: %s %s failed: %s", dist, pn, e)
                continue
            cand = (product or {}).get("description") if product else None
            if cand and str(cand).strip():
                desc = str(cand).strip()
                break
        if desc:
            row["Description"] = desc
            updated += 1
        else:
            failed += 1

    if updated:
        atomic_write_rows(input_csv, file_fieldnames, rows, encoding="utf-8-sig")

    result, _ = rebuild(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    return {"inventory": result, "summary": {"updated": updated, "failed": failed, "skipped": skipped}}
```

Ensure `Callable` is imported (`from typing import Callable` or `from collections.abc import Callable`) and `logger` exists in the module (it does — used elsewhere; confirm the name).

- [ ] **Step 4: Run the domain test to verify it passes**

Run: `python -m pytest tests/python/domain/test_inventory_fetch_descriptions.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire the facade + bridge**

In `domain/api_inventory.py`, after `update_part_fields` (~line 153):

```python
    def fetch_missing_descriptions(self) -> dict:
        """Fetch descriptions for parts with a distributor PN but none set."""
        dm = self._api._distributors
        fetchers = {
            "lcsc": dm.fetch_lcsc_product,
            "digikey": dm.fetch_digikey_product,
            "mouser": dm.fetch_mouser_product,
            "pololu": dm.fetch_pololu_product,
        }
        with self._api._lock:
            return domain.inventory.fetch_missing_descriptions(
                fetchers=fetchers,
                input_csv=self._api.input_csv,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                events_dir=self._api.events_dir,
                conn=self._api._get_cache(),
            )
```

In `inventory_api.py`, next to `update_part_fields` (~line 255), add the delegating bridge method (match the file's existing delegation style — the inventory facade is likely `self._inv`):

```python
    def fetch_missing_descriptions(self) -> dict:
        return self._inv.fetch_missing_descriptions()
```

Read the surrounding lines to confirm the facade attribute name (`self._inv`) and copy the exact convention.

- [ ] **Step 6: Register in the API surface test**

In `tests/python/test_api_surface.py`, add to the expected-methods map (alongside `'update_part_fields': '(part_key, fields_json)'` / `'get_sourced_distributors': '(part_key)'`):

```python
    'fetch_missing_descriptions': '()',
```

Match the exact signature-string format the test uses for zero-arg methods (check an existing no-arg entry like `rebuild_inventory`).

- [ ] **Step 7: Run backend tests**

Run: `python -m pytest tests/python/domain/test_inventory_fetch_descriptions.py tests/python/test_api_surface.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add domain/inventory.py domain/api_inventory.py inventory_api.py \
        tests/python/domain/test_inventory_fetch_descriptions.py tests/python/test_api_surface.py
git commit -m "feat(inventory): fetch_missing_descriptions batch backend method"
```

---

## Task 2: Command-palette "Fetch Missing Descriptions" action

**Files:**
- Modify: `js/app-init.js` (add a command in the Global group where `cmds.push({...})` blocks live, ~line 236 next to "Rebuild Inventory")
- Test: `tests/js/fetch-missing-descriptions-command.test.mjs` (create; match the existing vitest dir/convention — confirm actual test dir, e.g. `tests/js/` or `js/**/__tests__`)

**Interfaces:**
- Consumes: `api("fetch_missing_descriptions") -> {inventory, summary}`; `onInventoryUpdated(inventory)`; `showToast(msg)`; `AppLog`.
- Produces: a command object `{id:'fetch-missing-descriptions', label:'Fetch Missing Descriptions', group:'Global', keywords:[...], run}` pushed into `cmds`.

- [ ] **Step 1: Write the failing test**

Create the test. Import the `run` behavior by extracting it, OR (simpler, matches repo style) test a small exported helper. To keep `app-init.js` testable, extract the action body into an exported function in a new tiny module `js/inventory/fetch-descriptions-command.js`:

```js
// what the test drives
import { runFetchMissingDescriptions } from '../../js/inventory/fetch-descriptions-command.js';

test('fetches, updates store, and toasts the summary', async () => {
  const calls = { toasts: [], updated: null };
  const deps = {
    api: async () => ({ inventory: [{ mpn: 'X' }], summary: { updated: 3, failed: 1, skipped: 2 } }),
    onInventoryUpdated: (inv) => { calls.updated = inv; },
    showToast: (m) => calls.toasts.push(m),
  };
  await runFetchMissingDescriptions(deps);
  expect(calls.updated).toEqual([{ mpn: 'X' }]);
  expect(calls.toasts[0]).toMatch(/3/);
  expect(calls.toasts[0]).toMatch(/fail/i);
});

test('no-op toast when nothing was updated', async () => {
  const calls = { toasts: [] };
  const deps = {
    api: async () => ({ inventory: [], summary: { updated: 0, failed: 0, skipped: 5 } }),
    onInventoryUpdated: () => {},
    showToast: (m) => calls.toasts.push(m),
  };
  await runFetchMissingDescriptions(deps);
  expect(calls.toasts[0]).toMatch(/no .*descriptions|nothing/i);
});

test('bails without crashing when api returns undefined', async () => {
  const deps = { api: async () => undefined, onInventoryUpdated: () => { throw new Error('should not be called'); }, showToast: () => {} };
  await runFetchMissingDescriptions(deps);  // must not throw
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .claude/worktrees/fetch-descriptions && npx vitest run tests/js/fetch-missing-descriptions-command.test.mjs`
Expected: FAIL — module `js/inventory/fetch-descriptions-command.js` not found.

- [ ] **Step 3: Implement the helper module**

Create `js/inventory/fetch-descriptions-command.js`:

```js
// @ts-check
/**
 * Run the bulk "fetch missing descriptions" action.
 * @param {{api:Function,onInventoryUpdated:Function,showToast:Function}} deps
 */
export async function runFetchMissingDescriptions({ api, onInventoryUpdated, showToast }) {
  const res = await api('fetch_missing_descriptions');
  if (!res) return; // api() already toasted the error
  const { inventory, summary } = res;
  if (Array.isArray(inventory)) onInventoryUpdated(inventory);
  const s = summary || { updated: 0, failed: 0 };
  if (!s.updated) {
    showToast('No missing descriptions to fetch');
    return;
  }
  let msg = 'Fetched ' + s.updated + ' description' + (s.updated === 1 ? '' : 's');
  if (s.failed) msg += ', ' + s.failed + ' failed';
  showToast(msg);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/fetch-missing-descriptions-command.test.mjs`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire the command into the palette**

In `js/app-init.js`, import at top with the other imports:

```js
import { runFetchMissingDescriptions } from './inventory/fetch-descriptions-command.js';
```

Then in the `cmds` build block, right after the `rebuild-inventory` push (~line 249):

```js
    cmds.push({
      id: 'fetch-missing-descriptions',
      label: 'Fetch Missing Descriptions',
      group: 'Global',
      keywords: ['description', 'ocr', 'fetch', 'distributor', 'fill'],
      run: () => runFetchMissingDescriptions({ api, onInventoryUpdated, showToast }),
    });
```

Confirm `api`, `onInventoryUpdated`, and `showToast` are already imported in `app-init.js` (they are used nearby — `rebuild-inventory` uses `api` + `onInventoryUpdated` + `showToast`).

- [ ] **Step 6: Lint + type check + tests**

Run: `npx eslint js/inventory/fetch-descriptions-command.js js/app-init.js && npx tsc --noEmit && npx vitest run tests/js/fetch-missing-descriptions-command.test.mjs`
Expected: no errors; tests PASS.

- [ ] **Step 7: Commit**

```bash
git add js/inventory/fetch-descriptions-command.js js/app-init.js tests/js/fetch-missing-descriptions-command.test.mjs
git commit -m "feat(inventory): command-palette Fetch Missing Descriptions action"
```

---

## Task 3: Per-part "Fetch description" button in the Adjust modal

**Files:**
- Modify: `js/inventory-modals.js` (stash description in `fetchRow` ~line 328; add `bestDescription`/`hasSourcedRows` to the controller return ~line 444; add button to the description row in `openAdjustModal` ~line 116; add delegated click handler in `init()`)
- Modify: `css/styles.css` (style `.fetch-desc-btn`)
- Test: `tests/js/adjust-fetch-description-button.test.mjs` (create)

**Interfaces:**
- Consumes: existing `createFetchController` internals; `showToast`.
- Produces: controller return gains `bestDescription() -> string` and `hasSourcedRows() -> boolean`. Each `rows[i]` gains a `description: string` field.

- [ ] **Step 1: Write the failing test (bestDescription selection logic)**

The row-selection logic is the risky part; test it as a pure function. Extract description-picking into an exported pure helper `pickBestDescription(rows, pinnedIndex, cheapestIndex)` in a new module `js/inventory/pick-description.js`, and have the controller use it.

Create `tests/js/adjust-fetch-description-button.test.mjs`:

```js
import { pickBestDescription } from '../../js/inventory/pick-description.js';

const R = (desc, unitPrice) => ({ description: desc, unitPrice });

test('prefers the pinned row when it has a description', () => {
  const rows = [R('cheap-desc', 1), R('pinned-desc', 5)];
  expect(pickBestDescription(rows, 1, 0)).toBe('pinned-desc');
});

test('falls back to cheapest when pinned row has no description', () => {
  const rows = [R('cheap-desc', 1), R('', 5)];
  expect(pickBestDescription(rows, 1, 0)).toBe('cheap-desc');
});

test('falls back to first row with any non-empty description', () => {
  const rows = [R('', 1), R('', 5), R('third', 9)];
  expect(pickBestDescription(rows, -1, -1)).toBe('third');
});

test('treats nan/none/whitespace as empty', () => {
  const rows = [R('nan', 1), R('  ', 5), R('real', 9)];
  expect(pickBestDescription(rows, -1, -1)).toBe('real');
});

test('returns empty string when no row has a description', () => {
  const rows = [R('', 1), R('nan', 5)];
  expect(pickBestDescription(rows, -1, -1)).toBe('');
});

test('returns empty string for empty rows', () => {
  expect(pickBestDescription([], -1, -1)).toBe('');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .claude/worktrees/fetch-descriptions && npx vitest run tests/js/adjust-fetch-description-button.test.mjs`
Expected: FAIL — module `js/inventory/pick-description.js` not found.

- [ ] **Step 3: Implement the pure helper**

Create `js/inventory/pick-description.js`:

```js
// @ts-check
function clean(d) {
  const s = (d == null ? '' : String(d)).trim();
  return (s.toLowerCase() === 'nan' || s.toLowerCase() === 'none') ? '' : s;
}

/**
 * Pick the best fetched description from distributor rows.
 * Preference: pinned row → cheapest row → first row with any description.
 * @param {Array<{description?:string}>} rows
 * @param {number} pinnedIndex
 * @param {number} cheapestIndex
 * @returns {string}
 */
export function pickBestDescription(rows, pinnedIndex, cheapestIndex) {
  if (!rows || !rows.length) return '';
  for (const i of [pinnedIndex, cheapestIndex]) {
    if (i >= 0 && i < rows.length) {
      const d = clean(rows[i].description);
      if (d) return d;
    }
  }
  for (const r of rows) {
    const d = clean(r.description);
    if (d) return d;
  }
  return '';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/adjust-fetch-description-button.test.mjs`
Expected: PASS (6 tests).

- [ ] **Step 5: Stash description in fetchRow + expose controller methods**

In `js/inventory-modals.js`:

Import the helper at the top with the other imports:
```js
import { pickBestDescription } from './inventory/pick-description.js';
import { cheapestRow } from ...  // confirm cheapestRow is already imported/defined; it is used in applySelection
```
(`cheapestRow` already exists in this file — reuse it, don't re-import if it's local.)

Add `description: ""` to the row object built in `configure` (the `rows = (sourced||[]).map(...)` block, alongside `prices: null`).

In `fetchRow`, inside the `if (product && Array.isArray(product.prices) ...)` success branch (~line 328), also stash the description regardless of prices. Restructure so the description is captured even when prices are absent:

```js
      const bridge = /** @type {any} */ (window).pywebview.api;
      const product = await bridge[r.method](r.partNumber);
      if (product && typeof product.description === 'string') {
        r.description = product.description;
      }
      if (product && Array.isArray(product.prices) && product.prices.length) {
        r.prices = product.prices;
        recompute(i);
        api("record_fetched_prices", pk, r.distributor, product.prices).catch(() => {});
        return;
      }
```

Update the controller's `return { configure, deleteEligibility }` (line 444) to:

```js
  return {
    configure,
    deleteEligibility,
    hasSourcedRows: () => rows.length > 0,
    bestDescription: () => pickBestDescription(rows, pinnedIndex, cheapestRow(rows)),
  };
```

Also extend the row typedef comment (line 181-184) to include `description:string`.

- [ ] **Step 6: Add the button to the description row**

In `openAdjustModal` (~line 116), special-case the description field to append a button inside its `<td>`:

```js
    if (key === "description") {
      var disabled = noDist ? " disabled" : "";
      html += "<tr><td>" + escHtml(label) + "</td><td>" +
        buildFieldInput(key, value, "", warnClass) +
        '<button type="button" class="fetch-desc-btn"' + disabled +
        ' title="Fill description from the matched distributor">Fetch description</button>' +
        "</td></tr>";
    } else {
      html += "<tr><td>" + escHtml(label) + "</td><td>" + buildFieldInput(key, value, "", warnClass) + "</td></tr>";
    }
```

(Replace the existing single `html += ...` line inside the loop with this if/else. Keep the Mouser-hint block that follows.)

- [ ] **Step 7: Add the delegated click handler in init()**

In `init()` (after modal DOM refs are wired, near the fetch panel wiring ~line 476), add a delegated listener on `modalDetailTable`:

```js
  modalDetailTable.addEventListener("click", (e) => {
    const btn = /** @type {HTMLElement} */ (e.target).closest(".fetch-desc-btn");
    if (!btn) return;
    if (!adjFetch.hasSourcedRows()) { showToast("No distributor PN to fetch from"); return; }
    const desc = adjFetch.bestDescription();
    if (!desc) { showToast("No description available yet — try again in a moment"); return; }
    const input = /** @type {HTMLInputElement} */ (
      modalDetailTable.querySelector('.modal-field-input[data-field="description"]')
    );
    if (!input) return;
    input.value = desc;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    showToast("Description filled — review and Apply");
  });
```

Confirm `showToast` is imported in `inventory-modals.js` (it is — used in `applyFix`).

- [ ] **Step 8: Style the button**

In `css/styles.css`, add near other modal-field styles (find `.modal-field-input` and place adjacent):

```css
.fetch-desc-btn {
  margin-left: 6px;
  padding: 2px 8px;
  font-size: 0.85em;
  cursor: pointer;
}
.fetch-desc-btn:disabled { opacity: 0.5; cursor: not-allowed; }
```

Match existing button styling variables/tokens used elsewhere in the modal rather than hardcoding if the file uses CSS custom properties for buttons — check neighbors.

- [ ] **Step 9: Lint, type check, unit tests**

Run: `npx eslint js/inventory/pick-description.js js/inventory-modals.js && npx tsc --noEmit && npx vitest run tests/js/adjust-fetch-description-button.test.mjs`
Expected: no errors; tests PASS.

- [ ] **Step 10: Commit**

```bash
git add js/inventory/pick-description.js js/inventory-modals.js css/styles.css tests/js/adjust-fetch-description-button.test.mjs
git commit -m "feat(inventory): per-part Fetch description button in Adjust modal"
```

---

## Task 4: Categorization — new category + rules for described parts

**Files:**
- Modify: `categorize.py` (add rules to `CATEGORY_RULES`, ~lines 38-99; possibly `SUBCATEGORY_RULES`)
- Modify: `data/constants.json` (add "Development Boards, Kits, Programmers" to `SECTION_ORDER` before "Other")
- Test: `tests/python/test_inventory_api_categorize.py` (add cases in `TestCategorize`)

**Interfaces:**
- Consumes: `categorize.categorize(row: dict) -> str` (matches on `Description`/`Manufacture Part Number`/`Manufacturer`, lowercased, first-match-wins).
- Produces: no new function; new rule entries + one new category name.

**Rule content (based on the current live "Other" parts and their known descriptions):**

| MPN | Expected category | Match basis |
|-----|-------------------|-------------|
| GRM21BR61A476ME15L (Murata MLCC) | Passives - Capacitors | desc contains "capacitor"/"cap cer"/"ceramic"/"mlcc" once fetched; add mpn `grm` as fallback |
| WS2812B-V5/W | LEDs | desc "led"; add mpn `ws2812` fallback |
| UJ40-C-V-G-SMT-TR (CUI USB-C) | Connectors | desc "usb-c"/"receptacle"; existing rules cover once described |
| 12401951F412A (Amphenol USB-C) | Connectors | existing "usb-c"/"connector" desc rules once described |
| WSD4066DN33 (MOSFET) | Discrete Semiconductors | desc "mosfet"; existing rule |
| PI3CH3257ZTAEX (bus switch/mux) | ICs - Interface | desc "switch"/"mux"; add mpn `pi3ch` → Interface |
| TCPP02-M18 (USB-C port protection) | ICs - USB | desc "usb"/"port protection"; add mpn `tcpp` → ICs - USB |
| STLINK-V3SET (programmer) | Development Boards, Kits, Programmers | new rule |

Most fall into existing categories once they have a description. Add **MPN-based fallback rules** so they categorize even if a specific distributor description is sparse, and one **new category**.

- [ ] **Step 1: Write the failing tests**

Add to `tests/python/test_inventory_api_categorize.py` inside `class TestCategorize` (follow the existing `_cat` / assert style used in that file — read a couple existing cases first):

```python
    def test_stlink_is_dev_tools(self):
        row = {"Manufacture Part Number": "STLINK-V3SET",
               "Description": "STLINK-V3 modular in-circuit debugger and programmer"}
        assert categorize(row) == "Development Boards, Kits, Programmers"

    def test_programmer_by_description(self):
        row = {"Manufacture Part Number": "XYZ", "Description": "in-circuit debugger programmer"}
        assert categorize(row) == "Development Boards, Kits, Programmers"

    def test_dev_tools_does_not_match_led(self):
        # an LED part must not be captured by the dev-tools rule
        row = {"Manufacture Part Number": "WS2812B-V5/W",
               "Description": "LED RGB addressable"}
        assert categorize(row) == "LEDs"

    def test_ws2812_by_mpn_fallback(self):
        row = {"Manufacture Part Number": "WS2812B-V5/W", "Description": ""}
        assert categorize(row) == "LEDs"

    def test_murata_grm_cap_by_mpn_fallback(self):
        row = {"Manufacture Part Number": "GRM21BR61A476ME15L", "Description": ""}
        assert categorize(row) == "Passives - Capacitors"

    def test_tcpp_usb_by_mpn_fallback(self):
        row = {"Manufacture Part Number": "TCPP02-M18", "Description": ""}
        assert categorize(row) == "ICs - USB"

    def test_pi3ch_mux_by_mpn_fallback(self):
        row = {"Manufacture Part Number": "PI3CH3257ZTAEX", "Description": ""}
        assert categorize(row) == "ICs - Interface"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .claude/worktrees/fetch-descriptions && python -m pytest tests/python/test_inventory_api_categorize.py -v -k "stlink or programmer or dev_tools or ws2812 or grm or tcpp or pi3ch"`
Expected: FAIL (category is "Other" for the new cases).

- [ ] **Step 3: Add the rules**

In `categorize.py` `CATEGORY_RULES`:

Add the dev-tools rule. Place it **near the top, before the LEDs rule** so a programmer whose description mentions an LED indicator can't be miscategorized — but its keywords are specific enough not to over-capture:

```python
    # Development boards / kits / programmers (dev tools, not components)
    {"category": "Development Boards, Kits, Programmers", "desc": [
        "programmer", "debugger", "debug probe", "in-circuit",
        "evaluation board", "eval board", "development board", "dev board",
        "development kit", "starter kit", "discovery kit", "nucleo",
    ]},
    {"category": "Development Boards, Kits, Programmers", "mpn": [
        "stlink", "st-link", "j-link", "jlink",
    ]},
```

Add MPN fallbacks for the sparse-description parts. Put the capacitor `grm` fallback **after** the existing capacitor desc rule; the USB `tcpp`/`usb57`… list already exists (extend it); add `pi3ch` to Interface and `ws2812` to LEDs:

```python
    # (extend LEDs) — add an mpn fallback rule right after the LED desc rule:
    {"category": "LEDs", "mpn": ["ws2812", "sk6812", "apa102"]},
    # (extend Capacitors) — add after the capacitor desc rule:
    {"category": "Passives - Capacitors", "mpn": ["grm", "cl10", "cl21", "cl31"]},
    # (extend USB) — add "tcpp" to the existing ICs - USB mpn list:
    #   {"category": "ICs - USB", "mpn": ["usb57", "husb238", "utc2000", "tcpp"]},
    # (extend Interface) — add an mpn fallback rule after the transceiver/driver desc rule:
    {"category": "ICs - Interface", "mpn": ["pi3ch"]},
```

Important ordering checks: the `grm` capacitor mpn rule must come **before** any broad rule that could catch it; `ws2812` LED mpn rule before the dev-tools rules only matters if a description conflicts — verify with the `test_dev_tools_does_not_match_led` test. If a real ordering conflict appears, reorder and re-run tests until all pass. Do NOT add a keyword so broad it captures unrelated parts (e.g. avoid bare `"kit"` — require `"development kit"`/`"starter kit"`).

- [ ] **Step 4: Add the section to constants.json**

In `data/constants.json`, `SECTION_ORDER`, insert `"Development Boards, Kits, Programmers"` immediately before `"Other"`:

```json
    "Mechanical & Hardware",
    "Development Boards, Kits, Programmers",
    "Other"
```

(Match the exact surrounding entries — read the array first.)

- [ ] **Step 5: Run categorization tests**

Run: `python -m pytest tests/python/test_inventory_api_categorize.py -v`
Expected: PASS (all existing + 7 new).

- [ ] **Step 6: Run the real-data guard**

Run: `python -m pytest tests/python/test_real_data.py -v`
Expected: PASS — this validates produced categories against `FLAT_SECTION_ORDER`; the new category must be present in `constants.json` or this fails.

- [ ] **Step 7: Commit**

```bash
git add categorize.py data/constants.json tests/python/test_inventory_api_categorize.py
git commit -m "feat(categorize): dev-tools category + MPN fallbacks so described parts leave Other"
```

---

## Task 5: End-to-end tests (Playwright)

**Files:**
- Test: `tests/e2e/fetch-descriptions.spec.mjs` (create; confirm the E2E dir + harness by reading an existing spec such as `sticky-buttons.spec.mjs` first)

**Interfaces:**
- Consumes: the running app (E2E harness that boots the pywebview backend or a mock bridge — follow the existing spec's setup exactly).

**Design notes:** Use realistic interactions only — real clicks/typing, no `dispatchEvent`/`force:true` (project policy). Two flows: (1) command-palette bulk action toasts a summary and the grid updates; (2) Adjust modal per-part button fills the Description input. If the E2E harness uses a mock backend, stub `fetch_missing_descriptions`/`fetch_*_product` to return deterministic descriptions there.

- [ ] **Step 1: Read an existing spec to learn the harness**

Read `tests/e2e/sticky-buttons.spec.mjs` (and `resize-visibility.spec.mjs`) to learn how the app is launched, how the inventory grid is seeded, how a row's Adjust modal is opened, and how the command palette is invoked. Note the fixtures/mock-bridge mechanism.

- [ ] **Step 2: Write the E2E spec**

Create `tests/e2e/fetch-descriptions.spec.mjs` mirroring that harness. Structure (adapt selectors/setup to the real harness):

```js
import { test, expect } from '@playwright/test';
// import the same app-launch/fixture helpers the other specs use

test('per-part Fetch description button fills the Description input', async ({ page }) => {
  // launch app with a seeded part that has a distributor PN and empty description,
  // and a mock bridge where fetch_<dist>_product returns { description: 'Mock Cap 47uF', prices: [] }
  await openAdjustModalForFirstPart(page);            // helper mirroring existing specs
  const descInput = page.locator('.modal-field-input[data-field="description"]');
  await expect(descInput).toHaveValue('');
  await page.getByRole('button', { name: 'Fetch description' }).click();
  await expect(descInput).toHaveValue('Mock Cap 47uF');
});

test('command palette Fetch Missing Descriptions toasts a summary', async ({ page }) => {
  // mock bridge fetch_missing_descriptions -> { inventory: [...], summary: { updated: 2, failed: 0, skipped: 0 } }
  await openCommandPalette(page);                     // helper mirroring existing specs
  await page.getByPlaceholder(/command|search/i).fill('Fetch Missing');
  await page.getByText('Fetch Missing Descriptions').click();
  await expect(page.locator('.toast, [role="status"]')).toContainText(/Fetched 2/);
});
```

Replace the placeholder helpers with the real ones from the existing specs — do not invent a new harness.

- [ ] **Step 3: Run the E2E tests**

Run: `npx playwright test fetch-descriptions`
Expected: PASS (2 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/fetch-descriptions.spec.mjs
git commit -m "test(e2e): fetch-descriptions modal button + command palette"
```

---

## Final verification (before PR)

- [ ] **Regenerate fixtures (backend changed):**

Run: `python scripts/generate-test-fixtures.py`
Then commit any changed fixtures: `git add tests/fixtures && git commit -m "chore(fixtures): refresh after fetch-descriptions"` (only if files changed).

- [ ] **Full verify:**

Run: `bash scripts/verify.sh`
Expected: all staleness guards (fixtures, code-map, manifests, layout-tokens) + ruff + pytest + eslint + tsc + vitest PASS.

- [ ] **Manual smoke (real app):** Launch with `DUBIS_WEBVIEW_PROFILE=ephemeral python app.pyw`, open a part that has a distributor PN and no description, click **Fetch description**, verify it fills, click **Apply**. Then run command palette → **Fetch Missing Descriptions**, verify the toast and that parts leave the "Other" section. (DigiKey-PN parts need a live DigiKey session; LCSC parts fetch without setup.)

- [ ] **Push + PR:**

Run: `bash scripts/push-pr.sh --title "feat(inventory): fetch missing descriptions + categorization gaps"`
Then watch CI: `gh pr checks <number>` and fix any failures until green.
```
