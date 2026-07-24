"""Digikey product-fetching client — session management and public API."""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
from typing import Any
from urllib.parse import quote

import digikey_session
from base_client import BaseProductClient
from digikey_cdp import cdp_get_cookies
from digikey_normalizer import normalize_result
from digikey_scrape_js import SCRAPE_JS
from digikey_session import (
    _await_cf_clearance,
    check_cookies_logged_in,
    find_default_browser_exe,
    inject_cookies_to_window,
    load_cookies_from_file,
    poll_cdp_for_cookies,
    save_cookies_to_file,
)
from dubis_errors import DistributorError, DistributorTimeout

logger = logging.getLogger(__name__)


class DigikeyClient(BaseProductClient):
    """Manages Digikey browser session, cookie sync, and product scraping."""

    provider = "digikey"

    def __init__(self, cookies_file: str | None = None) -> None:
        super().__init__()
        self._window = None
        self._loaded = threading.Event()
        self._lock = threading.Lock()
        self._cdp_port: int | None = None
        self._sync_result: dict[str, Any] = {}
        self._poll_stop = threading.Event()
        self._pending_cookies: list[dict] | None = None
        self._cookies_file: str | None = cookies_file

    # ── Internal helpers ──────────────────────────────────────────────────

    def _ensure_window(self) -> None:
        """Ensure the hidden Digikey webview window exists, creating if needed.

        NOT thread-safe — caller must hold ``_lock``.
        If pending cookies were stored by the login flow, they are injected
        after the window is ready.
        """
        if self._window is not None:
            return
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

    # ── Backward-compatible shims (delegate to extracted modules) ────────

    @staticmethod
    def _find_default_browser_exe() -> str | None:
        return find_default_browser_exe()

    @staticmethod
    def _check_cookies_logged_in(cookies: list[dict]) -> bool:
        return check_cookies_logged_in(cookies)

    _normalize_result = staticmethod(normalize_result)
    _cdp_get_cookies = staticmethod(cdp_get_cookies)

    def _set_logged_in(self, cookies: list[dict]) -> None:
        """Store cookies as the active Digikey session and persist to disk."""
        self._pending_cookies = cookies
        self._sync_result = {
            "status": "ok",
            "message": "Logged in",
            "logged_in": True,
            "cookies_injected": len(cookies),
            "browser": "cdp",
        }
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
        self._pending_cookies = None
        self._sync_result = {
            "status": "expired",
            "message": "Session expired — please re-login",
            "logged_in": False,
            "cookies_injected": 0,
        }
        if delete_cookies_file and self._cookies_file:
            try:
                os.remove(self._cookies_file)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Failed to remove cookies file: %s", exc)

    def _save_cookies(self, cookies: list[dict]) -> None:
        """Persist Digikey cookies to disk."""
        save_cookies_to_file(cookies, self._cookies_file)

    def _load_cookies(self) -> list[dict] | None:
        """Load persisted Digikey cookies from disk."""
        return load_cookies_from_file(self._cookies_file)

    def _poll_loop(self, port: int) -> None:
        """Background thread: poll CDP for cookies, store when found."""
        poll_cdp_for_cookies(
            port=port,
            poll_stop=self._poll_stop,
            on_logged_in=self._set_logged_in,
            sync_result=self._sync_result,
        )

    def _inject_cookies_to_window(self, cookies: list[dict]) -> int:
        """Inject cookie dicts into the WebView2 session via CookieManager."""
        return inject_cookies_to_window(self._window, cookies)

    # ── Public API ────────────────────────────────────────────────────────

    def validate_session_http(self, cookies: list[dict]) -> bool:
        """Lightweight, no-webview probe of whether a cached session is live.

        See ``digikey_session.validate_session_http`` for the full contract
        (three-state: True / definitively-expired False / raises on
        inconclusive Cloudflare or network errors).
        """
        return digikey_session.validate_session_http(cookies)

    def ensure_session(self, interactive: bool = False) -> bool:
        """Cache-first session orchestrator.

        1. Validate saved cookies over plain HTTP (no webview). If they
           validate, mark them as the active session and return ``True``.
           Inconclusive probe errors (offline / Cloudflare) are treated as
           "not validated" — fall through rather than crash.
        2. If ``not interactive``, return ``False`` without opening a browser.
        3. Interactive: launch the visible login browser and poll
           ``sync_cookies`` for up to ~120s until login succeeds.
        """
        saved = self._load_cookies()
        if saved:
            try:
                if self.validate_session_http(saved):
                    self._set_logged_in(saved)
                    return True
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Inconclusive (offline / Cloudflare) — not validated, but do
                # not crash. Fall through to the interactive path if allowed.
                logger.debug("DK ensure_session: validation inconclusive: %s", exc)

        if not interactive:
            return False

        self.start_login()
        deadline = time.time() + 120.0
        while time.time() < deadline:
            if self.sync_cookies().get("logged_in"):
                return True
            time.sleep(2)
        return bool(self.sync_cookies().get("logged_in"))

    def check_session(self) -> dict[str, Any]:
        """Check if there's an existing Digikey session.

        Tries saved cookies first, then launches the browser headless
        with CDP to read fresh cookies. Called on app startup.
        See ``digikey_session.check_session`` for the full flow.
        """
        return digikey_session.check_session(self)

    def start_login(self) -> dict[str, Any]:
        """Launch the default browser with CDP enabled and open the login page.

        Starts a background thread that polls CDP for cookies so that
        ``sync_cookies`` can return instantly with no I/O.
        See ``digikey_session.start_login`` for the full flow.
        """
        return digikey_session.start_login(self)

    def sync_cookies(self) -> dict[str, Any]:
        """Return the latest cookie sync status from the background poll thread.

        Does zero I/O — just reads cached state set by ``_poll_loop``.
        """
        return dict(self._sync_result) if self._sync_result else {
            "status": "error",
            "message": "Login not started.",
            "logged_in": False,
            "cookies_injected": 0,
        }

    def get_login_status(self) -> dict[str, bool]:
        """Check whether user is logged into Digikey.

        Uses the fastest available check: pending cookies from CDP, cached
        sync result from the poll thread, or the hidden webview as last resort.
        """
        if self._pending_cookies:
            return {"logged_in": check_cookies_logged_in(self._pending_cookies)}
        if self._sync_result.get("logged_in"):
            return {"logged_in": True}
        return {"logged_in": False}

    def validate_session(self) -> dict[str, Any]:
        """Test whether the current Digikey session actually works.

        Navigates the hidden webview to a logged-in-only page and checks
        whether we land there or get redirected to login / stuck on a
        Cloudflare challenge. On failure, invalidates the in-memory session
        so subsequent ``get_login_status`` calls return ``logged_in=False``.

        Cookie-presence is not enough to know the session is live: cf_clearance
        is fingerprint-bound and dkuhint can be stale on the server side.

        Intended to be called at startup (after ``check_session`` reports
        a session is found) and any other time the UI wants to confirm.
        """
        was_logged_in = bool(self._pending_cookies) or self._sync_result.get(
            "logged_in", False,
        )
        if not was_logged_in:
            return {
                "logged_in": False, "changed": False,
                "message": "No saved session to validate",
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

    def _probe_session(self) -> bool:
        """Navigate to MyDigiKey/Account and check we don't end up at /login.

        Returns True if the session is usable (lands on the account page),
        False if redirected to login or the Cloudflare challenge persists.
        See ``digikey_session._probe_session`` for the full flow.
        """
        return digikey_session._probe_session(self)

    def logout(self) -> dict[str, str]:
        """Log out of Digikey and clear the product cache."""
        self._poll_stop.set()  # stop any running poll thread
        self._sync_result = {}
        self._pending_cookies = None
        if self._cookies_file:
            try:
                os.remove(self._cookies_file)
            except FileNotFoundError:
                pass
        if self._window is not None:
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

        with self._lock:
            self._ensure_window()

            search_url = (
                "https://www.digikey.com/en/products/result?keywords="
                + quote(part_number, safe="")
            )
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
                if "/login" in final_url.lower() or "/mydigikey" in final_url.lower():
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
