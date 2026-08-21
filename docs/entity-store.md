# Entity-store convention

Every first-class user-created entity in dubIS follows one persistence
pattern. SQLite (`cache.db`) is a deletable materialized view — an entity
stored only in SQLite WILL be lost on cache deletion or schema bump.

The pattern (reference implementations: `saved_searches.py`,
`domain/generic_parts.py` `_persist`/`load_into_db`):

1. **Durable file** in `data/` (JSON for structured records, CSV for
   append-only logs) written with `csv_io.atomic_write_text` /
   `csv_io.atomic_write_rows` after every mutation.
2. **`load_into_db(conn, data_dir)`** — idempotent restore into SQLite,
   called from `domain/inventory.py:rebuild()`.
3. **SQLite table** in `cache_db.create_schema` — derived cache only. It may
   be dropped on `SCHEMA_VERSION` bumps precisely because of rule 1+2.
4. **Schema entry** in `domain/schema.py` if the entity's fields reach the
   frontend (then regenerate types: `python scripts/gen-inventory-types.py`).

Existing entities and their durable stores:

| Entity | Durable store | Restored by |
|---|---|---|
| Purchase history | `data/purchase_ledger.csv` | full rebuild (merge) |
| Adjustments | `data/adjustments.csv` | full rebuild / catch_up |
| Vendors | `data/vendors.json` | `populate_full` |
| Purchase orders | `data/purchase_orders.csv` | `populate_full` |
| Saved searches | `data/saved_searches.json` | `saved_searches.load_into_db` |
| Generic parts (manual state) | `data/generic_parts.json` | `domain/generic_parts.load_into_db` |
| Part identity registry | `data/part_registry.json` | loaded each rebuild (`domain/part_registry.py`) |
| Price observations | `events/price_observations.csv` | `populate_prices_cache` |
| Part attributes (distributor parametrics) | `data/part_attributes.csv` | `domain/attributes.load_into_db` |

Note the two shapes of durable log. `events/price_observations.csv` is
append-only because a price is a *sighting* (the history is the data). A
parametric attribute is a *property* of the part, so
`data/part_attributes.csv` is keyed on (part_id, canonical_name, distributor)
and re-fetching a part updates its rows in place — the file stays
one-row-per-fact instead of growing on every hover.

### Price-observation schema and its migration

`events/price_observations.csv` is append-only, and its header is
`domain.pricing.FIELDNAMES`. The five packaging columns (`packaging`,
`carrier`, `is_reel`, `reel_qty`, `reel_fee`) were appended after the original
eight, so a deployment's existing file has a *shorter* header than the code
expects. Two mechanisms keep that working:

- **Writes migrate.** `record_observations` goes through
  `csv_io.append_csv_rows`, which calls `csv_io.migrate_csv_header` first — the
  file is rewritten once with the full header (old rows getting empty packaging
  cells) and appended to thereafter. `domain.pricing.migrate_observations` does
  the same thing on demand, and is idempotent: a file already on the current
  header is left byte-identical.
- **Reads tolerate.** `read_observations`, `populate_prices_cache` and
  `cart_qty.tier_ladders` all read missing columns as `""`, so an unmigrated
  file loads correctly and reading never rewrites it.

`""` means **unknown**, and unknown is not a value: an empty `carrier` is not
`bulk` and an empty `is_reel` is not "not a reel". Nothing may default a
missing packaging to a real carrier — `domain/packaging.py`'s `carrier_of`
returns `None` for exactly this reason.

**Audit trails (never replayed):** `events/part_events.csv` records generic-part
mutations for forensics only. Do not build restore logic on it.

New entities (BOMs, boards, feeders, part maps) MUST follow this pattern —
copy `saved_searches.py`, not a SQLite-only design.

## Consistency and recovery

- The part registry underpins every other store's `part_id` references:
  `generic_parts.json` members, price observations, and (future) feeder
  assignments are keyed by canonical part ids that only stay stable while
  `data/part_registry.json` exists. Deleting the registry alone does not
  crash anything (self-heals to derived keys), but if the ledger has been
  enriched, canonical ids can change and other stores' references are
  warn-skipped (ghost-retained in JSON) until the registry is restored or
  the references re-created. Delete the registry only together with a
  willingness to re-curate.
- A `PartRegistryCollisionError` during rebuild (a ledger row whose PNs map
  to two different registered parts) fails loudly by design and names the
  offending PNs; recovery is to fix the ledger row or delete
  `data/part_registry.json` to re-derive identities from scratch.
