"""Unit tests for DigikeyClient cache-first session validation.

Covers ``validate_session_http``, ``ensure_session``, ``check_session``, the
pairing nonces, cookie filtering, and ``receive_session`` (the extension push). All network / browser / sleep
boundaries are mocked — these tests never hit the network, open a browser,
or sleep for real, so they run under the default (non-``live``) pytest suite.
"""

import urllib.error
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import digikey_client
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

    def test_the_real_signed_out_redirect_returns_false(self, client):
        # Observed live 2026-09-29: an OAuth authorize URL on auth.digikey.com,
        # with neither "/login" nor "/signin" in it.
        url = ("https://auth.digikey.com/as/authorization.oauth2?response_type=code"
               "&vnd_pi_requested_resource=https%3A%2F%2Fwww.digikey.com%2FMyDigiKey%2FAccount")
        with patch("urllib.request.urlopen", return_value=_fake_urlopen(url, 200)):
            assert client.validate_session_http(SAVED) is False

    def test_401_returns_false_not_inconclusive(self, client):
        # Also observed live: a plain request with no session gets 401 on the
        # account page. Cloudflare's refusals are 403, which stay inconclusive.
        err = urllib.error.HTTPError(
            url="https://www.digikey.com/MyDigiKey/Account",
            code=401, msg="Unauthorized", hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
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
                patch.object(client, "_set_logged_in") as set_logged:
            assert client.ensure_session() is True
            set_logged.assert_called_once_with(SAVED)

    def test_inconclusive_returns_false(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http",
                             side_effect=urllib.error.URLError("offline")):
            assert client.ensure_session() is False

    def test_no_saved_session_returns_false(self, client):
        with patch.object(client, "_load_cookies", return_value=None):
            assert client.ensure_session() is False


# ── check_session ────────────────────────────────────────────────────────


class TestCheckSession:
    def test_validated_saved_session(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=True), \
                patch.object(client, "_set_logged_in") as set_logged, \
                patch("subprocess.Popen") as popen:
            result = client.check_session()
        assert result == {"logged_in": True, "message": "Validated saved session"}
        set_logged.assert_called_once_with(SAVED)
        popen.assert_not_called()

    def test_inconclusive_keeps_saved_session(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http",
                             side_effect=urllib.error.URLError("offline")), \
                patch.object(client, "_set_logged_in") as set_logged:
            result = client.check_session()
        assert result["logged_in"] is True
        assert result["message"] == "Loaded saved session"
        set_logged.assert_called_once_with(SAVED)

    def test_expired_is_not_logged_in(self, client):
        with patch.object(client, "_load_cookies", return_value=SAVED), \
                patch.object(client, "validate_session_http", return_value=False), \
                patch.object(client, "_set_logged_in") as set_logged:
            result = client.check_session()
        assert result["logged_in"] is False
        assert "expired" in result["message"]
        set_logged.assert_not_called()

    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_no_saved_session_is_a_truthful_dict_on_every_platform(self, client, platform):
        # The regression this replaces: macOS/Linux used to answer
        # "Windows-only" here, and before that a 500. Signing in now happens in
        # the user's own browser, so no platform launches anything at startup.
        with patch("sys.platform", platform), \
                patch.object(client, "_load_cookies", return_value=None), \
                patch("subprocess.Popen") as popen:
            result = client.check_session()
        assert result["logged_in"] is False
        assert "extension" in result["message"]
        popen.assert_not_called()


# ── Pairing nonces ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_nonces():
    digikey_session._nonces.clear()
    yield
    digikey_session._nonces.clear()


class TestNonces:
    def test_nonce_carries_the_digikey_prefix(self):
        # The extension routes a pasted code by this prefix.
        assert digikey_session.mint_nonce().startswith("DK-")

    def test_single_use(self):
        nonce = digikey_session.mint_nonce()
        assert digikey_session.consume_nonce(nonce) is True
        assert digikey_session.consume_nonce(nonce) is False

    def test_unknown_and_empty_are_refused(self):
        digikey_session.mint_nonce()
        assert digikey_session.consume_nonce("DK-not-real") is False
        assert digikey_session.consume_nonce("") is False

    def test_expiry(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(digikey_session, "_monotonic", lambda: clock[0])
        nonce = digikey_session.mint_nonce(ttl=600)
        clock[0] += 601
        assert digikey_session.consume_nonce(nonce) is False

    def test_a_jlc_nonce_is_not_a_digikey_nonce(self):
        import jlc_session
        jlc = jlc_session.mint_nonce()
        try:
            assert digikey_session.consume_nonce(jlc) is False
        finally:
            jlc_session._nonces.clear()


# ── filter_cookies ───────────────────────────────────────────────────────


class TestFilterCookies:
    def test_keeps_digikey_and_subdomains_only(self):
        kept = digikey_session.filter_cookies([
            {"name": "a", "value": "1", "domain": ".digikey.com"},
            {"name": "b", "value": "2", "domain": "www.digikey.com"},
            {"name": "c", "value": "3", "domain": "digikey.com"},
            {"name": "evil", "value": "x", "domain": ".notdigikey.com"},
            {"name": "evil2", "value": "x", "domain": "digikey.com.evil.example"},
            {"name": "jlc", "value": "x", "domain": ".jlcpcb.com"},
        ])
        assert [c["name"] for c in kept] == ["a", "b", "c"]

    def test_maps_the_chrome_shape(self):
        [c] = digikey_session.filter_cookies([{
            "name": "dkuhint", "value": "v", "domain": ".digikey.com", "path": "/",
            "secure": True, "httpOnly": True, "expirationDate": 1790000000.5,
            "sameSite": "lax", "storeId": "0",
        }])
        assert c == {"name": "dkuhint", "value": "v", "domain": ".digikey.com", "path": "/",
                     "secure": True, "httpOnly": True, "expires": 1790000000.5}

    @pytest.mark.parametrize("bad", [None, "x", {}, [None], [{"name": "", "value": "v", "domain": ".digikey.com"}],
                                     [{"name": "n", "value": 5, "domain": ".digikey.com"}]])
    def test_junk_is_dropped(self, bad):
        assert digikey_session.filter_cookies(bad) == []


# ── receive_session (the extension push) ─────────────────────────────────


PUSHED = [{"name": "dkuhint", "value": "1", "domain": ".digikey.com", "path": "/"},
          {"name": "other", "value": "2", "domain": ".jlcpcb.com"}]


class TestReceiveSession:
    def test_valid_push_logs_in_and_persists(self, tmp_path):
        c = DigikeyClient(cookies_file=str(tmp_path / "dk.json"))
        nonce = c.mint_pairing_nonce()["nonce"]
        with patch.object(c, "validate_session_http", return_value=True):
            result = c.receive_session(nonce, PUSHED)
        assert result == {"logged_in": True, "state": "valid", "cookie_count": 1}
        assert c.get_login_status() == {"logged_in": True}
        assert [x["name"] for x in c._load_cookies()] == ["dkuhint"]

    def test_inconclusive_check_is_accepted_as_unverified(self, client):
        nonce = client.mint_pairing_nonce()["nonce"]
        err = urllib.error.HTTPError("u", 403, "Forbidden", None, None)
        with patch.object(client, "validate_session_http", side_effect=err):
            result = client.receive_session(nonce, PUSHED)
        assert result["state"] == "unverified"
        assert client.get_login_status() == {"logged_in": True}

    def test_login_redirect_rejects(self, client):
        from dubis_errors import DistributorAuthError
        nonce = client.mint_pairing_nonce()["nonce"]
        with patch.object(client, "validate_session_http", return_value=False), \
                pytest.raises(DistributorAuthError, match="not signed in"):
            client.receive_session(nonce, PUSHED)
        assert client.get_login_status() == {"logged_in": False}

    def test_bad_nonce_rejects_before_any_check(self, client):
        from dubis_errors import DistributorAuthError
        with patch.object(client, "validate_session_http") as check, \
                pytest.raises(DistributorAuthError, match="expired"):
            client.receive_session("DK-nope", PUSHED)
        check.assert_not_called()

    def test_nonce_is_single_use_across_pushes(self, client):
        from dubis_errors import DistributorAuthError
        nonce = client.mint_pairing_nonce()["nonce"]
        with patch.object(client, "validate_session_http", return_value=True):
            client.receive_session(nonce, PUSHED)
            with pytest.raises(DistributorAuthError):
                client.receive_session(nonce, PUSHED)

    def test_no_digikey_cookies_is_a_value_error(self, client):
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(ValueError, match="digikey.com"):
            client.receive_session(nonce, [{"name": "x", "value": "y", "domain": ".jlcpcb.com"}])

    def test_the_response_never_carries_a_cookie_value(self, client):
        nonce = client.mint_pairing_nonce()["nonce"]
        with patch.object(client, "validate_session_http", return_value=True):
            result = client.receive_session(nonce, [{"name": "s", "value": "SECRET-VALUE",
                                                     "domain": ".digikey.com"}])
        assert "SECRET-VALUE" not in repr(result)


# ── Fetch backend selection ──────────────────────────────────────────────


class TestIsLoginUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://auth.digikey.com/as/authorization.oauth2?x=1", True),
        ("https://www.digikey.com/MyDigiKey/Login?ReturnUrl=x", True),
        ("https://www.digikey.com/signin", True),
        ("https://www.digikey.com/MyDigiKey/Account", False),
        ("https://www.digikey.com/en/products/detail/yageo/RC0402FR-0710KL/726523", False),
        ("", False),
    ])
    def test_cases(self, url, expected):
        assert digikey_session.is_login_url(url) is expected


