# Per-distributor Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every distributor a part was sourced from as its own fetch-price row (with a per-distributor quantity), in the Adjust and Price modals, auto-picking the cheapest into the single Unit Price field.

**Architecture:** A new backend query `get_sourced_distributors(part_key)` returns the union of (distributor PNs on the part record) and (distributors found in the purchase ledger for that part). The frontend `createFetchController` in `js/inventory-modals.js` is rewritten from a single-supplier control into a multi-row panel that auto-fetches every sourced distributor concurrently on open, shows a per-row quantity + tier price, and feeds the cheapest into the modal's Unit Price (overridable by clicking a row).

**Tech Stack:** Python 3.13 + sqlite3 (backend), vanilla ES-module JS (no framework/build), pytest (Python), vitest (JS unit), Playwright (E2E).

**Spec:** `docs/specs/2026-06-30-per-distributor-pricing-design.md`

## Global Constraints

- **Error policy:** prefer `AppLog.warn`/`AppLog.error` (JS) and `logger.warning` (Python) over silent catches; throw rather than silently fail. (Copied from CLAUDE.md.)
- **Test policy:** never `pytest.skip`/`importorskip`/`mark.skip`; add missing deps to `requirements-dev.txt`.
- **E2E policy:** realistic interactions only — no `dispatchEvent`/`force:true` in Playwright specs.
- **Layout tokens:** no hard-coded px in CSS; use tokens in `css/tokens.css` (else `check-layout-tokens` fails). Append new modal CSS at the **end** of `css/modals.css` so line-anchored token-ignore entries don't shift.
- **API surface is frozen:** any new public API method must be added to `tests/python/test_api_surface.py`'s `EXPECTED` dict or the surface test fails.
- **pywebview bridge convention:** JS calls backend as `api("method_name", ...args)` (string-keyed).
- **Distributor order is fixed** everywhere for determinism: `lcsc, digikey, mouser, pololu`.
- Run `bash scripts/verify.sh` before the PR (runs fixtures/code-map/manifest/layout-token guards + ruff + pytest + eslint + tsc + vitest).

---

## File Structure

- `domain/pricing.py` — **modify**: add `get_sourced_distributors(conn, purchase_csv, part_key)`.
- `domain/api_pricing.py` — **modify**: add `PricingFacade.get_sourced_distributors(part_key)`.
- `inventory_api.py` — **modify**: add thin delegate `get_sourced_distributors(part_key)`.
- `tests/python/domain/test_pricing.py` — **modify**: tests for `get_sourced_distributors`.
- `tests/python/test_api_surface.py` — **modify**: register the new method signature.
- `js/inventory-modals.js` — **modify**: add pure `rowPrice`/`cheapestRow`; rewrite `createFetchController` into a multi-row panel; update `init()` wiring for both modals.
- `index.html` — **modify**: replace the Adjust-modal fetch markup with a single panel container.
- `css/tokens.css` — **modify**: add any needed `--fetch-*` tokens.
- `css/modals.css` — **modify**: append multi-row panel styles.
- `tests/js/fetch-rows.test.js` — **create**: vitest unit tests for `rowPrice` + `cheapestRow`.
- `tests/js/e2e/helpers.mjs` — **modify**: mock `get_sourced_distributors`.
- `tests/js/e2e/adjust-fetch-price.spec.mjs` — **rewrite**: multi-row panel behaviors.

---

## Task 1: Backend — `get_sourced_distributors`

**Files:**
- Modify: `domain/pricing.py` (add function after `get_price_summary`, ~line 264)
- Test: `tests/python/domain/test_pricing.py`

**Interfaces:**
- Consumes: existing `resolve_part_key(conn, key)` in `domain/pricing.py`.
- Produces:
  ```python
  def get_sourced_distributors(
      conn: sqlite3.Connection, purchase_csv: str, part_key: str
  ) -> list[dict[str, str]]:
      # -> [{"distributor": "lcsc", "part_number": "C555"}, ...]
      # deduped by distributor; order fixed: lcsc, digikey, mouser, pololu.
  ```

- [ ] **Step 1: Write the failing tests**

Append to `tests/python/domain/test_pricing.py`:

