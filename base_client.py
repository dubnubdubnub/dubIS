"""Abstract base class for distributor product clients."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from dubis_errors import DistributorError

logger = logging.getLogger(__name__)


class BaseProductClient(ABC):
    """Base class for distributor product clients.

    Subclasses must define:
      - provider: str — distributor name (e.g. "lcsc", "mouser")
      - _fetch_raw(identifier) — fetch and return product info (no caching)

    The base class provides:
      - fetch_product(identifier) — cache-aware wrapper around _fetch_raw
      - clear_cache() — empty the session cache
    """

    provider: str

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any] | None] = {}

    def fetch_product(self, identifier: str) -> dict[str, Any] | None:
        """Fetch product details by identifier, with session caching.

        Returns a normalized dict of product info, or None if not found/failed.
        Results (including None) are cached for the session.

        ValueError from subclass validation propagates immediately (not cached).
        DistributorError (and subclasses like DistributorTimeout) propagates.
        Other exceptions are caught, cached as None, and logged.
        """
        if identifier in self._cache:
            return self._cache[identifier]

        try:
            result = self._fetch_raw(identifier)
        except (ValueError, DistributorError):
            raise
        except Exception as exc:
            logger.warning(
                "%s fetch failed for %s: %s: %s",
                self.provider, identifier, type(exc).__name__, exc,
            )
            self._cache[identifier] = None
            return None

        self._cache[identifier] = result
        return result

    @abstractmethod
    def _fetch_raw(self, identifier: str) -> dict[str, Any] | None:
        """Fetch product details by identifier (no caching).

        Subclasses implement the actual fetch logic here.
        Raise ValueError for invalid identifiers (will propagate to caller).
        Return None if the product is not found or the fetch fails.
        Return a normalized dict on success.
        """

    def clear_cache(self) -> None:
        """Clear the session cache."""
        self._cache.clear()
