"""Tests for DigikeyClient."""

import json

import pytest

from digikey_client import DigikeyClient, hidden_window_available
from digikey_normalizer import normalize_result
from digikey_session import check_cookies_logged_in, save_cookies_to_file
from dubis_errors import DistributorError


class TestDigikeyClient:
    def test_empty_part_number_raises(self):
        client = DigikeyClient()
        with pytest.raises(ValueError, match="empty"):
            client.fetch_product("")

    def test_whitespace_part_number_raises(self):
        client = DigikeyClient()
        with pytest.raises(ValueError, match="empty"):
            client.fetch_product("   ")

    def test_caching(self):
        """Pre-populated cache returns without network."""
        client = DigikeyClient()
        cached = {"productCode": "DK-123", "provider": "digikey"}
        client._cache["DK-123"] = cached
        assert client.fetch_product("DK-123") is cached

    def test_none_cached(self):
        """None values are cached and returned."""
        client = DigikeyClient()
        client._cache["NOPE"] = None
        assert client.fetch_product("NOPE") is None

    def test_sync_cookies_no_login(self):
        """sync_cookies returns error when login not started."""
        client = DigikeyClient()
        result = client.sync_cookies()
        assert result["status"] == "error"
        assert result["logged_in"] is False

    def test_login_status_default(self):
        """Default login status is not logged in."""
        client = DigikeyClient()
        assert client.get_login_status() == {"logged_in": False}

    def test_login_status_with_pending_cookies(self):
        """Login status checks pending cookies."""
        client = DigikeyClient()
        client._pending_cookies = [{"name": "dkuhint", "value": "test"}]
        assert client.get_login_status() == {"logged_in": True}

    def test_login_status_with_sync_result(self):
        """Login status checks sync result."""
        client = DigikeyClient()
        client._sync_result = {"logged_in": True}
        assert client.get_login_status() == {"logged_in": True}

    def test_check_cookies_logged_in(self):
        cookies = [{"name": "dkuhint"}, {"name": "other"}]
        assert check_cookies_logged_in(cookies) is True

    def test_check_cookies_not_logged_in(self):
        cookies = [{"name": "other"}]
        assert check_cookies_logged_in(cookies) is False

    def test_normalize_jsonld_product(self):
        raw = {
            "@type": "Product",
            "name": "Test Resistor",
            "sku": "RES-123",
            "mpn": "RC0402FR-0710KL",
            "description": "10k Resistor",
            "brand": {"name": "Yageo"},
            "image": "https://example.com/img.jpg",
            "url": "https://www.digikey.com/product/123",
            "offers": {
                "price": "0.10",
                "availability": "InStock",
            },
            "_stock": 5000,
        }
        result = normalize_result(raw, "RES-123")
        assert result["productCode"] == "RES-123"
        assert result["title"] == "Test Resistor"
        assert result["manufacturer"] == "Yageo"
        assert result["mpn"] == "RC0402FR-0710KL"
        assert result["stock"] == 5000
        assert result["prices"] == [{"qty": 1, "price": 0.10}]
        assert result["provider"] == "digikey"

    def test_normalize_nextdata_envelope(self):
        """Test the new Next.js SSR envelope.data structure (PR #83)."""
        raw = {
            "_source": "nextdata",
            "_props": {
                "envelope": {
                    "data": {
                        "productOverview": {
                            "rolledUpProductNumber": "DK-456",
                            "title": "Cap 100nF",
                            "manufacturer": "TDK",
                            "manufacturerProductNumber": "C0402C104K4RAC",
                            "detailedDescription": "100nF 16V Ceramic Cap",
                            "datasheetUrl": "https://example.com/ds.pdf",
                        },
                        "priceQuantity": {
                            "qtyAvailable": "10,000",
                            "pricing": [{
                                "mergedPricingTiers": [
                                    {"brkQty": "1", "unitPrice": "$0.10"},
                                    {"brkQty": "100", "unitPrice": "$0.05"},
                                ],
                            }],
                        },
                        "productAttributes": {
                            "attributes": [
                                {
                                    "id": "1",
                                    "label": "Package / Case",
                                    "values": [{"value": "0402"}],
                                },
                                {
                                    "id": "2",
                                    "label": "Capacitance",
                                    "values": [{"value": "100nF"}],
                                },
                                {
                                    "id": "-1",
                                    "label": "Skip This",
                                    "values": [{"value": "skipped"}],
                                },
                            ],
                            "categories": [
                                {"label": "Capacitors"},
                                {"label": "Ceramic"},
                            ],
                        },
                        "carouselMedia": [
                            {"type": "Image", "displayUrl": "//img.digikey.com/photo.jpg"},
                        ],
                        "breadcrumb": [
                            {"url": "/en/products/detail/DK-456"},
                        ],
                    },
                },
            },
        }
        result = normalize_result(raw, "DK-456")
        assert result["productCode"] == "DK-456"
        assert result["title"] == "Cap 100nF"
        assert result["manufacturer"] == "TDK"
        assert result["mpn"] == "C0402C104K4RAC"
        assert result["stock"] == 10000
        assert result["package"] == "0402"
        assert result["description"] == "100nF 16V Ceramic Cap"
        assert result["pdfUrl"] == "https://example.com/ds.pdf"
        assert result["imageUrl"] == "https://img.digikey.com/photo.jpg"
        assert result["digikeyUrl"] == "https://www.digikey.com/en/products/detail/DK-456"
        assert result["category"] == "Ceramic"
        assert result["subcategory"] == "Capacitors"
        assert result["provider"] == "digikey"
        # Prices
        assert len(result["prices"]) == 2
        assert result["prices"][0] == {"qty": 1, "price": 0.10}
        assert result["prices"][1] == {"qty": 100, "price": 0.05}
        # Attributes: should skip id=-1, include id=1 and id=2
        attr_names = [a["name"] for a in result["attributes"]]
        assert "Package / Case" in attr_names
        assert "Capacitance" in attr_names
        assert "Skip This" not in attr_names

    def test_normalize_nextdata_empty_envelope(self):
        """Nextdata with empty envelope should return empty shell."""
        raw = {"_source": "nextdata", "_props": {}}
        result = normalize_result(raw, "X-1")
        assert result["productCode"] == "X-1"
        assert result["stock"] == 0
        assert result["prices"] == []
        assert result["provider"] == "digikey"

    def test_normalize_unknown_format(self):
        """Unknown format returns empty shell with part number."""
        raw = {"random_key": "random_value"}
        result = normalize_result(raw, "UNKNOWN-1")
        assert result["productCode"] == "UNKNOWN-1"
        assert result["title"] == ""
        assert result["stock"] == 0
        assert result["provider"] == "digikey"

    def test_normalize_jsonld_list_offers(self):
        """Handles offers as array."""
        raw = {
            "@type": "Product",
            "name": "Test",
            "sku": "X",
            "offers": [{"price": "1.50"}],
            "brand": {},
            "image": [],
        }
        result = normalize_result(raw, "X")
        assert result["prices"] == [{"qty": 1, "price": 1.50}]
        assert result["imageUrl"] == ""

    def test_normalize_nextdata_protocol_relative_image(self):
        """Protocol-relative image URLs get https: prepended."""
        raw = {
            "_source": "nextdata",
            "_props": {
                "envelope": {
                    "data": {
                        "carouselMedia": [
                            {"type": "Image", "displayUrl": "//img.example.com/photo.jpg"},
                        ],
                        "productOverview": {},
                        "priceQuantity": {},
                        "productAttributes": {},
                    },
                },
            },
        }
        result = normalize_result(raw, "IMG-1")
        assert result["imageUrl"] == "https://img.example.com/photo.jpg"

    def test_normalize_nextdata_absolute_breadcrumb_url(self):
        """Absolute breadcrumb URLs are kept as-is."""
        raw = {
            "_source": "nextdata",
            "_props": {
                "envelope": {
                    "data": {
                        "breadcrumb": [
                            {"url": "https://www.digikey.com/en/products/detail/ABC-123"},
                        ],
                        "productOverview": {},
                        "priceQuantity": {},
                        "productAttributes": {},
                    },
                },
            },
        }
        result = normalize_result(raw, "ABC-123")
        assert result["digikeyUrl"] == "https://www.digikey.com/en/products/detail/ABC-123"


