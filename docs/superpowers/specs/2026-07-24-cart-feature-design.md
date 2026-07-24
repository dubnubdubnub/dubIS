# Cart Feature — Design Spec

**Date:** 2026-07-24
**Status:** Approved (brainstorming complete)

## Summary

Add a shopping-cart feature to dubIS. Users build one or more persistent carts of
parts they intend to purchase, then export each cart as an LCSC or DigiKey part
list (CSV download or copy-to-clipboard paste format) that can be ordered on the
distributor's site. A cart button lives in the app header (left of the component
count / inventory worth), parts can be added via multiple interactions, and a cart
modal — styled like the inventory view — provides line-item editing and cart
management.

## Scope decisions (from brainstorming)

- **Export target:** CSV download (LCSC + DigiKey layouts) **and** copy-to-clipboard
  paste format (part# + qty per line). **Direct distributor API cart submission is
  explicitly OUT** — verified infeasible: LCSC exposes only a public product-detail
  read endpoint (no ordering API), and the DigiKey client is Cloudflare DOM-scraping
  with login cookies (no cart/order POST path). Building submission would be fragile
  browser automation, not a supported API.
- **Multiple carts:** exactly one **active** cart at a time; others idle. Add actions
  (link-to-cart, cart-add mode, add-BOM-missing) always target the active cart.
- **Cart identity:** user-named, shared globally. Name prefills with
  `"<YYYY-MM-DD> · <loaded BOM filename>"` (date only if no BOM loaded). The
  **active-cart pointer is per-user** (keyed by auth identity) so concurrent users
  don't clobber each other.
- **Cart item model:** stores a part *reference* + qty + optional target distributor.
  The distributor part number for ordering is **resolved at export time** (not frozen
  at add time). Missing-BOM parts not yet in inventory are stored in a `raw` form.
- **Default qty-to-purchase:** BOM shortfall (`need − on-hand`) if the add happens in
  a BOM-shortfall context, else `1`. Then apply cost-stepping rounding **when
  price-break tier data is available** for that part/distributor (reconstructed from
  `events/price_observations.csv`); fall back to the raw number when tier data is
  absent. Order-multiple ("Multiple") is not persisted, so treat it as 1.

## Cost-stepping qty rule (best-effort)

Given a base qty `N` (shortfall or 1) and, if available, the sorted price-break
ladder `[(brkQty, unitPrice), …]` for the part+distributor:

1. **Base = BOM shortfall** (`N = max(0, need − on_hand)`), else `N = 1` when there is
   no shortfall context.
2. If a tier ladder exists:
   - Let `step` = the smallest break qty `≥ N` ("nearest cost stepping").
   - If rounding to `step` would **more than double** `N` (`step > 2·N`), don't use it —
     instead **round `N` up to the nearest 10**.
   - When there is **no shortfall amount** (base was 1): default to the **lowest break
     qty** ("next lowest cost stepping"), unless that break's *extended* price
     (`brkQty · unitPrice`) **> \$30**, in which case default to `5`.
3. If no tier ladder exists: use `N` unchanged.

All values are user-editable in the cart modal; this only sets the initial qty.

## Architecture

### Backend — `carts` durable entity (mandated entity-store pattern)

Follows `saved_searches.py` / `docs/entity-store.md` exactly:

- **Source of truth:** `data/carts.json` — atomic-written (`csv_io.atomic_write_text`)
  after every mutation.
- **Per-user active pointer:** `data/cart_active.json` — a small map
  `{ identity: cart_id }` keyed by auth identity (`local`, `mcp@ci`, …). Atomic-written.
- **SQLite cache:** droppable `carts` + `cart_items` tables in `cache_db.create_schema`;
  `carts.load_into_db(conn, data_dir)` restores them idempotently (INSERT OR REPLACE),
  called from `domain/inventory.py:rebuild()`.
- **Domain module:** `carts.py` (new) mirroring `saved_searches.py`: `_json_path`,
  `_persist`, `_row_to_dict`, `create`, `list_carts`, `get`, `rename`, `delete`,
  `add_item`, `update_item`, `remove_item`, `clear`, `set_active`, `get_active`,
  `split_by_distributor`, `consolidate`, `load_into_db`.
- **Facade:** cart methods exposed on `InventoryApi` (`inventory_api.py`), surface
  frozen by `tests/python/test_api_surface.py` (update the frozen surface).

**Cart record:**
```json
{
  "id": "cart_<uuid>",
  "name": "2026-07-24 · reflow_v3.csv",
  "created_at": "2026-07-24T…",
  "items": [
    { "part_id": "C15742", "qty": 5, "target_distributor": "lcsc" },
    { "raw": { "mpn": "…", "description": "…", "footprint": "…" }, "qty": 10,
      "target_distributor": null }
  ]
}
```
`part_id` uses the canonical inventory part id (see entity-store part_id stability
caveat). Items carry EITHER `part_id` OR `raw`, never both. Item identity within a
cart is the `part_id` or a hash of the `raw` fields (the item "ref").

### /v1 API — `server/routes/carts.py`

New `APIRouter(prefix="/v1")`, registered in `server/app.py`, handlers call
`request.app.state.api.<cart_method>(...)`. Request bodies as inline Pydantic models.
Cart mutations are independent of inventory, so they return plain dicts (not
`finish_mutation`) and publish a **new `carts.updated` SSE event** instead of
`inventory.updated`.

| Method & path | Purpose |
|---|---|
| `GET /v1/carts` | list carts (+ which is active for the caller) |
| `POST /v1/carts` | create cart (optional name; server prefill if absent) |
| `GET /v1/carts/{id}` | get one cart (resolved line details for display) |
| `PUT /v1/carts/{id}` | rename cart |
| `DELETE /v1/carts/{id}` | delete cart |
| `POST /v1/carts/{id}/active` | set caller's active cart |
| `POST /v1/carts/{id}/items` | add item (part_id or raw; qty; distributor). Server computes default qty if omitted. |
| `PATCH /v1/carts/{id}/items/{ref}` | update qty / target distributor |
| `DELETE /v1/carts/{id}/items/{ref}` | remove line |
| `POST /v1/carts/{id}/clear` | clear all lines |
| `POST /v1/carts/{id}/add-bom-missing` | add all missing/short members of the loaded BOM (server receives the missing set from the client) |
| `POST /v1/carts/{id}/split` | fish out one distributor's lines into a NEW cart; body `{distributor, remove_from_source: bool}` |
| `POST /v1/carts/{id}/consolidate` | force all multi-distributor lines to a single `{distributor}` |
| `GET /v1/carts/{id}/export?distributor=lcsc\|digikey&format=csv\|paste` | export; returns CSV text or paste text + list of unresolved lines |

**SSE contract:** the `carts.updated` event type is added to `server/events.py`'s
known types and to the SSE type↔handler exhaustiveness guard; the frontend handler
in `js/sse.js` triggers a debounced carts re-fetch (parallel to the inventory refresh
path, not coupled to it).

### Frontend

- **Header button + badge:** cart `<button>` with item-count badge inserted as the
  first child of `<div class="header-right">` (`index.html`), left of `#inv-count`.
  A small **cart-add-mode toggle** sits beside it (turns purple when active).
- **State:** `store.js` gains `carts`, `activeCartId`, and setters; cross-panel
  propagation via a new `cartsSignal` (`js/signals.js`) per the signals-for-state
  rule. A new `CART_CHANGED` EventBus event is added ONLY if a discrete UI event is
  needed; state sync prefers the signal. (Whichever is used is registered in the
  EventBus completeness guard.)
- **Add-to-cart interactions:**
  1. **Linking mode extended** — the existing Link flow (`setLinkingMode`) that
     highlights eligible targets with a purple dotted box now ALSO decorates the header
     cart icon as a valid target. Clicking the cart while a part is armed adds that part
     to the active cart (and exits/continues linking per existing UX).
  2. **Cart-add mode** — the toggle beside the cart icon; while active, clicking any
     inventory row adds it to the active cart. Mirrors label-select / linking mode
     structure.
  3. **BOM panel button** — "Add all missing to cart" in the BOM panel toolbar (only
     enabled when a BOM is loaded); sends the linkable/missing set
     (`buildLinkableKeys` output: `missing`, `possible`, `*-short`) to
     `POST /v1/carts/{id}/add-bom-missing`.
- **Cart modal:** built with the `DataGrid` component (`js/components/data-grid.js`),
  styled to resemble the inventory view. A **top button bar** provides:
  - inline **qty-to-purchase** edit (stepper / `onCellEdit`)
  - **delete line** (row action) + **clear cart**
  - **per-line target distributor** picker (from the part's sourced distributors)
  - **cart management:** rename active, create new, switch active, delete cart
  - **split by distributor → new cart** (with a "remove from this cart" toggle)
  - **consolidate:** force all multi-distributor parts to one distributor
  - **export:** LCSC CSV, DigiKey CSV, copy paste-format (with unresolved-line warning)

### Export logic (backend)

For each cart line, resolve the distributor PN via `get_sourced_distributors` (record
PN preferred, ledger PN fallback) for the chosen distributor:
- **LCSC CSV columns:** `Index,LCSC#,MPN,Manufacturer,Package,Customer #,Description,RoHS,Quantity,MOQ,Multiple,Unit Price($),Extended Price($),Product Link`
- **DigiKey CSV columns:** `Index,DigiKey Part #,Manufacturer Part Number,Manufacturer,Description,Customer Reference,Quantity,Backorder,Unit Price,Extended Price`
- **Paste format:** `<part#>\t<qty>` per line (distributor quick-paste tools).

`MOQ`/`Multiple`/prices are filled from cached price summary when present, else left
at MOQ=1/Multiple=1/blank price. Lines with **no PN for the chosen distributor** are
returned in an `unresolved` array (not written to the CSV body) so the UI can warn.

## Data flow

```
cart mutation (POST/PATCH/DELETE /v1/carts/…)
        │
        ▼
carts.py domain  ──► data/carts.json (atomic write, source of truth)
        │           data/cart_active.json (per-user active pointer)
        ▼
cache_db (carts / cart_items tables, droppable) via load_into_db on rebuild
        │
        ▼
events.publish("carts.updated")  ──SSE──►  js/sse.js  ──►  cartsSignal
        │                                                     │
        └── GET /v1/carts refetch ◄───────────────────────────┘
```

## Testing

**Python (`tests/python/`):**
- cart CRUD + `data/carts.json` round-trip; cache drop+rebuild restores carts
- per-user active pointer isolation (two identities)
- default-qty computation: shortfall path, no-shortfall path, tier-ladder rounding,
  >2× guard → round-to-10, >\$30 → 5, no-ladder fallback
- export: LCSC CSV columns, DigiKey CSV columns, paste format, unresolved lines
- split (with/without remove_from_source) and consolidate
- `test_api_surface.py` updated for the new facade methods
- exhaustiveness guards: `carts.updated` in SSE known-types + SSE type↔handler guard

**JS (`vitest`):**
- store cart state + setters, `cartsSignal` propagation
- default-qty helper (mirrors Python; fixtures regenerated via
  `scripts/generate-test-fixtures.py`)
- EventBus completeness guard if a `CART_CHANGED` event is added

**E2E (`Playwright`, required for new features — realistic interactions only):**
- add via cart-add mode (toggle → click rows → badge increments)
- add via linking mode (arm part → click cart target → item added)
- "Add all missing to cart" from a loaded BOM
- cart modal: edit qty, delete line, switch/rename/create cart, split, consolidate
- export: CSV download + copy paste-format

## Files touched (anticipated)

**New:** `carts.py`, `server/routes/carts.py`, `js/cart/` (modal, store glue,
add-mode, export UI), css for cart modal + header button, `data/carts.json` +
`data/cart_active.json` (created at runtime), tests.

**Modified:** `server/app.py` (register router), `server/events.py` (new SSE type),
`cache_db.py` (schema + tables), `domain/inventory.py` (call `load_into_db`),
`inventory_api.py` (facade methods), `index.html` (header button + toggle + BOM
button), `js/store.js` + `js/signals.js` (cart state), `js/sse.js` (handler),
`js/inventory/*` (cart-add mode + linking-mode cart target), `js/bom/*` (add-missing
button), exhaustiveness-guard test data, `test_api_surface.py`.
