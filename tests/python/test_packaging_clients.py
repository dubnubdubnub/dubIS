"""Packaging/reel capture in the DigiKey, Mouser and Pololu clients.

Companion to tests/python/test_packaging.py (which covers the shared
vocabulary plus LCSC). Every test here drives the **real client code** with
only the network leg stubbed, because tests/python/test_normalizers.py's habit
of replaying parsing logic inline is what let the LCSC change pass through
untested.

Seams, one per client:

* **DigiKey** — ``digikey_normalizer.normalize_result(envelope, pn)``.
  ``DigikeyClient._fetch_raw`` is a hidden-WebView2 driver whose only parsing
  step is this call, so the "network" leg is a real browser window that cannot
  be exercised in-process. Handing ``normalize_result`` a scrape envelope is
  therefore both the real entry point and the whole of the code under test.
* **Mouser** — ``urllib.request.urlopen``, with a real credentials file in
  tmp_path deciding which of the client's two paths runs. That keeps
  ``fetch_product`` → ``_fetch_raw`` → ``_fetch_via_api``/``_fetch_via_scrape``
  → ``_normalize_api_part``/``_parse_product_page`` all real.
* **Pololu** — ``urllib.request.urlopen``, replaying the captured product
  pages from tests/fixtures/generated/distributor-scrapes.json.

DigiKey and Mouser have no committed fixtures (both need credentials, see
CLAUDE.md), so their payloads here are hand-authored to mirror the shapes the
client/normalizer code actually reads.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

import pytest

from digikey_normalizer import normalize_result
from domain.product import build_product
from mouser_client import MouserClient
from pololu_client import PololuClient

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "generated", "distributor-scrapes.json"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _stub_urlopen(monkeypatch, module, body: bytes):
    """Replace *module*'s urlopen with one returning *body*. Network only."""

    @contextlib.contextmanager
    def fake_urlopen(_req, timeout=None):
        class _Resp:
            @staticmethod
            def read():
                return body

        yield _Resp()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)


def _entry(product: dict[str, Any], name_fragment: str) -> dict[str, Any]:
    return next(p for p in product["packagings"] if name_fragment in p["name"])


# ---------------------------------------------------------------------------
# DigiKey — seam: digikey_normalizer.normalize_result
# ---------------------------------------------------------------------------


