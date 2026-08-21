"""Part-attribute facade — parametric persistence and lookup.

Mirrors `domain/api_pricing.py`: the domain functions in `domain/attributes.py`
take an explicit connection and data dir, and this facade is what supplies
them from `InventoryApi` state under the API lock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import domain.attributes

logger = logging.getLogger(__name__)


class AttributesFacade:
    """All cache-touching methods take the API lock — same rationale as
    `PricingFacade`: the SQLite connection is one shared object, so unlocked
    reads can observe a concurrent mutation's uncommitted state and unlocked
    writes interleave with it."""

    def __init__(self, api) -> None:
        self._api = api

    def record_fetched_attributes(
        self,
        part_key: str,
        distributor: str,
        attributes: list[dict[str, Any]] | None,
    ) -> int:
        """Persist parametric attributes fetched from a distributor. Returns rows written."""
        with self._api._lock:
            return domain.attributes.record_fetched_attributes(
                self._api._get_cache(), self._api.base_dir,
                part_key, distributor, attributes,
            )

    def get_part_attributes(self, part_key: str) -> list[dict[str, Any]]:
        """Stored parametric attributes for a part, across all distributors."""
        with self._api._lock:
            return domain.attributes.get_attributes(
                self._api._get_cache(), self._api.base_dir, part_key,
            )

    # ── fetch-path integration ────────────────────────────────────────────

    def persist_from_product(
        self,
        distributor: str,
        part_key: str,
        product: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Record `product["attributes"]`, then return *product* unchanged.

        Sits on the distributor fetch path so every product the app fetches
        leaves its parametrics behind. Persistence is best-effort: a product
        preview must not fail because the attribute store could not be written
        (it is warned about, not swallowed silently, per the error policy).

        A product with no attributes returns before the lock and the cache
        connection are touched — a fetch that has nothing to record must not
        be the thing that lazily opens cache.db.
        """
        if not product:
            return product
        attributes = product.get("attributes")
        if not attributes:
            return product
        try:
            self.record_fetched_attributes(part_key, distributor, attributes)
        except Exception as exc:  # noqa: BLE001 - a preview must survive a store failure
            logger.warning(
                "record_fetched_attributes failed for %s/%s: %s", distributor, part_key, exc)
        return product

    def recording_fetcher(
        self,
        distributor: str,
        fetch: Callable[[str], dict[str, Any] | None],
    ) -> Callable[[str], dict[str, Any] | None]:
        """Wrap a `DistributorManager.fetch_*` callable so its results persist."""
        def _fetch(identifier: str) -> dict[str, Any] | None:
            return self.persist_from_product(distributor, identifier, fetch(identifier))
        return _fetch
