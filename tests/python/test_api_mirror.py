"""Tests for MirrorFacade enable/disable/info API wired into InventoryApi."""

import os
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
    assert api._mirror._read_state()["enabled"] is True
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


def test_daemon_script_path_exists(tmp_path):
    """daemon_script must point to the real inventory_mirror.py (not data/inventory_mirror.py)."""
    api = make_api(tmp_path)
    cfg = api._mirror._config()
    assert cfg.daemon_script.endswith("inventory_mirror.py"), (
        f"daemon_script does not end with inventory_mirror.py: {cfg.daemon_script!r}"
    )
    assert os.path.exists(cfg.daemon_script), (
        f"daemon_script path does not exist: {cfg.daemon_script!r}"
    )


def test_enable_seeds_allowlist_when_empty(tmp_path):
    """enable_inventory_mirror seeds inventoryMirror.allowlist with self_login when it was empty."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value="alice@example.com"), \
         mock.patch("mirror_install.tailscale.enable_serve", return_value="https://host.ts.net"), \
         mock.patch("mirror_install.base.get_installer") as gi:
        gi.return_value = mock.Mock()
        info = api.enable_inventory_mirror()
    assert info["allowlist"] == ["alice@example.com"]
    assert api._mirror._read_state()["allowlist"] == ["alice@example.com"]


def test_enable_does_not_overwrite_existing_allowlist(tmp_path):
    """enable_inventory_mirror must not overwrite a pre-existing non-empty allowlist."""
    api = make_api(tmp_path)
    api._mirror._write_state({"allowlist": ["existing@example.com"]})
    with mock.patch("mirror_install.tailscale.self_login", return_value="alice@example.com"), \
         mock.patch("mirror_install.tailscale.enable_serve", return_value="https://host.ts.net"), \
         mock.patch("mirror_install.base.get_installer") as gi:
        gi.return_value = mock.Mock()
        info = api.enable_inventory_mirror()
    assert info["allowlist"] == ["existing@example.com"]


def test_enable_installer_receives_seeded_allowlist(tmp_path):
    """The MirrorConfig passed to installer.install must have the seeded allowlist (ordering check)."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value="alice@example.com"), \
         mock.patch("mirror_install.tailscale.enable_serve", return_value="https://host.ts.net"), \
         mock.patch("mirror_install.base.get_installer") as gi:
        installer_mock = mock.Mock()
        gi.return_value = installer_mock
        api.enable_inventory_mirror()
    cfg_passed = installer_mock.install.call_args[0][0]
    assert cfg_passed.allowlist == ["alice@example.com"]


def test_enable_leaves_allowlist_empty_when_self_login_unavailable(tmp_path):
    """When self_login returns '', allowlist stays empty and enable_serve error propagates."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value=""), \
         mock.patch("mirror_install.base.get_installer") as gi, \
         mock.patch("mirror_install.tailscale.enable_serve",
                    side_effect=RuntimeError("tailscale is not logged in")):
        gi.return_value = mock.Mock()
        with pytest.raises(RuntimeError, match="not logged in"):
            api.enable_inventory_mirror()
    assert api._mirror._read_state().get("allowlist", []) == []
    assert api.get_inventory_mirror_info()["enabled"] is False


def test_enabled_survives_js_preferences_overwrite(tmp_path):
    """Regression: JS store save_preferences must not clobber mirror 'enabled' state."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value="owner@x.com"), \
         mock.patch("mirror_install.tailscale.enable_serve", return_value="https://host.ts.net"), \
         mock.patch("mirror_install.base.get_installer") as gi:
        gi.return_value = mock.Mock()
        api.enable_inventory_mirror()
    assert api.get_inventory_mirror_info()["enabled"] is True

    # Simulate JS store overwriting preferences.json with no inventoryMirror key.
    import json
    api.save_preferences(json.dumps({"thresholds": {}, "shortcuts": {"vimNav": True}}))

    info = api.get_inventory_mirror_info()
    assert info["enabled"] is True, "enabled was clobbered by JS preferences save"
    assert info["serve_url"] == "https://host.ts.net", "serve_url was clobbered"
    assert info["allowlist"] == ["owner@x.com"], "allowlist was clobbered"


def test_enable_rolls_back_install_when_serve_fails(tmp_path):
    """If enable_serve raises, installer.uninstall is called (rollback) and error propagates."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value="owner@x.com"), \
         mock.patch("mirror_install.tailscale.enable_serve",
                    side_effect=RuntimeError("tailscale is not logged in")), \
         mock.patch("mirror_install.base.get_installer") as gi:
        installer_mock = mock.Mock()
        gi.return_value = installer_mock
        with pytest.raises(RuntimeError, match="not logged in"):
            api.enable_inventory_mirror()
    installer_mock.install.assert_called_once()
    installer_mock.uninstall.assert_called_once()
    assert api.get_inventory_mirror_info()["enabled"] is False


def test_enable_rollback_uninstall_failure_is_swallowed(tmp_path):
    """If rollback uninstall itself raises, the ORIGINAL error still propagates unchanged."""
    api = make_api(tmp_path)
    with mock.patch("mirror_install.tailscale.self_login", return_value="owner@x.com"), \
         mock.patch("mirror_install.tailscale.enable_serve",
                    side_effect=RuntimeError("tailscale is not logged in")), \
         mock.patch("mirror_install.base.get_installer") as gi:
        installer_mock = mock.Mock()
        installer_mock.uninstall.side_effect = OSError("service removal failed")
        gi.return_value = installer_mock
        with pytest.raises(RuntimeError, match="not logged in"):
            api.enable_inventory_mirror()
    assert api.get_inventory_mirror_info()["enabled"] is False


def test_mirror_paths_under_base_dir_not_data_subdir(tmp_path):
    """Token and snapshot paths must be directly under base_dir, not base_dir/data/."""
    api = make_api(tmp_path)
    expected_token = os.path.join(api.base_dir, "mirror_token")
    expected_snapshot = os.path.join(api.base_dir, "inventory_mirror.json")

    # MirrorFacade paths
    assert api._mirror._token_file() == expected_token, (
        f"_token_file() nests under data/: {api._mirror._token_file()!r}"
    )
    assert api._mirror._snapshot_file() == expected_snapshot, (
        f"_snapshot_file() nests under data/: {api._mirror._snapshot_file()!r}"
    )

    # _mirror_token() in InventoryApi must agree with _token_file()
    # Write a token at the correct location and confirm _mirror_token() reads it.
    os.makedirs(api.base_dir, exist_ok=True)
    with open(expected_token, "w", encoding="utf-8") as f:
        f.write("test-tok")
    assert api._mirror_token() == "test-tok", (
        "_mirror_token() reads from a different path than _token_file()"
    )
