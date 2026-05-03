"""Tests for digikey_normalizer — 3-strategy product data normalization."""

from digikey_normalizer import normalize_result


class TestNormalizeJsonld:
    def test_basic_product(self):
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
        assert result["imageUrl"] == "https://example.com/img.jpg"
        assert result["digikeyUrl"] == "https://www.digikey.com/product/123"

    def test_list_offers(self):
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

    def test_empty_offers(self):
        raw = {
            "@type": "Product",
            "name": "No Offers",
            "sku": "NO-1",
            "offers": {},
            "brand": "BrandStr",
        }
        result = normalize_result(raw, "NO-1")
        assert result["prices"] == []
        assert result["manufacturer"] == "BrandStr"

    def test_stock_from_availability(self):
        """When _stock is absent, infers from InStock availability."""
        raw = {
            "@type": "Product",
            "name": "InStock Item",
            "sku": "IS-1",
            "offers": {"availability": "https://schema.org/InStock"},
        }
        result = normalize_result(raw, "IS-1")
        assert result["stock"] == 1

    def test_out_of_stock(self):
        raw = {
            "@type": "Product",
            "name": "OOS Item",
            "sku": "OOS-1",
            "offers": {"availability": "OutOfStock"},
        }
        result = normalize_result(raw, "OOS-1")
        assert result["stock"] == 0


class TestNormalizeNextdata:
    def test_full_envelope(self):
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
        # Attributes: should skip id=-1
        attr_names = [a["name"] for a in result["attributes"]]
        assert "Package / Case" in attr_names
        assert "Capacitance" in attr_names
        assert "Skip This" not in attr_names

    def test_empty_envelope(self):
        raw = {"_source": "nextdata", "_props": {}}
        result = normalize_result(raw, "X-1")
        assert result["productCode"] == "X-1"
        assert result["stock"] == 0
        assert result["prices"] == []
        assert result["provider"] == "digikey"

    def test_protocol_relative_image(self):
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

    def test_absolute_breadcrumb_url(self):
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


