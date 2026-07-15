# Fetch descriptions for distributor-matched parts + close categorization gaps

**Date:** 2026-07-15
**Status:** Approved (design)
**Branch:** `claude/feature-fetch-descriptions`

## Problem

Parts added via OCR often land in inventory with a distributor part number matched
but **no description**. Two consequences:

1. The part shows blank in the inventory grid.
2. `categorize.py` matches categories by keyword substring against
   **Description / MPN / Manufacturer**. With an empty description, most parts fall
   through to the default `"Other"` bucket.

On the current live data (`data/cache.db`): **13 parts** have a distributor PN and no
description; **8 parts** sit in `"Other"` — and all 8 are among those 13. So the two
problems are the same problem: fetch the descriptions and most "Other" parts
auto-recategorize.

## Goals

- Let the user fetch a missing description for a single part from the Adjust modal.
- Let the user bulk-fetch descriptions for every part that has a distributor PN and no
  description, in one action.
- After descriptions are populated, ensure parts land in a real category — extend the
  categorizer rules (and add a new category for dev tools/boards/programmers) so the
  `"Other"` bucket contains only genuinely-miscellaneous parts.

## Non-goals

- No change to how OCR import matches parts to distributor PNs.
- No new distributor client or scraping logic — reuse the existing
  `fetch_*_product` chain, which already returns `description` for all four
  distributors (LCSC, DigiKey, Mouser, Pololu).
- No re-architecture of `categorize.py` (keep the keyword-rule list; just add rules).

## Background: what already exists

- **Adjust modal** lives in `js/inventory-modals.js`. It builds a per-distributor
  fetch panel via `createFetchController(...)` which, on modal open, auto-fetches the
  full product dict for every sourced distributor (`fetchRow`, ~line 320) — but
  **consumes only `product.prices` and discards `product.description`**.
- **Description write path:** `api("update_part_fields", pk, { description: "..." })`
  → `domain/inventory.py:update_part_fields` writes the `Description` column in
  `purchase_ledger.csv`, then rebuilds. The Adjust modal already uses this call for
  metadata edits and for correcting distributor PNs.
- **Distributor fetch:** `fetch_lcsc_product` / `fetch_digikey_product` /
  `fetch_pololu_product` / `fetch_mouser_product` on the pywebview bridge
  (`inventory_api.py` → `domain/api_distributor.py` → `distributor_manager.py` →
  `base_client.fetch_product`). Every client's normalized dict includes a
  `description` field.
- **Categorizer:** `categorize.py` — `CATEGORY_RULES` (list, first-match-wins,
  substring keywords on `desc`/`mpn`/`mfr`, `exclude_desc` veto) + `SUBCATEGORY_RULES`.
  Default `"Other"`. Runs inside `inventory_ops.rebuild()`. Section order in
  `data/constants.json` → `SECTION_ORDER`.
- **Command palette:** `js/app-init.js` builds a `cmds` array; Global group already has
  "Rebuild Inventory", "Manage Vendors", etc. New bulk action fits here.

## Design

### Part A — Per-part "Fetch description" button (Adjust modal)

1. In `createFetchController`'s `fetchRow` (`js/inventory-modals.js`), stash the fetched
   description on the row: `r.description = product.description` (right where it already
   reads `product.prices`). No new network call — the data is already fetched and thrown
   away today. Add `description` to the row typedef.
2. Expose a controller method `bestDescription()` that returns the description from the
   preferred row — pinned row if set, else cheapest, else first sourced row — that has a
   non-empty, non-`nan` description; `""` if none.
3. Add a **"Fetch description"** button in the Adjust modal next to the Description
   input. On click it calls `adjFetch.bestDescription()` and, if non-empty, sets the
   Description `<input>` value (fires an `input` event so `getChangedFields()` picks it
   up). It does **not** save — the user reviews and clicks **Apply**.
4. Button state:
   - Hidden/disabled when the part has no distributor PN (reuse the same signal the
     panel uses to decide it has sourced rows).
   - If fetches are still pending or every fetched description is empty, toast a clear
     message ("No description available from <distributors>").

Interface: the button reads already-fetched panel state; its only dependency is the
fetch controller. No backend change for Part A.

### Part B — Bulk "Fetch Missing Descriptions" (command palette)

1. **Backend:** new method `fetch_missing_descriptions()` exposed on the bridge
   (`inventory_api.py`), delegating to a domain function (in `domain/api_inventory.py` /
   `domain/inventory.py`). Behavior:
   - Read the purchase ledger. For each part with **≥1 distributor PN and an empty/`nan`
     description**, fetch the product from that part's distributor(s) (first distributor
     that returns a non-empty description wins).
   - Collect all `{part_key: description}` results, write them to the `Description`
     column in a **single** atomic pass, then **rebuild once**. (Avoids one round-trip +
     one rebuild per part.)
   - Return `{ inventory: [...fresh...], summary: { updated, failed, skipped } }` (or
     fresh inventory + a summary the JS can toast).
   - Per-part fetch failures are caught and counted in `failed`, not fatal — one dead
     distributor lookup must not abort the batch. Log each failure via `AppLog`-equivalent
     (Python logger).
