"""Tests for DistributorManager product fetching and Digikey session."""

import sys

import pytest

from digikey_client import DigikeyClient
from distributor_manager import DistributorManager
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient


@pytest.fixture
def dist_mgr(tmp_path):
    """DistributorManager wired to a temp directory."""
    return DistributorManager(str(tmp_path), lambda: None)


class TestDistributorManagerInit:
    def test_has_all_clients(self, dist_mgr):
        assert isinstance(dist_mgr._lcsc, LcscClient)
        assert isinstance(dist_mgr._digikey, DigikeyClient)
        assert isinstance(dist_mgr._pololu, PololuClient)
        assert isinstance(dist_mgr._mouser, MouserClient)


class TestFetchProduct:
    """Test the unified _fetch_product helper and the 4 public fetch methods."""

    def test_fetch_lcsc_delegates(self, dist_mgr):
        cached = {"productCode": "C2040", "provider": "lcsc"}
        dist_mgr._lcsc._cache["C2040"] = cached
        assert dist_mgr.fetch_lcsc_product("C2040") is cached

    def test_fetch_digikey_delegates(self, dist_mgr):
        cached = {"productCode": "DK-1", "provider": "digikey"}
        dist_mgr._digikey._cache["DK-1"] = cached
        assert dist_mgr.fetch_digikey_product("DK-1") is cached

    def test_fetch_pololu_delegates(self, dist_mgr):
        cached = {"productCode": "1992", "provider": "pololu"}
        dist_mgr._pololu._cache["1992"] = cached
        assert dist_mgr.fetch_pololu_product("1992") is cached

    def test_fetch_mouser_delegates(self, dist_mgr):
        cached = {"productCode": "736-FGG0B305CLAD52", "provider": "mouser"}
        dist_mgr._mouser._cache["736-FGG0B305CLAD52"] = cached
        assert dist_mgr.fetch_mouser_product("736-FGG0B305CLAD52") is cached

    def test_debug_false_strips_debug_key(self, dist_mgr):
        cached = {"productCode": "C2040", "_debug": {"raw": "stuff"}, "provider": "lcsc"}
        dist_mgr._lcsc._cache["C2040"] = cached
        result = dist_mgr.fetch_lcsc_product("C2040")
        assert "_debug" not in result

    def test_debug_true_keeps_debug_key(self, dist_mgr):
        cached = {"productCode": "C2040", "_debug": {"raw": "stuff"}, "provider": "lcsc"}
        dist_mgr._lcsc._cache["C2040"] = cached
        result = dist_mgr.fetch_lcsc_product("C2040", debug=True)
        assert "_debug" in result
        assert result["_debug"] == {"raw": "stuff"}

    def test_fetch_returns_none_on_miss(self, dist_mgr):
        """None cache entry is returned as None."""
        dist_mgr._lcsc._cache["C9999"] = None
        assert dist_mgr.fetch_lcsc_product("C9999") is None

    def test_debug_strip_does_not_affect_none(self, dist_mgr):
        """_debug stripping doesn't crash on None results."""
        dist_mgr._lcsc._cache["C9999"] = None
        assert dist_mgr.fetch_lcsc_product("C9999") is None


class TestDigikeySession:
    """Test the 5 Digikey session management methods."""

    def test_get_login_status(self, dist_mgr):
        assert dist_mgr.get_digikey_login_status() == {"logged_in": False}

    def test_sync_cookies(self, dist_mgr):
        result = dist_mgr.sync_digikey_cookies()
        assert result["logged_in"] is False

    def test_logout(self, dist_mgr):
        result = dist_mgr.logout_digikey()
        assert result == {"status": "ok"}

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg only available on Windows")
    def test_check_session(self, dist_mgr):
        result = dist_mgr.check_digikey_session()
        assert "logged_in" in result

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg only available on Windows")
    def test_start_login(self, dist_mgr):
        # start_login launches a browser process; it returns a dict with status
        result = dist_mgr.start_digikey_login()
        assert isinstance(result, dict)


class TestGetCacheCallback:
    """Test that get_cache callback is passed through to DistributorManager."""

    def test_default_get_cache_returns_none(self, tmp_path):
        mgr = DistributorManager(str(tmp_path), lambda: None)
        assert mgr._get_cache() is None

    def test_custom_get_cache_is_used(self, tmp_path):
        sentinel = object()
        mgr = DistributorManager(str(tmp_path), lambda: sentinel)
        assert mgr._get_cache() is sentinel
