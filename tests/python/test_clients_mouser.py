"""Tests for MouserClient."""

import json
import logging
import urllib.request

import pytest

import mouser_client
from mouser_client import MouserClient


class TestMouserClient:
    def test_invalid_empty_raises(self):
        client = MouserClient()
        with pytest.raises(ValueError, match="Invalid Mouser part number"):
            client.fetch_product("")

    def test_invalid_too_long_raises(self):
        client = MouserClient()
        with pytest.raises(ValueError, match="Invalid Mouser part number"):
            client.fetch_product("x" * 61)

    def test_valid_part_number_accepted(self):
        """Valid Mouser PNs pass validation (will fail at network)."""
        client = MouserClient()
        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            result = client.fetch_product("736-FGG0B305CLAD52")
            assert result is None
        finally:
            urllib.request.urlopen = original

    def test_caching(self):
        """Second call returns cached result without network."""
        client = MouserClient()
        original = urllib.request.urlopen
        call_count = [0]

        def fake_urlopen(*args, **kwargs):
            call_count[0] += 1
            raise TimeoutError("mocked")

        urllib.request.urlopen = fake_urlopen
        try:
            client.fetch_product("736-FGG0B305CLAD52")
            client.fetch_product("736-FGG0B305CLAD52")
            assert call_count[0] == 1
        finally:
            urllib.request.urlopen = original

    def test_successful_parse_all_tooltip_fields(self):
        """Verify all fields used by renderTooltip() are populated correctly.

        The part-preview tooltip renders: productCode, title, description,
        imageUrl, manufacturer, mpn, package, category, subcategory,
        attributes, stock, prices, pdfUrl, mouserUrl, provider.
        """
        client = MouserClient()
        original = urllib.request.urlopen

        mock_html = """
        <html>
        <head>
            <meta name="description" content="Circular Push Pull Connectors LEMO 0B series">
            <meta property="og:image" content="https://www.mouser.com/images/lemo/lrg/FGG0B305.jpg">
            <script type="application/ld+json">
            {
                "@type": "Product",
                "name": "FGG.0B.305.CLAD52 Circular Push Pull Connector",
                "sku": "736-FGG0B305CLAD52",
                "mpn": "FGG.0B.305.CLAD52",
                "brand": {"name": "LEMO"},
                "image": "https://www.mouser.com/images/lemo/lrg/FGG0B305.jpg",
                "description": "Circular Push Pull Connectors LEMO 0B series 5-pos",
                "offers": {"price": "37.55", "availability": "https://schema.org/InStock"}
            }
            </script>
        </head>
        <body>
            <h1>FGG.0B.305.CLAD52 Circular Push Pull Connector</h1>
            <a class="breadcrumb-item" href="#">Connectors</a>
            <a class="breadcrumb-item" href="#">Circular</a>
            <div>500 In Stock</div>
            <div>10+ $35.00  25+ $33.50</div>
            <table>
                <tr><th>Contact Gender</th><td>Plug</td></tr>
                <tr><th>Number of Contacts</th><td>5</td></tr>
            </table>
            <a href="https://www.mouser.com/datasheet/FGG0B305.pdf">Datasheet</a>
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
            product = client.fetch_product("736-FGG0B305CLAD52")
            assert product is not None

            # -- Every field the tooltip renders --
            assert product["productCode"] == "736-FGG0B305CLAD52"
            assert "Connector" in product["title"]
            assert product["title"]  # non-empty
            assert product["description"]  # non-empty
            assert product["imageUrl"].startswith("https://")
            assert product["manufacturer"] == "LEMO"
            assert product["mpn"] == "FGG.0B.305.CLAD52"
            assert isinstance(product["package"], str)  # may be empty for connectors
            assert product["category"]  # non-empty
            assert isinstance(product["subcategory"], str)
            assert len(product["attributes"]) >= 1
            for attr in product["attributes"]:
                assert "name" in attr and "value" in attr
            assert isinstance(product["stock"], int)
            assert product["stock"] > 0
            assert len(product["prices"]) >= 1
            for p in product["prices"]:
                assert isinstance(p["qty"], int)
                assert isinstance(p["price"], float)
            assert isinstance(product["pdfUrl"], str)
            assert product["mouserUrl"] == "https://www.mouser.com/ProductDetail/736-FGG0B305CLAD52"
            assert product["provider"] == "mouser"
        finally:
            urllib.request.urlopen = original

    def test_parse_empty_page_returns_none(self):
        """Empty page with no title returns None."""
        result = MouserClient._parse_product_page(
            "<html><body></body></html>",
            "NOPE",
            "https://www.mouser.com/ProductDetail/NOPE",
        )
        assert result is None

    def test_parse_graph_wrapped_jsonld(self):
        """Mouser pages that wrap the Product in @graph parse correctly.

        Reproduces the "Product not found" tooltip bug: with @graph wrapping,
        extract_jsonld_product was returning None, falling back to <h1>, and
        when the page lacked an SSR <h1> the parse returned None.
        """
        mock_html = """
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "BreadcrumbList", "itemListElement": []},
                    {"@type": "Organization", "name": "Mouser Electronics"},
                    {
                        "@type": "Product",
                        "name": "FGG.0B.305.CLAD52 Circular Connector",
                        "sku": "736-FGG0B305CLAD52",
                        "mpn": "FGG.0B.305.CLAD52",
                        "brand": {"name": "LEMO"},
                        "image": "https://www.mouser.com/images/lemo/lrg/FGG0B305.jpg",
                        "description": "LEMO 0B series 5-position connector",
                        "offers": {
                            "price": "37.55",
                            "availability": "https://schema.org/InStock"
                        }
                    }
                ]
            }
            </script>
        </head>
        <body></body>
        </html>
        """
        result = MouserClient._parse_product_page(
            mock_html,
            "736-FGG0B305CLAD52",
            "https://www.mouser.com/ProductDetail/736-FGG0B305CLAD52",
        )
        assert result is not None
        assert result["productCode"] == "736-FGG0B305CLAD52"
        assert result["title"] == "FGG.0B.305.CLAD52 Circular Connector"
        assert result["manufacturer"] == "LEMO"
        assert result["mpn"] == "FGG.0B.305.CLAD52"
        assert result["stock"] == 1
        assert result["prices"] == [{"qty": 1, "price": 37.55}]
        assert result["provider"] == "mouser"

    def test_fetch_logs_diagnostics_when_parse_fails(self, caplog):
        """When the page fails to parse, _fetch_raw logs diagnostics for debugging.

        Same diagnostic pattern as PR #204 for DigiKey: log URL, response title,
        body length, and JSON-LD count so we can tell bot-block pages apart from
        format changes without needing to reproduce locally.
        """
        client = MouserClient()
        original = urllib.request.urlopen

        # Bot-block page: HTTP 200 but no JSON-LD and an "Access Denied" h1.
        denied_page = (
            b"<html><head><title>Access to this page has been denied.</title></head>"
            b"<body><h1>Access Denied</h1></body></html>"
        )

        class FakeResp:
            def read(self):
                return denied_page
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        urllib.request.urlopen = lambda *a, **kw: FakeResp()
        try:
            with caplog.at_level(logging.WARNING, logger="mouser_client"):
                result = client.fetch_product("BLOCKED-PART")
            # The h1 "Access Denied" causes the parser to return a partial dict
            # rather than None — but we should at least see a diagnostic warning.
            assert any(
                "BLOCKED-PART" in rec.message and "Access" in rec.message
                for rec in caplog.records
            ), f"Expected diagnostic warning, got: {[r.message for r in caplog.records]}"
            # The result should be None — parse should detect the bot-block title.
            assert result is None
        finally:
            urllib.request.urlopen = original


class TestMouserApiKey:
    """API key storage on disk (mirrors DigiKey cookie persistence pattern)."""

    def test_no_credentials_file_returns_none(self):
        client = MouserClient()
        assert client.get_api_key() is None

    def test_save_and_load_roundtrip(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("abc-123-secret")
        # Status method should reflect the saved key.
        status = client.get_api_key_status()
        assert status["configured"] is True
        # New client instance loads the same key from disk.
        client2 = MouserClient(credentials_file=creds)
        assert client2.get_api_key() == "abc-123-secret"

    def test_set_strips_whitespace(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("  key-with-padding  \n")
        assert client.get_api_key() == "key-with-padding"

    def test_set_empty_clears_credentials(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("real-key")
        assert client.get_api_key_status()["configured"] is True
        client.set_api_key("")
        assert client.get_api_key_status()["configured"] is False
        assert not (tmp_path / "mouser_credentials.json").exists()

    def test_clear_removes_file(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")
        client.clear_api_key()
        assert not (tmp_path / "mouser_credentials.json").exists()
        assert client.get_api_key() is None

    def test_set_clears_session_cache(self, tmp_path):
        """Changing keys should invalidate cached fetch results, otherwise a
        previous "no key → scrape returned None" would keep being returned."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client._cache["PN-1"] = None
        client.set_api_key("new-key")
        assert "PN-1" not in client._cache

    def test_corrupt_credentials_file_returns_none(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        with open(creds, "w") as f:
            f.write("{not json")
        client = MouserClient(credentials_file=creds)
        assert client.get_api_key() is None


class TestMouserApiFetch:
    """When an API key is configured, _fetch_raw uses the Mouser Search API
    instead of HTML scraping. This is the primary fix for the bot-block issue."""

    _API_RESPONSE = {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [{
                "Availability": "500 In Stock",
                "DataSheetUrl": "https://www.mouser.com/datasheet/2/280/FGG.pdf",
                "Description": "Connectors LEMO 0B series 5-pos plug",
                "ImagePath": "https://www.mouser.com/images/lemo/lrg/FGG0B305.jpg",
                "Category": "Circular Connectors",
                "LeadTime": "61 Days",
                "LifecycleStatus": "Active",
                "Manufacturer": "LEMO",
                "ManufacturerPartNumber": "FGG.0B.305.CLAD52",
                "Min": "1",
                "Mult": "1",
                "MouserPartNumber": "736-FGG0B305CLAD52",
                "ProductDetailUrl": "https://www.mouser.com/ProductDetail/736-FGG0B305CLAD52",
                "PriceBreaks": [
                    {"Quantity": 1, "Price": "$37.55", "Currency": "USD"},
                    {"Quantity": 10, "Price": "$35.00", "Currency": "USD"},
                    {"Quantity": 25, "Price": "$33.50", "Currency": "USD"},
                ],
                "ProductAttributes": [
                    {"AttributeName": "Contact Gender", "AttributeValue": "Plug"},
                    {"AttributeName": "Number of Contacts", "AttributeValue": "5"},
                ],
            }],
        },
    }

    def _install_mock_urlopen(self, response_payload, captured_requests, status_code=200):
        original = urllib.request.urlopen

        body = json.dumps(response_payload).encode()
        resp_status = status_code

        class FakeResp:
            status = resp_status
            def read(self):
                return body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_urlopen(req, *a, **kw):
            captured_requests.append({
                "url": req.full_url,
                "method": req.get_method(),
                "headers": dict(req.headers),
                "data": req.data,
            })
            return FakeResp()

        urllib.request.urlopen = fake_urlopen
        return original

    def test_api_path_used_when_key_configured(self, tmp_path):
        """Sanity: with a key set, fetch hits api.mouser.com, not www.mouser.com."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("test-key-123")

        captured = []
        original = self._install_mock_urlopen(self._API_RESPONSE, captured)
        try:
            product = client.fetch_product("736-FGG0B305CLAD52")
        finally:
            urllib.request.urlopen = original

        assert len(captured) == 1
        assert "api.mouser.com" in captured[0]["url"]
        assert "apikey=test-key-123" in captured[0]["url"].lower()
        assert captured[0]["method"] == "POST"
        # Body is the SearchByPartRequest payload.
        body = json.loads(captured[0]["data"])
        assert body["SearchByPartRequest"]["mouserPartNumber"] == "736-FGG0B305CLAD52"

        assert product is not None
        assert product["productCode"] == "736-FGG0B305CLAD52"
        assert product["title"] == "Connectors LEMO 0B series 5-pos plug"
        assert product["manufacturer"] == "LEMO"
        assert product["mpn"] == "FGG.0B.305.CLAD52"
        assert product["description"] == "Connectors LEMO 0B series 5-pos plug"
        assert product["imageUrl"].startswith("https://")
        assert product["pdfUrl"].endswith(".pdf")
        assert product["category"] == "Circular Connectors"
        assert product["stock"] == 500
        assert product["mouserUrl"] == \
            "https://www.mouser.com/ProductDetail/736-FGG0B305CLAD52"
        assert product["provider"] == "mouser"

        # Price breaks parsed from "$37.55" strings to floats with quantities.
        assert len(product["prices"]) == 3
        assert product["prices"][0] == {"qty": 1, "price": 37.55}
        assert product["prices"][1] == {"qty": 10, "price": 35.00}
        assert product["prices"][2] == {"qty": 25, "price": 33.50}

        # Attributes preserved from API.
        attr_names = [a["name"] for a in product["attributes"]]
        assert "Contact Gender" in attr_names
        assert "Number of Contacts" in attr_names

    def test_api_no_results_returns_none(self, tmp_path):
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")

        captured = []
        original = self._install_mock_urlopen(
            {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
            captured,
        )
        try:
            assert client.fetch_product("DOES-NOT-EXIST") is None
        finally:
            urllib.request.urlopen = original

    def test_api_error_payload_returns_none(self, tmp_path, caplog):
        """Mouser returns 200 with an Errors array on auth/quota/etc. failures."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("bad-key")

        captured = []
        original = self._install_mock_urlopen(
            {
                "Errors": [{"Id": 0, "Code": "Invalid", "Message": "Invalid API key"}],
                "SearchResults": None,
            },
            captured,
        )
        try:
            with caplog.at_level(logging.WARNING, logger="mouser_client"):
                result = client.fetch_product("WHATEVER")
        finally:
            urllib.request.urlopen = original

        assert result is None
        assert any(
            "Invalid API key" in rec.message
            for rec in caplog.records
        ), f"Expected error log, got: {[r.message for r in caplog.records]}"

    def test_no_key_falls_back_to_scrape(self, tmp_path):
        """Without an API key, _fetch_raw uses the legacy HTML scrape so users
        without a Mouser API key still get tooltips when bot detection allows."""
        client = MouserClient(credentials_file=str(tmp_path / "missing.json"))
        assert client.get_api_key() is None

        captured = []
        original = self._install_mock_urlopen(
            # Body content is irrelevant — we only assert the URL.
            {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
            captured,
        )
        try:
            client.fetch_product("ANY-PART")
        finally:
            urllib.request.urlopen = original

        assert len(captured) == 1
        # Hits www.mouser.com (scrape), NOT api.mouser.com.
        assert "api.mouser.com" not in captured[0]["url"]
        assert "www.mouser.com" in captured[0]["url"]

    def test_falls_back_to_keyword_when_partnumber_empty(self, tmp_path):
        """The /partnumber endpoint matches Mouser PNs primarily — a user who
        has the manufacturer part number (e.g. FGG.0B.305.CLAD52) in their
        inventory column won't find anything by partnumber. Fall back to
        /keyword which searches MPNs and descriptions."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")

        captured = []
        original = urllib.request.urlopen

        responses = [
            # First call (partnumber): no results.
            {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
            # Second call (keyword): returns the LEMO connector.
            self._API_RESPONSE,
        ]
        idx = [0]

        def fake_urlopen(req, *a, **kw):
            captured.append({"url": req.full_url, "data": req.data})
            body = json.dumps(responses[idx[0]]).encode()
            idx[0] += 1

            class FakeResp:
                def read(self):
                    return body
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
            return FakeResp()

        urllib.request.urlopen = fake_urlopen
        try:
            product = client.fetch_product("FGG.0B.305.CLAD52")
        finally:
            urllib.request.urlopen = original

        # Two API calls: first /partnumber, then /keyword.
        assert len(captured) == 2
        assert "/search/partnumber" in captured[0]["url"]
        assert "/search/keyword" in captured[1]["url"]

        # Keyword body uses the right schema.
        kw_body = json.loads(captured[1]["data"])
        assert kw_body["SearchByKeywordRequest"]["keyword"] == "FGG.0B.305.CLAD52"

        # Got the part back.
        assert product is not None
        assert product["mpn"] == "FGG.0B.305.CLAD52"
        assert product["productCode"] == "736-FGG0B305CLAD52"

    def test_partnumber_hit_skips_keyword(self, tmp_path):
        """When /partnumber finds the part, don't waste a /keyword call."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")

        captured = []
        original = self._install_mock_urlopen(self._API_RESPONSE, captured)
        try:
            product = client.fetch_product("736-FGG0B305CLAD52")
        finally:
            urllib.request.urlopen = original

        assert product is not None
        # Exactly one call — the partnumber one. No keyword fallback.
        assert len(captured) == 1
        assert "/search/partnumber" in captured[0]["url"]

    def test_keyword_chooses_best_match_by_mpn(self, tmp_path):
        """Keyword search returns multiple parts ranked by Mouser. We want
        the one whose MPN actually matches the user's input — not just the
        first result, which may be a near-miss accessory or alternate."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")

        partnumber_empty = {
            "Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []},
        }
        keyword_multi = {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 3,
                "Parts": [
                    # First result — a near-miss accessory.
                    {
                        "MouserPartNumber": "999-OTHER",
                        "ManufacturerPartNumber": "FGG.0B.305.OTHER",
                        "Description": "Accessory",
                        "Manufacturer": "LEMO",
                        "PriceBreaks": [], "ProductAttributes": [],
                        "Availability": "0",
                    },
                    # Second result — the exact MPN match.
                    {
                        "MouserPartNumber": "736-FGG0B305CLAD52",
                        "ManufacturerPartNumber": "FGG.0B.305.CLAD52",
                        "Description": "The right one",
                        "Manufacturer": "LEMO",
                        "PriceBreaks": [{"Quantity": 1, "Price": "$37.55"}],
                        "ProductAttributes": [],
                        "Availability": "500 In Stock",
                    },
                ],
            },
        }
        responses = [partnumber_empty, keyword_multi]
        idx = [0]

        def fake_urlopen(req, *a, **kw):
            body = json.dumps(responses[idx[0]]).encode()
            idx[0] += 1

            class FakeResp:
                def read(self):
                    return body
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
            return FakeResp()

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            product = client.fetch_product("FGG.0B.305.CLAD52")
        finally:
            urllib.request.urlopen = original

        assert product is not None
        # Should pick the MPN-matching result, not the first-listed one.
        assert product["mpn"] == "FGG.0B.305.CLAD52"
        assert product["title"] == "The right one"

    def test_keyword_falls_back_to_first_when_no_exact_match(self, tmp_path):
        """If keyword returns multiple parts but none exact-match the input,
        return the first (Mouser's relevance ranking)."""
        creds = str(tmp_path / "mouser_credentials.json")
        client = MouserClient(credentials_file=creds)
        client.set_api_key("k")

        partnumber_empty = {
            "Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []},
        }
        keyword_no_exact = {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 2,
                "Parts": [
                    {
                        "MouserPartNumber": "111-FIRST",
                        "ManufacturerPartNumber": "FIRST-MPN",
                        "Description": "First listed",
                        "Manufacturer": "X",
                        "PriceBreaks": [], "ProductAttributes": [],
                        "Availability": "0",
                    },
                    {
                        "MouserPartNumber": "222-SECOND",
                        "ManufacturerPartNumber": "SECOND-MPN",
                        "Description": "Second listed",
                        "Manufacturer": "Y",
                        "PriceBreaks": [], "ProductAttributes": [],
                        "Availability": "0",
                    },
                ],
            },
        }
        responses = [partnumber_empty, keyword_no_exact]
        idx = [0]

        def fake_urlopen(req, *a, **kw):
            body = json.dumps(responses[idx[0]]).encode()
            idx[0] += 1

            class FakeResp:
                def read(self):
                    return body
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
            return FakeResp()

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            product = client.fetch_product("UNRELATED-INPUT")
        finally:
            urllib.request.urlopen = original

        assert product is not None
        assert product["productCode"] == "111-FIRST"


# ── the product page's pricing table ─────────────────────────────────────────
# Mouser separates its ladders by carrier in one table, which is the packaging
# model dubIS wants handed over for free. The markup below is trimmed from a
# real GRM155R71H103KA88D page.

PRICING_TABLE_HTML = """
<table class="pricing-table">
  <tr><th class="text-right">Qty.</th><th class="text-right">Unit Price</th>
      <th class="text-right ext-price-col">Ext. Price</th></tr>
  <tr><th class="sub-heading">Cut Tape / MouseReel&trade; &dagger;</th></tr>
  <tr><th class="text-right pricebreak-col">1</th>
      <td class="text-right">$0.10</td>
      <td class="text-right ext-price-col">$0.10</td></tr>
  <tr><th class="text-right pricebreak-col">10</th>
      <td class="text-right">$0.015</td>
      <td class="text-right ext-price-col">$0.15</td></tr>
  <tr><th class="text-right pricebreak-col">5,000</th>
      <td class="text-right">$0.006</td>
      <td class="text-right ext-price-col">$30.00</td></tr>
  <tr><th class="sub-heading">Full Reel (Order in multiples of 10000)</th></tr>
  <tr><th class="text-right pricebreak-col">10,000</th>
      <td class="text-right">$0.004</td>
      <td class="text-right ext-price-col">$40.00</td></tr>
</table>
"""


class TestPricingTable:
    def test_splits_the_ladders_by_packaging(self):
        groups = mouser_client._parse_pricing_table(PRICING_TABLE_HTML)
        assert [g["name"] for g in groups] == [
            "Cut Tape / MouseReel™",
            "Full Reel (Order in multiples of 10000)",
        ]
        assert groups[0]["prices"] == [
            {"qty": 1, "price": 0.10},
            {"qty": 10, "price": 0.015},
            {"qty": 5000, "price": 0.006},
        ]
        assert groups[1]["prices"] == [{"qty": 10000, "price": 0.004}]

    def test_reads_unit_price_not_extended_price(self):
        """Ext. Price is unit x qty; taking it would be wrong by orders of magnitude."""
        groups = mouser_client._parse_pricing_table(PRICING_TABLE_HTML)
        assert groups[0]["prices"][-1]["price"] == 0.006  # not 30.00
        assert groups[1]["prices"][0]["price"] == 0.004   # not 40.00

    def test_footnote_marker_is_not_part_of_the_packaging_name(self):
        """The name is the key observations group under; a dagger would split it."""
        groups = mouser_client._parse_pricing_table(PRICING_TABLE_HTML)
        assert "†" not in groups[0]["name"]
        assert not groups[0]["name"].endswith(" ")

    def test_breaks_before_any_packaging_heading_are_dropped(self):
        """A break with no carrier above it cannot be attributed to one."""
        html = """
        <table class="pricing-table">
          <tr><th class="text-right pricebreak-col">1</th><td>$9.99</td></tr>
          <tr><th class="sub-heading">Tray</th></tr>
          <tr><th class="text-right pricebreak-col">5</th><td>$8.00</td></tr>
        </table>
        """
        groups = mouser_client._parse_pricing_table(html)
        assert groups == [{"name": "Tray", "prices": [{"qty": 5, "price": 8.00}]}]

    def test_no_table_is_empty_not_an_empty_ladder(self):
        assert mouser_client._parse_pricing_table("<html><body>nope</body></html>") == []

    def test_a_packaging_with_no_breaks_is_dropped(self):
        html = """
        <table class="pricing-table">
          <tr><th class="sub-heading">Discontinued</th></tr>
          <tr><th class="sub-heading">Tube</th></tr>
          <tr><th class="text-right pricebreak-col">1</th><td>$1.00</td></tr>
        </table>
        """
        assert [g["name"] for g in mouser_client._parse_pricing_table(html)] == ["Tube"]


class TestProductPageUsesTheTable:
    def _page(self, extra=""):
        return f"""
        <html><head><title>GRM155R71H103KA88D Murata | Mouser</title>
        <script type="application/ld+json">{{"@type":"Product",
          "name":"GRM155R71H103KA88D","mpn":"GRM155R71H103KA88D",
          "brand":{{"name":"Murata"}},
          "offers":{{"price":"0.10","availability":"InStock"}}}}</script>
        </head><body>{PRICING_TABLE_HTML}{extra}</body></html>
        """

    def test_headline_prices_come_from_the_default_packaging(self):
        p = MouserClient._parse_product_page(self._page(), "GRM155R71H103KA88D", "u")
        assert [b["qty"] for b in p["prices"]] == [1, 10, 5000]

    def test_every_packaging_keeps_its_own_ladder(self):
        p = MouserClient._parse_product_page(self._page(), "GRM155R71H103KA88D", "u")
        by_name = {g["name"]: g for g in p["packagings"]}
        assert len(by_name) == 2
        cut = by_name["Cut Tape / MouseReel™"]
        reel = by_name["Full Reel (Order in multiples of 10000)"]
        # domain.packaging classifies these, and the distinction is the whole
        # point: a fee on the cut-tape ladder is what makes a part reel buyable.
        assert cut["isReel"] is False
        assert reel["isReel"] is True
        assert len(cut["prices"]) == 3 and len(reel["prices"]) == 1

    def test_the_reel_multiple_is_read_off_the_heading(self):
        p = MouserClient._parse_product_page(self._page(), "GRM155R71H103KA88D", "u")
        assert p["reelQty"] == 10000

    def test_mousereel_fee_still_rides_along(self):
        p = MouserClient._parse_product_page(
            self._page(extra="<div>MouseReel service fee $7.00</div>"),
            "GRM155R71H103KA88D", "u")
        assert float(p["reelFee"]) == 7.00

    def test_a_page_with_no_table_falls_back_to_the_attribute(self):
        """Unchanged behaviour for pages that never had a pricing table."""
        html = """
        <html><head><title>Thing | Mouser</title>
        <script type="application/ld+json">{"@type":"Product","name":"Thing",
          "offers":{"price":"1.50"}}</script></head>
        <body><tr><td>Packaging</td><td>Tray</td></tr></body></html>
        """
        p = MouserClient._parse_product_page(html, "PN", "u")
        assert [g["name"] for g in p["packagings"]] == ["Tray"]


class TestFetchPathSelection:
    def test_mouser_part_numbers_are_told_from_mpns(self):
        """Only the first decides which URL is tried first; both are tried."""
        assert mouser_client._MOUSER_PN_RE.match("736-FGG0B305CLAD52")
        assert mouser_client._MOUSER_PN_RE.match("81-GRM155R71H103KA88D")
        assert not mouser_client._MOUSER_PN_RE.match("GRM155R71H103KA88D")
        assert not mouser_client._MOUSER_PN_RE.match("CL05B104KB54PNC")

    def test_a_not_found_page_is_not_a_product(self):
        """Mouser answers an unresolvable /ProductDetail/ with a 200 and an
        apology, which has a title and therefore used to parse: the tooltip
        showed "Sorry, we can't find the page you're looking for." as a part
        name, and `_fetch_via_browser` stopped there instead of falling back to
        the search page that would have resolved the MPN."""
        html = (
            "<html><body><h1>Sorry, we can’t find the page you’re "
            "looking for.</h1></body></html>"
        )
        assert MouserClient._parse_product_page(
            html, "LM358DR", "https://www.mouser.com/ProductDetail/LM358DR"
        ) is None

    def test_a_part_number_that_merely_looks_like_an_error_still_parses(self):
        """The not-found check is phrase-based on purpose: matching a bare
        "404" would hide every part whose number contains those digits."""
        html = "<html><body><h1>RC0805FR-07404RL Yageo</h1></body></html>"
        result = MouserClient._parse_product_page(
            html, "RC0805FR-07404RL", "https://www.mouser.com/ProductDetail/x"
        )
        assert result is not None
        assert result["title"] == "RC0805FR-07404RL Yageo"

    def test_browser_is_skipped_when_there_is_no_window_to_use(self, monkeypatch):
        """The container has no GUI loop; it must fall through, not raise."""
        monkeypatch.setattr(mouser_client.browser_page, "available", lambda: False)
        client = MouserClient()
        assert client._fetch_via_browser("736-ANYTHING") is None

    def test_a_configured_key_still_wins(self, monkeypatch, tmp_path):
        """The API is cleaner and unblocked; the browser is the keyless path."""
        creds = tmp_path / "mouser_credentials.json"
        creds.write_text(json.dumps({"api_key": "k"}))
        client = MouserClient(credentials_file=str(creds))
        called = []
        monkeypatch.setattr(client, "_fetch_via_api",
                            lambda pn, key: called.append("api") or {"ok": True})
        monkeypatch.setattr(client, "_fetch_via_browser",
                            lambda pn: called.append("browser"))
        assert client._fetch_raw("736-X") == {"ok": True}
        assert called == ["api"]

    def test_without_a_key_the_browser_is_tried_before_the_legacy_scrape(self, monkeypatch):
        client = MouserClient()
        order = []
        monkeypatch.setattr(client, "_fetch_via_browser",
                            lambda pn: order.append("browser"))
        monkeypatch.setattr(client, "_fetch_via_scrape",
                            lambda pn: order.append("scrape") or {"ok": True})
        assert client._fetch_raw("736-X") == {"ok": True}
        assert order == ["browser", "scrape"]


class TestReelingFeeApplicability:
    """Mouser prints the same MouseReel explainer on every product page.

    The $7.00 in it is real, but it is boilerplate about the service, not a
    statement that this part can be reeled -- observed on a LEMO connector
    sold in bulk. Recording it there would hand the reel preset a
    "Tray + reeling" offer to choose.
    """

    _EXPLAINER = ("<div>Cut Tape Product is cut from a full reel. "
                  "MouseReel&#8482; (Add $7.00 reeling fee) A product reel is cut "
                  "to customer-specified quantities.</div>")

    def _page(self, spec_packaging, table=""):
        return f"""
        <html><head><title>Thing | Mouser</title>
        <script type="application/ld+json">{{"@type":"Product","name":"Thing",
          "offers":{{"price":"1.50"}}}}</script></head>
        <body>{table}
        <tr><td>Packaging</td><td>{spec_packaging}</td></tr>
        {self._EXPLAINER}</body></html>
        """

    def test_a_part_that_comes_on_tape_keeps_the_fee(self):
        p = MouserClient._parse_product_page(self._page("Cut Tape"), "PN", "u")
        assert float(p["reelFee"]) == 7.00

    def test_a_bulk_part_does_not_get_a_reeling_fee(self):
        p = MouserClient._parse_product_page(self._page("Bulk"), "PN", "u")
        assert p["reelFee"] is None

    def test_a_tray_part_does_not_get_a_reeling_fee(self):
        p = MouserClient._parse_product_page(self._page("Tray"), "PN", "u")
        assert p["reelFee"] is None

    def test_a_page_whose_carriers_we_could_not_read_gets_no_fee(self):
        """Unknown is not permission — the rule domain/predicates.py applies."""
        p = MouserClient._parse_product_page(self._page(""), "PN", "u")
        assert p["packagings"] == []
        assert p["reelFee"] is None

    def test_the_fee_is_not_the_first_price_break(self):
        """The table's own sub-heading says MouseReel; the next $ is a price."""
        p = MouserClient._parse_product_page(
            self._page("Cut Tape", table=PRICING_TABLE_HTML), "PN", "u")
        assert float(p["reelFee"]) == 7.00  # not 0.10