class TestNormalizeCombined:
    def test_jsonld_only_uses_dom_for_richer_prices(self):
        """When JSON-LD has 1 price tier and DOM has many, prefer DOM tiers."""
        raw = {
            "_source": "dk_combined",
            "jsonld": {
                "@type": "Product",
                "name": "Yageo Resistor",
                "sku": "YAG2274TR-ND",
                "mpn": "RC0402FR-0710KL",
                "brand": {"name": "Yageo"},
                "url": "https://www.digikey.com/product/YAG2274TR",
                "offers": {"price": "0.10", "availability": "InStock"},
            },
            "nextdata": None,
            "rsc": True,
            "dom": {
                "priceTiers": [
                    {"qty": 1, "price": 0.10},
                    {"qty": 10, "price": 0.034},
                    {"qty": 25, "price": 0.0252},
                    {"qty": 50, "price": 0.0204},
                    {"qty": 100, "price": 0.0168},
                    {"qty": 250, "price": 0.01316},
                    {"qty": 500, "price": 0.01112},
                    {"qty": 1000, "price": 0.00952},
                    {"qty": 5000, "price": 0.00698},
                ],
                "datasheetUrl": "https://yageo.com/RC0402.pdf",
                "packagings": [
                    {"name": "Cut Tape (CT)", "code": "CT", "href": "/p/YAG2274CT-ND"},
                    {"name": "Tape & Reel (TR)", "code": "TR", "href": "/p/YAG2274TR-ND"},
                ],
                "stock": 0,
            },
            "_url": "https://www.digikey.com/product/YAG2274TR",
            "_title": "RC0402FR-0710KL",
        }
        result = normalize_result(raw, "YAG2274TR-ND")
        # Prices should come from DOM (9 tiers, not 1)
        assert len(result["prices"]) == 9
        assert result["prices"][0] == {"qty": 1, "price": 0.10}
        assert result["prices"][-1] == {"qty": 5000, "price": 0.00698}
        # Datasheet URL pulled from DOM
        assert result["pdfUrl"] == "https://yageo.com/RC0402.pdf"
        # Packagings: two entries; the TR variant matched and got the prices
        assert len(result["packagings"]) == 2
        codes = [p["code"] for p in result["packagings"]]
        assert "CT" in codes
        assert "TR" in codes
        active = next(p for p in result["packagings"] if p["code"] == "TR")
        assert active["partNumber"] == "YAG2274TR-ND"
        assert len(active["prices"]) == 9

    def test_nextdata_packagings_extracted(self):
        """All pricing entries should yield a packaging variant when next.js data is rich."""
        raw = {
            "_source": "dk_combined",
            "jsonld": None,
            "nextdata": {
                "envelope": {
                    "data": {
                        "productOverview": {
                            "rolledUpProductNumber": "YAG2274TR-ND",
                            "manufacturerProductNumber": "RC0402FR",
                            "datasheetUrl": "https://example.com/ds.pdf",
                        },
                        "priceQuantity": {
                            "qtyAvailable": "10000",
                            "pricing": [
                                {
                                    "packageType": {"name": "Cut Tape (CT)"},
                                    "digiKeyProductNumber": "YAG2274CT-ND",
                                    "mergedPricingTiers": [
                                        {"brkQty": "1", "unitPrice": "$0.12"},
                                        {"brkQty": "10", "unitPrice": "$0.04"},
                                    ],
                                },
                                {
                                    "packageType": {"name": "Tape & Reel (TR)"},
                                    "digiKeyProductNumber": "YAG2274TR-ND",
                                    "mergedPricingTiers": [
                                        {"brkQty": "5000", "unitPrice": "$0.00698"},
                                    ],
                                },
                            ],
                        },
                        "productAttributes": {},
                    },
                },
            },
            "rsc": False,
            "dom": {
                "priceTiers": [],
                "packagings": [],
                "datasheetUrl": "",
                "stock": 0,
            },
        }
        result = normalize_result(raw, "YAG2274TR-ND")
        assert len(result["packagings"]) == 2
        ct = next(p for p in result["packagings"] if "CT" in p["name"])
        tr = next(p for p in result["packagings"] if "TR" in p["name"])
        assert ct["partNumber"] == "YAG2274CT-ND"
        assert tr["partNumber"] == "YAG2274TR-ND"
        # Active packaging selected for the requested PN — its prices flow into result["prices"]
        assert result["prices"] == tr["prices"]

    def test_fallback_when_neither_jsonld_nor_nextdata(self):
        raw = {
            "_source": "dk_combined",
            "jsonld": None,
            "nextdata": None,
            "rsc": True,
            "dom": {
                "priceTiers": [{"qty": 1, "price": 1.50}],
                "packagings": [],
                "datasheetUrl": "https://example.com/ds.pdf",
                "stock": 42,
            },
            "_url": "https://www.digikey.com/p/X",
        }
        result = normalize_result(raw, "X-1")
        assert result["prices"] == [{"qty": 1, "price": 1.50}]
        assert result["pdfUrl"] == "https://example.com/ds.pdf"
        assert result["stock"] == 42
        assert result["digikeyUrl"] == "https://www.digikey.com/p/X"


class TestNormalizeFallback:
    def test_unknown_format(self):
        raw = {"random_key": "random_value"}
        result = normalize_result(raw, "UNKNOWN-1")
        assert result["productCode"] == "UNKNOWN-1"
        assert result["title"] == ""
        assert result["stock"] == 0
        assert result["provider"] == "digikey"

    def test_empty_dict(self):
        result = normalize_result({}, "EMPTY-1")
        assert result["productCode"] == "EMPTY-1"
        assert result["provider"] == "digikey"
        assert result["prices"] == []

    def test_none_like_input(self):
        """Non-matching dict returns fallback."""
        result = normalize_result({"foo": "bar"}, "FOO-1")
        assert result["productCode"] == "FOO-1"
        assert result["manufacturer"] == ""
