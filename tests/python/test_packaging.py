"""Packaging carrier vocabulary + reel metadata on the normalized product.

Covers domain/packaging.py's classifier, the coercion helpers in
domain/product.py, and the LCSC client's packaging capture driven end-to-end
against the captured fixtures (rather than replaying its parsing inline, which
would drift from the client the way tests/python/test_normalizers.py does).
"""

import json
import os

import pytest

from domain.packaging import carrier_of, is_reel
from domain.product import annotate_packagings, build_product

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "generated", "distributor-scrapes.json"
)


class TestCarrierOf:
    @pytest.mark.parametrize("name,expected", [
        ("Cut Tape (CT)", "tape"),
        ("Tape & Reel (TR)", "tape"),
        ("Tape &amp; Reel", "tape"),
        ("Digi-Reel®", "tape"),
        ("MouseReel", "tape"),
        ("Reel", "tape"),
        ("Strip", "tape"),
        ("Ammo Pack", "tape"),
        ("Tray", "tray"),
        ("Tube", "tube"),
        ("Stick", "tube"),
        ("Bulk", "bulk"),
        ("Bag", "bulk"),
        ("", None),
        (None, None),
        ("something unmapped", None),
    ])
    def test_maps_vendor_names_to_carriers(self, name, expected):
        assert carrier_of(name) == expected

    def test_is_case_and_whitespace_insensitive(self):
        assert carrier_of("  TAPE & REEL (TR)  ") == "tape"


class TestIsReel:
    @pytest.mark.parametrize("name", [
        "Tape & Reel (TR)", "Reel", "Digi-Reel®", "MouseReel", "Custom Reel",
    ])
    def test_whole_reels(self, name):
        assert is_reel(name) is True

    @pytest.mark.parametrize("name", [
        # Cut lengths, and carriers that are not tape at all.
        "Cut Tape (CT)", "Cut Tape", "Tape & Box", "Strip", "Tray", "Tube",
        "Bulk", "Bag", "", None, "unmapped",
        # Ammo is fan-folded tape in a box, not a reel.
        "Ammo Pack",
    ])
    def test_not_reels(self, name):
        assert is_reel(name) is False

    def test_cut_tape_wins_over_the_reel_substring(self):
        """'Cut Tape' contains no reel token, but 'Cut Tape / MouseReel' does.

        DigiKey and Mouser both label the cut-length option with the reeling
        service appended. The cut marker must win, or a strip is sold as a reel.
        """
        assert is_reel("Cut Tape / MouseReel") is False


class TestReelCoercion:
    @pytest.mark.parametrize("raw,expected", [
        ("3,000", 3000), (3000, 3000), (3000.0, 3000), ("10000", 10000),
        (0, None), ("0", None), ("", None), (None, None), ("junk", None), (-5, None),
    ])
    def test_reel_qty(self, raw, expected):
        p = _product(reel_qty=raw)
        assert p["reelQty"] == expected

    @pytest.mark.parametrize("raw,expected", [
        (3.0, 3.0), ("3.0", 3.0), ("$7.00", 7.0), (7, 7.0),
        (0, None), ("0", None), ("", None), (None, None), ("junk", None), (-1, None),
    ])
    def test_reel_fee(self, raw, expected):
        p = _product(reel_fee=raw)
        assert p["reelFee"] == expected


class TestAnnotatePackagings:
    def test_derives_carrier_and_reelness_from_the_name(self):
        out = annotate_packagings([{"name": "Tape & Reel (TR)"}, {"name": "Tray"}])
        assert out[0]["carrier"] == "tape" and out[0]["isReel"] is True
        assert out[1]["carrier"] == "tray" and out[1]["isReel"] is False

    def test_client_supplied_values_win(self):
        """A client with better data than the name string may override.

        LCSC is the real case: it reports unit "Reel" alongside isReel False
        for parts it will not reel (e.g. C393939).
        """
        out = annotate_packagings([{"name": "Reel", "isReel": False}])
        assert out[0]["isReel"] is False
        assert out[0]["carrier"] == "tape"

    def test_empty_and_malformed_input(self):
        assert annotate_packagings(None) == []
        assert annotate_packagings([]) == []
        assert annotate_packagings(["not a dict", 3]) == []

    def test_does_not_mutate_the_caller_list(self):
        original = [{"name": "Tray"}]
        annotate_packagings(original)
        assert original == [{"name": "Tray"}]


def _product(**kw):
    return build_product(
        product_code="C1", title="t", manufacturer="m", mpn="X", package="0402",
        description="d", stock=1, prices=[], provider="lcsc", **kw,
    )


class TestProductDefaults:
    def test_absent_packaging_is_empty_not_none(self):
        """Callers iterate packagings unconditionally; None would break them."""
        p = _product()
        assert p["packagings"] == []
        assert p["reelQty"] is None
        assert p["reelFee"] is None


def _lcsc_fixture_records():
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "productCode" in obj and "encapStandard" in obj:
                found.setdefault(obj["productCode"], obj)
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data.get("lcsc", {}))
    return found


_LCSC_RECORDS = _lcsc_fixture_records()


@pytest.mark.skipif(not _LCSC_RECORDS, reason="no LCSC fixtures captured")
class TestLcscClientPackaging:
    """Drive the real client so this cannot drift from lcsc_client.py."""

    @pytest.fixture(params=sorted(_LCSC_RECORDS), ids=sorted(_LCSC_RECORDS))
    def normalized(self, request, monkeypatch):
        import contextlib
        import json as _json

        import lcsc_client as mod

        record = _LCSC_RECORDS[request.param]

        # Stub the HTTP call only. LcscClient._fetch_raw *is* the normalizer, so
        # patching it would bypass exactly the code under test; intercepting
        # urlopen keeps the whole parse path real.
        @contextlib.contextmanager
        def fake_urlopen(_req, timeout=None):
            class _Resp:
                @staticmethod
                def read():
                    return _json.dumps({"result": record}).encode("utf-8")
            yield _Resp()

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        return record, mod.LcscClient().fetch_product(request.param)

    def test_reel_metadata_is_carried_through(self, normalized):
        record, product = normalized
        packet = record.get("minPacketNumber")
        expected_qty = int(packet) if packet and int(packet) > 0 else None
        assert product["reelQty"] == expected_qty

        fee = record.get("reelPrice")
        expected_fee = float(fee) if fee and float(fee) > 0 else None
        assert product["reelFee"] == expected_fee

    def test_lcsc_isreel_flag_is_authoritative(self, normalized):
        """LCSC's own boolean must survive, even when the unit name disagrees."""
        record, product = normalized
        assert product["packagings"], "LCSC always reports a packet unit"
        entry = product["packagings"][0]
        assert entry["isReel"] is bool(record.get("isReel"))
        assert entry["name"] == (record.get("minPacketUnit") or "Standard")

    def test_carrier_is_classified(self, normalized):
        record, product = normalized
        entry = product["packagings"][0]
        assert entry["carrier"] == carrier_of(record.get("minPacketUnit"))
