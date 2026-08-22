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
