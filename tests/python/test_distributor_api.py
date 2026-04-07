"""Tests for DistributorApi facade."""

import pytest

from digikey_client import DigikeyClient
from distributor_api import DistributorApi
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient


@pytest.fixture
def dist_api(tmp_path):
    """DistributorApi wired to a temp directory."""
    return DistributorApi(base_dir=str(tmp_path), debug=False)


@pytest.fixture
def dist_api_debug(tmp_path):
    """DistributorApi with debug=True."""
    return DistributorApi(base_dir=str(tmp_path), debug=True)


class TestDistributorApiInit:
    def test_creates_distributor_manager(self, dist_api):
        assert dist_api._distributors is not None

    def test_has_all_clients(self, dist_api):
        assert isinstance(dist_api._distributors._lcsc, LcscClient)
        assert isinstance(dist_api._distributors._digikey, DigikeyClient)
        assert isinstance(dist_api._distributors._pololu, PololuClient)
        assert isinstance(dist_api._distributors._mouser, MouserClient)

    def test_debug_flag_stored(self, dist_api, dist_api_debug):
        assert dist_api._debug is False
        assert dist_api_debug._debug is True


class TestFetchProduct:
    """Test the unified _fetch_product helper and the 4 public fetch methods."""

    def test_fetch_lcsc_delegates(self, dist_api):
        cached = {"productCode": "C2040", "provider": "lcsc"}
        dist_api._distributors._lcsc._cache["C2040"] = cached
        assert dist_api.fetch_lcsc_product("C2040") is cached

    def test_fetch_digikey_delegates(self, dist_api):
        cached = {"productCode": "DK-1", "provider": "digikey"}
        dist_api._distributors._digikey._cache["DK-1"] = cached
        assert dist_api.fetch_digikey_product("DK-1") is cached

    def test_fetch_pololu_delegates(self, dist_api):
        cached = {"productCode": "1992", "provider": "pololu"}
        dist_api._distributors._pololu._cache["1992"] = cached
        assert dist_api.fetch_pololu_product("1992") is cached

    def test_fetch_mouser_delegates(self, dist_api):
        cached = {"productCode": "736-FGG0B305CLAD52", "provider": "mouser"}
        dist_api._distributors._mouser._cache["736-FGG0B305CLAD52"] = cached
        assert dist_api.fetch_mouser_product("736-FGG0B305CLAD52") is cached

    def test_debug_false_strips_debug_key(self, dist_api):
        cached = {"productCode": "C2040", "_debug": {"raw": "stuff"}, "provider": "lcsc"}
        dist_api._distributors._lcsc._cache["C2040"] = cached
        result = dist_api.fetch_lcsc_product("C2040")
        assert "_debug" not in result

    def test_debug_true_keeps_debug_key(self, dist_api_debug):
        cached = {"productCode": "C2040", "_debug": {"raw": "stuff"}, "provider": "lcsc"}
        dist_api_debug._distributors._lcsc._cache["C2040"] = cached
        result = dist_api_debug.fetch_lcsc_product("C2040")
        assert "_debug" in result
        assert result["_debug"] == {"raw": "stuff"}

    def test_fetch_returns_none_on_miss(self, dist_api):
        """None cache entry is returned as None."""
        dist_api._distributors._lcsc._cache["C9999"] = None
        assert dist_api.fetch_lcsc_product("C9999") is None

    def test_debug_strip_does_not_affect_none(self, dist_api):
        """_debug stripping doesn't crash on None results."""
        dist_api._distributors._lcsc._cache["C9999"] = None
        assert dist_api.fetch_lcsc_product("C9999") is None


class TestDigikeySession:
    """Test the 5 Digikey session management methods."""

    def test_get_login_status(self, dist_api):
        assert dist_api.get_digikey_login_status() == {"logged_in": False}

    def test_sync_cookies(self, dist_api):
        result = dist_api.sync_digikey_cookies()
        assert result["logged_in"] is False

    def test_logout(self, dist_api):
        result = dist_api.logout_digikey()
        assert result == {"status": "ok"}

    def test_check_session(self, dist_api):
        result = dist_api.check_digikey_session()
        assert "logged_in" in result

    def test_start_login(self, dist_api):
        # start_login launches a browser process; it returns a dict with status
        result = dist_api.start_digikey_login()
        assert isinstance(result, dict)


class TestGetCacheCallback:
    """Test that get_cache callback is passed through to DistributorManager."""

    def test_default_get_cache_returns_none(self, tmp_path):
        api = DistributorApi(base_dir=str(tmp_path))
        assert api._distributors._get_cache() is None

    def test_custom_get_cache_is_used(self, tmp_path):
        sentinel = object()
        api = DistributorApi(base_dir=str(tmp_path), get_cache=lambda: sentinel)
        assert api._distributors._get_cache() is sentinel
