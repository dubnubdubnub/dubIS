"""Unit tests for DigikeyClient cache-first session validation.

Covers ``validate_session_http``, ``ensure_session``, and the
``check_session`` saved-cookies wiring. All network / browser / sleep
boundaries are mocked — these tests never hit the network, open a browser,
or sleep for real, so they run under the default (non-``live``) pytest suite.
"""

import urllib.error
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import digikey_session
from digikey_client import DigikeyClient

DKUHINT = {"name": "dkuhint", "value": "1", "domain": ".digikey.com"}
SAVED = [DKUHINT, {"name": "session", "value": "abc", "domain": ".digikey.com"}]


@contextmanager
def _fake_urlopen(geturl, status=200):
    """Build a context-manager mock matching urllib.request.urlopen usage."""
    resp = MagicMock()
    resp.geturl.return_value = geturl
    resp.status = status
    yield resp


@pytest.fixture
def client():
    return DigikeyClient(cookies_file=None)


# ── validate_session_http ────────────────────────────────────────────────


class TestValidateSessionHttp:
    def test_live_session_returns_true(self, client):
        with patch(
            "urllib.request.urlopen",
            return_value=_fake_urlopen("https://www.digikey.com/MyDigiKey/Account", 200),
        ):
            assert client.validate_session_http(SAVED) is True

    def test_login_redirect_returns_false(self, client):
        with patch(
            "urllib.request.urlopen",
            return_value=_fake_urlopen("https://www.digikey.com/login?return=x", 200),
        ):
            assert client.validate_session_http(SAVED) is False

    def test_signin_redirect_returns_false(self, client):
        with patch(
            "urllib.request.urlopen",
            return_value=_fake_urlopen("https://www.digikey.com/signin", 200),
        ):
            assert client.validate_session_http(SAVED) is False

    def test_403_raises_not_false(self, client):
        err = urllib.error.HTTPError(
            url="https://www.digikey.com/MyDigiKey/Account",
            code=403, msg="Forbidden", hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(urllib.error.HTTPError):
                client.validate_session_http(SAVED)

    def test_urlerror_raises(self, client):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with pytest.raises(urllib.error.URLError):
                client.validate_session_http(SAVED)

    def test_timeout_raises(self, client):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            with pytest.raises(TimeoutError):
                client.validate_session_http(SAVED)

    def test_empty_cookies_returns_false(self, client):
        with patch("urllib.request.urlopen") as mock_open:
            assert client.validate_session_http([]) is False
            mock_open.assert_not_called()

    def test_cookies_without_name_value_returns_false(self, client):
        with patch("urllib.request.urlopen") as mock_open:
            assert client.validate_session_http([{"domain": ".digikey.com"}]) is False
            mock_open.assert_not_called()


# ── ensure_session ───────────────────────────────────────────────────────


class TestEnsureSession:
    def test_warm_cache_returns_true_no_browser(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=True), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch.object(client, "start_login") as start_login:
            assert client.ensure_session(interactive=False) is True
            set_logged.assert_called_once_with(SAVED)
            start_login.assert_not_called()

    def test_inconclusive_non_interactive_returns_false(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http",
                             side_effect=urllib.error.URLError("offline")), \
                patch.object(client, "start_login") as start_login:
            assert client.ensure_session(interactive=False) is False
            start_login.assert_not_called()

    def test_interactive_stale_cache_logs_in(self, client):
        with patch.object(client, "_load_cookies", return_value=None), \
                patch.object(client, "start_login") as start_login, \
                patch.object(client, "sync_cookies", return_value={"logged_in": True}), \
                patch("digikey_client.time.sleep") as sleep, \
                patch("digikey_client.time.time", side_effect=[0.0, 1.0, 2.0]):
            assert client.ensure_session(interactive=True) is True
            start_login.assert_called_once()
            sleep.assert_not_called()

    def test_interactive_timeout_returns_false(self, client):
        # time.time advances past the 120s deadline immediately.
        with patch.object(client, "_load_cookies", return_value=None), \
                patch.object(client, "start_login") as start_login, \
                patch.object(client, "sync_cookies", return_value={"logged_in": False}), \
                patch("digikey_client.time.sleep"), \
                patch("digikey_client.time.time", side_effect=[0.0, 1000.0, 1000.0]):
            assert client.ensure_session(interactive=True) is False
            start_login.assert_called_once()


# ── check_session wiring ─────────────────────────────────────────────────


class TestCheckSessionWiring:
    def test_validated_saved_session(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=True), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch("subprocess.Popen") as popen:
            result = client.check_session()
            assert result["logged_in"] is True
            assert result["message"] == "Validated saved session"
            set_logged.assert_called_once_with(SAVED)
            popen.assert_not_called()

    def test_inconclusive_keeps_saved_session(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http",
                             side_effect=urllib.error.URLError("offline")), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch("subprocess.Popen") as popen:
            result = client.check_session()
            assert result["logged_in"] is True
            assert result["message"] == "Loaded saved session"
            set_logged.assert_called_once_with(SAVED)
            popen.assert_not_called()

    def test_expired_falls_through_to_headless(self, client):
        # validate -> False must NOT short-circuit to logged_in True; it falls
        # through to the headless path. With no browser exe, that path returns
        # the existing "No browser found" dict. Platform pinned to win32
        # because the headless CDP path is the Windows one — off Windows
        # `check_session` stops at the unsupported branch below and never
        # reaches `find_default_browser_exe`.
        with patch("digikey_session.sys.platform", "win32"), \
                patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=False), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch("digikey_session.find_default_browser_exe", return_value=None):
            result = client.check_session()
            assert result["logged_in"] is False
            assert result["message"] == "No browser found"
            set_logged.assert_not_called()


