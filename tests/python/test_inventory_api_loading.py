"""Tests for InventoryApi — inventory loading, raw pipeline, and constants."""

import json
import os

import pytest

from inventory_api import InventoryApi
from tests.python.helpers import make_part as _make_part
from tests.python.helpers import write_ledger as _write_ledger


class TestGetPartKey:
    def test_lcsc_preferred(self):
        row = {"LCSC Part Number": "C123456", "Manufacture Part Number": "STM32", "Digikey Part Number": "DK-1"}
        assert InventoryApi.get_part_key(row) == "C123456"

    def test_mpn_fallback(self):
        row = {"LCSC Part Number": "", "Manufacture Part Number": "STM32F405", "Digikey Part Number": ""}
        assert InventoryApi.get_part_key(row) == "STM32F405"

    def test_digikey_fallback(self):
        row = {"LCSC Part Number": "", "Manufacture Part Number": "", "Digikey Part Number": "DK-123"}
        assert InventoryApi.get_part_key(row) == "DK-123"

    def test_empty_returns_empty(self):
        row = {"LCSC Part Number": "", "Manufacture Part Number": "", "Digikey Part Number": ""}
        assert InventoryApi.get_part_key(row) == ""

    def test_lcsc_requires_c_prefix(self):
        row = {"LCSC Part Number": "X999", "Manufacture Part Number": "MPN1", "Digikey Part Number": ""}
        assert InventoryApi.get_part_key(row) == "MPN1"

    def test_mouser_lowest_priority(self):
        """Mouser PN is used only when no LCSC/MPN/DK/Pololu."""
        row = {
            "LCSC Part Number": "", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "736-FGG0B305CLAD52",
        }
        assert InventoryApi.get_part_key(row) == "736-FGG0B305CLAD52"

    def test_mouser_not_used_if_pololu_present(self):
        row = {
            "LCSC Part Number": "", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "1992",
            "Mouser Part Number": "736-FGG0B305CLAD52",
        }
        assert InventoryApi.get_part_key(row) == "1992"


class TestSharedConstants:
    def test_section_order_loaded_from_json(self):
        """SECTION_ORDER should match data/constants.json (mixed format)."""
        constants_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "constants.json",
        )
        with open(constants_path, encoding="utf-8") as f:
            constants = json.load(f)
        assert InventoryApi.SECTION_ORDER == constants["SECTION_ORDER"]

    def test_flat_section_order_contains_compound_keys(self):
        """FLAT_SECTION_ORDER should include compound section strings."""
        flat = InventoryApi.FLAT_SECTION_ORDER
        assert "Passives - Capacitors" in flat
        assert "Passives - Capacitors > MLCC" in flat
        assert "Passives - Capacitors > Aluminum Polymer" in flat
        assert "Passives - Capacitors > Tantalum" in flat
        assert "Discrete Semiconductors > MOSFETs" in flat
        assert "ICs - Power / Voltage Regulators > Load Switches" in flat
        assert "ICs - Power / Voltage Regulators > Switchers" in flat
        assert "ICs - Power / Voltage Regulators > LDOs" in flat
        # Flat sections still present
        assert "Passives - Resistors" in flat
        assert "Other" in flat

    def test_section_hierarchy_structure(self):
        """SECTION_HIERARCHY should have correct structure."""
        hier = InventoryApi.SECTION_HIERARCHY
        # Find capacitors entry
        cap = next(h for h in hier if h["name"] == "Passives - Capacitors")
        assert cap["children"] == ["MLCC", "Aluminum Polymer", "Tantalum"]
        # Resistors entry has children
        res = next(h for h in hier if h["name"] == "Passives - Resistors")
        assert res["children"] == ["Chip Resistors", "Variable / Trimmers"]

    def test_fieldnames_loaded_from_json(self):
        """FIELDNAMES should match data/constants.json."""
        constants_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "constants.json",
        )
        with open(constants_path, encoding="utf-8") as f:
            constants = json.load(f)
        assert InventoryApi.FIELDNAMES == constants["FIELDNAMES"]


