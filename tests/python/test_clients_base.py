"""Tests that InventoryApi correctly delegates to distributor client instances."""

from digikey_client import DigikeyClient
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient


class TestInventoryApiDelegation:
    """Verify that InventoryApi correctly delegates to client instances."""

    def test_api_has_clients(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        assert isinstance(api._distributors._lcsc, LcscClient)
        assert isinstance(api._distributors._digikey, DigikeyClient)
        assert isinstance(api._distributors._pololu, PololuClient)
        assert isinstance(api._distributors._mouser, MouserClient)

    def test_digikey_cookies_file_configured(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        assert api._distributors._digikey._cookies_file is not None
        assert "digikey_cookies.json" in api._distributors._digikey._cookies_file

    def test_fetch_lcsc_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        cached = {"productCode": "C2040", "provider": "lcsc"}
        api._distributors._lcsc._cache["C2040"] = cached
        assert api.fetch_lcsc_product("C2040") is cached

    def test_fetch_digikey_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        cached = {"productCode": "DK-1", "provider": "digikey"}
        api._distributors._digikey._cache["DK-1"] = cached
        assert api.fetch_digikey_product("DK-1") is cached

    def test_digikey_session_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        assert api.get_digikey_login_status() == {"logged_in": False}

    def test_sync_cookies_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        result = api.sync_digikey_cookies()
        assert result["logged_in"] is False

    def test_logout_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        result = api.logout_digikey()
        assert result == {"status": "ok"}

    def test_fetch_pololu_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        cached = {"productCode": "1992", "provider": "pololu"}
        api._distributors._pololu._cache["1992"] = cached
        assert api.fetch_pololu_product("1992") is cached

    def test_fetch_mouser_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        cached = {"productCode": "736-FGG0B305CLAD52", "provider": "mouser"}
        api._distributors._mouser._cache["736-FGG0B305CLAD52"] = cached
        assert api.fetch_mouser_product("736-FGG0B305CLAD52") is cached

    def test_mouser_credentials_file_configured(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        assert api._distributors._mouser._credentials_file is not None
        assert "mouser_credentials.json" in api._distributors._mouser._credentials_file

    def test_mouser_api_key_status_delegates(self):
        from inventory_api import InventoryApi
        api = InventoryApi()
        status = api.get_mouser_api_key_status()
        assert isinstance(status, dict)
        assert "configured" in status

    def test_set_mouser_api_key_delegates(self, tmp_path):
        from inventory_api import InventoryApi
        api = InventoryApi()
        # Redirect the credentials file to a tmp path so we don't touch real data/.
        api._distributors._mouser._credentials_file = str(
            tmp_path / "mouser_credentials.json"
        )
        api.set_mouser_api_key("test-key")
        assert api.get_mouser_api_key_status()["configured"] is True
        api.clear_mouser_api_key()
        assert api.get_mouser_api_key_status()["configured"] is False
