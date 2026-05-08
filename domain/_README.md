# domain — Python domain layer: business logic extracted from inventory_api.py

## Owns

Pure business-logic functions for pricing and price history. Does NOT own database
connections, file paths, or CSV I/O setup — those are passed in as parameters.

## Used by

- `cache_db.py` — imports `parse_price`, `parse_qty` for cache population
- `file_dialogs.py` — imports `ensure_parsed` for JSON parsing
- `inventory_ops.py` — imports `derive_missing_price`, `parse_price`, `parse_qty`
- `inventory_api.py` — imports `record_fetched_prices`, `get_price_summary`, `populate_prices_cache`, `resolve_part_key`, `record_observations`

## Public exports

- `pricing.py`: `parse_qty`, `parse_price`, `ensure_parsed`, `derive_missing_price` — scalar parsing helpers
- `pricing.py`: `record_observations`, `read_observations` — append/read price observation CSV
- `pricing.py`: `populate_prices_cache`, `resolve_part_key` — SQLite prices-table helpers
- `pricing.py`: `record_fetched_prices`, `get_price_summary` — distributor price fetch + summary

## Internal layout

- `__init__.py` — package marker (empty)
- `pricing.py` — all pricing logic: parse helpers, observation log, SQLite cache helpers
