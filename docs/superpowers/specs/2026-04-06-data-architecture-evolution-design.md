# Data Architecture Evolution — Part/Stock Divorce, Generic Parts, Feeders

**Date:** 2026-04-06
**Approach:** Layered Evolution (3 phases, each independently useful)
**Goal:** Evolve the flat-file event-sourced data model into a richer domain model with part/stock separation, generic parts, price history, and feeder tracking — while preserving the append-only CSV event logs as the single source of truth.
**Storage strategy:** CSV event logs (source of truth, human-readable, git-friendly) + SQLite cache (derived, fast queries, not manually editable).

---

## Problem Statement

The current data model conflates part definitions with stock quantities (a single row in `purchase_ledger.csv` is both "this part exists" and "I bought N of them"). This makes it impossible to:

1. Track a part without stock (planning, reordering)
2. Group interchangeable parts (generic/meta-parts)
3. Track price history (edits overwrite in place)
4. Annotate stock location (feeder tracking)
5. Query inventory efficiently (full CSV scan on every read)

The full-rebuild-on-every-write pattern is correct and fast at current scale (~500 parts), but will degrade linearly as event history grows.

---

## Design Principles

1. **CSV event logs are the single source of truth.** The SQLite cache is derived and can be deleted and rebuilt at any time.
2. **Incremental cache updates for normal operations.** Full rebuild only on cold start, verification, or cache corruption.
3. **Spot-check verification after large operations.** PO imports and BOM consumptions trigger a targeted consistency check on affected parts.
4. **Startup catch-up, not full rebuild.** Cache stores a checkpoint; on startup, only events after the checkpoint are replayed.
5. **Zero JS frontend changes in Phase 1.** The API surface is identical; only the internal implementation changes.

---

## Phase 1: Part/Stock Divorce + SQLite Cache

### Cache Schema

**`cache_meta` table:**
```sql
CREATE TABLE cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Keys: 'purchase_ledger_lines', 'adjustments_lines', 'checksum', 'schema_version'
```

**`parts` table:**
```sql
CREATE TABLE parts (
    part_id       TEXT PRIMARY KEY,  -- best key: LCSC > MPN > Digikey > Pololu > Mouser
    lcsc          TEXT,
    mpn           TEXT,
    digikey       TEXT,
    pololu        TEXT,
    mouser        TEXT,
    manufacturer  TEXT,
    description   TEXT,
    package       TEXT,
    rohs          TEXT,
    section       TEXT,              -- computed by categorize.py
    sort_key      REAL,              -- extracted component value for within-section sorting (ohms, farads, henries; NULL for non-passive parts)
    date_code     TEXT
);
```

**`stock` table:**
```sql
CREATE TABLE stock (
    part_id     TEXT PRIMARY KEY REFERENCES parts(part_id),
    quantity    INTEGER NOT NULL DEFAULT 0,
    unit_price  REAL NOT NULL DEFAULT 0.0,
    ext_price   REAL NOT NULL DEFAULT 0.0
);
```

### Event Logs (unchanged)

- `data/purchase_ledger.csv` — same columns, same append-only behavior
- `data/adjustments.csv` — same columns, same append-only behavior
- `data/inventory.csv` — **no longer generated**; replaced by SQLite cache

### Cache Lifecycle

**Full population (cold start / verify):**
1. `read_and_merge(purchase_ledger.csv)` -> populate `parts` + `stock`
2. `apply_adjustments(adjustments.csv)` -> update `stock.quantity`
3. `categorize_and_sort()` -> update `parts.section` + `parts.sort_key`
4. Write checkpoint to `cache_meta`

**Incremental update (normal operation):**
1. Append event to CSV log (same as today)
2. Apply delta to cache:
   - New part: `INSERT INTO parts` + `INSERT INTO stock`
   - Quantity change: `UPDATE stock SET quantity = quantity + delta`
   - Price change: `UPDATE stock SET unit_price = ..., ext_price = ...`
3. Update checkpoint in `cache_meta`

**Verification (after PO import or BOM consumption):**
1. For each affected `part_id`, replay all events from the CSV logs for just that part
2. Compare replayed quantity against cache value
3. If mismatch: log warning, rebuild affected parts from scratch

**Startup catch-up:**
1. Read checkpoint from `cache_meta` (last known line counts)
2. If CSV files have more lines than checkpoint: replay only new lines
3. If line counts match: cache is current, use directly
4. If `cache.db` missing: full population

### API Changes (internal only)

| Method | Before | After |
|--------|--------|-------|
| `rebuild()` | merge CSV -> write `inventory.csv` | merge CSV -> populate/verify cache.db |
| `_load_organized()` | parse `inventory.csv` | `SELECT * FROM parts JOIN stock USING (part_id) ORDER BY section, sort_key` |
| `adjust_part()` | append CSV + full rebuild | append CSV + `UPDATE stock` + update checkpoint |
| `import_purchases()` | append CSV + full rebuild | append CSV + upsert parts/stock + update checkpoint |
| `consume_bom()` | append CSV + full rebuild | append CSV + batch `UPDATE stock` + update checkpoint |

