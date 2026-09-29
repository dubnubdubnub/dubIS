"""Unit tests for digikey_browser: finding a Chromium, the profile, the port file.

Nothing here launches a browser (the conftest guard would refuse it anyway).
The live half, a real fetch through a real window, was run by hand on macOS on
2026-09-29 and is described in docs/plans/2026-09-20-extension-credential-capture.md.
"""

from __future__ import annotations

import os
import plistlib
import socket
from unittest.mock import patch

import pytest

import digikey_browser
from digikey_browser import DigikeyBrowser


def _fake_app(base, name, exe_name=None, executable=True):
    """Lay out a minimal `<name>.app` bundle the way macOS does."""
    exe_name = exe_name or name
    contents = os.path.join(base, f"{name}.app", "Contents")
    os.makedirs(os.path.join(contents, "MacOS"))
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleExecutable": exe_name}, f)
    exe = os.path.join(contents, "MacOS", exe_name)
    with open(exe, "w") as f:
        f.write("#!/bin/sh\n")
    if executable:
        os.chmod(exe, 0o755)
    return exe


class TestMacChromiumExe:
    def test_prefers_the_default_browser_when_it_is_chromium(self, tmp_path):
        _fake_app(tmp_path, "Google Chrome")
        brave = _fake_app(tmp_path, "Brave Browser")
        assert digikey_browser.mac_chromium_exe("com.brave.browser", (str(tmp_path),)) == brave

    def test_falls_back_when_the_default_cannot_speak_cdp(self, tmp_path):
        # Safari or Firefox as the default: use whichever Chromium exists.
        chrome = _fake_app(tmp_path, "Google Chrome")
        assert digikey_browser.mac_chromium_exe("com.apple.safari", (str(tmp_path),)) == chrome

    def test_none_when_no_chromium_is_installed(self, tmp_path):
        _fake_app(tmp_path, "Safari")
        assert digikey_browser.mac_chromium_exe("com.apple.safari", (str(tmp_path),)) is None

    def test_skips_a_bundle_whose_executable_is_not_executable(self, tmp_path):
        _fake_app(tmp_path, "Google Chrome", executable=False)
        edge = _fake_app(tmp_path, "Microsoft Edge")
        assert digikey_browser.mac_chromium_exe(None, (str(tmp_path),)) == edge

    def test_reads_the_executable_name_from_info_plist(self, tmp_path):
        exe = _fake_app(tmp_path, "Chromium", exe_name="chromium-bin")
        assert digikey_browser.mac_chromium_exe("org.chromium.chromium", (str(tmp_path),)) == exe


class TestDefaultHttpBundleId:
    def test_reads_the_http_handler(self, tmp_path):
        plist = tmp_path / "ls.plist"
        with open(plist, "wb") as f:
            plistlib.dump({"LSHandlers": [
                {"LSHandlerContentType": "public.html", "LSHandlerRoleAll": "com.apple.safari"},
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "com.Brave.Browser"},
            ]}, f)
        assert digikey_browser.mac_default_http_bundle_id(str(plist)) == "com.brave.browser"

    def test_missing_or_corrupt_plist_is_none(self, tmp_path):
        assert digikey_browser.mac_default_http_bundle_id(str(tmp_path / "nope.plist")) is None
        bad = tmp_path / "bad.plist"
        bad.write_bytes(b"not a plist")
        assert digikey_browser.mac_default_http_bundle_id(str(bad)) is None


class TestFindBrowserExe:
    def test_linux_uses_path_lookup(self):
        with patch("digikey_browser.sys.platform", "linux"), \
                patch("shutil.which", side_effect=lambda c: "/usr/bin/chromium" if c == "chromium" else None):
            assert digikey_browser.find_browser_exe() == "/usr/bin/chromium"

    def test_linux_with_no_chromium_is_none(self):
        with patch("digikey_browser.sys.platform", "linux"), patch("shutil.which", return_value=None):
            assert digikey_browser.find_browser_exe() is None

    def test_windows_without_winreg_warns_and_is_none(self, caplog):
        with patch("digikey_browser.sys.platform", "win32"), \
                patch.dict("sys.modules", {"winreg": None}), \
                caplog.at_level("WARNING", logger="digikey_browser"):
            assert digikey_browser.find_browser_exe() is None
        assert any("winreg unavailable" in r.message for r in caplog.records)


class TestProfileAndPort:
    def test_profile_lives_beside_the_cookie_file(self, tmp_path):
        cookies = tmp_path / "digikey_cookies.json"
        assert digikey_browser.default_profile_dir(str(cookies)) == str(
            tmp_path / digikey_browser.PROFILE_DIRNAME)

    def test_no_cookie_file_means_a_per_process_temp_profile(self):
        path = digikey_browser.default_profile_dir(None)
        assert str(os.getpid()) in path

    def test_no_port_file_means_not_running(self, tmp_path):
        assert DigikeyBrowser(str(tmp_path)).running_port() is None

    def test_a_stale_port_file_is_not_trusted(self, tmp_path):
        # A browser that quit leaves the file behind; a dead port is not "running".
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        (tmp_path / digikey_browser.PORT_FILE).write_text(str(dead))
        assert DigikeyBrowser(str(tmp_path)).running_port() is None

    def test_a_live_port_file_is_reused(self, tmp_path):
        (tmp_path / digikey_browser.PORT_FILE).write_text("9333")
        with patch("digikey_browser.devtools_alive", return_value=True):
            assert DigikeyBrowser(str(tmp_path)).running_port() == 9333

    @pytest.mark.parametrize("junk", ["", "not-a-port"])
    def test_a_junk_port_file_is_not_running(self, tmp_path, junk):
        (tmp_path / digikey_browser.PORT_FILE).write_text(junk)
        assert DigikeyBrowser(str(tmp_path)).running_port() is None

    def test_launch_args_pin_a_non_default_profile(self, tmp_path):
        # Chrome 136+ ignores --remote-debugging-port without --user-data-dir.
        args = DigikeyBrowser(str(tmp_path)).launch_args("/x/chrome", 9444)
        assert "--remote-debugging-port=9444" in args
        assert f"--user-data-dir={tmp_path}" in args
        assert not any(a.startswith("--headless") for a in args)  # headless fails Cloudflare

    def test_no_chromium_is_a_loud_distributor_error(self):
        from dubis_errors import DistributorError

        with patch("digikey_browser.find_browser_exe", return_value=None), \
                pytest.raises(DistributorError, match="Chromium"):
            digikey_browser.require_browser_exe()
