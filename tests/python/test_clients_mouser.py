"""Tests for MouserClient."""

import json
import logging
import urllib.request

import pytest

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
