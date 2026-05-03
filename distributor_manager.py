"""Manages distributor client instances and distributor inference."""

from __future__ import annotations

import logging
import os
from typing import Any

from base_client import BaseProductClient
from digikey_client import DigikeyClient
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient

logger = logging.getLogger(__name__)


class DistributorManager:
    """Manages distributor clients, inference, and Digikey session methods."""

    def __init__(self, base_dir: str, get_cache) -> None:
        """Initialise distributor clients.

        Args:
            base_dir: Path to the data directory (for cookie files, etc.).
            get_cache: Zero-argument callable that returns a sqlite3.Connection.
                       Used by _infer_distributor_for_key to look up cached part
                       metadata without holding a direct reference to the connection.
        """
        self._lcsc = LcscClient()
        self._digikey = DigikeyClient(
            cookies_file=os.path.join(base_dir, "digikey_cookies.json"),
        )
        self._pololu = PololuClient()
        self._mouser = MouserClient()
        self._get_cache = get_cache

    # ── Distributor inference ─────────────────────────────────────────────

    @staticmethod
    def infer_distributor(row: dict[str, str]) -> str:
        """Infer distributor from which part number fields are populated."""
        if (row.get("LCSC Part Number") or "").strip():
            return "lcsc"
        if (row.get("Digikey Part Number") or "").strip():
            return "digikey"
        if (row.get("Mouser Part Number") or "").strip():
            return "mouser"
        if (row.get("Pololu Part Number") or "").strip():
            return "pololu"
        return "unknown"

    def infer_distributor_for_key(self, part_key: str) -> str:
        """Infer distributor from a part key string."""
        if part_key.upper().startswith("C") and part_key[1:].isdigit():
            return "lcsc"
        conn = self._get_cache()
        row = conn.execute(
            "SELECT digikey, pololu, mouser FROM parts WHERE part_id = ?",
            (part_key,),
        ).fetchone()
        if row:
            if row["digikey"]:
                return "digikey"
            if row["pololu"]:
                return "pololu"
            if row["mouser"]:
                return "mouser"
        return "unknown"

    # ── Digikey session management ────────────────────────────────────────

    def check_digikey_session(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.check_session()

    def start_digikey_login(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.start_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.sync_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        """Delegate to DigikeyClient."""
        return self._digikey.get_login_status()

    def validate_digikey_session(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.validate_session()

    def logout_digikey(self) -> dict[str, str]:
        """Delegate to DigikeyClient."""
        return self._digikey.logout()

    # ── Product fetching ─────────────────────────────────────────────────

    def _fetch_product(
        self, client: BaseProductClient, identifier: str, *, debug: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch a product via the given client, stripping _debug in non-debug mode."""
        result = client.fetch_product(identifier)
        if result and not debug:
            result.pop("_debug", None)
        return result

    def fetch_lcsc_product(
        self, product_code: str, *, debug: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch an LCSC product."""
        return self._fetch_product(self._lcsc, product_code, debug=debug)

    def fetch_digikey_product(
        self, part_number: str, *, debug: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch a Digikey product."""
        return self._fetch_product(self._digikey, part_number, debug=debug)

    def fetch_pololu_product(
        self, sku: str, *, debug: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch a Pololu product."""
        return self._fetch_product(self._pololu, sku, debug=debug)

    def fetch_mouser_product(
        self, part_number: str, *, debug: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch a Mouser product."""
        return self._fetch_product(self._mouser, part_number, debug=debug)
