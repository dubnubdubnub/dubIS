"""Pricing facade — price history recording and lookup."""

from __future__ import annotations

from typing import Any

import domain.pricing
import inventory_ops


class PricingFacade:
    """All cache-touching methods take the API lock: the SQLite connection is
    a single shared object, so unlocked reads can observe a concurrent
    mutation's uncommitted mid-transaction state (same-connection readers see
    inside open transactions), and unlocked writes interleave with it."""

    def __init__(self, api) -> None:
        self._api = api

    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        """Record prices fetched from a distributor API/scraper."""
        with self._api._lock:
            return domain.pricing.record_fetched_prices(
                self._api._get_cache(), self._api.events_dir, part_key, distributor, price_tiers,
            )

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        """Get aggregated pricing per distributor for a part."""
        with self._api._lock:
            return domain.pricing.get_price_summary(
                self._api._get_cache(), self._api.events_dir, part_key,
            )

    def get_last_po_quantity(self, part_key: str) -> int | None:
        """Quantity from the most recent purchase-ledger row for this part, or None."""
        return inventory_ops.last_po_quantity(self._api.input_csv, part_key)

    def get_sourced_distributors(self, part_key: str) -> list[dict[str, str]]:
        """Distributors this part was sourced from (record PNs ∪ ledger PNs)."""
        with self._api._lock:
            return domain.pricing.get_sourced_distributors(
                self._api._get_cache(), self._api.input_csv, part_key,
            )