class TestDigikeyCookiePersistence:
    """Verify cookie save/load works correctly."""

    def test_save_and_load_cookies(self, tmp_path):
        cookies_file = str(tmp_path / "dk_cookies.json")
        client = DigikeyClient(cookies_file=cookies_file)
        cookies = [{"name": "dkuhint", "value": "test"}, {"name": "other", "value": "x"}]
        save_cookies_to_file(cookies, cookies_file)
        loaded = client._load_cookies()
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["name"] == "dkuhint"

    def test_load_cookies_no_file(self, tmp_path):
        cookies_file = str(tmp_path / "nonexistent.json")
        client = DigikeyClient(cookies_file=cookies_file)
        assert client._load_cookies() is None

    def test_load_cookies_not_logged_in(self, tmp_path):
        """Cookies without dkuhint should not be returned."""
        cookies_file = str(tmp_path / "dk_cookies.json")
        client = DigikeyClient(cookies_file=cookies_file)
        cookies = [{"name": "other_cookie", "value": "test"}]
        save_cookies_to_file(cookies, cookies_file)
        assert client._load_cookies() is None

    def test_load_cookies_corrupt_json(self, tmp_path):
        cookies_file = str(tmp_path / "dk_cookies.json")
        with open(cookies_file, "w") as f:
            f.write("{bad json!!")
        client = DigikeyClient(cookies_file=cookies_file)
        assert client._load_cookies() is None

    def test_no_cookies_file_configured(self):
        """Client without cookies_file skips persistence."""
        client = DigikeyClient()
        save_cookies_to_file([{"name": "dkuhint"}], None)  # should not error
        assert client._load_cookies() is None

    def test_set_logged_in_persists(self, tmp_path):
        cookies_file = str(tmp_path / "dk_cookies.json")
        client = DigikeyClient(cookies_file=cookies_file)
        cookies = [{"name": "dkuhint", "value": "test"}]
        client._set_logged_in(cookies)
        assert client._sync_result["logged_in"] is True
        assert client._pending_cookies == cookies
        # Verify file was written
        with open(cookies_file) as f:
            saved = json.load(f)
        assert len(saved) == 1

    def test_logout_removes_cookie_file(self, tmp_path):
        cookies_file = str(tmp_path / "dk_cookies.json")
        client = DigikeyClient(cookies_file=cookies_file)
        # Save cookies
        save_cookies_to_file([{"name": "dkuhint"}], cookies_file)
        assert (tmp_path / "dk_cookies.json").exists()
        # Logout
        client.logout()
        assert not (tmp_path / "dk_cookies.json").exists()


