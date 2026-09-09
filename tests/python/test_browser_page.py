"""Tests for browser_page — the hidden-window HTML fetch.

The window itself needs a pywebview event loop, which no test process has, so
what is tested here is everything around it: that its absence is detected
rather than raised, that a challenge page is not mistaken for content, and
that the ordinary failures come back as None.
"""

import browser_page
from browser_page import BrowserPage


class TestAvailability:
    def test_no_gui_loop_means_unavailable(self):
        """A test process, like the container, has no loop to attach to."""
        assert browser_page.available() is False

    def test_fetch_is_a_no_op_when_unavailable(self):
        """Callers fall back to their old path; they do not handle an exception."""
        assert BrowserPage().fetch_html("https://example.com") is None

    def test_available_is_false_when_webview_is_absent(self, monkeypatch):
        """The container has no pywebview at all."""
        import builtins
        real_import = builtins.__import__

        def no_webview(name, *args, **kwargs):
            if name == "webview":
                raise ImportError("no webview here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_webview)
        assert browser_page.available() is False

    def test_available_is_false_when_the_loop_has_not_started(self, monkeypatch):
        """pywebview imports fine in a CLI; it just has no running loop."""
        import sys
        import types
        stub = types.ModuleType("webview")
        stub.windows = []
        monkeypatch.setitem(sys.modules, "webview", stub)
        assert browser_page.available() is False
        stub.windows = [object()]
        assert browser_page.available() is True


class TestInterstitialDetection:
    def test_a_challenge_page_is_not_content(self):
        for title in ("Just a moment...", "Checking your browser before access",
                      "Attention Required! | Cloudflare",
                      "Verifying you are human"):
            html = f"<html><head><title>{title}</title></head><body></body></html>"
            assert BrowserPage._looks_like_interstitial(html) is True

    def test_a_real_page_is_content(self):
        html = "<html><head><title>GRM155R71H103KA88D | Mouser</title></head></html>"
        assert BrowserPage._looks_like_interstitial(html) is False

    def test_only_the_head_of_the_document_is_examined(self):
        """A product page that happens to quote the phrase far down is not a block."""
        html = "<html><head><title>Real Product</title></head><body>" \
               + ("x" * 5000) + "just a moment</body></html>"
        assert BrowserPage._looks_like_interstitial(html) is False


class TestLifecycle:
    def test_destroy_without_a_window_is_harmless(self):
        BrowserPage().destroy()

    def test_current_url_without_a_window_is_none(self):
        assert BrowserPage().current_url() is None


# ── the shared-browser (CDP) backend ─────────────────────────────────────────
# The browser lives in the cluster and is shared with other consumers, so the
# rules that matter are about being a good guest: reuse the existing profile,
# close what you opened, and never take the process down.

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _no_cdp_by_default(monkeypatch):
    """Keep the desktop-path tests honest if a real endpoint is configured."""
    monkeypatch.delenv(browser_page.CDP_ENV, raising=False)
    monkeypatch.delenv(browser_page.CDP_PAUSE_ENV, raising=False)


class FakePage:
    def __init__(self, context, html, url):
        self._context = context
        self._html = html
        self.url = url
        self.closed = False
        self.goto_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)

    def content(self):
        return self._html() if callable(self._html) else self._html

    def wait_for_timeout(self, ms):
        self._context.waits.append(ms)

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, html, url):
        self._html, self._url = html, url
        self.pages = []
        self.waits = []

    def new_page(self):
        page = FakePage(self, self._html, self._url)
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts
        self.closed = False

    def close(self):
        self.closed = True


def install_fake_playwright(monkeypatch, *, html="<html></html>",
                            url="https://example.com/landed", contexts=None):
    """Stand in for playwright.sync_api so no real browser is needed."""
    shared = FakeContext(html, url)
    browser = FakeBrowser(contexts if contexts is not None else [shared])
    state = {"browser": browser, "shared": shared, "endpoints": [],
             "stopped": False, "extra_contexts": 0}

    class FakeChromium:
        def connect_over_cdp(self, endpoint):
            state["endpoints"].append(endpoint)
            return browser

    class FakePw:
        chromium = FakeChromium()

        def stop(self):
            state["stopped"] = True

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: FakePw())
    pkg = types.ModuleType("playwright")
    pkg.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return state


