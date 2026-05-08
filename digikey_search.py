"""Digikey search helpers — session-check, login flow, and search-page scraping."""

from __future__ import annotations

import logging
import random
import subprocess
import threading
import time
from typing import Any
from urllib.parse import quote

from digikey_cdp import cdp_get_cookies
from digikey_normalizer import normalize_result
from digikey_scrape_js import SCRAPE_JS
from digikey_session import check_cookies_logged_in, find_default_browser_exe
from dubis_errors import DistributorError, DistributorTimeout

logger = logging.getLogger(__name__)


def check_session(
    load_cookies_cb,
    set_logged_in_cb,
) -> dict[str, Any]:
    """Check for an existing Digikey session at app startup.

    Tries saved cookies first (instant), then headless-browser CDP.

    Args:
        load_cookies_cb: Zero-arg callable that returns saved cookie list or None.
        set_logged_in_cb: Callable(cookies) that stores the session in client state.
    """
    # 1. Try saved cookies from disk (instant)
    saved = load_cookies_cb()
    if saved:
        set_logged_in_cb(saved)
        logger.debug("Startup: loaded saved session (%d cookies)", len(saved))
        return {"logged_in": True, "message": "Loaded saved session"}

    # 2. Try headless browser CDP
    exe = find_default_browser_exe()
    if not exe:
        logger.debug("Startup: no browser found for session check")
        return {"logged_in": False, "message": "No browser found"}

    port = random.randint(19200, 19299)
    proc = subprocess.Popen(
        [exe, "--headless=new", f"--remote-debugging-port={port}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Give headless browser a moment to start
        time.sleep(1.5)
        cookies = cdp_get_cookies(port)
        dk_cookies = [c for c in cookies if "digikey.com" in c.get("domain", "")]
        if dk_cookies and check_cookies_logged_in(dk_cookies):
            set_logged_in_cb(dk_cookies)
            logger.debug("Startup: found browser session (%d cookies)", len(dk_cookies))
            return {"logged_in": True, "message": "Found browser session"}
        logger.debug("Startup: no existing session (%d digikey cookies)", len(dk_cookies))
        return {"logged_in": False, "message": "No existing session"}
    except (OSError, TimeoutError) as exc:
        logger.debug("Startup: session check failed: %s", exc)
        return {"logged_in": False, "message": f"Session check failed: {exc}"}
    finally:
        try:
            proc.terminate()
        except OSError:
            pass


def start_login(
    poll_stop: threading.Event,
    set_sync_result_cb,
    set_cdp_port_cb,
    make_poll_stop_cb,
    start_poll_thread_cb,
) -> dict[str, Any]:
    """Launch the default browser with CDP enabled and open the Digikey login page.

    Starts a background thread that polls CDP for cookies so that
    ``sync_cookies`` can return instantly with no I/O.

    Args:
        poll_stop: Event to signal any running poll thread to stop.
        set_sync_result_cb: Callable(result_dict) to update in-memory sync state.
        set_cdp_port_cb: Callable(port_or_None) to store the CDP port.
        make_poll_stop_cb: Zero-arg callable that creates and returns a new Event.
        start_poll_thread_cb: Callable(port) to start the background CDP poll thread.
    """
    poll_stop.set()  # stop any previous poll thread

    url = "https://www.digikey.com/MyDigiKey/Login"
    exe = find_default_browser_exe()
    logger.debug("Login: browser exe=%s", exe)
    if not exe:
        import webbrowser
        webbrowser.open(url)
        set_cdp_port_cb(None)
        set_sync_result_cb({
            "status": "error",
            "message": "Could not find browser — cookie sync unavailable.",
            "logged_in": False,
            "cookies_injected": 0,
        })
        return {"status": "opened", "cdp": False, "message": "Browser opened (no CDP)"}

    port = random.randint(19200, 19299)
    logger.debug("Login: launching with CDP port %d", port)
    subprocess.Popen(
        [exe, f"--remote-debugging-port={port}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    set_cdp_port_cb(port)
    set_sync_result_cb({
        "status": "waiting",
        "message": "Browser opened — waiting for login...",
        "logged_in": False,
        "cookies_injected": 0,
    })

    # Start background CDP poll thread
    new_stop = make_poll_stop_cb()
    start_poll_thread_cb(port, new_stop)

    logger.debug("Login: browser launched, poll thread started")
    return {"status": "opened", "cdp": True, "port": port, "message": "Browser opened — waiting for login"}


def navigate_and_scrape(
    window_cb,
    get_window,
    loaded_event: threading.Event,
    lock: threading.Lock,
    invalidate_session_cb,
    part_number: str,
) -> dict[str, Any] | None:
    """Navigate the hidden webview to the DK search URL and scrape product data.

    Handles Cloudflare interstitial, login redirects, JS evaluation, and
    result normalization. This is the core of ``_fetch_raw``.

    Args:
        window_cb: Zero-arg callable that ensures the webview window exists
                   (called while holding ``lock``).
        get_window: Zero-arg callable that returns the current Window instance.
        loaded_event: Threading Event set when the page fires ``loaded``.
        lock: Mutex that serializes window navigation.
        invalidate_session_cb: Callable(delete_cookies_file=bool) called on session expiry.
        part_number: DK part number / search query (must be non-empty).

    Returns:
        Normalized product dict on success, or None on soft failure.

    Raises:
        ValueError: If part_number is empty.
        DistributorTimeout: On JS evaluation timeout.
        DistributorError: On OS-level errors.
    """
    part_number = str(part_number).strip()
    if not part_number:
        raise ValueError("Part number must not be empty")

    with lock:
        window_cb()
        window = get_window()
        search_url = (
            "https://www.digikey.com/en/products/result?keywords="
            + quote(part_number, safe="")
        )
        logger.debug("DK fetch: loading %s", search_url)
        loaded_event.clear()
        window.load_url(search_url)
        if not loaded_event.wait(timeout=15):
            logger.warning("DK fetch: page load timed out for %s", part_number)
            return None

        # Cloudflare interstitial: the `loaded` event fires on the
        # "Just a moment..." challenge page, before CF's JS redirects to
        # the real product page. Poll the title and wait for the challenge
        # to clear (or the URL to leave /products/result).
        cf_deadline = time.time() + 25.0
        cf_seen = False
        while time.time() < cf_deadline:
            try:
                title = window.evaluate_js("document.title") or ""
            except RuntimeError:
                title = ""
            if title and "Just a moment" not in title:
                if cf_seen:
                    logger.debug(
                        "DK fetch: CF challenge cleared after %.1fs (title=%r)",
                        25.0 - (cf_deadline - time.time()), title,
                    )
                break
            cf_seen = True
            time.sleep(0.5)
        else:
            logger.warning(
                "DK fetch: Cloudflare bot challenge did not resolve in 25s for %s "
                "(title=%r) — invalidating session",
                part_number, title,
            )
            invalidate_session_cb(delete_cookies_file=False)
            return None

        try:
            # Get the final URL to check for redirects (e.g. login page)
            final_url = window.evaluate_js("window.location.href") or ""
            logger.debug("DK fetch: final URL = %s", final_url)

            # Detect login/auth redirects
            if "/login" in final_url.lower() or "/mydigikey" in final_url.lower():
                logger.warning(
                    "DK fetch: redirected to login page (%s) — session expired, invalidating",
                    final_url,
                )
                invalidate_session_cb(delete_cookies_file=True)
                return None

            result = window.evaluate_js(SCRAPE_JS)
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