def _dk_combined(
    *,
    pricing: list[dict[str, Any]] | None = None,
    attributes: list[dict[str, Any]] | None = None,
    dom_packagings: list[dict[str, Any]] | None = None,
    dom_tiers: list[dict[str, Any]] | None = None,
    extra_price_quantity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``dk_combined`` scrape envelope in the shape the normalizer reads."""
    price_quantity: dict[str, Any] = {
        "qtyAvailable": "10,000",
        "pricing": pricing if pricing is not None else [],
    }
    price_quantity.update(extra_price_quantity or {})
    return {
        "_source": "dk_combined",
        "jsonld": None,
        "nextdata": {
            "envelope": {
                "data": {
                    "productOverview": {
                        "rolledUpProductNumber": "YAG2274TR-ND",
                        "title": "RES 10K OHM 1% 1/16W 0402",
                        "manufacturer": "Yageo",
                        "manufacturerProductNumber": "RC0402FR-0710KL",
                    },
                    "priceQuantity": price_quantity,
                    "productAttributes": {
                        "attributes": attributes if attributes is not None else [],
                        "categories": [{"label": "Resistors"}],
                    },
                },
            },
        },
        "rsc": False,
        "dom": {
            "priceTiers": dom_tiers or [],
            "packagings": dom_packagings or [],
            "datasheetUrl": "",
            "stock": 0,
        },
    }


_DK_PRICING = [
    {
        "packageType": {"name": "Cut Tape (CT)"},
        "digiKeyProductNumber": "YAG2274CT-ND",
        "mergedPricingTiers": [
            {"brkQty": "1", "unitPrice": "$0.12"},
            {"brkQty": "10", "unitPrice": "$0.04"},
        ],
    },
    {
        "packageType": {"name": "Digi-Reel®"},
        "digiKeyProductNumber": "YAG2274DKR-ND",
        "mergedPricingTiers": [{"brkQty": "1", "unitPrice": "$0.12"}],
    },
    {
        "packageType": {"name": "Tape & Reel (TR)"},
        "digiKeyProductNumber": "YAG2274TR-ND",
        "mergedPricingTiers": [{"brkQty": "5,000", "unitPrice": "$0.00698"}],
    },
]


class TestDigikeyPackagingsGoThroughTheFactory:
    def test_ladders_are_annotated_with_carrier_and_reelness(self):
        """Annotation only happens inside build_product.

        Before this change the normalizer assigned ``result["packagings"]``
        after the product was built, so the entries never passed through
        ``annotate_packagings`` and carried no carrier/isReel at all.
        """
        product = normalize_result(
            _dk_combined(pricing=_DK_PRICING), "YAG2274TR-ND",
        )
        assert len(product["packagings"]) == 3

        cut = _entry(product, "Cut Tape")
        assert cut["carrier"] == "tape" and cut["isReel"] is False
        assert cut["partNumber"] == "YAG2274CT-ND"
        assert cut["prices"] == [{"qty": 1, "price": 0.12}, {"qty": 10, "price": 0.04}]

        for fragment in ("Digi-Reel", "Tape & Reel"):
            entry = _entry(product, fragment)
            assert entry["carrier"] == "tape", fragment
            assert entry["isReel"] is True, fragment

    def test_active_packaging_still_drives_the_top_level_prices(self):
        """Regression guard on _pick_active_packaging — must not drift."""
        product = normalize_result(
            _dk_combined(pricing=_DK_PRICING), "YAG2274TR-ND",
        )
        assert product["prices"] == [{"qty": 5000, "price": 0.00698}]
        assert product["prices"] == _entry(product, "Tape & Reel")["prices"]

    def test_active_packaging_follows_the_requested_part_number(self):
        product = normalize_result(
            _dk_combined(pricing=_DK_PRICING), "YAG2274CT-ND",
        )
        assert product["prices"] == _entry(product, "Cut Tape")["prices"]

    def test_dom_scraped_packagings_are_annotated_too(self):
        """The DOM fallback path (names/codes only) must annotate as well."""
        product = normalize_result(
            _dk_combined(
                pricing=[],
                dom_tiers=[{"qty": 1, "price": 0.1}, {"qty": 10, "price": 0.05}],
                dom_packagings=[
                    {"name": "Cut Tape (CT)", "code": "CT", "href": "/p/x"},
                    {"name": "Tape & Reel (TR)", "code": "TR", "href": "/p/y"},
                ],
            ),
            "YAG2274TR-ND",
        )
        assert len(product["packagings"]) == 2
        active = _entry(product, "Tape & Reel")
        assert active["partNumber"] == "YAG2274TR-ND"
        assert active["isReel"] is True and active["carrier"] == "tape"
        assert _entry(product, "Cut Tape")["isReel"] is False


class TestDigikeyReelQty:
    @pytest.mark.parametrize("raw,expected", [
        ("3,000", 3000), ("10000", 10000), ("1", 1), ("0", None), ("", None),
        ("Bulk", None),
    ])
    def test_manufacturer_standard_package_becomes_reel_qty(self, raw, expected):
        """DK renders the factory reel quantity as "3,000"."""
        product = normalize_result(
            _dk_combined(
                pricing=_DK_PRICING,
                attributes=[{
                    "id": "1989",
                    "label": "Manufacturer Standard Package",
                    "values": [{"value": raw}],
                }],
            ),
            "YAG2274TR-ND",
        )
        assert product["reelQty"] == expected

    def test_shorter_standard_package_label_also_accepted(self):
        product = normalize_result(
            _dk_combined(
                attributes=[{
                    "id": "1989",
                    "label": "Standard Package",
                    "values": [{"value": "2,500"}],
                }],
            ),
            "X-ND",
        )
        assert product["reelQty"] == 2500

    def test_absent_standard_package_is_none(self):
        product = normalize_result(
            _dk_combined(
                pricing=_DK_PRICING,
                attributes=[{
                    "id": "1",
                    "label": "Package / Case",
                    "values": [{"value": "0402"}],
                }],
            ),
            "YAG2274TR-ND",
        )
        assert product["reelQty"] is None
        assert product["package"] == "0402"

    def test_standard_package_survives_the_direct_nextdata_path(self):
        """normalize_result also dispatches bare nextdata envelopes."""
        product = normalize_result(
            {
                "_source": "nextdata",
                "_props": {
                    "envelope": {
                        "data": {
                            "productOverview": {},
                            "priceQuantity": {},
                            "productAttributes": {
                                "attributes": [{
                                    "id": "1989",
                                    "label": "Manufacturer Standard Package",
                                    "values": [{"value": "3,000"}],
                                }],
                            },
                        },
                    },
                },
            },
            "X-ND",
        )
        assert product["reelQty"] == 3000


class TestDigikeyReelFee:
    @pytest.mark.parametrize("raw,expected", [
        ("$7.00", 7.0), (7, 7.0), ("7.5", 7.5), (0, None), ("", None), (None, None),
    ])
    def test_fee_on_the_price_quantity_block(self, raw, expected):
        product = normalize_result(
            _dk_combined(
                pricing=_DK_PRICING, extra_price_quantity={"digiReelFee": raw},
            ),
            "YAG2274TR-ND",
        )
        assert product["reelFee"] == expected

    def test_fee_on_the_digi_reel_pricing_entry(self):
        pricing = [dict(p) for p in _DK_PRICING]
        pricing[1]["reelingFee"] = "$7.00"
        product = normalize_result(
            _dk_combined(pricing=pricing), "YAG2274TR-ND",
        )
        assert product["reelFee"] == 7.0

    def test_absent_fee_is_none(self):
        product = normalize_result(
            _dk_combined(pricing=_DK_PRICING), "YAG2274TR-ND",
        )
        assert product["reelFee"] is None


class TestDigikeyAbsentPackaging:
    """No packaging data must give [] / None, never a crash."""

    def test_nextdata_with_no_pricing_and_no_dom(self):
        product = normalize_result(_dk_combined(), "X-ND")
        assert product["packagings"] == []
        assert product["reelQty"] is None and product["reelFee"] is None

    def test_jsonld_only_envelope(self):
        product = normalize_result(
            {
                "_source": "dk_combined",
                "jsonld": {
                    "@type": "Product", "name": "R", "sku": "X-ND",
                    "brand": {"name": "Yageo"}, "offers": {"price": "0.1"},
                },
                "nextdata": None,
                "dom": {"priceTiers": [], "packagings": [], "datasheetUrl": "",
                        "stock": 0},
            },
            "X-ND",
        )
        assert product["packagings"] == []
        assert product["reelQty"] is None and product["reelFee"] is None

    def test_unknown_format_fallback(self):
        product = normalize_result({"nothing": "useful"}, "X-ND")
        assert product["packagings"] == []
        assert product["reelQty"] is None and product["reelFee"] is None

    def test_malformed_pricing_entries_are_skipped(self):
        product = normalize_result(
            _dk_combined(pricing=["oops", None, {"packageType": None}]), "X-ND",
        )
        assert product["packagings"] == []


# ---------------------------------------------------------------------------
# Mouser — seam: urllib.request.urlopen (+ a real credentials file)
# ---------------------------------------------------------------------------


def _mouser_api_part(**overrides: Any) -> dict[str, Any]:
    """A Search API v2 ``SearchResults.Parts[i]`` in the shape the client reads."""
    part: dict[str, Any] = {
        "MouserPartNumber": "603-RC0402FR-0710KL",
        "ManufacturerPartNumber": "RC0402FR-0710KL",
        "Manufacturer": "YAGEO",
        "Description": "RES 10K OHM 1% 1/16W 0402",
        "Category": "Chip Resistor - Surface Mount",
        "DataSheetUrl": "https://example.com/ds.pdf",
        "ProductDetailUrl": "https://www.mouser.com/ProductDetail/x",
        "ImagePath": "https://example.com/img.jpg",
        "Availability": "12,000 In Stock",
        "Min": "1",
        "Mult": "1",
        "Reeling": False,
        "PriceBreaks": [
            {"Quantity": 1, "Price": "$0.10", "Currency": "USD"},
            {"Quantity": 100, "Price": "$0.02", "Currency": "USD"},
        ],
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
        ],
    }
    part.update(overrides)
    return part


def _mouser_api_product(monkeypatch, tmp_path, part: dict[str, Any]) -> dict[str, Any]:
    """Drive the real client's API path end to end; only urlopen is stubbed."""
    import mouser_client as mod

    creds = tmp_path / "mouser_credentials.json"
    creds.write_text(json.dumps({"api_key": "test-key"}), encoding="utf-8")
    payload = {"Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [part]}}
    _stub_urlopen(monkeypatch, mod, json.dumps(payload).encode("utf-8"))

    product = MouserClient(credentials_file=str(creds)).fetch_product(
        part["MouserPartNumber"],
    )
    assert product is not None and product["provider"] == "mouser"
    return product


def _attrs(packaging: str | None) -> list[dict[str, str]]:
    attrs = [{"AttributeName": "Resistance", "AttributeValue": "10 kOhms"}]
    if packaging is not None:
        attrs.append({"AttributeName": "Packaging", "AttributeValue": packaging})
    return attrs


class TestMouserApiPackaging:
    @pytest.mark.parametrize("packaging,carrier,reel", [
        ("Cut Tape", "tape", False),
        ("Tape & Reel", "tape", True),
        ("MouseReel", "tape", True),
        ("Tray", "tray", False),
        ("Tube", "tube", False),
        ("Bulk", "bulk", False),
    ])
    def test_the_packaging_attribute_becomes_an_annotated_entry(
        self, monkeypatch, tmp_path, packaging, carrier, reel,
    ):
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(ProductAttributes=_attrs(packaging)),
        )
        assert len(product["packagings"]) == 1
        entry = product["packagings"][0]
        assert entry["name"] == packaging
        assert entry["carrier"] == carrier
        assert entry["isReel"] is reel
        assert entry["partNumber"] == "603-RC0402FR-0710KL"
        # The ladder rides along, as it does for LCSC.
        assert entry["prices"] == product["prices"]

    def test_min_and_mult_are_carried_on_the_entry(self, monkeypatch, tmp_path):
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(
                Min="3,000", Mult="3,000", ProductAttributes=_attrs("Tape & Reel"),
            ),
        )
        entry = product["packagings"][0]
        assert entry["minBuyQty"] == 3000
        assert entry["orderMultiple"] == 3000

    def test_a_tape_order_multiple_is_the_reel_quantity(self, monkeypatch, tmp_path):
        """Mouser's "order in multiples of 3000" arrives as Mult on the API."""
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(Mult="3,000", ProductAttributes=_attrs("Tape & Reel")),
        )
        assert product["reelQty"] == 3000

    @pytest.mark.parametrize("packaging,mult", [
        # A multiple of 1 is not a reel of one part...
        ("Tape & Reel", "1"),
        # ...and a bulk pack size is not a reel quantity at all.
        ("Bulk", "5"),
        ("Tray", "490"),
    ])
    def test_non_reel_multiples_are_not_reel_quantities(
        self, monkeypatch, tmp_path, packaging, mult,
    ):
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(Mult=mult, ProductAttributes=_attrs(packaging)),
        )
        assert product["reelQty"] is None

    def test_reeling_is_exposed_without_overriding_the_carrier_reading(
        self, monkeypatch, tmp_path,
    ):
        """`Reeling` means "MouseReel is offered", not "this is a reel".

        Using it as an isReel override would sell a cut length as a whole reel,
        so it gets its own key and the name-derived answer stands.
        """
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(Reeling=True, ProductAttributes=_attrs("Cut Tape")),
        )
        entry = product["packagings"][0]
        assert entry["reelingAvailable"] is True
        assert entry["isReel"] is False

    def test_missing_reeling_flag_is_unknown_not_false(self, monkeypatch, tmp_path):
        part = _mouser_api_part(ProductAttributes=_attrs("Cut Tape"))
        del part["Reeling"]
        product = _mouser_api_product(monkeypatch, tmp_path, part)
        assert product["packagings"][0]["reelingAvailable"] is None

    def test_no_packaging_attribute_gives_an_empty_list(self, monkeypatch, tmp_path):
        product = _mouser_api_product(
            monkeypatch, tmp_path, _mouser_api_part(ProductAttributes=_attrs(None)),
        )
        assert product["packagings"] == []
        assert product["reelQty"] is None

    def test_no_product_attributes_at_all(self, monkeypatch, tmp_path):
        part = _mouser_api_part()
        del part["ProductAttributes"]
        product = _mouser_api_product(monkeypatch, tmp_path, part)
        assert product["packagings"] == []

    def test_the_api_publishes_no_mousereel_fee(self, monkeypatch, tmp_path):
        """Documented gap: only the product page renders the $7 surcharge."""
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(Reeling=True, ProductAttributes=_attrs("Cut Tape")),
        )
        assert product["reelFee"] is None

    def test_junk_multiples_do_not_crash(self, monkeypatch, tmp_path):
        product = _mouser_api_product(
            monkeypatch, tmp_path,
            _mouser_api_part(
                Min="", Mult="n/a", ProductAttributes=_attrs("Tape & Reel"),
            ),
        )
        assert product["reelQty"] is None
        assert product["packagings"][0]["minBuyQty"] is None
        assert product["packagings"][0]["orderMultiple"] is None


