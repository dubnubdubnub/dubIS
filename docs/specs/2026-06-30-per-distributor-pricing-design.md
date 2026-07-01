# Per-distributor pricing in the Adjust/Price modals

**Date:** 2026-06-30
**Status:** Approved design — ready for implementation plan
**Branch:** `claude/feature-per-distributor-pricing`

## Problem

A part can be sourced from more than one distributor (e.g. bought from LCSC once
and DigiKey once). The current "Fetch current price" control in the Adjust and
Price modals is single-supplier: when a part has 2+ distributor PNs it shows a
dropdown to pick *one* supplier, fetches its price, and fills the single Unit
Price field. There is no way to see prices from all the distributors a part comes
from at once, and no way to set a quantity per distributor.

We want: a view that shows, for every distributor a part was sourced from, that
distributor's fetched price, with a per-distributor quantity input, feeding the
modal's single Unit Price.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| What counts as "sourced from"? | **Union** of (has a distributor PN) ∪ (purchased from, per ledger) |
| Where does it live? | The **Adjust and Price modals** — replace the single-supplier fetch controls |
| What does setting a row's quantity do? | Show the price-break tier price at that qty (existing `pickTier`), feeding Unit Price |
| How are prices fetched on open? | **Auto-fetch all** distributor rows concurrently when the modal opens |
| How does a row feed the single Unit Price? | **Auto-pick cheapest** at its quantity; overridable by clicking a row |

## Architecture

### 1. Backend — `get_sourced_distributors`

New facade method on `PricingFacade` (`domain/api_pricing.py`), delegating to a
new function in `domain/pricing.py`:

```python
get_sourced_distributors(part_key: str) -> list[dict]
# -> [{"distributor": "lcsc", "part_number": "C429942"}, ...]
```

The returned list is the **union** of two sets, deduped by distributor:

1. **Has-PN** — for each of the four fetchable distributors
   (`lcsc`/`digikey`/`mouser`/`pololu`), if the current part record (parts table)
   has a non-empty PN column, include `{distributor, part_number=<record PN>}`.

2. **Purchased** — scan `purchase_ledger.csv` for rows belonging to this part.
   A ledger row belongs to the part when any of its PN columns
   (`Digikey/LCSC/Pololu/Mouser Part Number`) equals one of the part's known PNs
   or its `part_id`. For each such row, for each non-empty distributor PN column,
   include `{distributor, part_number=<ledger PN>}`. When the same distributor
   appears in multiple ledger rows, keep the most recent row's PN.

**Dedup rule:** one entry per distributor. When a distributor appears in both
sets, prefer the **record PN** (set 1) over the ledger PN; this is the PN the
row will fetch with.

`part_key` is resolved to the inventory `part_id` via the existing
`resolve_part_key(conn, key)` helper before matching.

Rationale for the union: the current inventory record only keeps the
last-written PN per distributor column (last-write-wins in `cache_db`). Scanning
the ledger recovers distributors a part was genuinely purchased from even when
the record no longer shows that PN — which is exactly the "bought from both LCSC
and DigiKey" case. Every entry carries a usable PN (record or ledger), so every
row is live-fetchable; there are no un-fetchable rows.

The existing `fetch_lcsc_product` / `fetch_digikey_product` /
`fetch_mouser_product` / `fetch_pololu_product`, `record_fetched_prices`, and
`get_price_summary` are reused unchanged.

### 2. Frontend — multi-row fetch panel

`createFetchController` in `js/inventory-modals.js` is rewritten from a
single-supplier control (supplier dropdown + one fetch button + flat tier chip
list) into a **multi-row panel**, one row per sourced distributor:

```
LCSC      [qty: 30 ]  C429942      $0.2856   (×30 = $8.57)    ● selected
Digikey   [qty: 30 ]  DF40C-30DP   $0.4100   (×30 = $12.30)
```

Each row shows: distributor label, an editable **quantity input**, the fetched
PN, the chosen-tier **unit price** at that quantity, and the **extended price**
(unit × qty). The controller is shared by both the Adjust modal and the Price
modal (both existing entry points kept). `index.html` markup for the old
`#adj-fetch-supplier` / `#adj-fetch-price` / `#adj-fetch-tiers` controls (and the
Price-modal equivalents) is replaced by a single per-row container the controller
renders into.

### 3. Fetch & selection behavior

