"""Digikey session management helpers — cookie I/O, CDP polling, window injection."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from digikey_cdp import cdp_get_cookies

logger = logging.getLogger(__name__)


def find_default_browser_exe() -> str | None:
    """Find the default browser executable on Windows via registry."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command",
        ) as key:
            cmd = winreg.QueryValueEx(key, "")[0]
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]
        return exe if os.path.exists(exe) else None
    except OSError:
        return None


def check_cookies_logged_in(cookies: list[dict]) -> bool:
    """Check whether cookies indicate a logged-in Digikey session.

    Looks for session cookies that are only present after login.
    """
    cookie_names = {c.get("name", "") for c in cookies}
    # dkuhint = "digikey user hint", only set after login
    return "dkuhint" in cookie_names


def save_cookies_to_file(cookies: list[dict], cookies_file: str | None) -> None:
    """Persist Digikey cookies to disk."""
    if not cookies_file:
        return
    try:
        with open(cookies_file, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
    except Exception as exc:
        logger.warning("Failed to save cookies: %s", exc)


def load_cookies_from_file(cookies_file: str | None) -> list[dict] | None:
    """Load persisted Digikey cookies from disk."""
    if not cookies_file:
        return None
    try:
        with open(cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if cookies and check_cookies_logged_in(cookies):
            return cookies
    except FileNotFoundError:
        logger.debug("No saved cookies file found")
    except json.JSONDecodeError as exc:
        logger.warning("Corrupt cookies file: %s", exc)
    return None


def poll_cdp_for_cookies(
    port: int,
    poll_stop: threading.Event,
    on_logged_in: Any,
    sync_result: dict[str, Any],
) -> None:
    """Poll CDP for cookies until logged in, stopped, or timed out.

    Does NOT touch the UI thread at all — no webview creation, no Invoke.
    Calls ``on_logged_in(cookies)`` when a valid session is detected.
    Updates ``sync_result`` in-place with status throughout polling.

    Broad exception catching in the loop body is intentional: CDP polling may
    raise a variety of network/JSON errors. We log and retry rather than abort.
    """
    for attempt in range(1, 41):  # max ~2 minutes at 3s intervals
        if poll_stop.is_set():
            return

        debug_log = []
        try:
            all_cdp = cdp_get_cookies(port)
            cdp_cookies = [c for c in all_cdp if "digikey.com" in c.get("domain", "")]
            debug_log.append(
                f"cdp(port={port}): {len(cdp_cookies)} digikey cookies "
                f"(of {len(all_cdp)} total)"
            )
            logger.debug("Poll #%d: %d digikey cookies", attempt, len(cdp_cookies))

            if cdp_cookies and check_cookies_logged_in(cdp_cookies):
                # Logged in — invoke callback
                on_logged_in(cdp_cookies)
                cookie_names = [c["name"] for c in cdp_cookies[:20]]
                sync_result["debug"] = debug_log + [f"names={cookie_names}"]
                logger.debug("Poll #%d: logged in!", attempt)
                return  # done

        except ConnectionRefusedError:
            debug_log.append(f"cdp(port={port}): ConnectionRefusedError")
            sync_result.update({
                "status": "browser_running",
                "message": "Close your browser and click Login again.",
                "logged_in": False,
                "cookies_injected": 0,
                "debug": debug_log,
            })
            logger.debug("Poll #%d: connection refused", attempt)
            return  # stop polling — browser was already running

        except Exception as exc:
            debug_log.append(f"cdp(port={port}): {type(exc).__name__}: {exc}")
            sync_result.update({
                "status": "waiting",
                "message": "Waiting for login...",
                "logged_in": False,
                "cookies_injected": 0,
                "debug": debug_log,
            })
            logger.debug("Poll #%d: %s: %s", attempt, type(exc).__name__, exc)

        # Wait 3s before next attempt, but check stop flag
        if poll_stop.wait(timeout=3):
            return

    # Timed out
    sync_result.update({
        "status": "error",
        "message": "Timed out waiting for login.",
        "logged_in": False,
        "cookies_injected": 0,
    })


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