2. **Frontend:** new command **"Fetch Missing Descriptions"** in the command palette
   Global group (`js/app-init.js`), next to "Rebuild Inventory". It calls the method,
   passes the fresh inventory to `onInventoryUpdated(...)`, and toasts the summary
   (e.g. "Fetched 11 descriptions, 2 failed").
3. Because the single rebuild re-runs `categorize()`, parts that gain a description
   **auto-recategorize** out of `"Other"` in the same operation — Part B and Part C
   compose.

### Part C — Close categorization gaps

After Part B runs against live data, tune `categorize.py` so the `"Other"` bucket holds
only genuinely-miscellaneous parts:

1. Add a new category **"Development Boards, Kits, Programmers"** for dev tools / eval
   boards / debug probes (e.g. `STLINK-V3SET`). Add it to `CATEGORY_RULES` with
   description/MPN keywords (`programmer`, `debugger`, `st-link`/`stlink`, `j-link`,
   `eval board`, `dev board`, `development kit`, `starter kit`, …) and add the section
   name to `data/constants.json` → `SECTION_ORDER` (before `"Other"`).
2. For the remaining current `"Other"` parts, add/extend rules so each lands correctly
   once described. Expected mappings (verify against actual fetched descriptions during
   implementation):
   - `GRM21BR61A476ME15L` (Murata MLCC) → Passives - Capacitors
   - `WS2812B-V5/W` (addressable LED) → LEDs
   - `UJ40-C-V-G-SMT-TR`, `12401951F412A` (USB-C connectors) → Connectors
   - `WSD4066DN33` (MOSFET) → Discrete Semiconductors
   - `PI3CH3257ZTAEX` (bus switch/mux) → ICs - Interface (or Discrete/Analog switch)
   - `TCPP02-M18` (USB-C port protection) → ICs - USB or ICs - ESD Protection
   - `STLINK-V3SET` → Development Boards, Kits, Programmers
3. Prefer **description-based** keywords over MPN-specific rules where the fetched
   description supports it (more general). Fall back to MPN keywords only for parts whose
   description is ambiguous.
4. Keep first-match-wins ordering correct — place the new tools rule where it won't
   shadow component rules (e.g. an ST-Link's description shouldn't match "led").

## Data flow (Part B)

```
Command "Fetch Missing Descriptions"
  → api("fetch_missing_descriptions")
      → domain: read purchase_ledger.csv
        for each part missing description with a distributor PN:
            fetch_*_product(pn)  →  product.description
        write all Description cells (one atomic pass)
        rebuild()  →  categorize() re-runs  →  cache.db repopulated
      → returns { inventory, summary }
  → onInventoryUpdated(inventory); showToast(summary)
```

## Error handling

- Per-part fetch failure (network / scraper / not found): caught, counted in `failed`,
  batch continues. (Follows project error policy: log, don't silently swallow, don't
  abort the whole batch on one bad lookup.)
- Per-part button with no distributor PN: button hidden/disabled (no error path).
- Per-part button with all-empty fetched descriptions: explicit toast, no silent no-op.
- `update_part_fields`/write failure: surfaces via existing `api()` toast + logging.

## Testing

- **Vitest:** per-part button fills the Description input from a stubbed fetched
  description; button disabled when the part has no distributor PN; toast when no
  description available. Regenerate fixtures if backend record shape changes (it does
  not here).
- **Python (`pytest`):** `fetch_missing_descriptions` with a mocked distributor client —
  asserts it writes descriptions for the right parts, skips parts that already have one,
  counts failures, and rebuilds exactly once. Add categorization cases to
  `tests/python/test_inventory_api_categorize.py` for each newly-covered part and the new
  "Development Boards, Kits, Programmers" category (including a `test` that STLINK does
  not mis-match "led"/other rules).
- **Playwright E2E:** command-palette "Fetch Missing Descriptions" happy path (realistic
  interaction, no force/dispatchEvent) and the Adjust-modal per-part button filling the
  Description field.
- Run `bash scripts/verify.sh` before PR (all staleness guards + ruff/pytest/eslint/tsc/
  vitest).

## Open questions / risks

- **DigiKey fetches need the CDP/WebView cookie flow**, which may not run in a headless
  dev/test context. Many missing-description parts have DigiKey PNs. Mitigation: bulk
  method degrades gracefully (counts those as `failed`); the user runs it in the real
  app where DigiKey scraping works. Categorization rules in Part C are chosen to work
  from the (known) real descriptions, and tests use fixed description strings, so Part C
  is not blocked by live DigiKey access during development.
```