- **On open** (`configure(part)`):
  1. Call `get_sourced_distributors(pk)` to get the rows.
  2. Render a row per distributor; default each row's quantity to
     `get_last_po_quantity(pk)` (falling back to the part's current qty / 1).
  3. **Auto-fetch all rows concurrently.** Each row resolves independently:
     - On success: render its price-break tiers, run `pickTier(prices, rowQty)`,
       show that tier's unit + extended price. Fire-and-forget
       `record_fetched_prices(pk, distributor, prices)` (as today).
     - On failure/timeout: show an inline per-row error and fall back to the
       last-known unit price from `get_price_summary(pk)[distributor]` if
       present; otherwise the row shows "unavailable". Other rows are unaffected.

- **Apply — auto-pick cheapest:** once fetches settle, the modal's single
  **Unit Price** auto-fills from the **cheapest** distributor at its quantity,
  and that row is highlighted as selected. This is the default selection.

- **Manual override:** clicking a row pins it — sets Unit Price to that row's
  current price, highlights it, and disables further cheapest auto-pick for this
  modal session.

- **Quantity change:** editing a row's quantity re-runs `pickTier` for that row
  and updates its unit/extended price. If no row is pinned, the cheapest
  selection re-evaluates and Unit Price updates; if a row is pinned, Unit Price
  follows the pinned row.

Unit Price still flows into the adjustment/price save exactly as today (via the
existing `linkPriceInputs` ext-price recompute).

### 4. Pure-function extraction (for testability)

Extract two pure helpers (alongside the existing exported `pickTier`):

- `rowPrice(prices, qty)` — `pickTier` + returns `{unitPrice, extPrice}`.
- `cheapestRow(rows)` — given rows with resolved unit prices, return the index of
  the cheapest (ties → lowest index); ignores rows with no price.

These are unit-tested without DOM.

## Data flow

```
openAdjustModal / openPriceModal
        │
        ▼
configure(part)
        │  api("get_sourced_distributors", pk)  ──► [{distributor, part_number}, ...]
        │  api("get_last_po_quantity", pk)       ──► default row qty
        ▼
render rows  ──►  auto-fetch all (concurrent):
                    api(fetch_<dist>_product, pn) per row
                    │ success → pickTier → row price; record_fetched_prices(...)
                    │ failure → get_price_summary fallback / "unavailable"
        ▼
cheapestRow(rows) → set modal Unit Price (unless a row is pinned)
        ▼
qty edit → rowPrice(prices, qty) → re-eval cheapest (if unpinned)
row click → pin → set Unit Price
```

## Error handling

- Per-row fetch failures are isolated (independent promises); one distributor
  failing never blocks the others or the modal.
- Failed rows fall back to cached `get_price_summary` price, else mark
  "unavailable" and are excluded from the cheapest calculation.
- Backend: `get_sourced_distributors` returns `[]` for an unknown/unresolvable
  part_key (logged via `AppLog`/logger, not silently). Per repo error policy,
  prefer warnings over silent catches.

## Testing

- **Python** (`tests/python/`): `get_sourced_distributors` — has-PN only,
  purchased-only (record PN absent), union/dedup with record-PN preference,
  most-recent-ledger-PN recovery, no-match → `[]`, unresolvable key → `[]`.
- **JS unit** (`tests/js/`): extend `pick-tier.test.js`; new tests for
  `rowPrice` and `cheapestRow` (cheapest pick, ties, rows-without-price ignored).
- **E2E** (`tests/js/e2e/adjust-fetch-price.spec.mjs`, rewritten): multi-row
  panel renders one row per sourced distributor; auto-fetch on open; cheapest
  auto-selected and fed to Unit Price; per-row qty change updates that row's
  price and re-selects cheapest; manual row click pins and overrides; a
  one-distributor-fails-others-survive case. Realistic interactions only, mocked
  distributor APIs (no `dispatchEvent`/`force:true`), per project policy. Extend
  the shared mock harness (`helpers.mjs`) with `get_sourced_distributors`.
- Regenerate fixtures (`python scripts/generate-test-fixtures.py`) and code-map;
  run `bash scripts/verify.sh` before PR.

## YAGNI / non-goals

- No new purchase-ledger writes; no multi-distributor "buy" form.
- No inventory-grid or group-flyout per-distributor display.
- No persisted per-distributor quantities (session-only in the modal).
- No change to the inventory record shape (`domain/schema.py`) — this feature
  adds a query method, not a stored field.

## Risk note

Auto-fetch-all-on-open fires N live scrapes (incl. DigiKey CDP / Mouser API)
every time a multi-distributor part's modal opens — slower open and more quota
use. Mitigated by independent per-row fetches + cached fallback. If latency
becomes a problem, switching to "seed cached + per-row fetch on demand" is a
localized behavioral change in `createFetchController` and does not affect the
backend or data model.
