"""Fetch a rendered page's HTML through a hidden webview window.

WHY A BROWSER AT ALL
Some distributors serve nothing useful to `urllib`. Mouser answers a plain
request with a bot-block page -- verified from two networks, so it is the
request that is refused, not the address -- while the same URL in a real
browser renders the full product page. `digikey_client.py` reached the same
conclusion years earlier and grew its own hidden window; this module is that
idea with the DigiKey-specific parts (cookie sync, CDP, login) left out, so a
second client can borrow it without borrowing a session model it does not have.

DELIBERATELY NOT A REFACTOR OF digikey_client
DigiKey's window is entangled with its cookie injection and login flow, and it
works. Rewriting it to sit on this helper is a change with no user-visible
payoff and a real chance of breaking the one scraper that is currently earning
its keep. Left as a follow-up, noted here so the duplication is a decision
rather than an oversight.

TWO WAYS TO GET A BROWSER
On the desktop, `webview` is already running, so a hidden window costs
nothing. A container has no GUI event loop and cannot make one -- but it can
*borrow* a browser that is already running somewhere else. Set `DUBIS_CDP_URL`
to a Chrome DevTools endpoint and the CDP backend drives that instead, which
is what lets the cluster price a Mouser part it previously could not fetch at
all.

SHARING SOMEONE ELSE'S BROWSER
The CDP backend attaches to a browser it does not own, so it behaves like a
guest. It uses `contexts[0]` -- the profile that already exists, cookies and
all; `new_context()` would hand back a blank logged-out profile and quietly
lose whatever sessions the browser was holding. It closes the pages it opens
and never the browser process: `close()` on a CDP connection detaches this
client, and killing the process would take out every other consumer.

A profile shared with ordinary human browsing is also the point, not a side
effect -- a profile whose entire history is distributor product pages fetched
at machine cadence is a louder bot signal than one mixed in with real traffic.
`human_pause` exists so the cadence does not undo that; see its docstring.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# How long to wait for a navigation to fire `loaded`.
LOAD_TIMEOUT_S = 20
# A challenge page fires `loaded` before its JS forwards to the real page, so
# one recheck after a pause distinguishes "still interstitial" from "arrived".
INTERSTITIAL_RECHECK_S = 4
_INTERSTITIAL_MARKERS = ("just a moment", "checking your browser",
                         "verifying you are human", "attention required")

# A Chrome DevTools endpoint to borrow instead of opening a local window.
CDP_ENV = "DUBIS_CDP_URL"
# Optional floor on how often this process may navigate a shared browser, as
# the median of a `human_pause`. Unset means no floor: an interactive hover
# should not stall, and one person hovering cannot burst hard enough to matter.
CDP_PAUSE_ENV = "DUBIS_CDP_PAUSE_MEDIAN"


def cdp_endpoint() -> str | None:
    """The configured CDP endpoint, or None to use a local window."""
    return (os.environ.get(CDP_ENV) or "").strip() or None


def human_pause(median_s: float, *, sigma: float = 0.6,
                floor_s: float | None = None, cap_s: float | None = None,
                rng: Any = random) -> float:
    """A pause drawn from a log-normal, in seconds.

    Log-normal because that is the shape human inter-action times actually
    have: strictly positive, mode below the median, and a long right tail, so
    most gaps cluster a little under `median_s` and a few run several times
    longer -- someone stopping to read a datasheet. A Gaussian is the wrong
    instrument twice over: it is symmetric, so it manufactures as many
    suspiciously short gaps as long ones, and its left tail runs negative.
    A fixed delay is worse still, being the one inter-arrival distribution no
    person has ever produced.

    `sigma` is the spread in log space; 0 collapses this to exactly
    `median_s`, which is how a caller asks for the old fixed behaviour. The
    floor and cap keep the tail from producing either a burst or a stall, and
    default to a quarter and six times the median.
    """
    if median_s <= 0:
        return 0.0
    if sigma <= 0:
        return float(median_s)
    value = rng.lognormvariate(math.log(median_s), sigma)
    floor = median_s * 0.25 if floor_s is None else floor_s
    cap = median_s * 6.0 if cap_s is None else cap_s
    return float(min(max(value, floor), cap))


def available() -> bool:
    """Whether this process can render a page at all, either way.

    True when a CDP endpoint is configured -- the browser is somebody else's
    and its reachability is not knowable without a round trip, so a failure to
    connect surfaces later as a None from `fetch_html` rather than as a lie
    here. Otherwise it comes down to whether a local GUI loop exists: false in
    the container (no `webview`) and false in a plain CLI, where nothing has
    called `webview.start()`.
    """
    if cdp_endpoint():
        return True
    try:
        import webview
    except ImportError:
        return False
    # pywebview only has a running loop once start() has been called, which the
    # desktop app does and a script does not. `webview.windows` is the cheapest
    # observable proof that the loop exists.
    return bool(getattr(webview, "windows", None))


class BrowserPage:
    """One reusable hidden window, created on first use.

    Not thread-safe by itself; `fetch_html` holds a lock, which is the only
    entry point that touches the window.
    """

    def __init__(self, title: str = "fetch", home: str = "about:blank") -> None:
        self._title = title
        self._home = home
        self._window: Any = None
        self._loaded = threading.Event()
        self._lock = threading.Lock()
        # CDP state, unused on the desktop path.
        self._pw: Any = None
        self._browser: Any = None
        self._last_nav: float = 0.0
        self._last_url: str | None = None

    # ── window lifecycle ─────────────────────────────────────────────────

    def _ensure_window(self) -> None:
        """Create the hidden window if it does not exist. Caller holds _lock."""
        if self._window is not None:
            return
        import webview

        self._loaded.clear()

        def on_loaded() -> None:
            self._loaded.set()

        def on_closing() -> bool:
            # Hide rather than destroy: destroying would make the next fetch
            # pay the window-creation cost again, and a user closing a window
            # they were never shown is not a request to tear anything down.
            try:
                self._window.hide()
            except (AttributeError, RuntimeError):
                pass
            return False

        self._window = webview.create_window(
            self._title, url=self._home, hidden=True, width=1200, height=900,
        )
        self._window.events.loaded += on_loaded
        self._window.events.closing += on_closing

    def destroy(self) -> None:
        """Drop the window, or detach from the shared browser. Safe when neither."""
        with self._lock:
            window, self._window = self._window, None
            browser, self._browser = self._browser, None
            pw, self._pw = self._pw, None
        if browser is not None:
            # Detaches this client only. The browser is shared and outlives us.
            try:
                browser.close()
            except Exception as exc:  # noqa: BLE001 - third-party error surface
                logger.debug("BrowserPage: detaching from CDP failed: %s", exc)
        if pw is not None:
            try:
                pw.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("BrowserPage: stopping playwright failed: %s", exc)
        if window is None:
            return
        try:
            window.destroy()
        except (AttributeError, RuntimeError) as exc:
            logger.debug("BrowserPage: destroy failed: %s", exc)

    # ── CDP backend ──────────────────────────────────────────────────────

    def _connect_cdp(self, endpoint: str) -> Any:
        """Attach to the shared browser, reusing the connection. Caller holds _lock.

        The connection is kept for the life of this object rather than remade
        per fetch: reconnecting for every part would be hundreds of attach and
        detach cycles against a browser other things are using.
        """
        if self._browser is not None:
            return self._browser
        # The sync API refuses to run inside a running asyncio loop, and says
        # so obscurely. The /v1 routes are plain `def`, so FastAPI runs them in
        # a worker thread and this is fine -- but an `async def` caller would
        # land here with a confusing Playwright error instead of a reason.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "browser_page's CDP backend uses Playwright's sync API and "
                "cannot run inside an asyncio loop; call it from a worker "
                "thread (a plain `def` route handler already is one)")
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(endpoint)
        return self._browser

    def _shared_context(self, browser: Any) -> Any:
        """The browser's existing profile -- the one holding its cookies.

        `new_context()` would return a blank, logged-out profile. Any session
        the browser was holding would silently not apply, which reads as "the
        site logged us out" rather than "we asked for a different profile".
        """
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError(
                "shared browser exposes no existing context; refusing to "
                "create one, since a fresh context would be logged out")
        return contexts[0]

    def _throttle(self) -> None:
        """Optional floor on navigation rate against a browser we share."""
        median = (os.environ.get(CDP_PAUSE_ENV) or "").strip()
        if not median:
            return
        try:
            target = float(median)
        except ValueError:
            logger.warning("%s is not a number: %r", CDP_PAUSE_ENV, median)
            return
        wait = self._last_nav + human_pause(target) - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _fetch_over_cdp(self, endpoint: str, url: str, settle_s: float) -> str | None:
        """Render `url` in the shared browser. Caller holds _lock."""
        try:
            browser = self._connect_cdp(endpoint)
            context = self._shared_context(browser)
        except Exception as exc:  # noqa: BLE001 - connect surfaces many types
            logger.warning("BrowserPage: cannot attach to %s: %s", endpoint, exc)
            self._browser = self._pw = None
            return None

        self._throttle()
        page = None
        try:
            page = context.new_page()
            self._last_url = None
            page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_S * 1000)
            self._last_nav = time.monotonic()
            self._last_url = page.url
            if settle_s:
                page.wait_for_timeout(settle_s * 1000)
            html = page.content()
            if html and self._looks_like_interstitial(html):
                logger.debug("BrowserPage: interstitial at %s, rechecking", url)
                page.wait_for_timeout(INTERSTITIAL_RECHECK_S * 1000)
                html = page.content()
                if html and self._looks_like_interstitial(html):
                    logger.warning("BrowserPage: still challenged at %s", url)
                    return None
            return html or None
        except Exception as exc:  # noqa: BLE001 - one page failing is not fatal
            logger.warning("BrowserPage: CDP fetch of %s failed: %s", url, exc)
            return None
        finally:
            # Ours to close; the browser is not.
            if page is not None:
                try:
                    page.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("BrowserPage: closing page failed: %s", exc)

    # ── fetching ─────────────────────────────────────────────────────────

    def fetch_html(self, url: str, *, settle_s: float = 0.0) -> str | None:
        """Navigate to `url` and return the rendered document, or None.

        `settle_s` is for pages that finish loading and then fill themselves
        in -- Mouser's price table arrives after `loaded` fires. It is a plain
        sleep because there is no signal to wait on that is not itself a guess;
        keeping it a caller's argument at least makes the guess visible.

        Returns None rather than raising on the ordinary failures (no loop, no
        load, still on a challenge page): one part failing to price is not a
        reason to abandon a run over hundreds of them.
        """
        if not available():
            return None
        endpoint = cdp_endpoint()
        if endpoint:
            with self._lock:
                return self._fetch_over_cdp(endpoint, url, settle_s)
        with self._lock:
            try:
                self._ensure_window()
            except (ImportError, RuntimeError, AttributeError) as exc:
                logger.warning("BrowserPage: cannot create window: %s", exc)
                return None

            self._loaded.clear()
            try:
                self._window.load_url(url)
            except (RuntimeError, AttributeError) as exc:
                logger.warning("BrowserPage: navigation to %s failed: %s", url, exc)
                return None
            if not self._loaded.wait(timeout=LOAD_TIMEOUT_S):
                logger.warning("BrowserPage: load timed out for %s", url)
                return None

            html = self._read_html(settle_s)
            if html and self._looks_like_interstitial(html):
                # Give the challenge its moment to forward, then read once more.
                logger.debug("BrowserPage: interstitial at %s, rechecking", url)
                html = self._read_html(INTERSTITIAL_RECHECK_S)
                if html and self._looks_like_interstitial(html):
                    logger.warning("BrowserPage: still challenged at %s", url)
                    return None
            return html

    def _read_html(self, settle_s: float) -> str | None:
        if settle_s:
            # A page that has fired `loaded` is allowed to keep rendering.
            threading.Event().wait(settle_s)
        try:
            html = self._window.evaluate_js("document.documentElement.outerHTML")
        except (RuntimeError, AttributeError) as exc:
            logger.warning("BrowserPage: reading the document failed: %s", exc)
            return None
        return html if isinstance(html, str) and html else None

    @staticmethod
    def _looks_like_interstitial(html: str) -> bool:
        head = html[:4000].lower()
        return any(marker in head for marker in _INTERSTITIAL_MARKERS)

    def current_url(self) -> str | None:
        """Where the last navigation ended up, which a redirect may have changed.

        Over CDP the page is closed as soon as it is read, so the URL is
        captured during the fetch rather than asked for afterwards. Callers
        depend on this to tell a search that redirected straight to a product
        from one that returned a list -- without it, a product page's own
        related-product links look like search results.
        """
        if cdp_endpoint():
            return self._last_url
        try:
            return self._window.get_current_url() if self._window else None
        except (RuntimeError, AttributeError):
            return None
