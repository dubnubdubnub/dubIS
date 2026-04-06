"""Tests for html_product_parser shared utilities."""

import pytest

from html_product_parser import (
    extract_attributes,
    extract_description,
    extract_image_url,
    extract_jsonld_product,
    extract_manufacturer,
    extract_mpn,
    extract_prices_from_jsonld,
    extract_stock_from_jsonld,
    extract_title,
)


class TestExtractJsonldProduct:
    def test_single_product(self):
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Test Part"}
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is not None
        assert result["name"] == "Test Part"

    def test_array_of_products(self):
        html = """
        <script type="application/ld+json">
        [
            {"@type": "WebSite", "name": "Example"},
            {"@type": "Product", "name": "My Part"}
        ]
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is not None
        assert result["name"] == "My Part"

    def test_no_product_type(self):
        html = """
        <script type="application/ld+json">
        {"@type": "WebSite", "name": "No Product Here"}
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is None

    def test_no_jsonld(self):
        html = "<html><body><h1>Hello</h1></body></html>"
        result = extract_jsonld_product(html)
        assert result is None

    def test_multiple_blocks_returns_first_product(self):
        html = """
        <script type="application/ld+json">
        {"@type": "WebSite"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "First Product"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Second Product"}
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is not None
        assert result["name"] == "First Product"

    def test_single_quoted_type_attribute(self):
        html = """
        <script type='application/ld+json'>
        {"@type": "Product", "name": "Single Quote Test"}
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is not None
        assert result["name"] == "Single Quote Test"

    def test_invalid_json_skipped(self):
        html = """
        <script type="application/ld+json">
        {bad json!!}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Valid"}
        </script>
        """
        result = extract_jsonld_product(html)
        assert result is not None
        assert result["name"] == "Valid"


class TestExtractTitle:
    def test_from_jsonld(self):
        jsonld = {"@type": "Product", "name": "My Part Name"}
        result = extract_title("", jsonld)
        assert result == "My Part Name"

    def test_from_h1(self):
        html = "<html><body><h1>My H1 Title</h1></body></html>"
        result = extract_title(html, None)
        assert result == "My H1 Title"

    def test_strips_inner_html(self):
        html = "<html><body><h1><span>Nested</span> Title</h1></body></html>"
        result = extract_title(html, None)
        assert result == "Nested Title"

    def test_unescapes_entities(self):
        html = '<html><body><h1>Part &amp; More &lt;3&gt;</h1></body></html>'
        result = extract_title(html, None)
        assert result == "Part & More <3>"

    def test_empty_when_no_title(self):
        html = "<html><body><p>No title here</p></body></html>"
        result = extract_title(html, None)
        assert result == ""

    def test_jsonld_takes_precedence_over_h1(self):
        html = "<html><body><h1>H1 Title</h1></body></html>"
        jsonld = {"@type": "Product", "name": "JSON-LD Title"}
        result = extract_title(html, jsonld)
        assert result == "JSON-LD Title"


class TestExtractDescription:
    def test_from_jsonld(self):
        jsonld = {"@type": "Product", "description": "A great part"}
        result = extract_description("", jsonld)
        assert result == "A great part"

    def test_from_meta(self):
        html = '<html><head><meta name="description" content="Meta description here"></head></html>'
        result = extract_description(html, None)
        assert result == "Meta description here"

    def test_empty_when_none(self):
        html = "<html><body></body></html>"
        result = extract_description(html, None)
        assert result == ""

    def test_jsonld_takes_precedence_over_meta(self):
        html = '<meta name="description" content="Meta desc">'
        jsonld = {"description": "JSON-LD desc"}
        result = extract_description(html, jsonld)
        assert result == "JSON-LD desc"

    def test_meta_unescapes_entities(self):
        html = '<meta name="description" content="Part &amp; Description">'
        result = extract_description(html, None)
        assert result == "Part & Description"


class TestExtractImageUrl:
    def test_from_jsonld_string(self):
        jsonld = {"image": "https://example.com/img.jpg"}
        result = extract_image_url("", jsonld)
        assert result == "https://example.com/img.jpg"

    def test_from_jsonld_array(self):
        jsonld = {"image": ["https://example.com/first.jpg", "https://example.com/second.jpg"]}
        result = extract_image_url("", jsonld)
        assert result == "https://example.com/first.jpg"

    def test_from_og_image(self):
        html = '<meta property="og:image" content="https://example.com/og.jpg">'
        result = extract_image_url(html, None)
        assert result == "https://example.com/og.jpg"

    def test_fixes_protocol_relative(self):
        jsonld = {"image": "//example.com/img.jpg"}
        result = extract_image_url("", jsonld)
        assert result == "https://example.com/img.jpg"

    def test_fixes_protocol_relative_og_image(self):
        html = '<meta property="og:image" content="//cdn.example.com/img.jpg">'
        result = extract_image_url(html, None)
        assert result == "https://cdn.example.com/img.jpg"

    def test_empty_jsonld_array(self):
        jsonld = {"image": []}
        html = '<meta property="og:image" content="https://fallback.com/img.jpg">'
        result = extract_image_url(html, jsonld)
        assert result == "https://fallback.com/img.jpg"

    def test_empty_when_none(self):
        result = extract_image_url("<html></html>", None)
        assert result == ""


