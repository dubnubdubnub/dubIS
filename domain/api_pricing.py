"""Pricing facade — price history recording and lookup."""

from __future__ import annotations

from typing import Any

import domain.pricing
import inventory_ops


class PricingFacade:
    def __init__(self, api) -> None:
        self._api = api

    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        """Record prices fetched from a distributor API/scraper."""
        return domain.pricing.record_fetched_prices(
            self._api._get_cache(), self._api.events_dir, part_key, distributor, price_tiers,
        )

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        """Get aggregated pricing per distributor for a part."""
        return domain.pricing.get_price_summary(
            self._api._get_cache(), self._api.events_dir, part_key,
        )

    def get_last_po_quantity(self, part_key: str) -> int | None:
        """Quantity from the most recent purchase-ledger row for this part, or None."""
        return inventory_ops.last_po_quantity(self._api.input_csv, part_key)