class TestHiddenWindowAvailability:
    """The GUI-loop guard around DigiKey's hidden pywebview window.

    Every test here monkeypatches `webview.windows` rather than relying on the
    ambient state of the test process, so both halves — "no loop" and "the
    desktop app's loop" — are exercised on every platform.

    Background: pywebview only has a GUI loop after `webview.start()`, which
    the desktop app calls and a headless `python -m server` (or the container)
    does not. Without one, `create_window` still returns a Window and appends
    it to the process-wide `webview.windows`, but nothing initializes it: the
    first `load_url` blocks 20s and raises `WebViewException` — a plain
    `Exception`, so it escaped `validate_session`'s `except (RuntimeError,
    OSError)` and turned `POST /v1/distributors/digikey/session/validate`
    (which the frontend calls at startup whenever cookies exist) into a 500,
    35 seconds late.
    """

    def test_unavailable_without_a_gui_loop(self, monkeypatch):
        import webview

        monkeypatch.setattr(webview, "windows", [])
        assert hidden_window_available() is False

    def test_available_once_a_window_exists(self, monkeypatch):
        import webview

        monkeypatch.setattr(webview, "windows", [object()])
        assert hidden_window_available() is True

    def test_validate_session_is_inconclusive_without_a_window(self, monkeypatch):
        """Answer truthfully instead of probing a window that cannot load.

        Inconclusive, not expired: a session synced onto a headless server is
        not dead just because nothing here can open a browser to check, and
        `_invalidate_session` would delete its cookie file.
        """
        import webview

        monkeypatch.setattr(webview, "windows", [])
        client = DigikeyClient()
        client._pending_cookies = [{"name": "dkuhint", "value": "test"}]

        def _must_not_probe():
            raise AssertionError("probed the window despite there being no GUI loop")

        monkeypatch.setattr(client, "_probe_session", _must_not_probe)

        result = client.validate_session()
        assert result["logged_in"] is True      # session kept
        assert result["changed"] is False       # ...and not invalidated
        assert result["supported"] is False     # ...and says why
        assert client._pending_cookies          # cookies untouched

    def test_validate_session_still_probes_on_the_desktop(self, monkeypatch):
        """The guard must not disable validation where it does work."""
        import webview

        monkeypatch.setattr(webview, "windows", [object()])
        client = DigikeyClient()
        client._pending_cookies = [{"name": "dkuhint", "value": "test"}]
        monkeypatch.setattr(client, "_probe_session", lambda: True)

        result = client.validate_session()
        assert result == {
            "logged_in": True, "changed": False, "message": "Session valid",
        }

    def test_ensure_window_raises_rather_than_making_a_phantom(self, monkeypatch):
        """No loop means no window — not a window that can never load.

        The phantom would be appended to `webview.windows`, which is exactly
        what `hidden_window_available()` and `browser_page.available()` read
        to decide a loop exists, so creating one makes both of them lie from
        then on.
        """
        import webview

        windows = []
        monkeypatch.setattr(webview, "windows", windows)
        client = DigikeyClient()
        with pytest.raises(DistributorError, match="browser window"):
            client._ensure_window()
        assert windows == []
        assert client._window is None

    def test_failed_fetch_leaves_browser_page_unavailable(self, monkeypatch):
        """The cross-module half: DigiKey must not poison `browser_page`.

        Both read the same process-wide `webview.windows`, so a DigiKey fetch
        that left a phantom window behind would make `browser_page.available()`
        claim a renderer this process does not have — and Mouser's keyless
        path would stop falling back.
        """
        import webview

        import browser_page

        monkeypatch.delenv("DUBIS_CDP_URL", raising=False)
        monkeypatch.setattr(webview, "windows", [])
        client = DigikeyClient()
        with pytest.raises(DistributorError, match="browser window"):
            client.fetch_product("296-1234-1-ND")
        assert browser_page.available() is False