def _mouser_page(spec_rows: str = "", body_extra: str = "") -> bytes:
    return f"""
    <html><head>
      <script type="application/ld+json">
      {{"@type": "Product", "name": "RES 10K 0402", "sku": "603-RC0402",
        "mpn": "RC0402FR-0710KL", "brand": {{"name": "YAGEO"}},
        "description": "10k 1%",
        "offers": {{"price": "0.10", "availability": "https://schema.org/InStock"}}}}
      </script>
    </head><body>
      <h1>RES 10K 0402</h1>
      <div>12,000 In Stock</div>
      <table>
        <tr><th>Resistance</th><td>10 kOhms</td></tr>
        {spec_rows}
      </table>
      {body_extra}
    </body></html>
    """.encode()


def _mouser_scrape_product(monkeypatch, page: bytes) -> dict[str, Any]:
    """Drive the real client's scrape path — no credentials file configured."""
    import mouser_client as mod

    _stub_urlopen(monkeypatch, mod, page)
    product = MouserClient().fetch_product("603-RC0402")
    assert product is not None and product["provider"] == "mouser"
    return product


class TestMouserScrapePackaging:
    _REEL_ROW = "<tr><th>Packaging</th><td>Tape &amp; Reel</td></tr>"

    def test_full_reel_multiple_and_mousereel_fee_come_off_the_page(self, monkeypatch):
        product = _mouser_scrape_product(monkeypatch, _mouser_page(
            spec_rows=self._REEL_ROW,
            body_extra=(
                "<div>Full Reel (Order in multiples of 3,000)</div>"
                "<div>MouseReel&#8482; reeling service <span>$7.00</span></div>"
            ),
        ))
        assert product["reelQty"] == 3000
        assert product["reelFee"] == 7.0
        entry = product["packagings"][0]
        assert entry["name"] == "Tape & Reel"
        assert entry["carrier"] == "tape" and entry["isReel"] is True
        assert entry["partNumber"] == "603-RC0402"

    def test_a_bare_order_multiple_on_a_non_tape_part_is_not_a_reel_qty(
        self, monkeypatch,
    ):
        product = _mouser_scrape_product(monkeypatch, _mouser_page(
            spec_rows="<tr><th>Packaging</th><td>Bulk</td></tr>",
            body_extra="<div>Order in multiples of 5</div>",
        ))
        assert product["reelQty"] is None
        assert product["packagings"][0]["carrier"] == "bulk"

    def test_a_bare_order_multiple_on_tape_is_accepted(self, monkeypatch):
        product = _mouser_scrape_product(monkeypatch, _mouser_page(
            spec_rows="<tr><th>Packaging</th><td>Cut Tape</td></tr>",
            body_extra="<div>Order in multiples of 4,000</div>",
        ))
        assert product["reelQty"] == 4000
        assert product["packagings"][0]["isReel"] is False

    def test_a_page_without_a_packaging_row(self, monkeypatch):
        product = _mouser_scrape_product(monkeypatch, _mouser_page())
        assert product["packagings"] == []
        assert product["reelQty"] is None and product["reelFee"] is None

    def test_case_row_is_not_mistaken_for_the_carrier(self, monkeypatch):
        """Mouser's "Package / Case" is a footprint, not a carrier."""
        product = _mouser_scrape_product(monkeypatch, _mouser_page(
            spec_rows="<tr><th>Package / Case</th><td>0402</td></tr>",
        ))
        assert product["packagings"] == []


