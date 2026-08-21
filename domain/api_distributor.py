"""Distributor facade — product fetches and DigiKey/Mouser session management.

Every `fetch_*_product` here funnels its result through
`AttributesFacade.persist_from_product`, so the parametric attributes each
client already returns are stored (per part, per distributor) instead of being
shown once and dropped. This is the same "record what the fetch told us" hook
`record_fetched_prices` provides for price tiers, on the read path the app
actually uses (`GET /v1/distributors/{name}/product/{code}`) — no extra route
and no client change needed.
"""

from __future__ import annotations

from typing import Any


class DistributorFacade:
    def __init__(self, api) -> None:
        self._api = api

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        return self._api._attrs.persist_from_product(
            "lcsc", product_code,
            self._api._distributors.fetch_lcsc_product(product_code, debug=self._api._debug),
        )

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        return self._api._attrs.persist_from_product(
            "digikey", part_number,
            self._api._distributors.fetch_digikey_product(part_number, debug=self._api._debug),
        )

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        return self._api._attrs.persist_from_product(
            "pololu", sku,
            self._api._distributors.fetch_pololu_product(sku, debug=self._api._debug),
        )

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        return self._api._attrs.persist_from_product(
            "mouser", part_number,
            self._api._distributors.fetch_mouser_product(part_number, debug=self._api._debug),
        )

    def check_digikey_session(self) -> dict[str, Any]:
        return self._api._distributors.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        return self._api._distributors.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        return self._api._distributors.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        return self._api._distributors.get_digikey_login_status()

    def validate_digikey_session(self) -> dict[str, Any]:
        return self._api._distributors.validate_digikey_session()

    def logout_digikey(self) -> dict[str, str]:
        return self._api._distributors.logout_digikey()

    def get_mouser_api_key_status(self) -> dict[str, bool]:
        return self._api._distributors.get_mouser_api_key_status()

    def set_mouser_api_key(self, key: str) -> dict[str, bool]:
        return self._api._distributors.set_mouser_api_key(key)

    def clear_mouser_api_key(self) -> dict[str, bool]:
        return self._api._distributors.clear_mouser_api_key()
