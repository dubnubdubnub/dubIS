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

DESKTOP ONLY, BY THE SAME RULE AS THE REST
`webview` needs a GUI event loop that a container does not have, so
`available()` is false there and callers fall back to whatever they did
before. This is the boundary CLAUDE.md already draws around DigiKey scraping,
OS file dialogs and OCR -- one more feature on the desktop side of it, not a
new kind of gap.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# How long to wait for a navigation to fire `loaded`.
LOAD_TIMEOUT_S = 20
# A challenge page fires `loaded` before its JS forwards to the real page, so
# one recheck after a pause distinguishes "still interstitial" from "arrived".
INTERSTITIAL_RECHECK_S = 4
_INTERSTITIAL_MARKERS = ("just a moment", "checking your browser",
                         "verifying you are human", "attention required")


def available() -> bool:
    """Whether a hidden window can be created in this process.

    False in the container (no `webview`) and false in a plain CLI, where
    nothing has called `webview.start()` so there is no loop to attach to.
    """
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
        """Drop the window. Safe to call when there is none."""
        with self._lock:
            window, self._window = self._window, None
        if window is None:
            return
        try:
            window.destroy()
        except (AttributeError, RuntimeError) as exc:
            logger.debug("BrowserPage: destroy failed: %s", exc)

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
        """Where the window ended up, which a redirect may have changed."""
        try:
            return self._window.get_current_url() if self._window else None
        except (RuntimeError, AttributeError):
            return None
