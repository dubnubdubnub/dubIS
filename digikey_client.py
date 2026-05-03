"""Digikey product-fetching client — session management and public API."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any
from urllib.parse import quote

from base_client import BaseProductClient
from digikey_cdp import cdp_get_cookies
from digikey_normalizer import normalize_result
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
                self._inject_cookies_to_window(self._pending_cookies)
                logger.debug("Injected %d pending cookies into dk window", len(self._pending_cookies))
            except Exception as exc:
                logger.warning("Pending cookie injection failed: %s", exc)
            self._pending_cookies = None

    @staticmethod
    def _find_default_browser_exe() -> str | None:
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

    @staticmethod
    def _check_cookies_logged_in(cookies: list[dict]) -> bool:
        """Check whether cookies indicate a logged-in Digikey session.

        Looks for session cookies that are only present after login.
        """
        cookie_names = {c.get("name", "") for c in cookies}
        # dkuhint = "digikey user hint", only set after login
        return "dkuhint" in cookie_names

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
        self._save_cookies(cookies)

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
        if not self._cookies_file:
            return
        try:
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f)
        except Exception as exc:
            logger.warning("Failed to save cookies: %s", exc)

    def _load_cookies(self) -> list[dict] | None:
        """Load persisted Digikey cookies from disk."""
        if not self._cookies_file:
            return None
        try:
            with open(self._cookies_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if cookies and self._check_cookies_logged_in(cookies):
                return cookies
        except FileNotFoundError:
            logger.debug("No saved cookies file found")
        except json.JSONDecodeError as exc:
            logger.warning("Corrupt cookies file: %s", exc)
        return None

    def _poll_loop(self, port: int) -> None:
        """Background thread: poll CDP for cookies, store when found.

        Does NOT touch the UI thread at all — no webview creation, no Invoke.
        Cookies are stored in ``_pending_cookies`` and injected later when
        ``_ensure_window`` creates the hidden scraping window.
        """
        for attempt in range(1, 41):  # max ~2 minutes at 3s intervals
            if self._poll_stop.is_set():
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

                if cdp_cookies and self._check_cookies_logged_in(cdp_cookies):
                    # Logged in — store and persist cookies
                    self._set_logged_in(cdp_cookies)
                    cookie_names = [c["name"] for c in cdp_cookies[:20]]
                    self._sync_result["debug"] = debug_log + [f"names={cookie_names}"]
                    logger.debug("Poll #%d: logged in!", attempt)
                    return  # done

            except ConnectionRefusedError:
                debug_log.append(f"cdp(port={port}): ConnectionRefusedError")
                self._sync_result = {
                    "status": "browser_running",
                    "message": "Close your browser and click Login again.",
                    "logged_in": False,
                    "cookies_injected": 0,
                    "debug": debug_log,
                }
                logger.debug("Poll #%d: connection refused", attempt)
                return  # stop polling — browser was already running

            except Exception as exc:
                # Broad catch intentional: CDP polling may raise a variety of
                # network/JSON errors (e.g. json.JSONDecodeError, http.client
                # errors). We log and retry rather than abort the poll loop.
                debug_log.append(f"cdp(port={port}): {type(exc).__name__}: {exc}")
                self._sync_result = {
                    "status": "waiting",
                    "message": "Waiting for login...",
                    "logged_in": False,
                    "cookies_injected": 0,
                    "debug": debug_log,
                }
                logger.debug("Poll #%d: %s: %s", attempt, type(exc).__name__, exc)

            # Wait 3s before next attempt, but check stop flag
            if self._poll_stop.wait(timeout=3):
                return

        # Timed out
        self._sync_result = {
            "status": "error",
            "message": "Timed out waiting for login.",
            "logged_in": False,
            "cookies_injected": 0,
        }

    def _inject_cookies_to_window(self, cookies: list[dict]) -> int:
        """Inject cookie dicts into the WebView2 session via CookieManager.

        All WebView2 access (CookieManager, CreateCookie, AddOrUpdateCookie)
        must happen on the UI thread, so the entire operation is marshaled
        via a single Invoke() call.
        """
        if self._window is None:
            raise RuntimeError("Digikey window not created")

        import System
        from webview.platforms.winforms import BrowserView

        uid = self._window.uid
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

    # ── Backward-compatible shims (delegate to extracted modules) ────────

    _normalize_result = staticmethod(normalize_result)
    _cdp_get_cookies = staticmethod(cdp_get_cookies)

    # ── Public API ────────────────────────────────────────────────────────

    def check_session(self) -> dict[str, Any]:
        """Check if there's an existing Digikey session.

        Tries saved cookies first, then launches the browser headless
        with CDP to read fresh cookies.  Called on app startup.
        """
        # 1. Try saved cookies from disk (instant)
        saved = self._load_cookies()
        if saved:
            self._set_logged_in(saved)
            logger.debug("Startup: loaded saved session (%d cookies)", len(saved))
            return {"logged_in": True, "message": "Loaded saved session"}

        # 2. Try headless browser CDP
        import random
        import subprocess
        import time

        exe = self._find_default_browser_exe()
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
            if dk_cookies and self._check_cookies_logged_in(dk_cookies):
                self._set_logged_in(dk_cookies)
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

    def start_login(self) -> dict[str, Any]:
        """Launch the default browser with CDP enabled and open the login page.

        Starts a background thread that polls CDP for cookies so that
        ``sync_cookies`` can return instantly with no I/O.
        """
        import random
        import subprocess

        self._poll_stop.set()  # stop any previous poll thread

        url = "https://www.digikey.com/MyDigiKey/Login"
        exe = self._find_default_browser_exe()
        logger.debug("Login: browser exe=%s", exe)
        if not exe:
            import webbrowser
            webbrowser.open(url)
            self._cdp_port = None
            self._sync_result = {
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
        self._cdp_port = port
        self._sync_result = {
            "status": "waiting",
            "message": "Browser opened — waiting for login...",
            "logged_in": False,
            "cookies_injected": 0,
        }

        # Start background CDP poll thread
        self._poll_stop = threading.Event()
        thread = threading.Thread(target=self._poll_loop, args=(port,), daemon=True)
        thread.start()

        logger.debug("Login: browser launched, poll thread started")
        return {"status": "opened", "cdp": True, "port": port, "message": "Browser opened — waiting for login"}

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
            return {"logged_in": self._check_cookies_logged_in(self._pending_cookies)}
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
        """
        with self._lock:
            self._ensure_window()
            probe_url = "https://www.digikey.com/MyDigiKey/Account"
            self._loaded.clear()
            self._window.load_url(probe_url)
            if not self._loaded.wait(timeout=15):
                logger.warning("DK probe: page load timed out")
                return False

            import time
            cf_deadline = time.time() + 25.0
            while time.time() < cf_deadline:
                try:
                    title = self._window.evaluate_js("document.title") or ""
                except RuntimeError:
                    title = ""
                if title and "Just a moment" not in title:
                    break
                time.sleep(0.5)
            else:
                logger.warning("DK probe: Cloudflare challenge did not clear")
                return False

            try:
                final_url = self._window.evaluate_js("window.location.href") or ""
            except RuntimeError:
                return False

            url_lower = final_url.lower()
            if "/login" in url_lower or "/signin" in url_lower:
                logger.warning("DK probe: redirected to %s — session expired", final_url)
                return False

            logger.debug("DK probe: session valid (final url=%s)", final_url)
            return True

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
            import time
            cf_deadline = time.time() + 25.0
            cf_seen = False
            while time.time() < cf_deadline:
                try:
                    title = self._window.evaluate_js("document.title") or ""
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
                self._invalidate_session(delete_cookies_file=False)
                return None

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

                result = self._window.evaluate_js(
                    "(function() {"
                    "  function findProduct(obj) {"
                    "    if (!obj || typeof obj !== 'object') return null;"
                    "    if (obj['@type'] === 'Product') return obj;"
                    "    if (Array.isArray(obj)) {"
                    "      for (var i = 0; i < obj.length; i++) {"
                    "        var r = findProduct(obj[i]);"
                    "        if (r) return r;"
                    "      }"
                    "    }"
                    # @graph is used by some JSON-LD schemas (array of entities)
                    "    if (Array.isArray(obj['@graph'])) {"
                    "      for (var j = 0; j < obj['@graph'].length; j++) {"
                    "        var r2 = findProduct(obj['@graph'][j]);"
                    "        if (r2) return r2;"
                    "      }"
                    "    }"
                    "    return null;"
                    "  }"
                    # Strategy 1: JSON-LD structured data
                    "  var scripts = document.querySelectorAll("
                    "    'script[type=\"application/ld+json\"]');"
                    "  for (var i = 0; i < scripts.length; i++) {"
                    "    try {"
                    "      var ld = JSON.parse(scripts[i].textContent);"
                    "      var prod = findProduct(ld);"
                    "      if (prod) {"
                    "        try {"
                    "          var bt = document.body.innerText || '';"
                    "          var sm = bt.match(/(\\d[\\d,]*)\\s+In\\s*Stock/i);"
                    "          if (sm) prod._stock = parseInt(sm[1].replace(/,/g,''),10);"
                    "        } catch(e2) {}"
                    "        return prod;"
                    "      }"
                    "    } catch(e) {}"
                    "  }"
                    # Strategy 2: __NEXT_DATA__ (Next.js SSR)
                    "  var ndEl = document.getElementById('__NEXT_DATA__');"
                    "  if (ndEl) {"
                    "    try {"
                    "      var nd = JSON.parse(ndEl.textContent);"
                    "      var pp = nd && nd.props && nd.props.pageProps;"
                    "      if (pp) return {_source: 'nextdata', _props: pp};"
                    "    } catch(e) {}"
                    "  }"
                    # Strategy 3: Next.js App Router RSC payload
                    "  var rscScripts = document.querySelectorAll("
                    "    'script');"
                    "  for (var k = 0; k < rscScripts.length; k++) {"
                    "    var txt = rscScripts[k].textContent || '';"
                    "    if (txt.indexOf('self.__next_f.push') !== -1) {"
                    "      return {_source: 'diag', _reason: 'next_app_router_rsc',"
                    "        _hint: 'Page uses Next.js App Router (RSC), not Pages Router'};"
                    "    }"
                    "  }"
                    # No data found — return diagnostics
                    "  var ldCount = scripts.length;"
                    "  var ldTypes = [];"
                    "  for (var m = 0; m < scripts.length; m++) {"
                    "    try {"
                    "      var p = JSON.parse(scripts[m].textContent);"
                    "      ldTypes.push(p['@type'] || (p['@graph'] ? '@graph['+p['@graph'].length+']' : typeof p));"
                    "    } catch(e) { ldTypes.push('parse_error'); }"
                    "  }"
                    "  return {_source: 'diag',"
                    "    _reason: 'no_product_data',"
                    "    _url: window.location.href,"
                    "    _title: document.title,"
                    "    _ldCount: ldCount,"
                    "    _ldTypes: ldTypes,"
                    "    _hasNextData: !!document.getElementById('__NEXT_DATA__'),"
                    "    _scriptCount: document.querySelectorAll('script').length};"
                    "})()"
                )
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
                "ld_count=%s, ld_types=%s, has_next_data=%s, scripts=%s)",
                part_number,
                result.get("_reason"),
                result.get("_url"),
                result.get("_title"),
                result.get("_ldCount"),
                result.get("_ldTypes"),
                result.get("_hasNextData"),
                result.get("_scriptCount"),
            )
            return None

        product = normalize_result(result, part_number)
        product["_debug"] = result
        return product