# ── Non-Windows platforms ────────────────────────────────────────────────
# `find_default_browser_exe` reads the Windows registry, and its bare
# `import winreg` used to raise ModuleNotFoundError — an ImportError, not the
# OSError the handler caught — so it escaped through `check_session` and made
# GET /v1/distributors/digikey/session a 500 on every macOS/Linux launch.
# These pin the truthful non-Windows answer. The platform is monkeypatched
# rather than skipped, so both halves run on every OS.


class TestPlatformSupport:
    def test_cdp_login_available_only_on_windows(self):
        for platform, expected in (("win32", True), ("darwin", False), ("linux", False)):
            with patch("digikey_session.sys.platform", platform):
                assert digikey_session.cdp_login_available() is expected

    def test_find_browser_exe_returns_none_off_windows(self):
        # None == "no browser resolved", the same member of `str | None` the
        # Windows path returns when the registry names no usable exe.
        for platform in ("darwin", "linux"):
            with patch("digikey_session.sys.platform", platform):
                assert digikey_session.find_default_browser_exe() is None

    def test_find_browser_exe_never_touches_registry_off_windows(self):
        # The guard must come *before* the import, not rely on catching it.
        with patch("digikey_session.sys.platform", "darwin"), \
                patch.dict("sys.modules", {"winreg": None}):
            assert digikey_session.find_default_browser_exe() is None

    def test_missing_winreg_on_windows_warns_and_returns_none(self, caplog):
        # The one case that is genuinely unexpected: a Windows build with no
        # `winreg`. Loud (warning), still no crash.
        with patch("digikey_session.sys.platform", "win32"), \
                patch.dict("sys.modules", {"winreg": None}), \
                caplog.at_level("WARNING", logger="digikey_session"):
            assert digikey_session.find_default_browser_exe() is None
        assert any("winreg unavailable" in r.message for r in caplog.records)

    def test_check_session_reports_unsupported_off_windows(self, client):
        # The regression: no 500, and the response never claims logged_in.
        with patch("digikey_session.sys.platform", "darwin"), \
                patch.object(client, "_load_cookies", return_value=None), \
                patch("subprocess.Popen") as popen, \
                patch("digikey_session.cdp_get_cookies") as cdp:
            result = client.check_session()
        assert result["logged_in"] is False
        assert result["supported"] is False
        assert "Windows-only" in result["message"]
        popen.assert_not_called()
        cdp.assert_not_called()

    def test_check_session_off_windows_still_uses_saved_cookies(self, client):
        # The platform guard sits *after* the cached-cookie path, so a synced
        # session keeps working on macOS/Linux — the unsupported branch is
        # only about launching a browser.
        with patch("digikey_session.sys.platform", "darwin"), \
                patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=True), \
                patch.object(client, "_set_logged_in") as set_logged:
            result = client.check_session()
        assert result["logged_in"] is True
        assert "supported" not in result
        set_logged.assert_called_once_with(SAVED)

    def test_start_login_falls_back_to_webbrowser_off_windows(self, client):
        with patch("digikey_session.sys.platform", "darwin"), \
                patch("webbrowser.open") as wb_open, \
                patch("subprocess.Popen") as popen:
            result = client.start_login()
        assert result == {"status": "opened", "cdp": False,
                          "message": "Browser opened (no CDP)"}
        wb_open.assert_called_once()
        popen.assert_not_called()
        assert client._sync_result["logged_in"] is False
        assert "cookie sync unavailable" in client._sync_result["message"]