```python
from domain.pricing import get_sourced_distributors


def _parts_conn():
    """In-memory parts table matching the columns resolve_part_key/query use."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE parts (part_id TEXT PRIMARY KEY, lcsc TEXT DEFAULT '', "
        "mpn TEXT DEFAULT '', digikey TEXT DEFAULT '', pololu TEXT DEFAULT '', "
        "mouser TEXT DEFAULT '')"
    )
    return conn


def _write_ledger(path, rows):
    cols = ["Digikey Part Number", "LCSC Part Number", "Pololu Part Number",
            "Mouser Part Number", "Manufacture Part Number", "Quantity"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


class TestGetSourcedDistributors:
    def test_has_pn_only(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        assert out == [{"distributor": "lcsc", "part_number": "C555"}]

    def test_purchased_recovers_distributor_absent_from_record(self, tmp_path):
        # Record only shows LCSC (last-write-wins), but the part was also bought
        # from Digikey earlier — the ledger must recover the Digikey row.
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [
            {"LCSC Part Number": "C555", "Quantity": "10"},
            {"Digikey Part Number": "DK-555", "LCSC Part Number": "C555", "Quantity": "5"},
        ])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        assert out == [
            {"distributor": "lcsc", "part_number": "C555"},
            {"distributor": "digikey", "part_number": "DK-555"},
        ]

    def test_record_pn_preferred_over_ledger_pn(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [{"LCSC Part Number": "C555-OLD", "Manufacture Part Number": "X"}])
        # Ledger row matches via nothing shared → not matched; use a shared key:
        _write_ledger(ledger, [{"LCSC Part Number": "C555"}])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        # record PN 'C555' wins (identical here); single lcsc entry, no dup.
        assert out == [{"distributor": "lcsc", "part_number": "C555"}]

    def test_most_recent_ledger_pn_wins(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, mpn) VALUES ('XYZ', 'XYZ')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [
            {"Digikey Part Number": "DK-OLD", "Manufacture Part Number": "XYZ"},
            {"Digikey Part Number": "DK-NEW", "Manufacture Part Number": "XYZ"},
        ])
        out = get_sourced_distributors(conn, str(ledger), "XYZ")
        assert {"distributor": "digikey", "part_number": "DK-NEW"} in out
        assert {"distributor": "digikey", "part_number": "DK-OLD"} not in out

    def test_no_match_returns_empty(self, tmp_path):
        conn = _parts_conn()
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [{"LCSC Part Number": "C999"}])
        assert get_sourced_distributors(conn, str(ledger), "C555") == []

    def test_missing_ledger_file_uses_record_only(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, digikey) VALUES ('DK1', 'DK1')")
        out = get_sourced_distributors(conn, str(tmp_path / "nope.csv"), "DK1")
        assert out == [{"distributor": "digikey", "part_number": "DK1"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .claude/worktrees/per-distributor-pricing && python -m pytest tests/python/domain/test_pricing.py -k GetSourcedDistributors -v`
Expected: FAIL with `ImportError: cannot import name 'get_sourced_distributors'`.

- [ ] **Step 3: Implement `get_sourced_distributors`**

Add to `domain/pricing.py` (after `get_price_summary`):

```python
# Distributor key → purchase_ledger.csv column header.
_LEDGER_PN_COLS = {
    "lcsc": "LCSC Part Number",
    "digikey": "Digikey Part Number",
    "mouser": "Mouser Part Number",
    "pololu": "Pololu Part Number",
}
_DISTRIBUTOR_ORDER = ("lcsc", "digikey", "mouser", "pololu")


def get_sourced_distributors(
    conn: sqlite3.Connection, purchase_csv: str, part_key: str
) -> list[dict[str, str]]:
    """Distributors a part was sourced from: union of (record PNs) ∪ (ledger PNs).

    Returns one entry per distributor, deduped, in fixed order. Each entry's
    part_number prefers the current record PN, falling back to the most recent
    matching purchase-ledger PN. Returns [] when nothing matches.
    """
    resolved = resolve_part_key(conn, part_key) or part_key

    # ── record PNs (has-PN set) ──
    record: dict[str, str] = {}
    known_pns: set[str] = {resolved}
    try:
        row = conn.execute(
            "SELECT lcsc, digikey, mouser, pololu, mpn FROM parts WHERE part_id = ?",
            (resolved,),
        ).fetchone()
    except sqlite3.Error:
        logger.warning("get_sourced_distributors: parts query failed for %r", part_key)
        row = None
    if row is not None:
        for dist in _DISTRIBUTOR_ORDER:
            val = (row[dist] or "").strip()
            if val:
                record[dist] = val
                known_pns.add(val)
        mpn = (row["mpn"] or "").strip()
        if mpn:
            known_pns.add(mpn)

    # ── ledger PNs (purchased set) — most recent row per distributor wins ──
    ledger: dict[str, str] = {}
    if os.path.exists(purchase_csv):
        with open(purchase_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                row_pns = {(r.get(c) or "").strip() for c in _LEDGER_PN_COLS.values()}
                row_pns.add((r.get("Manufacture Part Number") or "").strip())
                row_pns.discard("")
                if not (row_pns & known_pns):
                    continue
                for dist, col in _LEDGER_PN_COLS.items():
                    val = (r.get(col) or "").strip()
                    if val:
                        ledger[dist] = val  # later row = more recent → overwrites

    # ── union, record PN preferred ──
    out: list[dict[str, str]] = []
    for dist in _DISTRIBUTOR_ORDER:
        pn = record.get(dist) or ledger.get(dist)
        if pn:
            out.append({"distributor": dist, "part_number": pn})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .claude/worktrees/per-distributor-pricing && python -m pytest tests/python/domain/test_pricing.py -k GetSourcedDistributors -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add domain/pricing.py tests/python/domain/test_pricing.py
git commit -m "feat(pricing): get_sourced_distributors — union of record PNs and ledger PNs"
```

