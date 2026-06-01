"""Tests for distributor_profiles: per-template heuristic OCR field extraction.

Profiles are heuristic (not full positional invoice parsers). The user reviews
and edits everything before import, so partial extraction is acceptable. These
tests feed representative OCR-style text per distributor template and assert the
distributor PN, qty, unit price, and MPN are extracted.
"""
import pytest

import distributor_profiles as dp


# Canonical ledger PN column per template (None for generic).
def test_profile_columns():
    assert dp.get_profile("lcsc").pn_column == "LCSC Part Number"
    assert dp.get_profile("digikey").pn_column == "Digikey Part Number"
    assert dp.get_profile("mouser").pn_column == "Mouser Part Number"
    assert dp.get_profile("pololu").pn_column == "Pololu Part Number"
    assert dp.get_profile("generic").pn_column is None


def test_unknown_template_raises():
    with pytest.raises(ValueError, match="template"):
        dp.get_profile("nope")


class TestLCSC:
    def test_extracts_lcsc_pn_qty_price(self):
        text = (
            "Line  LCSC#     MPN                 Mfr   Qty   Unit\n"
            "1     C429942   DF40C-30DP-0.4V(51) HRS   100   0.4521\n"
            "2     C1525     0603WAF1002T5E      UNI   500   0.0080\n"
        )
        items = dp.parse_with_template("lcsc", text)
        assert len(items) >= 2
        first = next(i for i in items if i["distributor_pn"] == "C429942")
        assert first["distributor"] == "lcsc"
        assert first["quantity"] == 100
        assert abs(first["unit_price"] - 0.4521) < 1e-6
        assert "DF40C-30DP" in first["mpn"]

    def test_lcsc_pn_format(self):
        prof = dp.get_profile("lcsc")
        assert prof.match_pn("C429942") == "C429942"
        assert prof.match_pn("c1525") == "C1525"
        assert prof.match_pn("DF40C-30DP") is None  # MPN, not an LCSC code


class TestDigiKey:
    def test_extracts_digikey_pn(self):
        text = (
            "DigiKey Part Number    Manufacturer Part Number   Qty   Unit Price\n"
            "296-1234-1-ND          SN74LVC1G08DBVR            25    0.4500\n"
            "311-1.00KHRCT-ND       RC0603FR-071KL            100    0.0100\n"
        )
        items = dp.parse_with_template("digikey", text)
        pns = {i["distributor_pn"] for i in items}
        assert "296-1234-1-ND" in pns
        first = next(i for i in items if i["distributor_pn"] == "296-1234-1-ND")
        assert first["distributor"] == "digikey"
        assert first["quantity"] == 25
        assert abs(first["unit_price"] - 0.45) < 1e-6
        assert "SN74LVC1G08DBVR" in first["mpn"]

    def test_digikey_pn_format(self):
        prof = dp.get_profile("digikey")
        assert prof.match_pn("296-1234-1-ND") == "296-1234-1-ND"
        assert prof.match_pn("311-1.00KHRCT-ND") == "311-1.00KHRCT-ND"
        assert prof.match_pn("C429942") is None


class TestMouser:
    def test_extracts_mouser_pn(self):
        text = (
            "Mouser No            Mfr No              Qty   Price\n"
            "595-SN74LVC1G08DBVR  SN74LVC1G08DBVR     25    0.4500\n"
            "81-GRM188R71H104KA93 GRM188R71H104KA93D  200   0.0120\n"
        )
        items = dp.parse_with_template("mouser", text)
        pns = {i["distributor_pn"] for i in items}
        assert "595-SN74LVC1G08DBVR" in pns
        first = next(i for i in items if i["distributor_pn"] == "595-SN74LVC1G08DBVR")
        assert first["distributor"] == "mouser"
        assert first["quantity"] == 25
        assert abs(first["unit_price"] - 0.45) < 1e-6

    def test_mouser_pn_format(self):
        prof = dp.get_profile("mouser")
        assert prof.match_pn("595-SN74LVC1G08DBVR") == "595-SN74LVC1G08DBVR"
        assert prof.match_pn("81-GRM188R71H104KA93") == "81-GRM188R71H104KA93"
        assert prof.match_pn("C429942") is None  # only digits before dash -> not mouser


class TestPololu:
    def test_extracts_pololu_sku(self):
        text = (
            "Item   Product                            Qty   Unit\n"
            "2820   A4988 Stepper Motor Driver Carrier   3    5.95\n"
            "1182   QTR-1A Reflectance Sensor           10    1.39\n"
        )
        items = dp.parse_with_template("pololu", text)
        pns = {i["distributor_pn"] for i in items}
        assert "2820" in pns
        first = next(i for i in items if i["distributor_pn"] == "2820")
        assert first["distributor"] == "pololu"
        assert first["quantity"] == 3
        assert abs(first["unit_price"] - 5.95) < 1e-6

    def test_pololu_pn_format(self):
        prof = dp.get_profile("pololu")
        assert prof.match_pn("2820") == "2820"
        assert prof.match_pn("1182") == "1182"
        assert prof.match_pn("C429942") is None

    def test_leading_year_or_order_number_not_taken_as_sku(self):
        # A leading year / order number before the real SKU must NOT be matched
        # as the SKU. We prefer no match over a confidently-wrong value, since
        # the user reviews each row. The buggy regex grabbed the first numeric
        # token (2026) as the SKU.
        text = "Order date 2026 item 2820 qty 3 5.95\n"
        items = dp.parse_with_template("pololu", text)
        pns = {i["distributor_pn"] for i in items}
        assert "2026" not in pns
        # Never the trailing qty either.
        assert "3" not in pns

    def test_whole_dollar_integer_price_is_extracted(self):
        # A whole-dollar unit price (no decimal point), or OCR that dropped the
        # decimal, must still extract the row instead of silently dropping it.
        text = "2820 A4988 Driver 3 6\n"
        items = dp.parse_with_template("pololu", text)
        first = next(i for i in items if i["distributor_pn"] == "2820")
        assert first["distributor"] == "pololu"
        assert first["quantity"] == 3
        assert abs(first["unit_price"] - 6.0) < 1e-6

    def test_eight_digit_leading_token_does_not_mis_extract_qty_as_sku(self):
        # An 8+ digit leading token exceeds the 1-5 digit SKU bound. The buggy
        # parser fell through and grabbed the trailing qty (10) as the SKU.
        text = "12345678 Some Product 10 1.39\n"
        items = dp.parse_with_template("pololu", text)
        pns = {i["distributor_pn"] for i in items}
        assert "10" not in pns
        assert "12345" not in pns  # not a 5-digit prefix of the 8-digit token


class TestGeneric:
    def test_generic_has_no_pn_column_and_no_distributor_pn(self):
        text = "TMR2615 MDT 50 4.20\nTMR2305 MDT 25 3.10\n"
        items = dp.parse_with_template("generic", text)
        assert len(items) == 2
        assert items[0]["mpn"] == "TMR2615"
        assert items[0]["distributor"] == "generic"
        assert items[0]["distributor_pn"] == ""
        assert items[0]["quantity"] == 50
        assert abs(items[0]["unit_price"] - 4.20) < 1e-6

    def test_distributor_profile_returns_empty_on_no_match(self):
        # No LCSC codes anywhere -> profile extracts nothing (caller falls back).
        text = "Just some prose with no part numbers here at all.\n"
        items = dp.parse_with_template("lcsc", text)
        assert items == []
