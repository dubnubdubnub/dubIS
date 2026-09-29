"""DigiKey session helpers: pairing nonces, cookie intake and I/O, validation.

HOW A SESSION ARRIVES
The user signs in to DigiKey in their own browser, so saved-password autofill
and SSO work, and the dubIS bridge extension (`extension/jlc-bridge/`) pushes
that session here. dubIS mints a single-use pairing code, the user pastes it
into the extension popup, the extension waits until DigiKey's account page
stops redirecting to login, and then POSTs the digikey.com cookies with the
code. The receive route is loopback-only, and the code is what proves a human
started the push (threat-model rules 3 and 5 in
docs/plans/2026-09-20-extension-credential-capture.md).

This replaced launching a browser with `--remote-debugging-port`. Chrome 136+
ignores that switch on the default profile, so the old flow waited forever, and
a dedicated profile would have lost autofill.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import secret_store

if TYPE_CHECKING:
    from digikey_client import DigikeyClient

logger = logging.getLogger(__name__)

#: Pairing codes carry this prefix so the extension popup can tell a DigiKey
#: code from a JLC one without asking the user which distributor it is for.
NONCE_PREFIX = "DK-"

#: Ten minutes, for the same reason as JLC's: the TTL spans a human sign-in
#: (paste, autofill, maybe 2FA), not a machine handshake. Single-use is the
#: property that makes it safe, not its length.
NONCE_TTL_SECONDS = 600

#: The site the extension may push cookies for. Anything else in a request is
#: dropped here, whatever the sender claims.
COOKIE_DOMAIN_SUFFIX = "digikey.com"

# Indirection so tests can drive expiry without sleeping.
_monotonic = time.monotonic

# In-memory only: a nonce that outlived its process would be a durable bearer
# token for pushing credentials at this server.
_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()


def mint_nonce(ttl: float = NONCE_TTL_SECONDS) -> str:
    """Mint a single-use pairing nonce valid for *ttl* seconds."""
    nonce = NONCE_PREFIX + secrets.token_urlsafe(24)
    with _nonce_lock:
        _prune_locked()
        _nonces[nonce] = _monotonic() + ttl
    return nonce


def consume_nonce(nonce: str) -> bool:
    """Redeem *nonce*. True exactly once, for an unexpired nonce."""
    if not nonce:
        return False
    with _nonce_lock:
        _prune_locked()
        return _nonces.pop(nonce, None) is not None


def _prune_locked() -> None:
    now = _monotonic()
    for nonce, expiry in list(_nonces.items()):
        if expiry <= now:
            del _nonces[nonce]


def filter_cookies(cookies: Any) -> list[dict[str, Any]]:
    """Keep only well-formed digikey.com cookies, normalized.

    Accepts what `chrome.cookies.getAll` returns (`expirationDate`, `httpOnly`)
    and emits the shape the rest of this module has always stored (`expires`).
    A cookie for any other domain is dropped, so a buggy or hostile sender
    cannot park an arbitrary jar in the data dir.
    """
    if not isinstance(cookies, list):
        return []
    kept: list[dict[str, Any]] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name, value = c.get("name"), c.get("value")
        domain = str(c.get("domain") or "")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            continue
        bare = domain.lstrip(".").lower()
        if bare != COOKIE_DOMAIN_SUFFIX and not bare.endswith("." + COOKIE_DOMAIN_SUFFIX):
            continue
        out: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly") or c.get("httponly")),
        }
        expires = c.get("expirationDate", c.get("expires"))
        if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires > 0:
            out["expires"] = float(expires)
        kept.append(out)
    return kept


def is_login_url(url: str) -> bool:
    """Whether *url* is where DigiKey sends someone who is not signed in.

    Verified live on 2026-09-29: a signed-out visit to /MyDigiKey/Account ends on
    ``https://auth.digikey.com/as/authorization.oauth2?...`` (title "Login"),
    which contains neither ``/login`` nor ``/signin``. The older rule matched
    only those two, so it read every signed-out session as signed in.
    """
    from urllib.parse import urlsplit

    lowered = (url or "").lower()
    host = urlsplit(lowered).hostname or ""
    return (
        host == "auth.digikey.com"
        or "authorization.oauth2" in lowered
        or "/login" in lowered
        or "/signin" in lowered
    )


def save_cookies_to_file(cookies: list[dict], cookies_file: str | None) -> None:
    """Persist Digikey cookies to disk, owner-readable only (`0600`).

    This file IS a live DigiKey session — anyone who can read it is logged in
    as the user. It used to be written at the process umask's default, i.e.
    world-readable; `secret_store.write_private_json` is the one place all
    three credential files now get their mode from
    (`docs/plans/2026-09-20-extension-credential-capture.md` rule 8).
    """
    if not cookies_file:
        return
    try:
        secret_store.write_private_json(cookies_file, cookies)
    except Exception as exc:
        logger.warning("Failed to save cookies: %s", exc)


def load_cookies_from_file(cookies_file: str | None) -> list[dict] | None:
    """Load persisted Digikey cookies from disk."""
    if not cookies_file:
        return None
    try:
        with open(cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if isinstance(cookies, list) and cookies:
            return cookies
    except FileNotFoundError:
        logger.debug("No saved cookies file found")
    except json.JSONDecodeError as exc:
        logger.warning("Corrupt cookies file: %s", exc)
    return None


def _await_cf_clearance(window: Any, timeout: float = 25.0) -> str | None:
    """Poll ``document.title`` until the Cloudflare "Just a moment"
    interstitial clears, or *timeout* seconds elapse.

    Shared by the account-page session probe and the product-fetch path,
    which both navigate the hidden webview and must wait out the same
    Cloudflare bot-challenge interstitial before reading the resulting page.

    Returns the last-seen (non-interstitial) title once cleared. Returns
    ``None`` if the challenge is still showing when *timeout* expires.
    """
    deadline = time.time() + timeout
    title = ""
    while time.time() < deadline:
        try:
            title = window.evaluate_js("document.title") or ""
        except RuntimeError:
            title = ""
        if title and "Just a moment" not in title:
            return title
        time.sleep(0.5)
    return None


def validate_session_http(cookies: list[dict]) -> bool:
    """Lightweight, no-webview probe of whether a cached session is live.

    Builds a ``Cookie:`` header from *cookies* (name=value pairs where both
    are present) and HTTP GETs the MyDigiKey account page with a
    browser-like User-Agent. urllib follows redirects by default.

    Three-state contract — ``cf_clearance`` is fingerprint-bound, so a
    plain urllib request can be blocked by Cloudflare (HTTP 403) even when
    the session is perfectly valid. A 403 therefore must NOT be read as
    "expired":

    - Returns ``True`` when the response lands on the account page
      (HTTP 200 and the FINAL url is not a sign-in page, see `is_login_url`).
    - Returns ``False`` ONLY on a definitive expiry signal: the final url is a
      sign-in page, or DigiKey answers HTTP 401. Verified live on 2026-09-29:
      a plain request with no session gets 401 on the account page itself,
      while Cloudflare's refusals are 403. Empty/no cookies also returns
      ``False``.
    - RAISES on inconclusive cases — HTTP 403 / other ``HTTPError``,
      ``URLError``, ``TimeoutError``, socket errors — rather than swallowing
      them into ``False``. The caller decides how to treat "don't know".
    """
    if not cookies:
        return False
    pairs = [
        f"{c['name']}={c['value']}"
        for c in cookies
        if c.get("name") and c.get("value")
    ]
    if not pairs:
        return False
    cookie_header = "; ".join(pairs)

    url = "https://www.digikey.com/MyDigiKey/Account"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_header,
    }
    req = urllib.request.Request(url, headers=headers)
    # Inconclusive errors (HTTPError incl. 403, URLError, TimeoutError,
    # socket errors) propagate to the caller — do NOT catch them here. The
    # one HTTPError that is an answer rather than a failure is 401.
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = (resp.geturl() or "").lower()
            status = getattr(resp, "status", None)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            logger.debug("DK http validate: 401 from the account page — session expired")
            return False
        raise

    if is_login_url(final_url):
        logger.debug("DK http validate: redirected to %s — session expired", final_url)
        return False
    if status == 200:
        logger.debug("DK http validate: session valid (final url=%s)", final_url)
        return True
    # 200-but-not-login is the only True case; anything else here is a
    # non-definitive response — treat as inconclusive.
    raise urllib.error.URLError(f"unexpected status {status} for {final_url}")


def check_session(client: "DigikeyClient") -> dict[str, Any]:
    """Report the saved DigiKey session at startup.

    The only source is the cookie file the extension's push wrote. Validated
    over plain HTTP so an expired session does not masquerade as logged-in;
    an inconclusive probe (offline, Cloudflare 403) keeps it, because a probe
    that could not run is not evidence of expiry.
    """
    saved = client._load_cookies()
    if not saved:
        return {"logged_in": False, "message": "Not signed in — pair the dubIS extension in Preferences"}
    try:
        validated = client.validate_session_http(saved)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Startup: session validation inconclusive: %s", exc)
        client._set_logged_in(saved)
        return {"logged_in": True, "message": "Loaded saved session"}
    if validated:
        client._set_logged_in(saved)
        logger.debug("Startup: validated saved session (%d cookies)", len(saved))
        return {"logged_in": True, "message": "Validated saved session"}
    logger.debug("Startup: saved session expired")
    return {"logged_in": False, "message": "Saved DigiKey session expired — sign in again"}


def _probe_session(client: "DigikeyClient") -> bool:
    """Navigate to MyDigiKey/Account and check we don't end up at /login.

    Returns True if the session is usable (lands on the account page),
    False if redirected to login or the Cloudflare challenge persists.
    """
    with client._lock:
        client._ensure_window()
        probe_url = "https://www.digikey.com/MyDigiKey/Account"
        client._loaded.clear()
        client._window.load_url(probe_url)
        if not client._loaded.wait(timeout=15):
            logger.warning("DK probe: page load timed out")
            return False

        if _await_cf_clearance(client._window) is None:
            logger.warning("DK probe: Cloudflare challenge did not clear")
            return False

        try:
            final_url = client._window.evaluate_js("window.location.href") or ""
        except RuntimeError:
            return False

        url_lower = final_url.lower()
        if is_login_url(url_lower):
            logger.warning("DK probe: redirected to %s — session expired", final_url)
            return False

        logger.debug("DK probe: session valid (final url=%s)", final_url)
        return True


def inject_cookies_to_window(window: Any, cookies: list[dict]) -> int:
    """Inject cookie dicts into the WebView2 session via CookieManager.

    All WebView2 access (CookieManager, CreateCookie, AddOrUpdateCookie)
    must happen on the UI thread, so the entire operation is marshaled
    via a single Invoke() call.
    """
    if window is None:
        raise RuntimeError("Digikey window not created")

    import System
    from webview.platforms.winforms import BrowserView

    uid = window.uid
    instance = BrowserView.instances.get(uid)
    if instance is None:
        raise RuntimeError("BrowserView instance not found")
    browser_form = instance.browser.form

    result = {"injected": 0, "error": None}

    def _inject_all():
        try:
            cookie_mgr = instance.browser.webview.CoreWebView2.CookieManager
            for c in cookies:
                name = c.get("name", "")
                if not name:
                    continue
                value = c.get("value", "")
                domain = c.get("domain", "")
                path = c.get("path", "/")
                try:
                    wv2_cookie = cookie_mgr.CreateCookie(name, value, domain, path)
                    wv2_cookie.IsHttpOnly = bool(c.get("httpOnly") or c.get("is_httponly"))
                    wv2_cookie.IsSecure = bool(c.get("secure") or c.get("is_secure"))
                    expires = c.get("expires")
                    if expires and float(expires) > 0:
                        epoch = System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc)
                        wv2_cookie.Expires = epoch.AddSeconds(float(expires))
                    cookie_mgr.AddOrUpdateCookie(wv2_cookie)
                    result["injected"] += 1
                except Exception as exc:
                    logger.debug("Failed to inject cookie %s: %s", name, exc)
        except Exception as exc:
            result["error"] = str(exc)

    browser_form.Invoke(System.Action(_inject_all))

    if result["error"]:
        raise RuntimeError(result["error"])
    return result["injected"]