class TestFetchBackend:
    @pytest.mark.parametrize("platform,expected", [("win32", "webview"), ("darwin", "cdp"),
                                                   ("linux", "cdp")])
    def test_by_platform(self, platform, expected):
        with patch("digikey_client.sys.platform", platform):
            assert digikey_client.fetch_backend() == expected

    def test_cdp_validation_never_opens_a_browser(self, client):
        # On macOS the fetch browser is a visible window; startup validation
        # must not open one. It probes over HTTP instead.
        client._session = SAVED
        with patch("digikey_client.fetch_backend", return_value="cdp"), \
                patch.object(client, "validate_session_http", return_value=True), \
                patch.object(client._browser, "ensure") as ensure, \
                patch.object(client, "_probe_session") as probe:
            result = client.validate_session()
        assert result == {"logged_in": True, "changed": False, "message": "Session valid"}
        ensure.assert_not_called()
        probe.assert_not_called()

    def test_cdp_validation_inconclusive_keeps_the_session(self, client):
        client._session = SAVED
        with patch("digikey_client.fetch_backend", return_value="cdp"), \
                patch.object(client, "validate_session_http",
                             side_effect=urllib.error.URLError("offline")), \
                patch.object(client, "_invalidate_session") as invalidate:
            result = client.validate_session()
        assert result["logged_in"] is True
        invalidate.assert_not_called()

    def test_cdp_validation_expired_invalidates(self, client):
        client._session = SAVED
        with patch("digikey_client.fetch_backend", return_value="cdp"), \
                patch.object(client, "validate_session_http", return_value=False):
            result = client.validate_session()
        assert result["changed"] is True and result["logged_in"] is False
        assert client.get_login_status() == {"logged_in": False}


# ── Through the real facade stack ────────────────────────────────────────


class TestFacadeWiring:
    """InventoryApi -> DistributorFacade -> DistributorManager -> DigikeyClient.

    A live run caught `create_digikey_pairing` missing from the middle layer,
    a 500 that every mocked-facade test in the suite walked straight past. So
    this goes through the real stack end to end, with only DigiKey's own HTTP
    check stubbed.
    """

    def test_pair_then_push_through_inventory_api(self, tmp_path):
        from tests.python.helpers import make_api

        api = make_api(tmp_path)
        try:
            nonce = api.create_digikey_pairing()["nonce"]
            assert nonce.startswith("DK-")
            with patch("digikey_session.validate_session_http", return_value=True):
                result = api.receive_digikey_session(
                    nonce, [{"name": "s", "value": "v", "domain": ".digikey.com"}])
            assert result == {"logged_in": True, "state": "valid", "cookie_count": 1}
            assert api.get_digikey_login_status() == {"logged_in": True}
        finally:
            api.shutdown()
