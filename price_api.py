"""Price API — facade for price history recording and summary queries."""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Callable

import price_history

logger = logging.getLogger(__name__)


class PriceApi:
    """Thin facade over price_history for recording and querying prices.

    Exposed to JS via InventoryApi delegation so the pywebview API surface
    stays identical.
    """

    def __init__(self, *, get_cache: Callable[[], sqlite3.Connection], events_dir: str) -> None:
        self._get_cache = get_cache
        self.events_dir = events_dir

    def _resolve_part_key(self, key: str) -> str | None:
        """Resolve a distributor-specific PN to the inventory part_id.

        Checks for a direct match first, then searches distributor columns
        (lcsc, mpn, digikey, pololu, mouser) in the parts table.
        """
        conn = self._get_cache()
        try:
            if conn.execute("SELECT 1 FROM parts WHERE part_id = ?", (key,)).fetchone():
                return key
            for col in ("lcsc", "mpn", "digikey", "pololu", "mouser"):
                row = conn.execute(
                    f"SELECT part_id FROM parts WHERE {col} = ?", (key,)
                ).fetchone()
                if row:
                    return row["part_id"]
        except (sqlite3.OperationalError, sqlite3.InterfaceError):
            # Connection may be busy from a concurrent populate_prices_cache
            logger.debug("_resolve_part_key: cache busy, falling back to raw key")
            return key
        return None

    def record_fetched_prices(self, part_key: str, distributor: str,
                              price_tiers: list[dict[str, Any]]) -> None:
        """Record prices fetched from a distributor API/scraper."""
        resolved_key = self._resolve_part_key(part_key)
        if not resolved_key:
            logger.warning("record_fetched_prices: no inventory part for %r", part_key)
            return
        os.makedirs(self.events_dir, exist_ok=True)
        observations = []
        for tier in price_tiers:
            price = float(tier.get("price", 0))
            if price <= 0:
                continue
            observations.append({
                "part_id": resolved_key,
                "distributor": distributor,
                "unit_price": price,
                "source": "live_fetch",
                "moq": tier.get("qty", ""),
            })
        if observations:
            price_history.record_observations(self.events_dir, observations)
            conn = self._get_cache()
            price_history.populate_prices_cache(conn, self.events_dir)

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        """Get aggregated pricing per distributor for a part."""
        resolved_key = self._resolve_part_key(part_key) or part_key
        conn = self._get_cache()
        try:
            if not conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone():
                if os.path.exists(self.events_dir):
                    price_history.populate_prices_cache(conn, self.events_dir)
            rows = conn.execute(
                "SELECT * FROM prices WHERE part_id = ?", (resolved_key,)
            ).fetchall()
        except (sqlite3.OperationalError, sqlite3.InterfaceError):
            # Cache busy from concurrent record_fetched_prices rebuild
            logger.debug("get_price_summary: cache busy for %r", part_key)
            return {}
        result = {}
        for row in rows:
            result[row["distributor"]] = {
                "latest_unit_price": row["latest_unit_price"],
                "avg_unit_price": row["avg_unit_price"],
                "price_count": row["price_count"],
                "last_observed": row["last_observed"],
                "moq": row["moq"],
                "source": row["source"],
            }
        return result
