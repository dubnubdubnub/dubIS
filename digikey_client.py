"""Digikey product-fetching client — session management and public API."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from base_client import BaseProductClient
from digikey_cdp import cdp_get_cookies
from digikey_normalizer import normalize_result
from digikey_product import perform_logout, probe_session, validate_session
from digikey_search import check_session, navigate_and_scrape, start_login
from digikey_session import (
    check_cookies_logged_in,
    find_default_browser_exe,
    inject_cookies_to_window,
    load_cookies_from_file,
    poll_cdp_for_cookies,
    save_cookies_to_file,
)

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
        """Mark the in-memory session as not-logged-in."""
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

    def check_session(self) -> dict[str, Any]:
        """Check if there's an existing Digikey session (called on app startup)."""
        return check_session(
            load_cookies_cb=self._load_cookies,
            set_logged_in_cb=self._set_logged_in,
        )

    def start_login(self) -> dict[str, Any]:
        """Launch the default browser with CDP enabled and open the login page."""
        def _set_sync(r):
            self._sync_result = r

        def _set_port(p):
            self._cdp_port = p

        def _make_poll_stop():
            self._poll_stop = threading.Event()
            return self._poll_stop

        def _start_poll_thread(port: int, stop_event: threading.Event) -> None:
            self._poll_stop = stop_event
            thread = threading.Thread(
                target=self._poll_loop, args=(port,), daemon=True,
            )
            thread.start()

        return start_login(
            poll_stop=self._poll_stop,
            set_sync_result_cb=_set_sync,
            set_cdp_port_cb=_set_port,
            make_poll_stop_cb=_make_poll_stop,
            start_poll_thread_cb=_start_poll_thread,
        )

    def sync_cookies(self) -> dict[str, Any]:
        """Return the latest cookie sync status (zero I/O)."""
        return dict(self._sync_result) if self._sync_result else {
            "status": "error",
            "message": "Login not started.",
            "logged_in": False,
            "cookies_injected": 0,
        }

    def get_login_status(self) -> dict[str, bool]:
        """Check whether user is logged into Digikey."""
        if self._pending_cookies:
            return {"logged_in": check_cookies_logged_in(self._pending_cookies)}
        if self._sync_result.get("logged_in"):
            return {"logged_in": True}
        return {"logged_in": False}

    def validate_session(self) -> dict[str, Any]:
        """Test whether the current Digikey session actually works."""
        return validate_session(
            pending_cookies=self._pending_cookies,
            sync_result=self._sync_result,
            probe_cb=self._probe_session,
            invalidate_session_cb=self._invalidate_session,
        )

    def _probe_session(self) -> bool:
        """Navigate to MyDigiKey/Account; return True if session is valid."""
        with self._lock:
            self._ensure_window()
            return probe_session(self._window, self._loaded)

    def logout(self) -> dict[str, str]:
        """Log out of Digikey and clear the product cache."""
        self._poll_stop.set()  # stop any running poll thread
        self._sync_result = {}
        self._pending_cookies = None
        perform_logout(self._window, self._loaded, self._cookies_file)
        self.clear_cache()
        return {"status": "ok"}

    def _fetch_raw(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Digikey product details by navigating the hidden browser window."""
        return navigate_and_scrape(
            window_cb=self._ensure_window,
            get_window=lambda: self._window,
            loaded_event=self._loaded,
            lock=self._lock,
            invalidate_session_cb=self._invalidate_session,
            part_number=part_number,
        )
