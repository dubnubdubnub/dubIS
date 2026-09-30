"""Digikey product-fetching client — session management and public API.

Two fetch backends, picked by platform (`fetch_backend()`):

* ``webview`` (Windows): a hidden pywebview window. It is WebView2, i.e.
  Chromium, and passes DigiKey's Cloudflare check.
* ``cdp`` (macOS, Linux): dubIS's own Chromium window, driven over CDP
  (`digikey_browser.py`). pywebview there is WKWebView or WebKitGTK, and the
  Cloudflare challenge on product pages never clears in WKWebView — verified
  live, hidden and visible alike — while a normal Chromium window clears it in
  seconds.

Neither needs a DigiKey login to read a product page. A session pushed by the
bridge extension is injected into whichever backend is active.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import urllib.error
from typing import Any
from urllib.parse import quote

import digikey_session
from base_client import BaseProductClient
from digikey_browser import DigikeyBrowser, default_profile_dir
from digikey_normalizer import normalize_result
from digikey_scrape_js import SCRAPE_JS
from digikey_session import (
    _await_cf_clearance,
    inject_cookies_to_window,
    load_cookies_from_file,
    save_cookies_to_file,
)
from dubis_errors import DistributorAuthError, DistributorError, DistributorTimeout

logger = logging.getLogger(__name__)


def hidden_window_available() -> bool:
    """Whether this process can drive DigiKey's hidden pywebview window.

    Mirrors `browser_page.available()`'s webview half — minus its CDP branch,
    which does not apply here: this client scrapes through `evaluate_js` on a
    pywebview window it owns, not through a browser running somewhere else.

    pywebview only has a GUI loop once `webview.start()` has been called,
    which the desktop app does and a headless `python -m server` (or the
    container) does not. Without one, `webview.create_window` still returns a
    Window and appends it to `webview.windows`, but nothing ever initializes
    it: the first `load_url` blocks 20s and then raises `WebViewException`,
    which is a plain `Exception` and escapes every narrow handler in this
    module. So availability is a queryable state callers branch on, not a
    failure to discover 35 seconds later.
    """
    try:
        import webview
    except ImportError:
        return False
    return bool(getattr(webview, "windows", None))


def fetch_backend() -> str:
    """``"webview"`` on Windows, ``"cdp"`` everywhere else. See the module docstring."""
    return "webview" if sys.platform == "win32" else "cdp"


# Cookies Cloudflare binds to the browser fingerprint that earned them. Carried
# into a *different* browser they are at best useless, so the CDP backend leaves
# them out and earns its own.
_FINGERPRINT_BOUND_COOKIES = frozenset({"cf_clearance", "__cf_bm"})


class DigikeyClient(BaseProductClient):
    """Manages Digikey browser session, cookie sync, and product scraping."""

    provider = "digikey"

    def __init__(self, cookies_file: str | None = None) -> None:
        super().__init__()
        self._window = None
        self._loaded = threading.Event()
        self._lock = threading.Lock()
        # The active session's cookies, or None when not signed in.
        self._session: list[dict] | None = None
        # Cookies not yet injected into the active fetch backend.
        self._pending_cookies: list[dict] | None = None
        self._cookies_file: str | None = cookies_file
        self._browser = DigikeyBrowser(default_profile_dir(cookies_file))

    # ── Internal helpers ──────────────────────────────────────────────────

    def _ensure_window(self) -> None:
        """Ensure the hidden Digikey webview window exists, creating if needed.

        NOT thread-safe — caller must hold ``_lock``.
        If pending cookies were stored by the login flow, they are injected
        after the window is ready.

        Raises ``DistributorError`` where there is no GUI loop to host the
        window, rather than creating one that can never load. That phantom
        window would still be appended to the process-wide ``webview.windows``
        — which is exactly what `hidden_window_available()` and
        `browser_page.available()` read to decide a loop exists, so creating
        it makes both of them lie from then on.
        """
        if self._window is not None:
            return
        if not hidden_window_available():
            raise DistributorError(
                "DigiKey needs the desktop app's browser window — this process has none",
                provider="digikey",
            )
        import webview

        self._loaded.clear()

        def on_loaded():
            self._loaded.set()

        def on_closing():
            try:
                self._window.hide()
            except (AttributeError, RuntimeError):
                pass
            return False  # Hide instead of destroy

        self._window = webview.create_window(
            "Digikey",
            url="https://www.digikey.com",
            hidden=True,
            width=900,
            height=700,
        )
        self._window.events.loaded += on_loaded
        self._window.events.closing += on_closing
        self._loaded.wait(timeout=15)

        # Inject cookies that were stored during login
        if self._pending_cookies:
            try:
                inject_cookies_to_window(self._window, self._pending_cookies)
                logger.debug("Injected %d pending cookies into dk window", len(self._pending_cookies))
            except Exception as exc:
                logger.warning("Pending cookie injection failed: %s", exc)
            self._pending_cookies = None

    def _set_logged_in(self, cookies: list[dict]) -> None:
        """Store cookies as the active Digikey session and persist to disk."""
        self._session = cookies
        self._pending_cookies = cookies
        save_cookies_to_file(cookies, self._cookies_file)

    def _invalidate_session(self, *, delete_cookies_file: bool) -> None:
        """Mark the in-memory session as not-logged-in.

        Called when a fetch confirms the session is unusable (login redirect
        or persistent Cloudflare challenge). When ``delete_cookies_file`` is
        True (definitive expiration like a login redirect), also remove the
        on-disk cookies so the next app start does not lie about
        ``existing session found``. The injected WebView2 cookies are left
        alone — they live for the WebView session and will be replaced when
        the user re-logs in.
        """
        self._session = None
        self._pending_cookies = None
        if delete_cookies_file and self._cookies_file:
            try:
                os.remove(self._cookies_file)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Failed to remove cookies file: %s", exc)

    def _load_cookies(self) -> list[dict] | None:
        """Load persisted Digikey cookies from disk."""
        return load_cookies_from_file(self._cookies_file)

    # ── Public API ────────────────────────────────────────────────────────

    def validate_session_http(self, cookies: list[dict]) -> bool:
        """Lightweight, no-webview probe of whether a cached session is live.

        See ``digikey_session.validate_session_http`` for the full contract
        (three-state: True / definitively-expired False / raises on
        inconclusive Cloudflare or network errors).
        """
        return digikey_session.validate_session_http(cookies)

    def ensure_session(self) -> bool:
        """Whether the saved session validates over plain HTTP (no browser).

        Inconclusive probe errors (offline / Cloudflare) count as not
        validated; there is no interactive fallback, because signing in now
        happens in the user's own browser through the bridge extension.
        """
        saved = self._load_cookies()
        if not saved:
            return False
        try:
            if self.validate_session_http(saved):
                self._set_logged_in(saved)
                return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.debug("DK ensure_session: validation inconclusive: %s", exc)
        return False

    def check_session(self) -> dict[str, Any]:
        """Report the saved DigiKey session. Called on app startup.

        See ``digikey_session.check_session`` for the full flow.
        """
        return digikey_session.check_session(self)

    def mint_pairing_nonce(self) -> dict[str, Any]:
        """Mint the single-use code the bridge extension must present."""
        return {
            "nonce": digikey_session.mint_nonce(),
            "ttl": int(digikey_session.NONCE_TTL_SECONDS),
        }

    def receive_session(self, nonce: str, cookies: Any) -> dict[str, Any]:
        """Consume *nonce*, check *cookies*, and make them the active session.

        The extension only pushes once DigiKey's account page stops redirecting
        to login in the user's own browser, so the sign-in was checked where it
        can be. The server repeats the check over plain HTTP. A definite
        "redirected to login" rejects the push. An inconclusive probe is
        accepted and reported as ``"unverified"``: Cloudflare binds
        ``cf_clearance`` to the browser that earned it, so a urllib request
        from here is often refused with a 403 even for a perfectly good
        session. JLC rejects on inconclusive instead, because an anonymous JLC
        request mints a session cookie of its own. DigiKey has no such trap.
        """
        if not digikey_session.consume_nonce(nonce):
            raise DistributorAuthError(
                "Pairing code is unknown or expired — click Sign in again to get a new one.",
                provider=self.provider,
            )
        kept = digikey_session.filter_cookies(cookies)
        if not kept:
            raise ValueError("No digikey.com cookies in the request")
        try:
            state = "valid" if self.validate_session_http(kept) else "expired"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.info("DK push: server-side check inconclusive (%s); accepting", exc)
            state = "unverified"
        if state == "expired":
            raise DistributorAuthError(
                "DigiKey says this session is not signed in — sign in at digikey.com, then send again.",
                provider=self.provider,
            )
        # Names only, never values: nobody has recorded which cookies make a
        # DigiKey session, and this is how the extension's allowlist gets narrowed.
        logger.info("DK push accepted (%s): %d cookies: %s",
                    state, len(kept), sorted({c["name"] for c in kept}))
        self._set_logged_in(kept)
        return {"logged_in": True, "state": state, "cookie_count": len(kept)}

    def get_login_status(self) -> dict[str, bool]:
        """Whether a DigiKey session is active in this process. No I/O."""
        return {"logged_in": bool(self._session)}

    def validate_session(self) -> dict[str, Any]:
        """Test whether the current Digikey session actually works.

        On Windows this navigates the hidden webview to a logged-in-only page
        and checks whether it lands there or is redirected to login or stuck
        on a Cloudflare challenge. Elsewhere it probes over plain HTTP
        instead: the CDP backend's window is visible, and opening one on every
        app start just to check a session would be worse than the check is
        worth. On failure, invalidates the in-memory session so subsequent
        ``get_login_status`` calls return ``logged_in=False``.

        Cookie-presence is not enough to know the session is live: cf_clearance
        is fingerprint-bound and dkuhint can be stale on the server side.

        Intended to be called at startup (after ``check_session`` reports
        a session is found) and any other time the UI wants to confirm.
        """
        was_logged_in = bool(self._session)
        if not was_logged_in:
            return {
                "logged_in": False, "changed": False,
                "message": "No saved session to validate",
            }

        if fetch_backend() == "cdp":
            return self._validate_session_over_http()

        if not hidden_window_available():
            # No GUI loop, so the probe cannot navigate anywhere. Inconclusive
            # is the honest answer and the safe one: a session synced onto a
            # headless server is not expired just because nothing here can
            # open a browser to check, and invalidating would delete its
            # cookie file. Without this guard the probe spent 15s waiting on a
            # window that never loads, then 20s inside `load_url`, then raised
            # `WebViewException` past the handler below — a 500 from
            # `POST /v1/distributors/digikey/session/validate`, which the
            # frontend calls at startup whenever cookies are present.
            logger.debug("DK session validation unsupported: no browser window in this process")
            return {
                "logged_in": was_logged_in, "changed": False, "supported": False,
                "message": "No browser window here — session left as-is",
            }

        try:
            ok = self._probe_session()
        except (RuntimeError, OSError) as exc:
            logger.warning("DK session validation error: %s", exc)
            # Inconclusive — keep the session as-is rather than invalidate
            return {
                "logged_in": was_logged_in, "changed": False,
                "message": f"Validation error: {exc}",
            }

        if not ok:
            self._invalidate_session(delete_cookies_file=True)
            return {
                "logged_in": False, "changed": True,
                "message": "Session expired — please re-login",
            }
        return {
            "logged_in": True, "changed": False,
            "message": "Session valid",
        }

    def _validate_session_over_http(self) -> dict[str, Any]:
        """Three-state HTTP validation; only a definite login redirect invalidates."""
        try:
            ok = self.validate_session_http(self._session or [])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.debug("DK session validation inconclusive: %s", exc)
            return {
                "logged_in": True, "changed": False,
                "message": f"Could not check the session ({exc}) — left as-is",
            }
        if not ok:
            self._invalidate_session(delete_cookies_file=True)
            return {
                "logged_in": False, "changed": True,
                "message": "Session expired — please sign in again",
            }
        return {"logged_in": True, "changed": False, "message": "Session valid"}

    def _probe_session(self) -> bool:
        """Navigate to the MyDigiKey page and check we don't end up at sign-in.

        Returns True if the session is usable (lands on the account page),
        False if redirected to login or the Cloudflare challenge persists.
        See ``digikey_session._probe_session`` for the full flow.
        """
        return digikey_session._probe_session(self)

    def logout(self) -> dict[str, str]:
        """Log out of Digikey and clear the product cache."""
        self._session = None
        self._pending_cookies = None
        if fetch_backend() == "cdp":
            try:
                self._browser.clear_cookies()
            except Exception as exc:  # noqa: BLE001 - logout must still finish
                logger.warning("Digikey logout: clearing the browser's cookies failed: %s", exc)
        if self._cookies_file:
            try:
                os.remove(self._cookies_file)
            except FileNotFoundError:
                pass
        if self._window is not None and fetch_backend() == "webview":
            try:
                import System
                from webview.platforms.winforms import BrowserView

                uid = self._window.uid
                instance = BrowserView.instances.get(uid)
                if instance is not None:
                    def _clear():
                        try:
                            cm = instance.browser.webview.CoreWebView2.CookieManager
                            cm.DeleteAllCookies()
                        except Exception as exc:
                            logger.debug("DeleteAllCookies failed: %s", exc)

                    instance.browser.form.Invoke(System.Action(_clear))
                self._loaded.clear()
                self._window.load_url(
                    "https://www.digikey.com/MyDigiKey/Logout"
                )
            except (RuntimeError, AttributeError, ImportError) as exc:
                logger.warning("Digikey logout failed: %s", exc)
        self.clear_cache()
        return {"status": "ok"}

    def _fetch_raw(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Digikey product details by navigating the hidden browser window.

        Navigates to the Digikey search page for *part_number*, waits for the
        page to load, then extracts structured product data (JSON-LD or
        Next.js SSR data) from the rendered DOM.

        Raises ValueError for empty part numbers.
        Raises DistributorTimeout / DistributorError for propagating errors.
        Returns None on soft failures (page load timeout, evaluate_js RuntimeError).
        """
        part_number = str(part_number).strip()
        if not part_number:
            raise ValueError("Part number must not be empty")

        search_url = (
            "https://www.digikey.com/en/products/result?keywords="
            + quote(part_number, safe="")
        )
        if fetch_backend() == "cdp":
            result = self._scrape_over_cdp(part_number, search_url)
        else:
            result = self._scrape_in_webview(part_number, search_url)

        if not result or not isinstance(result, dict):
            logger.debug("DK fetch: no product data for %s", part_number)
            return None

        # Diagnostic envelope — log details and return None
        if result.get("_source") == "diag":
            logger.warning(
                "DK fetch: scrape failed for %s — %s (url=%s, title=%r, "
                "has_jsonld=%s, has_next_data=%s, scripts=%s)",
                part_number,
                result.get("_reason"),
                result.get("_url"),
                result.get("_title"),
                result.get("_hasJsonLd"),
                result.get("_hasNextData"),
                result.get("_scriptCount"),
            )
            return None

        product = normalize_result(result, part_number)
        product["_debug"] = result
        return product

    def _scrape_in_webview(self, part_number: str, search_url: str) -> Any:
        """The Windows backend: navigate the hidden WebView2 window and scrape.

        Returns the raw scrape result, or None on a soft failure.
        """
        with self._lock:
            self._ensure_window()

            logger.debug("DK fetch: loading %s", search_url)
            self._loaded.clear()
            self._window.load_url(search_url)
            if not self._loaded.wait(timeout=15):
                logger.warning("DK fetch: page load timed out for %s", part_number)
                return None

            # Cloudflare interstitial: the `loaded` event fires on the
            # "Just a moment..." challenge page, before CF's JS redirects to
            # the real product page. Poll the title and wait for the challenge
            # to clear (or the URL to leave /products/result).
            title = _await_cf_clearance(self._window)
            if title is None:
                logger.warning(
                    "DK fetch: Cloudflare bot challenge did not resolve in 25s for %s "
                    "— invalidating session",
                    part_number,
                )
                self._invalidate_session(delete_cookies_file=False)
                return None
            logger.debug("DK fetch: CF challenge cleared (title=%r)", title)

            try:
                # Get the final URL to check for redirects (e.g. login page)
                final_url = self._window.evaluate_js("window.location.href") or ""
                logger.debug("DK fetch: final URL = %s", final_url)

                # Detect login/auth redirects
                if digikey_session.is_login_url(final_url) or "/mydigikey" in final_url.lower():
                    logger.warning(
                        "DK fetch: redirected to login page (%s) — session expired, invalidating",
                        final_url,
                    )
                    self._invalidate_session(delete_cookies_file=True)
                    return None

                result = self._window.evaluate_js(SCRAPE_JS)
                logger.debug(
                    "DK fetch: scrape result type=%s, keys=%s",
                    type(result).__name__,
                    list(result.keys()) if isinstance(result, dict) else "N/A",
                )
            except TimeoutError as exc:
                logger.error("DK fetch: timed out for %s: %s", part_number, exc)
                raise DistributorTimeout(
                    f"Digikey fetch timed out for {part_number!r}",
                    provider="digikey",
                    part_number=part_number,
                ) from exc
            except OSError as exc:
                logger.error("DK fetch: OS error for %s: %s", part_number, exc)
                raise DistributorError(
                    f"Digikey fetch OS error for {part_number!r}: {exc}",
                    provider="digikey",
                ) from exc
            except RuntimeError as exc:
                logger.error("DK fetch: evaluate_js failed for %s: %s", part_number, exc)
                return None
        return result

    def _scrape_over_cdp(self, part_number: str, search_url: str) -> Any:
        """The macOS/Linux backend: scrape in dubIS's own Chromium window.

        Returns the raw scrape result, or None on a soft failure. A missing
        browser or a port that never opens raises `DistributorError`, because
        that is a setup problem the user has to fix, not a product that could
        not be found.
        """
        with self._lock:
            if self._pending_cookies:
                self._inject_into_cdp_browser(self._pending_cookies)
                self._pending_cookies = None
            try:
                page = self._browser.visit(search_url, SCRAPE_JS)
            except DistributorError:
                raise
            except TimeoutError as exc:
                raise DistributorTimeout(
                    f"Digikey fetch timed out for {part_number!r}",
                    provider="digikey",
                    part_number=part_number,
                ) from exc
            except Exception as exc:  # noqa: BLE001 - Playwright raises its own types
                logger.warning("DK fetch (cdp): %s failed: %s", part_number, exc)
                return None
        if not page["cleared"]:
            logger.warning("DK fetch (cdp): Cloudflare challenge did not clear for %s", part_number)
            return None
        final_url = (page["url"] or "").lower()
        if digikey_session.is_login_url(final_url) or "/mydigikey" in final_url:
            logger.warning("DK fetch (cdp): redirected to %s — session expired", page["url"])
            self._invalidate_session(delete_cookies_file=True)
            return None
        return page["value"]

    def _inject_into_cdp_browser(self, cookies: list[dict]) -> None:
        """Hand a pushed session to the CDP browser, minus fingerprint-bound cookies."""
        usable = [c for c in cookies if c.get("name") not in _FINGERPRINT_BOUND_COOKIES]
        if not usable:
            return
        try:
            self._browser.add_cookies(usable)
            logger.debug("DK: injected %d cookies into the CDP browser", len(usable))
        except Exception as exc:  # noqa: BLE001 - product pages work without a login
            logger.warning("DK: could not inject the session into the CDP browser: %s", exc)
