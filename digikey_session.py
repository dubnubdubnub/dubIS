"""Digikey session management helpers — cookie I/O, CDP polling, window injection."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from digikey_cdp import cdp_get_cookies

if TYPE_CHECKING:
    from digikey_client import DigikeyClient

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
      (HTTP 200 and the FINAL url is not a login/signin page).
    - Returns ``False`` ONLY on a definitive expiry signal: the final url
      contains ``/login`` or ``/signin`` (DigiKey redirects unauthenticated
      users there, served as 200). Empty/no cookies also returns ``False``.
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
    # socket errors) propagate to the caller — do NOT catch them here.
    with urllib.request.urlopen(req, timeout=10) as resp:
        final_url = (resp.geturl() or "").lower()
        status = getattr(resp, "status", None)

    if "/login" in final_url or "/signin" in final_url:
        logger.debug("DK http validate: redirected to %s — session expired", final_url)
        return False
    if status == 200:
        logger.debug("DK http validate: session valid (final url=%s)", final_url)
        return True
    # 200-but-not-login is the only True case; anything else here is a
    # non-definitive response — treat as inconclusive.
    raise urllib.error.URLError(f"unexpected status {status} for {final_url}")


def check_session(client: "DigikeyClient") -> dict[str, Any]:
    """Check if there's an existing Digikey session.

    Tries saved cookies first, then launches the browser headless
    with CDP to read fresh cookies. Called on app startup.
    """
    # 1. Try saved cookies from disk (instant). Validate them over plain
    #    HTTP so an expired session doesn't masquerade as logged-in.
    saved = client._load_cookies()
    if saved:
        try:
            validated = client.validate_session_http(saved)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Inconclusive (offline / Cloudflare 403) — never downgrade a
            # saved session just because the network was unreachable.
            logger.debug("Startup: session validation inconclusive: %s", exc)
            client._set_logged_in(saved)
            return {"logged_in": True, "message": "Loaded saved session"}
        if validated:
            client._set_logged_in(saved)
            logger.debug("Startup: validated saved session (%d cookies)", len(saved))
            return {"logged_in": True, "message": "Validated saved session"}
        # Definitively expired — fall through to the headless CDP fallback
        # so a fresh browser session can still be discovered.
        logger.debug("Startup: saved session expired, trying headless CDP")

    # 2. Try headless browser CDP
    import random
    import subprocess

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
            client._set_logged_in(dk_cookies)
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


def start_login(client: "DigikeyClient") -> dict[str, Any]:
    """Launch the default browser with CDP enabled and open the login page.

    Starts a background thread that polls CDP for cookies so that
    ``sync_cookies`` can return instantly with no I/O.
    """
    import random
    import subprocess

    client._poll_stop.set()  # stop any previous poll thread

    url = "https://www.digikey.com/MyDigiKey/Login"
    exe = find_default_browser_exe()
    logger.debug("Login: browser exe=%s", exe)
    if not exe:
        import webbrowser
        webbrowser.open(url)
        client._cdp_port = None
        client._sync_result = {
            "status": "error",
            "message": "Could not find browser — cookie sync unavailable.",
            "logged_in": False,
            "cookies_injected": 0,
        }
        return {"status": "opened", "cdp": False, "message": "Browser opened (no CDP)"}

    port = random.randint(19200, 19299)
    logger.debug("Login: launching with CDP port %d", port)
    subprocess.Popen(
        [exe, f"--remote-debugging-port={port}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    client._cdp_port = port
    client._sync_result = {
        "status": "waiting",
        "message": "Browser opened — waiting for login...",
        "logged_in": False,
        "cookies_injected": 0,
    }

    # Start background CDP poll thread
    client._poll_stop = threading.Event()
    thread = threading.Thread(target=client._poll_loop, args=(port,), daemon=True)
    thread.start()

    logger.debug("Login: browser launched, poll thread started")
    return {"status": "opened", "cdp": True, "port": port, "message": "Browser opened — waiting for login"}


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
        if "/login" in url_lower or "/signin" in url_lower:
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