---

## Task 2: Backend — facade + API delegate + surface freeze

**Files:**
- Modify: `domain/api_pricing.py` (add method to `PricingFacade`)
- Modify: `inventory_api.py` (add delegate near line 289, next to `get_price_summary`)
- Modify: `tests/python/test_api_surface.py` (register signature)

**Interfaces:**
- Consumes: `domain.pricing.get_sourced_distributors` (Task 1); `self._api._get_cache()`, `self._api.input_csv`.
- Produces: public API method `get_sourced_distributors(part_key)` returning `list[dict[str,str]]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/python/test_api_surface.py`'s `EXPECTED` dict (keep it alphabetical — insert between `get_price_summary` and `get_warnings`):

```python
    'get_sourced_distributors': '(part_key)',
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .claude/worktrees/per-distributor-pricing && python -m pytest tests/python/test_api_surface.py -v`
Expected: FAIL — method present in EXPECTED but not on the API class (or signature mismatch).

- [ ] **Step 3: Implement facade + delegate**

In `domain/api_pricing.py`, add to `PricingFacade`:

```python
    def get_sourced_distributors(self, part_key: str) -> list[dict[str, str]]:
        """Distributors this part was sourced from (record PNs ∪ ledger PNs)."""
        return domain.pricing.get_sourced_distributors(
            self._api._get_cache(), self._api.input_csv, part_key,
        )
```

In `inventory_api.py`, add next to `get_price_summary` (around line 289):

```python
    def get_sourced_distributors(self, part_key: str) -> list[dict[str, str]]:
        return self._pricing.get_sourced_distributors(part_key)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd .claude/worktrees/per-distributor-pricing && python -m pytest tests/python/test_api_surface.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add domain/api_pricing.py inventory_api.py tests/python/test_api_surface.py
git commit -m "feat(api): expose get_sourced_distributors on the pywebview surface"
```

---

## Task 3: Frontend — pure helpers `rowPrice` + `cheapestRow`

**Files:**
- Modify: `js/inventory-modals.js` (add exports near `pickTier`, ~line 38)
- Create: `tests/js/fetch-rows.test.js`

**Interfaces:**
- Consumes: existing exported `pickTier(prices, targetQty)`.
- Produces:
  ```js
  export function rowPrice(prices, qty)   // -> {tier, unitPrice, extPrice} | {tier:null, unitPrice:null, extPrice:null}
  export function cheapestRow(rows)       // rows: [{unitPrice}], -> index of min finite unitPrice (ties→lowest index), or -1
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/js/fetch-rows.test.js` (mocks mirror `tests/js/pick-tier.test.js` so the module loads in isolation):

```js
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  Modal: vi.fn(() => ({ open: vi.fn(), close: vi.fn(), el: { classList: { contains: () => true } } })),
  linkPriceInputs: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));
vi.mock('../../js/undo-redo.js', () => ({
  UndoRedo: { register: vi.fn(), save: vi.fn(), popLast: vi.fn(), _undo: [] },
}));
vi.mock('../../js/store.js', () => ({ onInventoryUpdated: vi.fn() }));

import { rowPrice, cheapestRow } from '../../js/inventory-modals.js';

describe('rowPrice', () => {
  const tiers = [{ qty: 1, price: 1.00 }, { qty: 100, price: 0.50 }];

  it('returns unit + extended price for the chosen tier', () => {
    expect(rowPrice(tiers, 100)).toEqual({ tier: { qty: 100, price: 0.50 }, unitPrice: 0.50, extPrice: 50 });
  });

  it('uses the low tier below the smallest break, ext = unit * qty', () => {
    expect(rowPrice(tiers, 1)).toEqual({ tier: { qty: 1, price: 1.00 }, unitPrice: 1.00, extPrice: 1.00 });
  });

  it('returns nulls when there are no prices', () => {
    expect(rowPrice([], 10)).toEqual({ tier: null, unitPrice: null, extPrice: null });
    expect(rowPrice(null, 10)).toEqual({ tier: null, unitPrice: null, extPrice: null });
  });
});

describe('cheapestRow', () => {
  it('returns the index of the lowest unit price', () => {
    expect(cheapestRow([{ unitPrice: 5 }, { unitPrice: 2 }, { unitPrice: 9 }])).toBe(1);
  });

  it('breaks ties toward the lowest index', () => {
    expect(cheapestRow([{ unitPrice: 2 }, { unitPrice: 2 }])).toBe(0);
  });

  it('ignores rows without a finite price', () => {
    expect(cheapestRow([{ unitPrice: null }, { unitPrice: 3 }, { unitPrice: undefined }])).toBe(1);
  });

  it('returns -1 when no row has a price', () => {
    expect(cheapestRow([{ unitPrice: null }, {}])).toBe(-1);
    expect(cheapestRow([])).toBe(-1);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .claude/worktrees/per-distributor-pricing && npx vitest run tests/js/fetch-rows.test.js`