# ---------------------------------------------------------------------------
# Pololu — seam: urllib.request.urlopen, replaying the captured pages
# ---------------------------------------------------------------------------


def _pololu_fixture_pages() -> dict[str, str]:
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    parts = (data.get("pololu") or {}).get("parts") or {}
    return {
        sku: rec["raw_html"]
        for sku, rec in parts.items()
        if isinstance(rec, dict) and rec.get("raw_html")
    }


_POLOLU_PAGES = _pololu_fixture_pages()


@pytest.mark.skipif(not _POLOLU_PAGES, reason="no Pololu fixtures captured")
class TestPololuPublishesNoPackaging:
    """Pololu carries no carrier/reel data — the absence must stay explicit.

    Driven against every captured page so a future "helpful" guess (parsing
    "5-Pack" out of a title, or reading "on a Reel" off a wire spool) shows up
    as a failure here rather than as a fabricated feeder-loadable reel.
    """

    @pytest.fixture(params=sorted(_POLOLU_PAGES), ids=sorted(_POLOLU_PAGES))
    def product(self, request, monkeypatch):
        import pololu_client as mod

        _stub_urlopen(
            monkeypatch, mod, _POLOLU_PAGES[request.param].encode("utf-8"),
        )
        result = PololuClient().fetch_product(request.param)
        assert result is not None, request.param
        return result

    def test_packagings_is_an_empty_list(self, product):
        assert product["packagings"] == []

    def test_reel_metadata_is_none(self, product):
        assert product["reelQty"] is None
        assert product["reelFee"] is None

    def test_the_rest_of_the_product_still_parses(self, product):
        """Guards against "empty packagings" hiding a broken parse."""
        assert product["title"]
        assert product["provider"] == "pololu"
        assert isinstance(product["prices"], list)


