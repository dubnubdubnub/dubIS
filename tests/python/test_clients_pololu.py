"""Tests for PololuClient."""

import urllib.request

import pytest

from pololu_client import PololuClient


class TestPololuClient:
    def test_invalid_sku_raises(self):
        client = PololuClient()
        with pytest.raises(ValueError, match="Invalid Pololu SKU"):
            client.fetch_product("BADSKU")

    def test_invalid_too_long_raises(self):
        client = PololuClient()
        with pytest.raises(ValueError, match="Invalid Pololu SKU"):
            client.fetch_product("1234567")

    def test_valid_sku_accepted(self):
        """Valid numeric SKUs pass validation (will fail at network)."""
        client = PololuClient()
        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            result = client.fetch_product("1992")
            assert result is None  # TimeoutError -> cached None
        finally:
            urllib.request.urlopen = original

    def test_caching(self):
        """Second call returns cached result without network."""
        client = PololuClient()
        original = urllib.request.urlopen
        call_count = [0]

        def fake_urlopen(*args, **kwargs):
            call_count[0] += 1
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            client.fetch_product("1992")
            client.fetch_product("1992")
            assert call_count[0] == 1  # only one network call
        finally:
            urllib.request.urlopen = original

    def test_successful_parse(self):
        """Successful product page is parsed correctly."""
        client = PololuClient()
        original = urllib.request.urlopen

        mock_html = """
        <html>
        <head>
            <meta name="description" content="5-pack of 2x20-pin crimp connector housings">
            <meta property="og:image" content="https://a.pololu-files.com/picture/123.jpg">
            <script type="application/ld+json">
            {
                "@type": "Product",
                "name": "0.1\\\" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack",
                "sku": "1992",
                "mpn": "1992",
                "brand": {"name": "PCX"},
                "image": "https://a.pololu-files.com/picture/123.jpg",
                "offers": {"price": "4.49", "availability": "https://schema.org/InStock"}
            }
            </script>
        </head>
        <body>
            <h1>0.1" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack</h1>
            <div>5+ $4.13  25+ $3.80  100+ $3.50</div>
            <div>250 in stock</div>
        </body>
        </html>
        """.encode()

        class FakeResp:
            def read(self):
                return mock_html
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        urllib.request.urlopen = lambda *a, **kw: FakeResp()
        try:
            product = client.fetch_product("1992")
            assert product is not None
            assert product["productCode"] == "1992"
            assert "Crimp Connector" in product["title"]
            assert product["manufacturer"] == "PCX"
            assert product["stock"] == 250
            assert product["provider"] == "pololu"
            assert product["pololuUrl"] == "https://www.pololu.com/product/1992"
            # Check price tiers
            assert len(product["prices"]) >= 1
            assert product["prices"][0]["price"] == 4.49
        finally:
            urllib.request.urlopen = original

    def test_parse_empty_page_returns_none(self):
        """Empty page with no title returns None."""
        result = PololuClient._parse_product_page("<html><body></body></html>", "9999", "https://www.pololu.com/product/9999")
        assert result is None
