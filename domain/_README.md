# domain — Python domain layer: business logic extracted from inventory_api.py

## Owns

Pure business-logic functions for pricing, price history, inventory pipeline, and generic parts.
Does NOT own database connections, file paths, or CSV I/O setup — those are passed in as parameters.

## Used by

- `cache_db.py` — imports `parse_price`, `parse_qty` for cache population
- `file_dialogs.py` — imports `ensure_parsed` for JSON parsing
- `inventory_ops.py` — imports `derive_missing_price`, `parse_price`, `parse_qty`
- `inventory_api.py` — imports `record_fetched_prices`, `get_price_summary`, `populate_prices_cache`, `resolve_part_key`, `record_observations`
- `domain.inventory` — imports `load_into_db` from `attributes` (rebuild restores the derived table)
- `inventory_api.py` — imports `create_generic_part_api`, `update_generic_part_api`, `add_member_api`, `remove_member_api`, `set_preferred_api`, `exclude_member`, `list_generic_parts_with_member_specs`, `fetch_members`, `resolve_bom_spec`, `extract_spec_for_part`, `review_member_api`, `list_member_reviews`
- `domain.inventory` — imports `auto_generate_passive_groups` from `generic_parts`

## Public exports

- `pricing.py`: `parse_qty`, `parse_price`, `ensure_parsed`, `derive_missing_price` — scalar parsing helpers
- `pricing.py`: `record_observations`, `read_observations` — append/read price observation CSV
- `pricing.py`: `FIELDNAMES`, `PACKAGING_FIELDNAMES`, `migrate_observations` — observation CSV schema + header migration
- `pricing.py`: `populate_prices_cache`, `resolve_part_key` — SQLite prices-table helpers
- `pricing.py`: `record_fetched_prices`, `get_price_summary` — distributor price fetch + summary
- `packaging.py`: `carrier_of`, `is_reel` — normalize distributor packaging prose to a carrier + reel-ness
- `packaging.py`: `clean_reel_qty`, `clean_reel_fee` — coerce scraped reel quantity/surcharge (0 → unknown)
- `product.py`: `build_product`, `annotate_packagings` — the normalized-product factory every client emits
- `attributes.py`: `record_fetched_attributes`, `get_attributes` — distributor parametric fetch + lookup
- `attributes.py`: `record_attributes`, `read_rows`, `build_rows` — durable part_attributes CSV
- `attributes.py`: `load_into_db` — rebuild the derived part_attributes cache table
- `attribute_parse.py`: `parse_value`, `ParsedValue` — value -> magnitude/range/unit, or unparsed
- `attribute_parse.py`: `canonical_name`, `normalize_name` — cross-distributor attribute names (alias table: CANONICAL_NAME_ALIASES)
- `generic_parts.py`: `create_generic_part`, `create_generic_part_api` — create generic group with auto-matching
- `generic_parts.py`: `update_generic_part_api` — update spec and re-run auto-matching
- `generic_parts.py`: `add_member`, `add_member_api`, `remove_member`, `remove_member_api` — member management
- `generic_parts.py`: `exclude_member`, `set_preferred`, `set_preferred_api` — member state
- `generic_parts.py`: `preview_members`, `fetch_members` — query helpers
- `generic_parts.py`: `list_generic_parts_with_member_specs` — full listing with extracted specs
- `generic_parts.py`: `resolve_bom_spec` — BOM resolution to best real part
- `generic_parts.py`: `auto_generate_passive_groups` — scan passives and create auto groups
- `generic_parts.py`: `extract_spec_for_part` — extract component spec from cache
- `generic_parts.py`: `review_member`, `review_member_api` — record why a member is (or is not) a valid alternate, with an approval state
- `generic_parts.py`: `get_member_review`, `reviews_for_group`, `list_member_reviews` — read membership reviews
- `generic_parts.py`: `default_review`, `last_rejection` — the unreviewed default and prior-rejection lookup
- `generic_parts.py`: `APPROVAL_STATES`, `DELTA_KINDS` — approval-state and spec-delta vocabularies
- `packages.py`: `normalize_package`, `packages_equivalent`, `package_info` — controlled package (land-pattern) vocabulary: vendor/KiCad string -> canonical token
- `predicates.py`: `Predicate`, `evaluate`, `evaluate_all`, `Report`, `Verdict` — evaluate substitution requirements against stored attributes; emits generic_parts spec_deltas
- `api_predicates.py`: `PredicatesFacade` — evaluate requirements for a part in this inventory (supplies attributes + cached package under the API lock)

## Internal layout

- `__init__.py` — package marker (empty)
- `pricing.py` — all pricing logic: parse helpers, observation log, SQLite cache helpers
- `attributes.py` — part attribute store: durable CSV, derived cache table, fetch recording
- `attribute_parse.py` — attribute value parsing + canonical attribute-name table
- `inventory.py` — inventory pipeline: rebuild, catch-up, adjust, import, consume
- `generic_parts.py` — generic parts CRUD, auto-matching, BOM resolution, membership interchangeability reviews
- `packages.py` — package vocabulary: alias table + generated families, pure/stdlib, no I/O (about the *body*, not the reel/tray carrier)
- `predicates.py` — requirement evaluation: numeric/enum/package predicates over the attribute store; unknown never reads as pass
- `api_predicates.py` — facade over `predicates.py`: resolves the candidate package from the parts cache, rejects malformed predicates
