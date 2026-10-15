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
import domain.distributor_categories

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
        """Record what this fetch told us about the part, then return it unchanged.

        Two facts, both already on the product and both previously dropped on
        the floor: its parametric ``attributes`` (this store), and the
        distributor's own ``category``/``subcategory``
        (``domain.distributor_categories`` -- the input ``category_map.csv``
        turns into a section).  They live in separate stores because they are
        read by different code for different reasons, but they arrive on the
        same response, so they are recorded on the same path: one place where a
        fetch leaves evidence behind, rather than two that can drift.

        Sits on the distributor fetch path so every product the app fetches
        leaves its evidence behind. Persistence is best-effort: a product
        preview must not fail because a store could not be written (it is
        warned about, not swallowed silently, per the error policy).

        A product with neither returns before the lock and the cache
        connection are touched -- a fetch that has nothing to record must not
        be the thing that lazily opens cache.db.
        """
        if not product:
            return product
        attributes = product.get("attributes")
        category = str(product.get("category") or "").strip()
        subcategory = str(product.get("subcategory") or "").strip()
        if not attributes and not (category or subcategory):
            return product
        try:
            if attributes:
                self.record_fetched_attributes(part_key, distributor, attributes)
            if category or subcategory:
                self.record_fetched_categories(part_key, distributor, product)
        except Exception as exc:  # noqa: BLE001 - a preview must survive a store failure
            logger.warning(
                "persist_from_product failed for %s/%s: %s", distributor, part_key, exc)
        return product

    def record_fetched_categories(
        self,
        part_key: str,
        distributor: str,
        product: dict[str, Any],
    ) -> bool:
        """Persist the distributor's own category for a part. True if it wrote."""
        with self._api._lock:
            return domain.distributor_categories.record_fetched_categories(
                self._api._get_cache(), self._api.base_dir,
                part_key, distributor, product,
            )

    def get_part_categories(self, part_key: str) -> list[dict[str, Any]]:
        """What each distributor calls this part, and where the map files it."""
        with self._api._lock:
            return domain.distributor_categories.get_part_categories(
                self._api._get_cache(), self._api.base_dir, part_key,
            )

    def recording_fetcher(
        self,
        distributor: str,
        fetch: Callable[[str], dict[str, Any] | None],
    ) -> Callable[[str], dict[str, Any] | None]:
        """Wrap a `DistributorManager.fetch_*` callable so its results persist."""
        def _fetch(identifier: str) -> dict[str, Any] | None:
            return self.persist_from_product(distributor, identifier, fetch(identifier))
        return _fetch
