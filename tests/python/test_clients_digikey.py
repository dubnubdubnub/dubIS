"""Tests for DigikeyClient."""

import json

import pytest

from digikey_client import DigikeyClient


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
        assert DigikeyClient._check_cookies_logged_in(cookies) is True

    def test_check_cookies_not_logged_in(self):
        cookies = [{"name": "other"}]
        assert DigikeyClient._check_cookies_logged_in(cookies) is False

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
        result = DigikeyClient._normalize_result(raw, "RES-123")
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
        result = DigikeyClient._normalize_result(raw, "DK-456")
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
        result = DigikeyClient._normalize_result(raw, "X-1")
        assert result["productCode"] == "X-1"
        assert result["stock"] == 0
        assert result["prices"] == []
        assert result["provider"] == "digikey"

    def test_normalize_unknown_format(self):
        """Unknown format returns empty shell with part number."""
        raw = {"random_key": "random_value"}
        result = DigikeyClient._normalize_result(raw, "UNKNOWN-1")
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
        result = DigikeyClient._normalize_result(raw, "X")
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
        result = DigikeyClient._normalize_result(raw, "IMG-1")
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
        result = DigikeyClient._normalize_result(raw, "ABC-123")
        assert result["digikeyUrl"] == "https://www.digikey.com/en/products/detail/ABC-123"


class TestDigikeyCookiePersistence:
    """Verify cookie save/load works correctly."""

    def test_save_and_load_cookies(self, tmp_path):
        cookies_file = str(tmp_path / "dk_cookies.json")
        client = DigikeyClient(cookies_file=cookies_file)
        cookies = [{"name": "dkuhint", "value": "test"}, {"name": "other", "value": "x"}]
        client._save_cookies(cookies)
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
        client._save_cookies(cookies)
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
        client._save_cookies([{"name": "dkuhint"}])  # should not error
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
        client._save_cookies([{"name": "dkuhint"}])
        assert (tmp_path / "dk_cookies.json").exists()
        # Logout
        client.logout()
        assert not (tmp_path / "dk_cookies.json").exists()
