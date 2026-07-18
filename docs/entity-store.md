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
| KiCad category/part mapping | `data/kicad_mapping.json` | `domain/kicad_mapping.load_into_db` |

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
