"""Tests for LcscClient."""

import json
import urllib.request

import pytest

from lcsc_client import LcscClient


class TestLcscClient:
    def test_invalid_product_code_raises(self):
        client = LcscClient()
        with pytest.raises(ValueError, match="Invalid LCSC product code"):
            client.fetch_product("BADCODE")

    def test_invalid_too_short_raises(self):
        client = LcscClient()
        with pytest.raises(ValueError, match="Invalid LCSC product code"):
            client.fetch_product("C12")

    def test_valid_code_formats_accepted(self):
        """Codes matching C + 4+ digits pass validation (will fail at network)."""
        client = LcscClient()
        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            result = client.fetch_product("C2040")
            assert result is None  # TimeoutError -> cached None
        finally:
            urllib.request.urlopen = original

    def test_caching(self):
        """Second call returns cached result without network."""
        client = LcscClient()
        original = urllib.request.urlopen
        call_count = [0]

        def fake_urlopen(*args, **kwargs):
            call_count[0] += 1
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            client.fetch_product("C10000")
            client.fetch_product("C10000")
            assert call_count[0] == 1  # only one network call
        finally:
            urllib.request.urlopen = original

    def test_request_sends_user_agent(self):
        """Request must carry an explicit User-Agent.

        LCSC's API returns HTTP 403 for the default ``Python-urllib/x.y``
        agent, which surfaces as a "Product not found" tooltip. A non-default
        User-Agent header avoids the block.
        """
        client = LcscClient()
        original = urllib.request.urlopen
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"result": {"productCode": "C2040"}}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_urlopen(req, *a, **kw):
            captured["ua"] = req.get_header("User-agent")
            return FakeResp()

        urllib.request.urlopen = fake_urlopen
        try:
            client.fetch_product("C2040")
        finally:
            urllib.request.urlopen = original

        assert captured["ua"], "request sent no User-Agent header"
        assert "python-urllib" not in captured["ua"].lower()

    def test_successful_fetch(self):
        """Successful API response is normalized correctly."""
        client = LcscClient()
        original = urllib.request.urlopen

        mock_response = json.dumps({
            "result": {
                "productCode": "C2040",
                "title": "100nF Cap",
                "brandNameEn": "Samsung",
                "productModel": "CL05A104KA5NNNC",
                "encapStandard": "0402",
                "productIntroEn": "100nF MLCC",
                "stockNumber": 50000,
                "productPriceList": [
                    {"ladder": 1, "productPrice": 0.0025},
                    {"ladder": 100, "productPrice": 0.001},
                ],
                "parentCatalogList": [
                    {"catalogName": "Capacitors"},
                    {"catalogName": "MLCC"},
                ],
                "paramVOList": [
                    {"paramNameEn": "Capacitance", "paramValueEn": "100nF"},
                ],
                "productImages": ["https://example.com/img.jpg"],
                "pdfUrl": "https://example.com/ds.pdf",
            }
        }).encode()

        class FakeResp:
            def read(self):
                return mock_response
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        urllib.request.urlopen = lambda *a, **kw: FakeResp()
        try:
            product = client.fetch_product("C2040")
            assert product is not None
            assert product["productCode"] == "C2040"
            assert product["manufacturer"] == "Samsung"
            assert product["mpn"] == "CL05A104KA5NNNC"
            assert product["package"] == "0402"
            assert product["stock"] == 50000
            assert len(product["prices"]) == 2
            assert product["prices"][0]["qty"] == 1
            assert product["category"] == "Capacitors"
            assert product["subcategory"] == "MLCC"
            assert len(product["attributes"]) == 1
            assert product["imageUrl"] == "https://example.com/img.jpg"
            assert product["provider"] == "lcsc"
        finally:
            urllib.request.urlopen = original