**JS frontend: zero changes.** Return format is identical.

### Migration

No data migration needed. On first run after the code change:
1. `cache.db` doesn't exist
2. Full population from existing CSVs
3. `inventory.csv` is left on disk but no longer read or written (can be deleted manually)

---

## Phase 2: Price History + Generic Parts

### New Event Logs

**`data/events/price_observations.csv`** (new, append-only):
```
timestamp, part_id, distributor, unit_price, currency, source, moq, note
```
- `distributor`: `lcsc`, `digikey`, `mouser`, `pololu`, `manufacturer_direct`
- `source`: `import` (from PO), `manual`, `live_fetch`, `manufacturer_direct`
- `moq`: minimum order quantity (relevant for manufacturer-direct quotes)
- Populated on: PO import (prices extracted), manual price entry, DigiKey/LCSC client fetch

**`data/events/part_events.csv`** (new, append-only):
```
timestamp, event_type, part_id, generic_part_id, data_json
```
- `event_type`: `create_generic`, `update_generic_spec`, `add_member`, `remove_member`, `set_preferred`
- `data_json`: JSON payload with event-specific data

### New Cache Tables

**`generic_parts`:**
```sql
CREATE TABLE generic_parts (
    generic_part_id  TEXT PRIMARY KEY,
    name             TEXT NOT NULL,          -- e.g. "100nF 0402 MLCC"
    part_type        TEXT NOT NULL,          -- capacitor, resistor, inductor, ic, etc.
    spec_json        TEXT NOT NULL,          -- {"value":"100nF","package":"0402","voltage_min":5}
    strictness_json  TEXT NOT NULL           -- which spec fields are required for matching
);
```

**`generic_part_members`:**
```sql
CREATE TABLE generic_part_members (
    generic_part_id  TEXT NOT NULL REFERENCES generic_parts(generic_part_id),
    part_id          TEXT NOT NULL REFERENCES parts(part_id),
    source           TEXT NOT NULL DEFAULT 'auto',  -- 'auto' or 'manual'
    preferred        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (generic_part_id, part_id)
);
```

**`prices`:**
```sql
CREATE TABLE prices (
    part_id            TEXT NOT NULL REFERENCES parts(part_id),
    distributor        TEXT NOT NULL,
    latest_unit_price  REAL,
    avg_unit_price     REAL,
    price_count        INTEGER NOT NULL DEFAULT 0,
    last_observed      TEXT,
    moq                INTEGER,
    source             TEXT,
    PRIMARY KEY (part_id, distributor)
);
```

### Auto-matching Logic

When a generic part is created or updated, or when a new real part is imported:
1. Parse `spec_json` + `strictness_json` to determine required fields
2. Query `parts` table for candidates matching required spec fields
3. Insert matches with `source='auto'`
4. Manual overrides (`source='manual'`) are never removed by auto-matching
5. Re-run matching on PO import (new parts may match existing generic specs)

### Spec Strictness

A generic part's spec defines what fields exist. Its strictness defines which are required for matching:

```json
// spec_json
{"value": "100nF", "package": "0402", "dielectric": "C0G", "voltage_min": 5, "tolerance": "10%"}

// strictness_json — only value and package are required
{"required": ["value", "package"], "optional": ["dielectric", "voltage_min", "tolerance"]}
```

For a relaxed prototype BOM: `required: ["value", "package"]`
For a precision analog board: `required: ["value", "package", "dielectric", "tolerance", "voltage_min"]`

### Popularity Score

For ranking real parts within a generic group:
```
score = (w_purchased * normalized_total_purchased)
      + (w_stock * normalized_current_stock)
      + (w_recency * recency_bonus)
```
Default weights: `w_purchased=0.5, w_stock=0.3, w_recency=0.2`
Configurable in `preferences.json` under `popularity_weights`.
Preferred parts are surfaced separately regardless of score.

### BOM Resolution with Generic Parts

1. Parse BOM row → extract component spec (value, package, type)
2. Match against generic parts by spec
3. If matched: rank member real parts by preferred → popularity → stock availability
4. Present top candidate to user, allow manual selection
5. Consumption happens against the selected real part's stock
6. If no generic match: fall back to current matching logic (exact key, fuzzy MPN, etc.)

### Price History on PO Import

When `import_purchases()` runs, for each imported row:
1. Append to `purchase_ledger.csv` (existing behavior)
2. Also append to `events/price_observations.csv` with `source='import'`
3. Update `prices` cache table (latest price, recalculate average)