class TestCdpSelection:
    def test_endpoint_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "  http://browser:9222  ")
        assert browser_page.cdp_endpoint() == "http://browser:9222"

    def test_blank_endpoint_is_no_endpoint(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "   ")
        assert browser_page.cdp_endpoint() is None

    def test_a_configured_endpoint_makes_the_container_capable(self, monkeypatch):
        """No GUI loop here, yet rendering is possible — that is the whole point."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        assert browser_page.available() is True


class TestSharedBrowserEtiquette:
    def test_it_reuses_the_existing_profile_not_a_fresh_one(self, monkeypatch):
        """`new_context()` would be logged out; contexts[0] holds the cookies."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch, html="<html>ok</html>")
        page = browser_page.BrowserPage()
        assert page.fetch_html("https://example.com") == "<html>ok</html>"
        assert state["shared"].pages, "should have opened a page on contexts[0]"
        assert not hasattr(state["browser"], "new_context_called")

    def test_it_closes_its_own_page(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch)
        browser_page.BrowserPage().fetch_html("https://example.com")
        assert state["shared"].pages[0].closed is True

    def test_it_does_not_close_the_shared_browser_on_a_fetch(self, monkeypatch):
        """Detaching per fetch would churn a browser other consumers are using."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch)
        page = browser_page.BrowserPage()
        page.fetch_html("https://example.com")
        page.fetch_html("https://example.com/2")
        assert state["browser"].closed is False
        assert len(state["endpoints"]) == 1, "connection should be reused"

    def test_the_page_is_closed_even_when_the_fetch_raises(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")

        def boom():
            raise RuntimeError("render exploded")

        state = install_fake_playwright(monkeypatch, html=boom)
        assert browser_page.BrowserPage().fetch_html("https://example.com") is None
        assert state["shared"].pages[0].closed is True

    def test_destroy_detaches_rather_than_leaking(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch)
        page = browser_page.BrowserPage()
        page.fetch_html("https://example.com")
        page.destroy()
        assert state["browser"].closed is True and state["stopped"] is True

    def test_a_browser_with_no_context_is_refused(self, monkeypatch):
        """Rather than making one, which would be a logged-out profile."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        install_fake_playwright(monkeypatch, contexts=[])
        assert browser_page.BrowserPage().fetch_html("https://example.com") is None


class TestCdpFetchBehaviour:
    def test_the_landed_url_survives_the_page_being_closed(self, monkeypatch):
        """Callers use it to tell a redirect-to-product from a results list."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        install_fake_playwright(monkeypatch, url="https://m.com/ProductDetail/X")
        page = browser_page.BrowserPage()
        page.fetch_html("https://m.com/search?q=X")
        assert page.current_url() == "https://m.com/ProductDetail/X"

    def test_a_challenge_page_is_rechecked_then_given_up_on(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(
            monkeypatch, html="<html><title>Just a moment...</title></html>")
        assert browser_page.BrowserPage().fetch_html("https://example.com") is None
        assert browser_page.INTERSTITIAL_RECHECK_S * 1000 in state["shared"].waits

    def test_settle_is_honoured(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch)
        browser_page.BrowserPage().fetch_html("https://example.com", settle_s=2.5)
        assert 2500 in state["shared"].waits

    def test_a_dropped_connection_is_reattached_without_a_second_driver(
        self, monkeypatch,
    ):
        """Someone restarting the browser mid-run must cost one fetch, not all
        of them. Playwright's sync API refuses to start a second driver in a
        thread that already has a live one, and refuses through the asyncio
        guard -- so restarting it turned the first hiccup into a permanent
        "cannot run inside an asyncio loop" for every later part."""
        monkeypatch.setenv(browser_page.CDP_ENV, "http://browser:9222")
        state = install_fake_playwright(monkeypatch, html="<html>ok</html>")
        browser = state["browser"]
        starts: list[int] = []
        attempts: list[str] = []

        def flaky_connect(endpoint):
            attempts.append(endpoint)
            if len(attempts) == 1:
                raise OSError("connection refused")
            return browser

        def start():
            starts.append(1)
            return types.SimpleNamespace(
                chromium=types.SimpleNamespace(connect_over_cdp=flaky_connect),
                stop=lambda: None,
            )

        monkeypatch.setattr(sys.modules["playwright.sync_api"], "sync_playwright",
                            lambda: types.SimpleNamespace(start=start))

        page = browser_page.BrowserPage()
        assert page.fetch_html("https://example.com") is None
        assert page.fetch_html("https://example.com") == "<html>ok</html>"
        assert len(attempts) == 2, "the second fetch should have reattached"
        assert len(starts) == 1, "the driver should have been started once"

    def test_an_unreachable_endpoint_is_a_None_not_a_crash(self, monkeypatch):
        monkeypatch.setenv(browser_page.CDP_ENV, "http://nowhere:9222")

        class Exploding:
            def connect_over_cdp(self, endpoint):
                raise OSError("connection refused")

        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: types.SimpleNamespace(
            start=lambda: types.SimpleNamespace(chromium=Exploding()))
        pkg = types.ModuleType("playwright")
        pkg.sync_api = module
        monkeypatch.setitem(sys.modules, "playwright", pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
        assert browser_page.BrowserPage().fetch_html("https://example.com") is None


class TestHumanPause:
    def test_it_is_right_skewed_not_symmetric(self):
        """A Gaussian would put as much mass below the median as above."""
        import statistics
        draws = [browser_page.human_pause(20) for _ in range(4000)]
        assert statistics.mean(draws) > statistics.median(draws)

    def test_the_median_is_the_median(self):
        import statistics
        draws = [browser_page.human_pause(20) for _ in range(4000)]
        assert 17 < statistics.median(draws) < 23

    def test_it_never_returns_a_burst_or_a_stall(self):
        draws = [browser_page.human_pause(10) for _ in range(2000)]
        assert min(draws) >= 2.5 and max(draws) <= 60.0

    def test_sigma_zero_is_the_old_fixed_delay(self):
        assert browser_page.human_pause(7, sigma=0) == 7.0

    def test_no_delay_asked_for_is_no_delay_given(self):
        assert browser_page.human_pause(0) == 0.0

    def test_successive_draws_differ(self):
        """A constant gap is the signature the jitter exists to remove."""
        draws = {browser_page.human_pause(5) for _ in range(50)}
        assert len(draws) > 40
