# domain — Python domain layer: business logic extracted from inventory_api.py

## Owns

Pure business-logic functions for pricing, price history, inventory pipeline, and generic parts.
Does NOT own database connections, file paths, or CSV I/O setup — those are passed in as parameters.

## Used by

- `cache_db.py` — imports `parse_price`, `parse_qty` for cache population
- `file_dialogs.py` — imports `ensure_parsed` for JSON parsing
- `inventory_ops.py` — imports `derive_missing_price`, `parse_price`, `parse_qty`
- `inventory_api.py` — imports `record_fetched_prices`, `get_price_summary`, `populate_prices_cache`, `resolve_part_key`, `record_observations`
- `inventory_api.py` — imports `create_generic_part_api`, `update_generic_part_api`, `add_member_api`, `remove_member_api`, `set_preferred_api`, `exclude_member`, `list_generic_parts_with_member_specs`, `fetch_members`, `resolve_bom_spec`, `extract_spec_for_part`
- `domain.inventory` — imports `auto_generate_passive_groups` from `generic_parts`

## Public exports

- `pricing.py`: `parse_qty`, `parse_price`, `ensure_parsed`, `derive_missing_price` — scalar parsing helpers
- `pricing.py`: `record_observations`, `read_observations` — append/read price observation CSV
- `pricing.py`: `populate_prices_cache`, `resolve_part_key` — SQLite prices-table helpers
- `pricing.py`: `record_fetched_prices`, `get_price_summary` — distributor price fetch + summary
- `generic_parts.py`: `create_generic_part`, `create_generic_part_api` — create generic group with auto-matching
- `generic_parts.py`: `update_generic_part_api` — update spec and re-run auto-matching
- `generic_parts.py`: `add_member`, `add_member_api`, `remove_member`, `remove_member_api` — member management
- `generic_parts.py`: `exclude_member`, `set_preferred`, `set_preferred_api` — member state
- `generic_parts.py`: `preview_members`, `fetch_members` — query helpers
- `generic_parts.py`: `list_generic_parts_with_member_specs` — full listing with extracted specs
- `generic_parts.py`: `resolve_bom_spec` — BOM resolution to best real part
- `generic_parts.py`: `auto_generate_passive_groups` — scan passives and create auto groups
- `generic_parts.py`: `extract_spec_for_part` — extract component spec from cache

## Internal layout

- `__init__.py` — package marker (empty)
- `pricing.py` — all pricing logic: parse helpers, observation log, SQLite cache helpers
- `inventory.py` — inventory pipeline: rebuild, catch-up, adjust, import, consume
- `generic_parts.py` — generic parts CRUD, auto-matching, BOM resolution