This means Phase 2 retroactively captures price data from imports going forward. Historical prices from before Phase 2 are not backfilled (the data wasn't captured).

### JLCPCB Basic Part Status

Stored as a field on the `parts` table:
```sql
ALTER TABLE parts ADD COLUMN jlcpcb_basic INTEGER DEFAULT NULL;
-- NULL = unknown, 1 = basic, 0 = not basic
```
Populated by the LCSC client when fetching part data. Surfaced in the UI as a badge on parts and as a filter on generic part member lists.

---

## Phase 3: Feeder Tracking + OpenPnP Integration

### New Cache Table

**`feeders`:**
```sql
CREATE TABLE feeders (
    feeder_id   TEXT PRIMARY KEY,   -- user-assigned name ("Feeder-12", "Left-Bank-3")
    part_id     TEXT REFERENCES parts(part_id),
    loaded_qty  INTEGER NOT NULL DEFAULT 0,
    loaded_at   TEXT,               -- ISO timestamp
    note        TEXT
);
```

### Adjustment Types (extended)

New adjustment types in `adjustments.csv`:
- `feeder_load` — units allocated to a feeder (qty is positive, indicates amount loaded)
- `feeder_unload` — units returned from a feeder
- `feeder_consume` — units consumed from a specific feeder (replaces plain `remove` for PnP operations)

New column added to `adjustments.csv`: `feeder_id` (empty for non-feeder operations, backward compatible).

### Feeder Operations

**Load feeder** (user loads tape/reel into feeder):
- `stock.quantity` unchanged (total inventory stays the same)
- `INSERT/UPDATE feeders SET loaded_qty = N, part_id = ..., loaded_at = now()`
- Append `feeder_load` to `adjustments.csv`

**Unload feeder** (user removes tape from feeder):
- `DELETE FROM feeders WHERE feeder_id = ...` (or set `loaded_qty = 0`)
- Append `feeder_unload` to `adjustments.csv`

**Consume from feeder** (OpenPnP places a part):
- `UPDATE stock SET quantity = quantity - 1`
- `UPDATE feeders SET loaded_qty = loaded_qty - 1`
- If `loaded_qty` hits 0: flag for operator attention (low-feeder alert)
- Append `feeder_consume` to `adjustments.csv` with `feeder_id`

**Stock invariant:** `stock.quantity` always reflects total parts owned. `feeders.loaded_qty` is a subset annotation — sum of all feeder `loaded_qty` for a part should be <= `stock.quantity`. Verification checks this invariant; violations are logged as warnings (can happen if stock is manually adjusted without updating feeders).

### PnP Server API Extensions

```
GET  /api/feeders                → list all feeders with loaded parts and quantities
POST /api/feeders                → load a feeder: { feeder_id, part_id, qty }
DELETE /api/feeders/:feeder_id   → unload a feeder

POST /api/consume                → existing endpoint, extended:
     { "part_id": "C1525", "qty": 1, "feeder_id": "Feeder-12" }
     feeder_id is optional; if omitted, behaves as today (general stock removal)
```

### ESP32 / AprilTag / Smart Rack Groundwork

Phase 3 does not implement hardware integration, but the data model and API support it:

- `feeder_id` is a stable string identifier that maps 1:1 to a physical tag (AprilTag/ArUco/QR)
- The REST API (`/api/feeders`) is the same interface an ESP32 controller would call
- A powered storage rack polls `GET /api/feeders` to display current state, and pushes updates via `POST /api/feeders` when a feeder is physically inserted/removed
- Future fields on `feeders` table: `tag_type`, `tag_value`, `last_seen`, `hardware_status`

---

## Known Technical Debt (Planned)

| Debt | Introduced in | Resolution |
|------|--------------|------------|
| `purchase_ledger.csv` conflates part + stock | Phase 1 (legacy) | Optional cleanup phase: split into `events/purchases.csv` |
| Phase 1 extraction logic (CSV → parts + stock) | Phase 1 | Becomes simpler if purchase ledger is reformed |
| Mixed event log naming (`adjustments.csv` vs `events/price_observations.csv`) | Phase 2 | Optional: move all to `events/` directory |
| `stock.unit_price`/`ext_price` in Phase 1 vs `prices` table in Phase 2 | Phase 1→2 | Phase 2 makes `prices` authoritative; Phase 1's stock prices become convenience copies |
| Popularity weights hardcoded defaults | Phase 2 | Already configurable in preferences.json |

---

## File Layout After All Phases

```
data/
  README.txt                    -- "do not manually edit; use the app"
  purchase_ledger.csv           -- source of truth: purchases (legacy format)
  adjustments.csv               -- source of truth: stock mutations (extended with feeder_id)
  cache.db                      -- SQLite derived cache (deletable, rebuildable)
  preferences.json              -- user configuration
  constants.json                -- shared schema
  events/
    price_observations.csv      -- source of truth: price history (Phase 2)
    part_events.csv             -- source of truth: generic part lifecycle (Phase 2)
```

---

## What Does NOT Change

- `constants.json` format
- `preferences.json` format (extended with new keys, backward compatible)
- JS frontend API surface (Phase 1 is backend-only)
- `categorize.py` logic
- Python public API method signatures (internal implementation changes)
- Existing test suites (all must continue passing)
- PnP server `/api/health` and `/api/parts` endpoints
- Append-only contract on event log CSVs
