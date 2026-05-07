"""Pricing domain — re-exports from price_ops and price_history.

This module presents the pricing domain as a unified namespace.  The
underlying implementations remain in price_ops.py and price_history.py
(both in the project root) so that existing import paths and tests
continue to work unchanged.

Usage from inventory_api.py:
    import domain.pricing
    domain.pricing.parse_qty(...)
    domain.pricing.record_fetched_prices(conn, events_dir, ...)
"""

from price_history import (  # noqa: F401
    FIELDNAMES,
    OBSERVATIONS_FILE,
    _build_part_id_resolver,
    get_price_summary,
    populate_prices_cache,
    read_observations,
    record_fetched_prices,
    record_observations,
    resolve_part_key,
)
from price_ops import (  # noqa: F401
    derive_missing_price,
    ensure_parsed,
    parse_price,
    parse_qty,
)
