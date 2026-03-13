"""Digikey product-fetching client — extracted from inventory_api.py."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


class DigikeyClient:
    """Manages Digikey browser session, cookie sync, and product scraping."""

    def __init__(self, cookies_file: str | None = None) -> None:
        self._cache: dict[str, dict[str, Any] | None] = {}
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
                all_cdp = self._cdp_get_cookies(port)
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

    @staticmethod
    def _cdp_get_cookies(port: int) -> list[dict]:
        """Read digikey.com cookies via Chrome DevTools Protocol.

        Implements a minimal WebSocket client (no external deps) to send a
        single CDP command and read the response.
        """
        import base64
        import http.client
        import socket
        import struct

        # 1. Get a page target's WebSocket URL (cookies need page context)
        conn = http.client.HTTPConnection("localhost", port, timeout=2)
        conn.request("GET", "/json")
        targets = json.loads(conn.getresponse().read())
        conn.close()
        # Prefer a digikey tab; fall back to any page target
        page = None
        for t in targets:
            if t.get("type") == "page":
                if page is None:
                    page = t
                if "digikey" in t.get("url", "").lower():
                    page = t
                    break
        if not page or "webSocketDebuggerUrl" not in page:
            raise RuntimeError(f"No page target found ({len(targets)} targets)")
        ws_path = "/" + page["webSocketDebuggerUrl"].split("/", 3)[3]

        # 2. WebSocket handshake
        sock = socket.create_connection(("localhost", port), timeout=2)
        ws_key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            sock.close()
            raise RuntimeError("WebSocket upgrade failed")

        # 3. Send Network.getAllCookies on the page target
        cmd = json.dumps({
            "id": 1,
            "method": "Network.getAllCookies",
        }).encode()
        mask = os.urandom(4)
        hdr = bytes([0x81])  # FIN + text opcode
        if len(cmd) < 126:
            hdr += bytes([0x80 | len(cmd)])
        else:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", len(cmd))
        hdr += mask
        sock.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(cmd)))

        # 4. Read frames until we get our response (id=1)
        def _recv(n):
            d = b""
            while len(d) < n:
                c = sock.recv(n - len(d))
                if not c:
                    raise RuntimeError("CDP connection closed")
                d += c
            return d

        try:
            for _ in range(50):  # safety limit
                h = _recv(2)
                plen = h[1] & 0x7F
                if plen == 126:
                    plen = struct.unpack(">H", _recv(2))[0]
                elif plen == 127:
                    plen = struct.unpack(">Q", _recv(8))[0]
                payload = _recv(plen)
                try:
                    msg = json.loads(payload)
                    if msg.get("id") == 1:
                        return msg.get("result", {}).get("cookies", [])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        finally:
            sock.close()
        raise RuntimeError("No CDP response received")

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

    @staticmethod
    def _normalize_result(
        raw: dict[str, Any], part_number: str
    ) -> dict[str, Any]:
        """Normalize scraped Digikey data to the same shape as LCSC product."""
        # JSON-LD Product schema
        if raw.get("@type") == "Product":
            offers = raw.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_val: float = 0
            try:
                price_val = float(
                    offers.get("price") or offers.get("lowPrice") or 0
                )
            except (ValueError, TypeError):
                pass

            brand = raw.get("brand") or {}
            image = raw.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""

            return {
                "productCode": raw.get("sku") or part_number,
                "title": raw.get("name", ""),
                "manufacturer": (
                    brand.get("name", "")
                    if isinstance(brand, dict)
                    else str(brand)
                ),
                "mpn": raw.get("mpn", "") or raw.get("sku", ""),
                "package": "",
                "description": raw.get("description", ""),
                "stock": raw.get("_stock") or (
                    1 if "InStock" in str(
                        offers.get("availability", "")
                    ) else 0
                ),
                "prices": (
                    [{"qty": 1, "price": price_val}] if price_val else []
                ),
                "imageUrl": image,
                "pdfUrl": "",
                "digikeyUrl": raw.get("url", ""),
                "attributes": [],
                "provider": "digikey",
            }

        # Next.js SSR data — extract from envelope.data structure
        if raw.get("_source") == "nextdata":
            props = raw.get("_props") or {}
            envelope = props.get("envelope") or {}
            data = envelope.get("data") or {}
            overview = data.get("productOverview") or {}
            pq = data.get("priceQuantity") or {}
            pa = data.get("productAttributes") or {}
            media = data.get("carouselMedia") or []
            crumbs = data.get("breadcrumb") or []

            # Stock
            stock = 0
            try:
                stock = int(
                    str(pq.get("qtyAvailable", "0")).replace(",", "")
                )
            except (ValueError, TypeError):
                pass

            # Prices — use first pricing option (smallest MOQ packaging)
            prices: list[dict[str, int | float]] = []
            pricing_list = pq.get("pricing") or []
            if pricing_list:
                tiers = pricing_list[0].get("mergedPricingTiers") or []
                for t in tiers:
                    try:
                        qty = int(
                            str(t.get("brkQty", "0")).replace(",", "")
                        )
                        price = float(
                            str(t.get("unitPrice", "0"))
                            .replace("$", "")
                            .replace(",", "")
                        )
                        prices.append({"qty": qty, "price": price})
                    except (ValueError, TypeError):
                        continue

            # Image — first Image type in carousel
            image_url = ""
            for m in media:
                if m.get("type") == "Image":
                    image_url = (
                        m.get("displayUrl") or m.get("smallPhoto") or ""
                    )
                    break
            if image_url.startswith("//"):
                image_url = "https:" + image_url

            # Package and attributes from attribute list
            package = ""
            attrs_out: list[dict[str, str]] = []
            skip_ids = {"-1", "-4", "-5", "1989", "-7"}
            for attr in pa.get("attributes") or []:
                vals = attr.get("values") or []
                val = vals[0].get("value", "") if vals else ""
                if attr.get("label") == "Package / Case":
                    package = val
                attr_id = str(attr.get("id", ""))
                if attr_id not in skip_ids and val and val != "-":
                    attrs_out.append(
                        {"name": attr.get("label", ""), "value": val}
                    )

            # Category from categories list
            cats = pa.get("categories") or []
            category = cats[-1]["label"] if cats else ""
            subcategory = cats[-2]["label"] if len(cats) >= 2 else ""

            # Digikey URL from last breadcrumb
            dk_url = ""
            if crumbs:
                dk_url = crumbs[-1].get("url", "")
                if dk_url and not dk_url.startswith("http"):
                    dk_url = "https://www.digikey.com" + dk_url

            return {
                "productCode": (
                    overview.get("rolledUpProductNumber") or part_number
                ),
                "title": overview.get("title") or "",
                "manufacturer": overview.get("manufacturer") or "",
                "mpn": overview.get("manufacturerProductNumber") or "",
                "package": package,
                "description": (
                    overview.get("detailedDescription")
                    or overview.get("description")
                    or ""
                ),
                "stock": stock,
                "prices": prices,
                "imageUrl": image_url,
                "pdfUrl": overview.get("datasheetUrl") or "",
                "digikeyUrl": dk_url,
                "category": category,
                "subcategory": subcategory,
                "attributes": attrs_out,
                "provider": "digikey",
            }

        # Unknown format — return empty shell
        return {
            "productCode": part_number,
            "title": "",
            "manufacturer": "",
            "mpn": "",
            "package": "",
            "description": "",
            "stock": 0,
            "prices": [],
            "imageUrl": "",
            "pdfUrl": "",
            "digikeyUrl": "",
            "attributes": [],
            "provider": "digikey",
        }

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
            cookies = self._cdp_get_cookies(port)
            dk_cookies = [c for c in cookies if "digikey.com" in c.get("domain", "")]
            if dk_cookies and self._check_cookies_logged_in(dk_cookies):
                self._set_logged_in(dk_cookies)
                logger.debug("Startup: found browser session (%d cookies)", len(dk_cookies))
                return {"logged_in": True, "message": "Found browser session"}
            logger.debug("Startup: no existing session (%d digikey cookies)", len(dk_cookies))
            return {"logged_in": False, "message": "No existing session"}
        except Exception as exc:
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
        self._cache.clear()
        return {"status": "ok"}

    def fetch_product(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Digikey product details by navigating the hidden browser window.

        Navigates to the Digikey search page for *part_number*, waits for the
        page to load, then extracts structured product data (JSON-LD or
        Next.js SSR data) from the rendered DOM.

        Results (including ``None``) are cached for the session.
        """
        part_number = str(part_number).strip()
        if not part_number:
            raise ValueError("Part number must not be empty")

        if part_number in self._cache:
            return self._cache[part_number]

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
                self._cache[part_number] = None
                return None

            try:
                # Get the final URL to check for redirects (e.g. login page)
                final_url = self._window.evaluate_js("window.location.href") or ""
                logger.debug("DK fetch: final URL = %s", final_url)

                result = self._window.evaluate_js(
                    "(function() {"
                    # Strategy 1: JSON-LD structured data
                    "  var scripts = document.querySelectorAll("
                    "    'script[type=\"application/ld+json\"]');"
                    "  for (var i = 0; i < scripts.length; i++) {"
                    "    try {"
                    "      var ld = JSON.parse(scripts[i].textContent);"
                    "      var prod = null;"
                    "      if (ld['@type'] === 'Product') prod = ld;"
                    "      if (!prod && Array.isArray(ld)) {"
                    "        for (var j = 0; j < ld.length; j++) {"
                    "          if (ld[j]['@type'] === 'Product') { prod = ld[j]; break; }"
                    "        }"
                    "      }"
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
                    "  return null;"
                    "})()"
                )
                logger.debug(
                    "DK fetch: scrape result type=%s, keys=%s",
                    type(result).__name__,
                    list(result.keys()) if isinstance(result, dict) else "N/A",
                )
            except Exception as exc:
                logger.error("DK fetch: evaluate_js failed for %s: %s", part_number, exc)
                self._cache[part_number] = None
                return None

        if not result or not isinstance(result, dict):
            logger.debug("DK fetch: no product data for %s", part_number)
            self._cache[part_number] = None
            return None

        product = self._normalize_result(result, part_number)
        product["_debug"] = result
        self._cache[part_number] = product
        return product