class TestExtractPricesFromJsonld:
    def test_single_offer(self):
        jsonld = {"offers": {"price": "9.99"}}
        result = extract_prices_from_jsonld(jsonld)
        assert result == [{"qty": 1, "price": 9.99}]

    def test_multiple_offers(self):
        jsonld = {"offers": [{"price": "5.00"}, {"price": "4.00"}]}
        result = extract_prices_from_jsonld(jsonld)
        assert len(result) == 2
        assert result[0] == {"qty": 1, "price": 5.00}
        assert result[1] == {"qty": 1, "price": 4.00}

    def test_no_offers(self):
        jsonld = {"name": "Part"}
        result = extract_prices_from_jsonld(jsonld)
        assert result == []

    def test_invalid_price_skipped(self):
        jsonld = {"offers": {"price": "not-a-number"}}
        result = extract_prices_from_jsonld(jsonld)
        assert result == []

    def test_none_jsonld(self):
        result = extract_prices_from_jsonld(None)
        assert result == []

    def test_offer_without_price_skipped(self):
        jsonld = {"offers": [{"availability": "InStock"}, {"price": "2.50"}]}
        result = extract_prices_from_jsonld(jsonld)
        assert result == [{"qty": 1, "price": 2.50}]


class TestExtractStockFromJsonld:
    def test_in_stock_dict(self):
        jsonld = {"offers": {"availability": "https://schema.org/InStock"}}
        result = extract_stock_from_jsonld(jsonld)
        assert result == 1

    def test_out_of_stock(self):
        jsonld = {"offers": {"availability": "https://schema.org/OutOfStock"}}
        result = extract_stock_from_jsonld(jsonld)
        assert result == 0

    def test_list_offers_in_stock(self):
        jsonld = {"offers": [{"availability": "https://schema.org/InStock"}]}
        result = extract_stock_from_jsonld(jsonld)
        assert result == 1

    def test_no_offers(self):
        jsonld = {"name": "Part"}
        result = extract_stock_from_jsonld(jsonld)
        assert result == 0

    def test_none_jsonld(self):
        result = extract_stock_from_jsonld(None)
        assert result == 0

    def test_empty_list_offers(self):
        jsonld = {"offers": []}
        result = extract_stock_from_jsonld(jsonld)
        assert result == 0


class TestExtractManufacturer:
    def test_from_dict(self):
        jsonld = {"brand": {"name": "Acme Corp"}}
        result = extract_manufacturer(jsonld)
        assert result == "Acme Corp"

    def test_from_string(self):
        jsonld = {"brand": "BrandName"}
        result = extract_manufacturer(jsonld)
        assert result == "BrandName"

    def test_no_brand(self):
        jsonld = {"name": "Part"}
        result = extract_manufacturer(jsonld)
        assert result == ""

    def test_none_jsonld(self):
        result = extract_manufacturer(None)
        assert result == ""

    def test_brand_dict_missing_name(self):
        jsonld = {"brand": {"type": "Organization"}}
        result = extract_manufacturer(jsonld)
        assert result == ""

    def test_brand_not_string_or_dict(self):
        jsonld = {"brand": 42}
        result = extract_manufacturer(jsonld)
        assert result == ""


class TestExtractMpn:
    def test_from_mpn(self):
        jsonld = {"mpn": "RC0402FR-0710KL"}
        result = extract_mpn(jsonld)
        assert result == "RC0402FR-0710KL"

    def test_from_sku_fallback(self):
        jsonld = {"sku": "12345"}
        result = extract_mpn(jsonld)
        assert result == "12345"

    def test_explicit_fallback(self):
        jsonld = {}
        result = extract_mpn(jsonld, fallback="my-fallback")
        assert result == "my-fallback"

    def test_no_mpn_no_sku(self):
        jsonld = {"name": "Part"}
        result = extract_mpn(jsonld)
        assert result == ""

    def test_none_jsonld_returns_fallback(self):
        result = extract_mpn(None, fallback="default-mpn")
        assert result == "default-mpn"

    def test_none_jsonld_empty_fallback(self):
        result = extract_mpn(None)
        assert result == ""

    def test_mpn_takes_precedence_over_sku(self):
        jsonld = {"mpn": "MPN-VALUE", "sku": "SKU-VALUE"}
        result = extract_mpn(jsonld)
        assert result == "MPN-VALUE"


class TestExtractAttributes:
    def test_table_rows(self):
        html = """
        <table>
            <tr><th>Capacitance</th><td>100nF</td></tr>
            <tr><th>Voltage</th><td>16V</td></tr>
        </table>
        """
        result = extract_attributes(html)
        assert len(result) == 2
        assert {"name": "Capacitance", "value": "100nF"} in result
        assert {"name": "Voltage", "value": "16V"} in result

    def test_excludes_names(self):
        html = """
        <table>
            <tr><th>Quantity</th><td>10</td></tr>
            <tr><th>Price</th><td>$5.00</td></tr>
            <tr><th>Capacitance</th><td>100nF</td></tr>
        </table>
        """
        result = extract_attributes(html, excluded_names=["Quantity", "Price"])
        assert len(result) == 1
        assert result[0]["name"] == "Capacitance"

    def test_unescapes_html(self):
        html = """
        <table>
            <tr><th>Temp &amp; Range</th><td>-40&deg;C to +85&deg;C</td></tr>
        </table>
        """
        result = extract_attributes(html)
        assert len(result) == 1
        assert result[0]["name"] == "Temp & Range"

    def test_empty_html(self):
        result = extract_attributes("<html><body></body></html>")
        assert result == []

    def test_excludes_case_insensitive(self):
        html = """
        <table>
            <tr><th>UNIT PRICE</th><td>$1.00</td></tr>
            <tr><th>Package</th><td>0402</td></tr>
        </table>
        """
        result = extract_attributes(html, excluded_names=["unit price"])
        assert len(result) == 1
        assert result[0]["name"] == "Package"

    def test_no_excluded_names(self):
        html = """
        <table>
            <tr><td>Row 1 Name</td><td>Row 1 Value</td></tr>
        </table>
        """
        result = extract_attributes(html)
        assert len(result) == 1