Expected: FAIL — `rowPrice`/`cheapestRow` not exported.

- [ ] **Step 3: Implement the helpers**

In `js/inventory-modals.js`, add after `pickTier` (after line 38):

```js
/** Resolve a distributor row's price at a target quantity.
 *  Returns the chosen tier plus unit + extended (unit × qty) price,
 *  or all-null when there are no usable price tiers. */
export function rowPrice(prices, qty) {
  const tier = pickTier(prices, qty);
  if (!tier) return { tier: null, unitPrice: null, extPrice: null };
  return { tier, unitPrice: tier.price, extPrice: tier.price * qty };
}

/** Index of the cheapest row by unitPrice (ties → lowest index).
 *  Rows whose unitPrice is not a finite number are ignored. Returns -1
 *  when no row has a usable price. */
export function cheapestRow(rows) {
  let best = -1;
  let bestPrice = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const p = rows[i] && rows[i].unitPrice;
    if (typeof p === "number" && isFinite(p) && p < bestPrice) {
      bestPrice = p;
      best = i;
    }
  }
  return best;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd .claude/worktrees/per-distributor-pricing && npx vitest run tests/js/fetch-rows.test.js`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add js/inventory-modals.js tests/js/fetch-rows.test.js
git commit -m "feat(inventory): pure rowPrice/cheapestRow helpers for multi-distributor pricing"
```

---

## Task 4: Frontend — rewrite `createFetchController` into a multi-row panel

This is the core UI task. The controller no longer takes a supplier dropdown + single button + flat tier list; it takes one **panel element** and renders a row per sourced distributor.

**Files:**
- Modify: `js/inventory-modals.js` (rewrite `createFetchController`; update the two `init()` call sites at ~line 248 and ~line 479; update `index.html` markup usage)
- Modify: `index.html` (Adjust-modal fetch markup, lines 248-252)
- Modify: `css/tokens.css`, `css/modals.css`

**Interfaces:**
- Consumes: `pickTier`, `rowPrice`, `cheapestRow` (Task 3); `FETCH_SUPPLIERS` (existing, maps distributor key → label + method); `api()`, `invPartKey()`, `escHtml()`, `AppLog`.
- Produces: `createFetchController({ panelEl, unitInput })` returning `{ configure(part) }`. Replaces the old `{ supplierSelect, fetchBtn, tiersEl, unitInput }` signature.

- [ ] **Step 1: Replace the Adjust-modal markup in `index.html`**

Replace lines 248-252 (the `#adj-fetch-row` / `#adj-fetch-supplier` / `#adj-fetch-price` / `#adj-fetch-tiers` block) with:

```html
    <div class="fetch-panel hidden" id="adj-fetch-panel"></div>
```

- [ ] **Step 2: Rewrite `createFetchController` (`js/inventory-modals.js`)**

Replace the entire `createFetchController` function (lines 127-218) with:

```js
/**
 * Wire the multi-distributor "current price" panel shared by the Adjust and
 * Price modals. Renders one row per distributor the part was sourced from
 * (union of record PNs + purchase-ledger PNs, from get_sourced_distributors),
 * auto-fetches every row's price concurrently on open, and feeds the cheapest
 * row's unit price into `unitInput` (overridable by clicking a row).
 *
 * @param {{panelEl: HTMLElement, unitInput: HTMLInputElement}} els
 */
function createFetchController({ panelEl, unitInput }) {
  /** @type {Array<{distributor:string,label:string,method:string,partNumber:string,
   *   qty:number,prices:Array<{qty:number,price:number}>|null,
   *   unitPrice:number|null,extPrice:number|null,error:string}>} */
  let rows = [];
  let pinnedIndex = -1;
  let pk = "";

  function setUnitPrice(price) {
    unitInput.value = price;
    unitInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function fmt(n) {
    return "$" + Number(n).toFixed(4);
  }

  // Render every row from current state. Highlights the selected row.
  function render(selectedIndex) {
    panelEl.innerHTML = rows.map((r, i) => {
      const sel = i === selectedIndex ? " selected" : "";
      let priceCell;
      if (r.error) {
        priceCell = '<span class="fetch-drow-err">' + escHtml(r.error) + '</span>';
      } else if (r.unitPrice == null) {
        priceCell = '<span class="fetch-drow-pending">…</span>';
      } else {
        priceCell = '<span class="fetch-drow-unit">' + escHtml(fmt(r.unitPrice)) +
          '</span><span class="fetch-drow-ext">×' + escHtml(String(r.qty)) + ' = ' +
          escHtml("$" + Number(r.extPrice).toFixed(2)) + '</span>';
      }
      return '<div class="fetch-drow' + sel + '" data-idx="' + i + '">' +
        '<span class="fetch-drow-label">' + escHtml(r.label) + '</span>' +
        '<input type="number" class="fetch-drow-qty" min="1" step="1" value="' +
          escHtml(String(r.qty)) + '" data-idx="' + i + '">' +
        '<span class="fetch-drow-pn">' + escHtml(r.partNumber) + '</span>' +
        priceCell + '</div>';
    }).join("");
    panelEl.classList.toggle("hidden", rows.length === 0);
  }

  // Recompute one row's price from its fetched tiers + current qty.
  function recompute(i) {
    const r = rows[i];
    if (!r.prices) return;
    const { unitPrice, extPrice } = rowPrice(r.prices, r.qty);
    r.unitPrice = unitPrice;
    r.extPrice = extPrice;
  }

  // Auto-pick cheapest (unless a row is pinned) and push its price to unitInput.
  function applySelection() {
    const idx = pinnedIndex >= 0 ? pinnedIndex : cheapestRow(rows);
    if (idx >= 0 && rows[idx].unitPrice != null) setUnitPrice(rows[idx].unitPrice);
    render(idx);
  }

  // Fetch one distributor row's live price; on failure fall back to cached
  // get_price_summary, else mark the row unavailable.
  async function fetchRow(i, priceSummary) {
    const r = rows[i];
    try {
      const product = await api(r.method, r.partNumber);
      if (product && Array.isArray(product.prices) && product.prices.length) {
        r.prices = product.prices;
        recompute(i);
        // fire-and-forget price-history logging (unchanged behavior)
        api("record_fetched_prices", pk, r.distributor, product.prices).catch(() => {});
        return;
      }
    } catch (e) {
      AppLog.warn("Price fetch failed for " + r.distributor + " " + r.partNumber);
    }
    // Fallback: last-known cached price for this distributor.
    const cached = priceSummary && priceSummary[r.distributor];
    if (cached && typeof cached.latest_unit_price === "number") {
      r.unitPrice = cached.latest_unit_price;
      r.extPrice = cached.latest_unit_price * r.qty;
    } else {
      r.error = "unavailable";
    }
  }

  // qty edits: update that row, re-select (unless pinned to another row).
  panelEl.addEventListener("input", (e) => {
    const input = /** @type {HTMLElement} */ (e.target);
    if (!input.classList.contains("fetch-drow-qty")) return;
    const i = Number(input.dataset.idx);
    const q = parseInt(/** @type {HTMLInputElement} */ (input).value, 10);
    rows[i].qty = q > 0 ? q : 1;
    recompute(i);
    applySelection();
  });

  // row click (not on the qty input): pin that row.
  panelEl.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (target.classList.contains("fetch-drow-qty")) return;
    const rowEl = target.closest(".fetch-drow");
    if (!rowEl) return;
    const i = Number(/** @type {HTMLElement} */ (rowEl).dataset.idx);
    if (rows[i].unitPrice == null) return;
    pinnedIndex = i;
    setUnitPrice(rows[i].unitPrice);
    render(i);
  });

  /** Set up the panel for a newly opened modal. */
  async function configure(part) {
    pk = invPartKey(part);
    pinnedIndex = -1;
    rows = [];
    panelEl.innerHTML = "";
    panelEl.classList.add("hidden");

    const [sourced, lastPoQty, priceSummary] = await Promise.all([
      api("get_sourced_distributors", pk),
      api("get_last_po_quantity", pk),
      api("get_price_summary", pk).catch(() => ({})),
    ]);
    const defaultQty = (typeof lastPoQty === "number" && lastPoQty > 0)
      ? lastPoQty : (part.qty > 0 ? part.qty : 1);

    rows = (sourced || []).map((s) => {
      const sup = FETCH_SUPPLIERS.find((f) => f.key === s.distributor);
      return {
        distributor: s.distributor,
        label: sup ? sup.label : s.distributor,
        method: sup ? sup.method : "",
        partNumber: s.part_number,
        qty: defaultQty,
        prices: null,
        unitPrice: null,
        extPrice: null,
        error: "",
      };
    }).filter((r) => r.method);

    if (rows.length === 0) { render(-1); return; }
    render(-1);  // show pending rows immediately

    await Promise.allSettled(rows.map((_, i) => fetchRow(i, priceSummary)));
    applySelection();
  }

  return { configure };
}
```

- [ ] **Step 3: Update the two `init()` call sites (`js/inventory-modals.js`)**

Replace the Adjust-modal controller wiring (currently lines 248-253):

```js
  adjFetch = createFetchController({
    panelEl: /** @type {HTMLElement} */ (document.getElementById("adj-fetch-panel")),
    unitInput: adjUnitPrice,
  });
```

Replace the Price-modal fetch injection block (currently lines 444-484) with a single panel element and updated controller wiring:

```js
  // ── Multi-distributor fetch panel for the price modal ────────────────────
  // Inject a single panel container before the action buttons.
  const priceModalInner = priceFormModal.el.querySelector(".modal");
  const priceActionsEl  = priceFormModal.el.querySelector(".modal-actions");

  const priceFetchPanel = el("div", { id: "price-fetch-panel", class: "fetch-panel hidden" });
  priceModalInner.insertBefore(priceFetchPanel, priceActionsEl);

  const priceUnitInputEl = /** @type {HTMLInputElement} */ (document.getElementById("price-unit"));
  priceFetchController = createFetchController({
    panelEl:   priceFetchPanel,
    unitInput: priceUnitInputEl,
  });
```

> Note: `configure` is now `async`; the existing `priceFormModal.open` wrapper (lines 489-494) calls `priceFetchController.configure(item)` — leave that call as-is (fire-and-forget; do not await). Same for `adjFetch.configure(item)` in `openAdjustModal` (line 107) — leave un-awaited.

- [ ] **Step 4: Add CSS tokens + panel styles**

In `css/tokens.css`, inside `:root`, add (values chosen to match existing modal spacing tokens — reuse existing `--fetch-*` if present, otherwise add):

```css
  --fetch-drow-gap: 8px;
  --fetch-drow-pad: 6px;
  --fetch-drow-radius: 6px;
```

Append to the **end** of `css/modals.css`:

```css
/* ── Multi-distributor fetch panel (Adjust & Price modals) ── */
.fetch-panel {
  display: flex;
  flex-direction: column;
  gap: var(--fetch-drow-gap);
  margin: var(--fetch-drow-gap) 0;
}
.fetch-drow {
  display: flex;
  align-items: center;
  gap: var(--fetch-drow-gap);
  padding: var(--fetch-drow-pad);
  border: 1px solid var(--border-color);
  border-radius: var(--fetch-drow-radius);
  cursor: pointer;
}
.fetch-drow.selected {
  border-color: var(--accent-color);
  background: var(--accent-bg, var(--hover-bg));
}
.fetch-drow-label { font-weight: 600; min-width: 4em; }
.fetch-drow-qty { width: 5em; }
.fetch-drow-pn { color: var(--text-muted); flex: 1; }
.fetch-drow-unit { font-weight: 600; }
.fetch-drow-ext { color: var(--text-muted); }
.fetch-drow-err { color: var(--danger-color, #c00); font-style: italic; }
.fetch-drow-pending { color: var(--text-muted); }
```

> If any referenced variable (`--border-color`, `--accent-color`, `--hover-bg`, `--text-muted`, `--danger-color`) does not exist in `css/tokens.css`, grep the existing modal CSS for the equivalent token in use and substitute it — do not hard-code a hex/px value (except inside a `var(..., fallback)` which `check-layout-tokens` permits).

- [ ] **Step 5: Lint + type-check**

Run: `cd .claude/worktrees/per-distributor-pricing && npx eslint js/inventory-modals.js && npx tsc --noEmit`
Expected: no errors. (Fix any `@ts-check` complaints about nullable DOM lookups with existing `/** @type {...} */` casts.)

- [ ] **Step 6: Run existing vitest unit tests (regression)**

Run: `cd .claude/worktrees/per-distributor-pricing && npx vitest run tests/js/pick-tier.test.js tests/js/fetch-rows.test.js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add js/inventory-modals.js index.html css/tokens.css css/modals.css
git commit -m "feat(inventory): multi-distributor fetch-price panel in Adjust/Price modals"
```

---

## Task 5: E2E — mock harness + rewritten spec

**Files:**
- Modify: `tests/js/e2e/helpers.mjs` (add `get_sourced_distributors` mock)
- Rewrite: `tests/js/e2e/adjust-fetch-price.spec.mjs`

**Interfaces:**
- Consumes: the panel DOM contract from Task 4 — `#adj-fetch-panel` / `#price-fetch-panel` containing `.fetch-drow[data-idx]` rows, each with `.fetch-drow-qty`, `.fetch-drow-unit`, `.fetch-drow.selected`.

- [ ] **Step 1: Add the mock to `helpers.mjs`**

In `addMockSetup`'s `api` object (after `get_price_summary`, ~line 72), add:

```js
        get_sourced_distributors: async (pk) => {
          const over = opts.sourcedDistributors || {};
          if (pk in over) return over[pk];
          // Default: has-PN set derived from the matching inventory row.
          const part = inv.find(p =>
            [p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser].includes(pk));
          if (!part) return [];
          const out = [];
          for (const d of ['lcsc', 'digikey', 'mouser', 'pololu']) {
            if ((part[d] || '').trim()) out.push({ distributor: d, part_number: part[d] });
          }
          return out;
        },
```

- [ ] **Step 2: Rewrite the spec**

Replace `tests/js/e2e/adjust-fetch-price.spec.mjs` in full:

```js
// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

// ── Test inventory ──
const INVENTORY = [
  { section: "Passives - Capacitors > MLCC",
    lcsc: "C2040", digikey: "", pololu: "", mouser: "",
    mpn: "CL05A104KA5NNNC", manufacturer: "Samsung",
    package: "0402", description: "100nF MLCC Capacitor",
    qty: 200, unit_price: 0.0025, ext_price: 0.50 },
  { section: "Connectors > Through Hole",
    lcsc: "C555", digikey: "", pololu: "", mouser: "M-555",
    mpn: "XYZ-555", manufacturer: "Acme",
    package: "SOT-23", description: "Dual-sourced widget",
    qty: 30, unit_price: 5.00, ext_price: 150.00 },
  // Cheapest flips with quantity: lcsc cheaper at qty 1, digikey cheaper at qty 100.
  { section: "ICs > Logic",
    lcsc: "C-FLIP", digikey: "DK-FLIP", pololu: "", mouser: "",
    mpn: "FLIP-1", manufacturer: "Flippy",
    package: "SOIC-8", description: "Flip part",
    qty: 10, unit_price: 0, ext_price: 0 },
  // One distributor errors (no Mouser mock) while LCSC succeeds.
  { section: "ICs > Analog",
    lcsc: "C-OK", digikey: "", pololu: "", mouser: "M-FAIL",
    mpn: "OK-1", manufacturer: "Okay",
    package: "SOT-23", description: "Partial-fetch part",
    qty: 10, unit_price: 0, ext_price: 0 },
  // Price-less single-source part → opens the Price modal via the ⚠ button.
  { section: "Passives - Resistors > Chip Resistors",
    lcsc: "C9999", digikey: "", pololu: "", mouser: "",
    mpn: "RES-9999", manufacturer: "Yageo",
    package: "0402", description: "Priceless resistor",
    qty: 50, unit_price: 0, ext_price: 0 },
];

const MOCK_PRODUCTS = {
  "lcsc:C2040": { productCode: "C2040", prices: [{ qty: 1, price: 0.0025 }, { qty: 100, price: 0.001 }], provider: "lcsc" },
  "lcsc:C555":  { productCode: "C555",  prices: [{ qty: 1, price: 5.55 }, { qty: 10, price: 4.44 }], provider: "lcsc" },
  "mouser:M-555": { productCode: "M-555", prices: [{ qty: 1, price: 9.99 }, { qty: 10, price: 8.00 }], provider: "mouser" },
  "lcsc:C-FLIP":   { productCode: "C-FLIP",  prices: [{ qty: 1, price: 1.00 }, { qty: 100, price: 0.90 }], provider: "lcsc" },
  "digikey:DK-FLIP": { productCode: "DK-FLIP", prices: [{ qty: 1, price: 1.10 }, { qty: 100, price: 0.50 }], provider: "digikey" },
  "lcsc:C-OK":  { productCode: "C-OK", prices: [{ qty: 1, price: 2.00 }, { qty: 10, price: 1.50 }], provider: "lcsc" },
  // no mouser:M-FAIL mock → that row fails to fetch
  "lcsc:C9999": { productCode: "C9999", prices: [{ qty: 1, price: 0.02 }, { qty: 100, price: 0.005 }], provider: "lcsc" },
};

// lastPoQty drives each row's default quantity (and thus which tier is chosen).
const LAST_PO_QTY = { C2040: 100, C555: 10, "C-FLIP": 1, "C-OK": 10, C9999: 100 };

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');
const readNumber = async (loc) => Number(await loc.inputValue());

test.describe('Multi-distributor fetch price — Adjust & Price modals', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY, { productMocks: MOCK_PRODUCTS, lastPoQty: LAST_PO_QTY });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('single source: one row, auto-fetch fills unit price from PO-matched tier', async ({ page }) => {
    await partRow(page, 'C2040').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(1);
    // qty-100 tier auto-picked (lastPoQty 100) and auto-selected as cheapest.
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveCount(1);
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(0.001, 6);
    expect(await readNumber(page.locator('#adj-ext-price'))).toBeCloseTo(0.2, 6);
  });

  test('multi source: two rows, cheapest auto-selected into unit price', async ({ page }) => {
    await partRow(page, 'C555').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(2);   // lcsc + mouser
    // At qty 10: lcsc 4.44 < mouser 8.00 → lcsc auto-selected.
    const selected = page.locator('#adj-fetch-panel .fetch-drow.selected');
    await expect(selected).toHaveCount(1);
    await expect(selected).toHaveAttribute('data-idx', '0');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(4.44, 4);

    // record_fetched_prices fired for both distributors.
    await expect.poll(async () =>
      (await page.evaluate(() => window.__apiCalls['record_fetched_prices'] || [])).length
    ).toBeGreaterThanOrEqual(2);
  });

  test('per-row qty change re-selects the cheapest distributor', async ({ page }) => {
    await partRow(page, 'C-FLIP').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await expect(page.locator('#adj-fetch-panel .fetch-drow')).toHaveCount(2);

    // At qty 1: lcsc 1.00 < digikey 1.10 → lcsc (idx 0) selected.
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(1.00, 4);

    // Bump the digikey row (idx 1) to qty 100 → digikey 0.50 now cheapest.
    const dkQty = page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"] .fetch-drow-qty');
    await dkQty.fill('100');
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(0.50, 4);
  });

  test('clicking a row pins it, overriding cheapest auto-pick', async ({ page }) => {
    await partRow(page, 'C555').locator('.adj-btn').click();
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');

    // Click the mouser row (idx 1) to pin the pricier source.
    await page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"]').click();
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(8.00, 4);

    // Editing the lcsc row's qty must NOT steal the selection back.
    await page.locator('#adj-fetch-panel .fetch-drow[data-idx="0"] .fetch-drow-qty').fill('1');
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(8.00, 4);
  });

  test('one distributor fails: its row shows unavailable, others still fetch', async ({ page }) => {
    await partRow(page, 'C-OK').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(2);   // lcsc (ok) + mouser (fails)
    // Mouser row shows the error state.
    await expect(page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"] .fetch-drow-err')).toHaveText(/unavailable/);
    // lcsc row (idx 0) is selected and drives the unit price (qty 10 → 1.50).
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(1.50, 4);
  });

  test('price modal (warning sign): panel fetches and fills unit price', async ({ page }) => {
    await partRow(page, 'C9999').locator('.price-warn-btn').click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#price-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(1);
    await expect(page.locator('#price-fetch-panel .fetch-drow.selected')).toHaveCount(1);
    // qty-100 tier (0.005) auto-picked; ext = 0.005 * 50 = 0.25.
    expect(await readNumber(page.locator('#price-unit'))).toBeCloseTo(0.005, 6);
    expect(await readNumber(page.locator('#price-ext'))).toBeCloseTo(0.25, 6);
  });
});
```

