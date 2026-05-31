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
        # the existing "No browser found" dict.
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=False), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch("digikey_client.find_default_browser_exe", return_value=None):
            result = client.check_session()
            assert result["logged_in"] is False
            assert result["message"] == "No browser found"
            set_logged.assert_not_called()
