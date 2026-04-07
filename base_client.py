"""Abstract base class for distributor product clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProductClient(ABC):
    """Base class for distributor product clients.

    Subclasses must define:
      - provider: str — distributor name (e.g. "lcsc", "mouser")
      - fetch_product(identifier) — fetch and return product info
    """

    provider: str

    @abstractmethod
    def fetch_product(self, identifier: str) -> dict[str, Any] | None:
        """Fetch product details by identifier.

        Returns a normalized dict of product info, or None if not found/failed.
        """