# ---------------------------------------------------------------------------
# The override seam
# ---------------------------------------------------------------------------


class TestClientSuppliedIsReelWins:
    """`annotate_packagings` uses setdefault so a client can override.

    None of DigiKey, Mouser or Pololu publishes an authoritative reel boolean
    (LCSC's `isReel` is the only one), so no client here sets it today —
    Mouser's `Reeling` is service availability, not reel-ness, and is
    deliberately kept off `isReel`. These tests pin the seam open so a future
    client that *does* learn better can use it.
    """

    def test_override_survives_a_mouser_shaped_build(self):
        product = build_product(
            product_code="603-X", title="t", manufacturer="m", mpn="X",
            package="", description="d", stock=1, prices=[], provider="mouser",
            packagings=[{"name": "Tape & Reel", "isReel": False}],
        )
        assert product["packagings"][0]["isReel"] is False
        # The carrier is still derived — only the supplied key is respected.
        assert product["packagings"][0]["carrier"] == "tape"

    def test_carrier_override_is_respected_too(self):
        product = build_product(
            product_code="X-ND", title="t", manufacturer="m", mpn="X",
            package="", description="d", stock=1, prices=[], provider="digikey",
            packagings=[{"name": "Bulk", "carrier": "tray"}],
        )
        assert product["packagings"][0]["carrier"] == "tray"


class TestNoClientBoltsPackagingsOnByHand:
    """Structural guard for the rule domain/product.py exists to enforce.

    `digikey_normalizer.py` used to do `result["packagings"] = ...` on the
    already-built product dict, which bypassed annotate_packagings entirely.
    Assigning the emitted key anywhere outside build_product means the
    normalized shape has drifted again.
    """

    @pytest.mark.parametrize("module", [
        "digikey_normalizer.py", "lcsc_client.py", "mouser_client.py",
        "pololu_client.py", "digikey_client.py",
    ])
    def test_no_post_hoc_assignment_of_the_emitted_keys(self, module):
        path = os.path.join(os.path.dirname(__file__), "..", "..", module)
        with open(path, encoding="utf-8") as f:
            source = f.read()
        for key in ("packagings", "reelQty", "reelFee"):
            assert f'["{key}"] =' not in source, (
                f"{module} assigns [{key!r}] directly — route it through "
                "build_product(...) instead"
            )
