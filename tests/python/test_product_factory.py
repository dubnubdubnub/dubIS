"""Oracle tests for domain/product.py's single normalized-product factory."""

from domain.product import build_product


def test_build_product_lcsc_shape():
    p = build_product(
        product_code="C123", title="t", manufacturer="m", mpn="MPN",
        package="0402", description="d", stock=10,
        prices=[{"qty": 1, "price": 0.1}],
        provider="lcsc", url="https://lcsc.com/x",
    )
    assert p["provider"] == "lcsc"
    assert p["lcscUrl"] == "https://lcsc.com/x"
    assert p["category"] is None and p["subcategory"] is None
    assert set(p) >= {
        "productCode", "title", "manufacturer", "mpn", "package", "description",
        "stock", "prices", "imageUrl", "pdfUrl", "category", "subcategory",
        "attributes", "packagings", "reelQty", "reelFee", "provider", "_debug",
    }


def test_build_product_full_shape_matches_current_lcsc_dict():
    """Exact byte-for-byte reproduction of today's lcsc_client.py dict."""
    expected = {
        "productCode": "C123",
        "title": "Widget",
        "manufacturer": "Acme",
        "mpn": "MPN-1",
        "package": "0402",
        "description": "A widget",
        "stock": 10,
        "prices": [{"qty": 1, "price": 0.1}],
        "imageUrl": "https://img/1.jpg",
        "pdfUrl": "https://pdf/1.pdf",
        "lcscUrl": "https://www.lcsc.com/product-detail/C123.html",
        "category": "Resistors",
        "subcategory": "Chip Resistors",
        "attributes": [{"name": "Tolerance", "value": "1%"}],
        "packagings": [],
        "reelQty": None,
        "reelFee": None,
        "provider": "lcsc",
        "_debug": {"raw": "data"},
    }
    p = build_product(
        product_code="C123", title="Widget", manufacturer="Acme", mpn="MPN-1",
        package="0402", description="A widget", stock=10,
        prices=[{"qty": 1, "price": 0.1}],
        image_url="https://img/1.jpg", pdf_url="https://pdf/1.pdf",
        url="https://www.lcsc.com/product-detail/C123.html",
        category="Resistors", subcategory="Chip Resistors",
        attributes=[{"name": "Tolerance", "value": "1%"}],
        provider="lcsc", debug={"raw": "data"},
    )
    assert p == expected


def test_provider_url_key_derived_per_provider():
    for provider, key in (
        ("lcsc", "lcscUrl"), ("digikey", "digikeyUrl"),
        ("mouser", "mouserUrl"), ("pololu", "pololuUrl"),
    ):
        p = build_product(
            product_code="X", title="", manufacturer="", mpn="", package="",
            description="", stock=0, prices=[], provider=provider,
            url="https://example.com",
        )
        assert p[key] == "https://example.com"
        assert p["provider"] == provider


def test_defaults_when_optional_fields_omitted():
    """Digikey jsonld/fallback omit imageUrl/pdfUrl/category etc today; the
    factory fills in empty-string/None defaults without callers passing them."""
    p = build_product(
        product_code="X", title="", manufacturer="", mpn="", package="",
        description="", stock=0, prices=[], provider="digikey",
    )
    assert p["imageUrl"] == ""
    assert p["pdfUrl"] == ""
    assert p["digikeyUrl"] == ""
    assert p["category"] is None
    assert p["subcategory"] is None
    assert p["attributes"] == []
    assert p["packagings"] == []
    assert p["reelQty"] is None
    assert p["reelFee"] is None
    assert p["_debug"] is None
