"""Distributor API — product fetching and Digikey session management."""

from __future__ import annotations

import logging
from typing import Any

from base_client import BaseProductClient
from distributor_manager import DistributorManager

logger = logging.getLogger(__name__)


class DistributorApi:
    """Thin facade over DistributorManager for product fetch + Digikey auth.

    Exposed to JS via InventoryApi delegation so the pywebview API surface
    stays identical.
    """

    def __init__(self, *, base_dir: str, get_cache=None, debug: bool = False) -> None:
        self._debug = debug
        self._distributors = DistributorManager(base_dir, get_cache or (lambda: None))

    # ── Shared fetch helper ──────────────────────────────────────────────

    def _fetch_product(self, client: BaseProductClient, identifier: str) -> dict[str, Any] | None:
        """Fetch a product via the given client, stripping _debug in non-debug mode."""
        result = client.fetch_product(identifier)
        if result and not self._debug:
            result.pop("_debug", None)
        return result

    # ── Product fetch (4 distributors) ───────────────────────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        """Delegate to LcscClient."""
        return self._fetch_product(self._distributors._lcsc, product_code)

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        """Delegate to DigikeyClient."""
        return self._fetch_product(self._distributors._digikey, part_number)

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        """Delegate to PololuClient."""
        return self._fetch_product(self._distributors._pololu, sku)

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        """Delegate to MouserClient."""
        return self._fetch_product(self._distributors._mouser, part_number)

    # ── Digikey session management ───────────────────────────────────────

    def check_digikey_session(self) -> dict[str, Any]:
        """Delegate to DistributorManager."""
        return self._distributors.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        """Delegate to DistributorManager."""
        return self._distributors.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        """Delegate to DistributorManager."""
        return self._distributors.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        """Delegate to DistributorManager."""
        return self._distributors.get_digikey_login_status()

    def logout_digikey(self) -> dict[str, str]:
        """Delegate to DistributorManager."""
        return self._distributors.logout_digikey()
