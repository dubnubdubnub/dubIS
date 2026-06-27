"""Tests for MirrorFacade enable/disable/info API wired into InventoryApi."""

from unittest import mock

import pytest

from tests.python.helpers import make_api


def test_mirror_disabled_by_default(tmp_path):
    api = make_api(tmp_path)
    info = api.get_inventory_mirror_info()
    assert info["enabled"] is False


def test_enable_writes_pref_and_token(tmp_path):
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.enable_serve", return_value="https://host.ts.net"), \
         mock.patch("mirror_install.base.get_installer") as gi:
        gi.return_value = mock.Mock()
        info = api.enable_inventory_mirror()
    assert info["enabled"] is True
    assert info["serve_url"] == "https://host.ts.net"
    assert api.load_preferences()["inventoryMirror"]["enabled"] is True
    assert api._mirror_token()  # a token now exists


def test_enable_propagates_tailscale_error(tmp_path):
    api = make_api(tmp_path)
    with mock.patch("mirror_install.base.get_installer") as gi, \
         mock.patch("mirror_install.tailscale.enable_serve",
                    side_effect=RuntimeError("tailscale is not logged in")):
        gi.return_value = mock.Mock()
        with pytest.raises(RuntimeError, match="not logged in"):
            api.enable_inventory_mirror()
    # On failure the pref must remain disabled.
    assert api.get_inventory_mirror_info()["enabled"] is False


def test_disable_clears_pref(tmp_path):
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.enable_serve", return_value="https://h"), \
         mock.patch("mirror_install.base.get_installer", return_value=mock.Mock()):
        api.enable_inventory_mirror()
    with mock.patch("mirror_install.tailscale.disable_serve"), \
         mock.patch("mirror_install.base.get_installer", return_value=mock.Mock()):
        info = api.disable_inventory_mirror()
    assert info["enabled"] is False
