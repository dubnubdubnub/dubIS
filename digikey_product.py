"""Digikey product-page helpers — session probe, logout navigation."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


def probe_session(window, loaded_event: threading.Event) -> bool:
    """Navigate to MyDigiKey/Account and check we don't end up at /login.

    Returns True if the session is usable (lands on the account page),
    False if redirected to login or the Cloudflare challenge persists.

    The caller is responsible for holding the window lock before calling this.
    """
    import time

    probe_url = "https://www.digikey.com/MyDigiKey/Account"
    loaded_event.clear()
    window.load_url(probe_url)
    if not loaded_event.wait(timeout=15):
        logger.warning("DK probe: page load timed out")
        return False

    cf_deadline = time.time() + 25.0
    while time.time() < cf_deadline:
        try:
            title = window.evaluate_js("document.title") or ""
        except RuntimeError:
            title = ""
        if title and "Just a moment" not in title:
            break
        time.sleep(0.5)
    else:
        logger.warning("DK probe: Cloudflare challenge did not clear")
        return False

    try:
        final_url = window.evaluate_js("window.location.href") or ""
    except RuntimeError:
        return False

    url_lower = final_url.lower()
    if "/login" in url_lower or "/signin" in url_lower:
        logger.warning("DK probe: redirected to %s — session expired", final_url)
        return False

    logger.debug("DK probe: session valid (final url=%s)", final_url)
    return True


def perform_logout(
    window,
    loaded_event: threading.Event,
    cookies_file: str | None,
) -> None:
    """Clear WebView2 cookies and navigate to the DK logout URL.

    Handles all WebView2-specific cookie clearing and the logout page navigation.
    Session state (poll thread, sync result, pending cookies) is managed by the caller.
    Errors are logged rather than raised so logout always completes.

    Args:
        window: pywebview Window instance (may be None — no-op in that case).
        loaded_event: Threading Event associated with the window load signal.
        cookies_file: Path to the on-disk cookies file to delete, or None.
    """
    if cookies_file:
        try:
            os.remove(cookies_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove cookies file: %s", exc)

    if window is None:
        return

    try:
        import System
        from webview.platforms.winforms import BrowserView

        uid = window.uid
        instance = BrowserView.instances.get(uid)
        if instance is not None:
            def _clear():
                try:
                    cm = instance.browser.webview.CoreWebView2.CookieManager
                    cm.DeleteAllCookies()
                except Exception as exc:
                    logger.debug("DeleteAllCookies failed: %s", exc)

            instance.browser.form.Invoke(System.Action(_clear))
        loaded_event.clear()
        window.load_url("https://www.digikey.com/MyDigiKey/Logout")
    except (RuntimeError, AttributeError, ImportError) as exc:
        logger.warning("Digikey logout failed: %s", exc)


def validate_session(
    pending_cookies: list[dict] | None,
    sync_result: dict[str, Any],
    probe_cb,
    invalidate_session_cb,
) -> dict[str, Any]:
    """Test whether the current Digikey session actually works.

    Navigates the hidden webview to a logged-in-only page and checks
    whether we land there or get redirected to login / stuck on a
    Cloudflare challenge.

    Args:
        pending_cookies: In-memory cookie list from the last login, or None.
        sync_result: Current sync-result dict (may have ``logged_in`` key).
        probe_cb: Zero-arg callable that navigates and returns bool (True = valid).
        invalidate_session_cb: Callable(delete_cookies_file=bool).
    """
    was_logged_in = bool(pending_cookies) or sync_result.get("logged_in", False)
    if not was_logged_in:
        return {
            "logged_in": False, "changed": False,
            "message": "No saved session to validate",
        }

    try:
        ok = probe_cb()
    except (RuntimeError, OSError) as exc:
        logger.warning("DK session validation error: %s", exc)
        # Inconclusive — keep the session as-is rather than invalidate
        return {
            "logged_in": was_logged_in, "changed": False,
            "message": f"Validation error: {exc}",
        }

    if not ok:
        invalidate_session_cb(delete_cookies_file=True)
        return {
            "logged_in": False, "changed": True,
            "message": "Session expired — please re-login",
        }
    return {
        "logged_in": True, "changed": False,
        "message": "Session valid",
    }