class TestParseQty:
    def test_basic_int(self):
        assert InventoryApi._parse_qty("42") == 42

    def test_with_commas(self):
        assert InventoryApi._parse_qty("1,000") == 1000

    def test_float_truncated(self):
        assert InventoryApi._parse_qty("3.7") == 3

    def test_empty_returns_default(self):
        assert InventoryApi._parse_qty("") == 0

    def test_garbage_returns_default(self):
        assert InventoryApi._parse_qty("abc", default=-1) == -1


class TestEnsureParsed:
    def test_parses_json_string(self):
        assert InventoryApi._ensure_parsed('{"a": 1}') == {"a": 1}

    def test_passes_through_dict(self):
        d = {"a": 1}
        assert InventoryApi._ensure_parsed(d) is d

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            InventoryApi._ensure_parsed("{bad}")


class TestReadRawInventory:
    def test_no_file_returns_empty(self, api):
        fieldnames, merged = api._read_raw_inventory()
        assert merged == {}
        assert fieldnames == list(InventoryApi.FIELDNAMES)

    def test_single_row(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=5)])
        _, merged = api._read_raw_inventory()
        assert "C100000" in merged
        assert merged["C100000"]["Quantity"] == "5"

    def test_merges_duplicates(self, api):
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=3, unit_price="0.01", ext_price="0.03"),
            _make_part(lcsc="C100000", qty=7, unit_price="0.02", ext_price="0.14"),
        ])
        _, merged = api._read_raw_inventory()
        assert merged["C100000"]["Quantity"] == "10"

    def test_skips_empty_keys(self, api):
        _write_ledger(api, [_make_part()])  # no lcsc, no mpn
        _, merged = api._read_raw_inventory()
        assert len(merged) == 0


class TestFullPipeline:
    def test_import_adjust_consume_rebuild(self, api):
        # 1. Import
        rows = [
            _make_part(lcsc="C100000", qty=100, desc="Resistor 10kΩ"),
            _make_part(lcsc="C200000", qty=50, desc="Capacitor 100nF 25V"),
        ]
        inv = api.import_purchases(rows)
        assert len(inv) == 2

        # 2. Adjust
        inv = api.adjust_part("add", "C100000", 10)
        part = next(r for r in inv if r["lcsc"] == "C100000")
        assert part["qty"] == 110

        # 3. Consume
        matches = [
            {"part_key": "C100000", "bom_qty": 5},
            {"part_key": "C200000", "bom_qty": 2},
        ]
        inv = api.consume_bom(matches, 3, "board.csv")
        r_part = next(r for r in inv if r["lcsc"] == "C100000")
        c_part = next(r for r in inv if r["lcsc"] == "C200000")
        assert r_part["qty"] == 95   # 110 - 15
        assert c_part["qty"] == 44   # 50 - 6

        # 4. Rebuild
        inv2 = api.rebuild_inventory()
        assert len(inv2) == 2


def test_purchase_ledger_migrates_to_include_po_id(api):
    """Loading inventory with an old-schema purchase_ledger.csv migrates the header."""
    import csv

    old_fields = ["LCSC Part Number", "Manufacture Part Number", "Manufacturer", "Quantity"]
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "C100", "Manufacture Part Number": "ABC1",
                    "Manufacturer": "TestMfg", "Quantity": "5"})

    # First import triggers append_csv_rows which calls migrate_csv_header
    api.import_purchases('[{"Manufacture Part Number":"NEW1","Quantity":"3","po_id":"po_test01"}]')

    with open(api.input_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert "po_id" in rows[0]
    # Old row got empty po_id, new row got the value
    old_row = next(r for r in rows if r.get("LCSC Part Number") == "C100")
    new_row = next(r for r in rows if r.get("Manufacture Part Number") == "NEW1")
    assert old_row["po_id"] == ""
    assert new_row["po_id"] == "po_test01"