- [ ] **Step 3: Run the E2E spec**

Run: `cd .claude/worktrees/per-distributor-pricing && npx playwright test adjust-fetch-price`
Expected: PASS (6 tests). If a row's auto-fetch hasn't resolved before an assertion, the assertions already use Playwright's auto-retrying `expect` — but if `#adj-unit-price` reads race, wrap the read in `await expect.poll(...)`.

- [ ] **Step 4: Commit**

```bash
git add tests/js/e2e/helpers.mjs tests/js/e2e/adjust-fetch-price.spec.mjs
git commit -m "test(e2e): multi-distributor fetch-price panel — auto-fetch, cheapest, pin, partial-failure"
```

---

## Task 6: Regenerate artifacts + full verify

**Files:** possibly `tests/fixtures/generated/*`, `docs/code-map.md` (regenerated, not hand-edited).

- [ ] **Step 1: Regenerate fixtures + code-map**

Run:
```bash
cd .claude/worktrees/per-distributor-pricing
python scripts/generate-test-fixtures.py
python scripts/gen-code-map.py 2>/dev/null || true   # if present; otherwise verify.sh regenerates
```
Expected: no errors; `git status` may show updated `docs/code-map.md` (new `get_sourced_distributors` symbol).

- [ ] **Step 2: Full verify**

Run: `cd .claude/worktrees/per-distributor-pricing && bash scripts/verify.sh`
Expected: all guards + ruff + pytest + eslint + tsc + vitest PASS. Fix anything red before proceeding. (Playwright is not part of verify.sh; it was run in Task 5.)

- [ ] **Step 3: Commit any regenerated artifacts**

```bash
git add -A
git commit -m "chore: regenerate fixtures + code-map for get_sourced_distributors" || echo "nothing to regenerate"
```

- [ ] **Step 4: Push + open PR**

Run: `cd .claude/worktrees/per-distributor-pricing && bash scripts/push-pr.sh --title "feat(inventory): per-distributor pricing in Adjust/Price modals"`
Then monitor CI (`gh pr checks <number>`) and fix failures until green.

---

## Self-Review

**Spec coverage:**
- Union "sourced from" (record ∪ ledger) → Task 1 (`get_sourced_distributors`) ✓
- Lives in Adjust + Price modals → Task 4 (both call sites) ✓
- Per-distributor quantity → Task 4 (`.fetch-drow-qty` + `recompute`) ✓
- Auto-fetch all on open → Task 4 (`Promise.allSettled` in `configure`) ✓
- Auto-pick cheapest, overridable by click → Task 3 (`cheapestRow`) + Task 4 (`applySelection`/pin) ✓
- Per-row failure isolation + cached fallback → Task 4 (`fetchRow` try/catch + `get_price_summary`) ✓
- `record_fetched_prices` still fires per success → Task 4 ✓
- Tests: Python (Task 1), JS unit (Task 3), E2E (Task 5), fixtures/code-map (Task 6) ✓
- Non-goals (no ledger writes, no grid display, no persisted qty, no schema change) — respected; no task adds them ✓

**Placeholder scan:** none — every code step contains full content.

**Type consistency:** `createFetchController({panelEl, unitInput})` used identically in both Task 4 call sites; `rowPrice`/`cheapestRow`/`get_sourced_distributors` signatures match between definition (Tasks 1, 3) and use (Task 4). Distributor order `lcsc, digikey, mouser, pololu` consistent across backend, mock, and spec.
