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

    # A representative LCSC packing list: a multi-column table (No. | LCSC Part# |
    # Full Description incl. "Mfr. Part#: <MPN>" | Qty Ordered | Qty Shipped | Net
    # Weight | COO) with NO prices. The per-line "trailing qty+price" parser finds
    # nothing, so it must be extracted column-wise: C-numbers + "Mfr. Part#:" MPNs,
    # with quantity taken from the equal ordered/shipped pair.
    _PACKLIST = (
        "PACKING LIST\n"
        "Ship From: Shenzhen LCSC Electronics Tech\n"
        "No. LCSC Part # Full Description of Goods Qty. Ordered Qty. Shipped Net Weight (G) COO\n"
        "1 C12624 Mfr. Part#: KT-0603G Emerald Green 525nm LED Indication - Discrete 3.1V 0603 4,000 4,000 88 CN\n"
        "2 C377861 Mfr. Part#: WSD4066DN33 N-Channel Array 40V 28A 1.68W Surface Mount DFN3x3-8L 200 200 25.6 CN\n"
        "3 C424643 Mfr. Part#: DF40C-40DP-0.4V(51) nan 500 500 52.5 JP\n"
        "4 C424644 Mfr. Part#: DF40C-40DS-0.4V(51) nan 100 100 18.9 JP\n"
        "6 C2874885 Mfr. Part#: WS2812B-V5/W SMD5050-4P LED Addressable, Specialty RoHS 1,000 1,000 160 CN\n"
        "Total 5800 5800 360.00\n"
        "LCSC Order No. Reference: WM2605160154\n"
    )

    def test_extracts_packing_list_table(self):
        items = dp.parse_with_template("lcsc", self._PACKLIST)
        assert len(items) == 5
        first = next(i for i in items if i["distributor_pn"] == "C12624")
        assert first["distributor"] == "lcsc"
        assert first["mpn"] == "KT-0603G"
        assert first["quantity"] == 4000
        assert first["unit_price"] == 0.0
        led = next(i for i in items if i["distributor_pn"] == "C2874885")
        assert led["mpn"] == "WS2812B-V5/W"
        assert led["quantity"] == 1000
        fet = next(i for i in items if i["distributor_pn"] == "C377861")
        assert fet["mpn"] == "WSD4066DN33"
        assert fet["quantity"] == 200

    def test_packing_list_strips_trailing_ocr_noise_from_mpn(self):
        # Table borders bleed stray underscores/dashes onto the OCR'd MPN
        # ("WSD4066DN33__"); they must be trimmed (real MPNs end alnum or ")").
        text = (
            "1 C377861 Mfr. Part#: WSD4066DN33__ N-Channel Array 200 200 25.6 CN\n"
            "2 C424643 Mfr. Part#: DF40C-40DP-0.4V(51) nan 500 500 52.5 JP\n"
        )
        items = dp.parse_with_template("lcsc", text)
        assert next(i for i in items if i["distributor_pn"] == "C377861")["mpn"] == "WSD4066DN33"
        # A legitimate trailing ")" is preserved.
        assert next(i for i in items if i["distributor_pn"] == "C424643")["mpn"] == "DF40C-40DP-0.4V(51)"

    def test_generic_template_autodetects_lcsc_packing_list(self):
        # Dropped on the default "generic" template, the C-number + "Mfr. Part#:"
        # combination is unmistakably an LCSC packing list and should still fill.
        items = dp.parse_with_template("generic", self._PACKLIST)
        assert len(items) == 5
        assert any(i["distributor_pn"] == "C12624" and i["mpn"] == "KT-0603G" for i in items)

    def test_generic_invoice_without_lcsc_markers_not_hijacked(self):
        items = dp.parse_with_template("generic", "TMR2615 MDT 50 4.20\n")
        assert items[0]["distributor"] == "generic"
        assert items[0]["distributor_pn"] == ""

    def test_tabular_invoice_still_parses_when_no_packlist_markers(self):
        # Regression guard: the legacy LCSC tabular invoice (with prices, no
        # "Mfr. Part#:" label) keeps using the per-line parser.
        text = (
            "Line  LCSC#     MPN                 Mfr   Qty   Unit\n"
            "1     C429942   DF40C-30DP-0.4V(51) HRS   100   0.4521\n"
        )
        items = dp.parse_with_template("lcsc", text)
        first = next(i for i in items if i["distributor_pn"] == "C429942")
        assert first["quantity"] == 100
        assert abs(first["unit_price"] - 0.4521) < 1e-6


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

    def test_extracts_packing_list_label_format(self):
        # A DigiKey packing list / certificate of compliance has NO prices and is
        # label-prefixed across separate lines (PART:/MFG#:/MFG:/QTY:), not a
        # tabular invoice. The per-line "trailing qty+price" parser matches none of
        # it. This text is real OCR output (word order within some lines is
        # scrambled by the scan), so the parser must extract column-wise rather
        # than line-wise. Fields per item must zip by appearance order.
        text = (
            "PART: 497-18216-ND\n"
            "MFG#: STLINK-V3SET 8471.90.0000 HTSUS:\n"
            "NFG : STMicroelectronics\n"            # OCR read 'M' as 'N'
            "ROHS3 COMP REACH UNAFFECTED Jan-2025\n"
            "COO: VIETNAM QTY: LOT 4\n"
            "490-9961-1-ND PART:\n"                 # value-before-label (scrambled)
            "MFG#: GRM21BR61A476ME15L\n"
            "MFG : Murata Electronics (VA)\n"
            "coo: JAPAN QTY: LoT 500\n"
        )
        items = dp.parse_with_template("digikey", text)
        assert len(items) == 2
        first = next(i for i in items if i["distributor_pn"] == "497-18216-ND")
        assert first["distributor"] == "digikey"
        assert first["mpn"] == "STLINK-V3SET"
        assert first["manufacturer"] == "STMicroelectronics"
        assert first["quantity"] == 4
        assert first["unit_price"] == 0.0
        second = next(i for i in items if i["distributor_pn"] == "490-9961-1-ND")
        assert second["mpn"] == "GRM21BR61A476ME15L"
        assert "Murata" in second["manufacturer"]
        assert second["quantity"] == 500

    def test_generic_template_autodetects_digikey_packing_list(self):
        # If the user drops a DigiKey packing list but leaves the template on the
        # default "generic", the unmistakable DigiKey markers (MFG#: labels + -ND
        # part numbers) should still trigger the packing-list extractor rather than
        # autofilling nothing. Guarded so ordinary generic invoices are untouched.
        text = (
            "DigiKey packing list\n"
            "PART: 497-18216-ND\n"
            "MFG#: STLINK-V3SET\n"
            "MFG : STMicroelectronics\n"
            "COO: VIETNAM QTY: LOT 4\n"
        )
        items = dp.parse_with_template("generic", text)
        assert len(items) == 1
        assert items[0]["distributor_pn"] == "497-18216-ND"
        assert items[0]["mpn"] == "STLINK-V3SET"
        assert items[0]["quantity"] == 4

    def test_generic_template_ignores_ordinary_invoice_without_digikey_markers(self):
        # Regression guard: a plain generic invoice (no MFG#: / -ND markers) must
        # NOT be hijacked by the DigiKey packing-list path.
        text = "TMR2615 MDT 50 4.20\n"
        items = dp.parse_with_template("generic", text)
        assert items[0]["distributor"] == "generic"
        assert items[0]["distributor_pn"] == ""

    def test_tabular_invoice_still_parses_when_no_packlist_labels(self):
        # Regression guard: the tabular-invoice path (with prices, no PART:/MFG#:
        # labels) must keep using the per-line parser, not the packing-list one.
        text = (
            "DigiKey Part Number    Manufacturer Part Number   Qty   Unit Price\n"
            "296-1234-1-ND          SN74LVC1G08DBVR            25    0.4500\n"
        )
        items = dp.parse_with_template("digikey", text)
        first = next(i for i in items if i["distributor_pn"] == "296-1234-1-ND")
        assert first["quantity"] == 25
        assert abs(first["unit_price"] - 0.45) < 1e-6
        assert "SN74LVC1G08DBVR" in first["mpn"]


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


class TestGenericFreeform:
    def _rows(self, text):
        import distributor_profiles
        return distributor_profiles.parse_with_template("generic", text)

    def test_mpn_not_first_token(self):
        rows = self._rows("1  KT-0603G  Kingbright  4000  0.0220")
        assert any(r["mpn"] == "KT-0603G" for r in rows)
        r = next(r for r in rows if r["mpn"] == "KT-0603G")
        assert r["quantity"] == 4000
        assert abs(r["unit_price"] - 0.022) < 1e-6

    def test_integer_unit_price(self):
        rows = self._rows("WS2812B-V5  Worldsemi  1000  6")
        assert rows and rows[0]["quantity"] == 1000 and rows[0]["unit_price"] == 6.0

    def test_label_noise_tolerated(self):
        rows = self._rows("Part: STM32F103C8T6  Qty 50  Unit Price $3.10")
        assert any("STM32F103C8T6" in r["mpn"] for r in rows)
