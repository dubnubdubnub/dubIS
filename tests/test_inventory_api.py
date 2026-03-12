"""Tests for InventoryApi."""

import json
import os

import pytest

from inventory_api import InventoryApi


@pytest.fixture
def api(tmp_path):
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    return inst


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


class TestSharedConstants:
    def test_section_order_loaded_from_json(self):
        """SECTION_ORDER should match data/constants.json (mixed format)."""
        constants_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
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
        # Flat entry has no children
        res = next(h for h in hier if h["name"] == "Passives - Resistors")
        assert res["children"] is None

    def test_fieldnames_loaded_from_json(self):
        """FIELDNAMES should match data/constants.json."""
        constants_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "constants.json",
        )
        with open(constants_path, encoding="utf-8") as f:
            constants = json.load(f)
        assert InventoryApi.FIELDNAMES == constants["FIELDNAMES"]


class TestCategorize:
    def test_resistor_by_description(self):
        row = {"Description": "Resistor 10k\u03a9 \u00b11%", "Package": "0402", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Resistors"

    def test_capacitor_by_description(self):
        """Generic capacitor without subcategory keywords stays at parent level."""
        row = {"Description": "Capacitor 100nF 25V", "Package": "0402", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors"

    def test_capacitor_mlcc(self):
        row = {"Description": "Cap Cer 100nF 25V X7R", "Package": "0402", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors > MLCC"

    def test_capacitor_mlcc_keyword(self):
        row = {"Description": "MLCC Capacitor 10uF", "Package": "0805", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors > MLCC"

    def test_capacitor_aluminum_polymer(self):
        row = {"Description": "Aluminum Electrolytic Capacitor 100uF 25V", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors > Aluminum Polymer"

    def test_capacitor_tantalum(self):
        row = {"Description": "Tantalum Capacitor 10uF 16V", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors > Tantalum"

    def test_mosfet_subcategory(self):
        row = {"Description": "N-Channel MOSFET 30V 5A", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Discrete Semiconductors > MOSFETs"

    def test_discrete_without_mosfet(self):
        row = {"Description": "NPN Transistor BJT 40V", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Discrete Semiconductors"

    def test_ldo_subcategory(self):
        row = {"Description": "LDO Voltage Regulator 3.3V 500mA", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > LDOs"

    def test_buck_switcher_subcategory(self):
        row = {"Description": "Buck Switching Regulator IC 5V 2A", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_boost_switcher_subcategory(self):
        row = {"Description": "Boost Voltage Regulator IC 12V", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_load_switch_subcategory(self):
        row = {"Description": "Load Switch IC 3.3V 1A", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > Load Switches"

    def test_pwr_switch_subcategory(self):
        row = {"Description": "IC PWR SWITCH 1:1 20VQFN", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > Load Switches"

    def test_generic_voltage_regulator(self):
        """Voltage regulator without subcategory keywords stays at parent."""
        row = {"Description": "Voltage Regulator IC 3.3V", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators"

    def test_connector_by_keyword(self):
        row = {"Description": "USB-C Connector", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Connectors"

    def test_mcu(self):
        row = {"Description": "Microcontroller ARM Cortex-M4", "Package": "LQFP-64", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Microcontrollers"

    def test_other_fallback(self):
        row = {"Description": "Something unknown", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Other"

    def test_switching_regulator_not_switch(self):
        row = {"Description": "Switching Regulator IC", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_tactile_switch(self):
        row = {"Description": "Tactile switch 6x6mm", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Switches"

    def test_esd_diode_not_diodes(self):
        row = {"Description": "ESD Protection Diode", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - ESD Protection"

    def test_diode_without_esd(self):
        row = {"Description": "Schottky Diode 40V", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Diodes"

    def test_connector_by_mpn(self):
        row = {"Description": "something", "Manufacture Part Number": "SM04B-GHS"}
        assert InventoryApi.categorize(row) == "Connectors"

    def test_motor_driver_by_mpn(self):
        row = {"Description": "IC chip", "Manufacture Part Number": "DRV8353"}
        assert InventoryApi.categorize(row) == "ICs - Motor Drivers"

    def test_resistor_by_manufacturer(self):
        row = {"Description": "Chip component", "Manufacture Part Number": "", "Manufacturer": "UNI-ROYAL"}
        assert InventoryApi.categorize(row) == "Passives - Resistors"

    def test_resistor_by_mfr_and_desc(self):
        row = {"Description": "100m\u03c9 shunt", "Manufacture Part Number": "", "Manufacturer": "TA-I Tech"}
        assert InventoryApi.categorize(row) == "Passives - Resistors"

    def test_voltage_reference_by_mpn(self):
        row = {"Description": "Voltage ref IC", "Manufacture Part Number": "REF3033"}
        assert InventoryApi.categorize(row) == "ICs - Voltage References"

    def test_sensor_by_mpn(self):
        row = {"Description": "Magnetic encoder", "Manufacture Part Number": "MT6835"}
        assert InventoryApi.categorize(row) == "ICs - Sensors"


class TestParseResistance:
    def test_kilo_ohm(self):
        assert InventoryApi.parse_resistance("10k\u03a9") == 10000.0

    def test_fractional_kilo_ohm(self):
        assert InventoryApi.parse_resistance("4.7k\u03a9") == 4700.0

    def test_mega_ohm(self):
        assert InventoryApi.parse_resistance("1M\u03a9") == 1000000.0

    def test_no_match_returns_inf(self):
        assert InventoryApi.parse_resistance("no resistor here") == float("inf")


class TestParseCapacitance:
    def test_nanofarad(self):
        assert InventoryApi.parse_capacitance("100nF") == pytest.approx(100e-9)

    def test_picofarad(self):
        assert InventoryApi.parse_capacitance("22pF") == pytest.approx(22e-12)

    def test_no_match_returns_inf(self):
        assert InventoryApi.parse_capacitance("no cap") == float("inf")


class TestLoadPreferences:
    def test_malformed_json_returns_empty(self, api):
        with open(api.prefs_json, "w") as f:
            f.write("{bad json!!")
        assert api.load_preferences() == {}

    def test_missing_file_returns_empty(self, api):
        assert api.load_preferences() == {}

    def test_valid_json_loaded(self, api):
        with open(api.prefs_json, "w") as f:
            json.dump({"theme": "dark"}, f)
        assert api.load_preferences() == {"theme": "dark"}


# ── Helper to write purchase_ledger.csv ──

def _write_ledger(api, rows):
    """Write rows to purchase_ledger.csv with standard fieldnames."""
    import csv
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=InventoryApi.FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = {fn: "" for fn in InventoryApi.FIELDNAMES}
            row.update(r)
            writer.writerow(row)


def _make_part(lcsc="", mpn="", qty=10, desc="Resistor 10kΩ", pkg="0402",
               unit_price="0.01", ext_price="0.10"):
    return {
        "LCSC Part Number": lcsc,
        "Manufacture Part Number": mpn,
        "Quantity": str(qty),
        "Description": desc,
        "Package": pkg,
        "Unit Price($)": unit_price,
        "Ext.Price($)": ext_price,
    }


# ── New helper tests ──

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


# ── Core pipeline tests ──

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


class TestApplyAdjustments:
    def _write_adj(self, api, rows):
        import csv
        with open(api.adjustments_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=InventoryApi.ADJ_FIELDNAMES)
            writer.writeheader()
            for r in rows:
                row = {fn: "" for fn in InventoryApi.ADJ_FIELDNAMES}
                row.update(r)
                writer.writerow(row)

    def test_set_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "set", "lcsc_part": "C100000", "quantity": "5"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "5"

    def test_add_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "add", "lcsc_part": "C100000", "quantity": "3"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "13"

    def test_consume_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "consume", "lcsc_part": "C100000", "quantity": "-4"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "6"

    def test_malformed_qty_skipped(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "set", "lcsc_part": "C100000", "quantity": "abc"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "10"  # unchanged

    def test_set_creates_new_part(self, api):
        fieldnames = list(InventoryApi.FIELDNAMES)
        merged = {}
        self._write_adj(api, [{"type": "set", "lcsc_part": "C999999", "quantity": "5"}])
        api._apply_adjustments(merged, fieldnames)
        assert "C999999" in merged
        assert merged["C999999"]["LCSC Part Number"] == "C999999"


class TestConsumeBom:
    def test_basic_consume(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=20)])
        matches = [{"part_key": "C100000", "bom_qty": 2}]
        result = api.consume_bom(matches, 3, "test.csv")
        # 20 - (2*3) = 14
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 14

    def test_writes_adjustments_csv(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=20)])
        api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], 2, "bom.csv")
        assert os.path.exists(api.adjustments_csv)

    def test_json_string_input(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        matches_json = json.dumps([{"part_key": "C100000", "bom_qty": 1}])
        result = api.consume_bom(matches_json, 1, "test.csv")
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 9

    def test_zero_board_qty_raises(self, api):
        with pytest.raises(ValueError, match="positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], 0, "test.csv")

    def test_empty_matches_raises(self, api):
        with pytest.raises(ValueError, match="empty"):
            api.consume_bom([], 1, "test.csv")

    def test_zero_bom_qty_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="bom_qty must be positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 0}], 1, "test.csv")

    def test_negative_board_qty_raises(self, api):
        with pytest.raises(ValueError, match="positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], -1, "test.csv")


class TestImportPurchases:
    def test_creates_ledger(self, api):
        rows = [_make_part(lcsc="C100000", qty=5)]
        result = api.import_purchases(rows)
        assert any(r["lcsc"] == "C100000" for r in result)

    def test_appends_to_existing(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=5)])
        rows = [_make_part(lcsc="C200000", qty=3, desc="Capacitor 100nF 25V")]
        result = api.import_purchases(rows)
        lcscs = {r["lcsc"] for r in result}
        assert "C100000" in lcscs
        assert "C200000" in lcscs

    def test_empty_rows_error(self, api):
        result = api.import_purchases([])
        assert result == {"error": "No rows to import"}

    def test_json_string_input(self, api):
        rows_json = json.dumps([_make_part(lcsc="C100000", qty=5)])
        result = api.import_purchases(rows_json)
        assert any(r["lcsc"] == "C100000" for r in result)


class TestAdjustPart:
    def test_add(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("add", "C100000", 5)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15

    def test_remove(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("remove", "C100000", 3)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 7

    def test_set(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("set", "C100000", 42)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 42

    def test_unknown_type_error(self, api):
        result = api.adjust_part("delete", "C100000", 1)
        assert "error" in result

    def test_negative_quantity_raises(self, api):
        with pytest.raises(ValueError, match="non-negative"):
            api.adjust_part("add", "C100000", -5)

    def test_empty_part_key_raises(self, api):
        with pytest.raises(ValueError, match="empty"):
            api.adjust_part("add", "", 5)


class TestUpdatePartPrice:
    def test_unit_price_auto_ext(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C100000", unit_price=0.05)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["unit_price"] == pytest.approx(0.05)
        assert part["ext_price"] == pytest.approx(0.50)

    def test_ext_price_auto_unit(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C100000", ext_price=1.00)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["unit_price"] == pytest.approx(0.10)
        assert part["ext_price"] == pytest.approx(1.00)

    def test_missing_part_creates_row(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C999999", unit_price=0.01)
        # Should not error — creates a new row
        assert isinstance(result, list)

    def test_no_ledger_error(self, api):
        result = api.update_part_price("C100000", unit_price=0.01)
        assert result == {"error": "No purchase ledger found"}


class TestDetectColumns:
    def test_digikey_headers(self, api):
        headers = ["Digi-Key Part Number", "Manufacturer Part Number",
                    "Manufacturer", "Quantity", "Unit Price", "Extended Price"]
        mapping = api.detect_columns(headers)
        assert mapping.get("0") == "Digikey Part Number"
        assert mapping.get("1") == "Manufacture Part Number"
        assert mapping.get("3") == "Quantity"

    def test_lcsc_headers(self, api):
        headers = ["LCSC Part Number", "Quantity", "Description"]
        mapping = api.detect_columns(headers)
        assert mapping.get("0") == "LCSC Part Number"
        assert mapping.get("1") == "Quantity"

    def test_no_match(self, api):
        headers = ["foo", "bar", "baz"]
        mapping = api.detect_columns(headers)
        assert mapping == {}

    def test_json_string_input(self, api):
        headers_json = json.dumps(["LCSC Part Number", "Quantity"])
        mapping = api.detect_columns(headers_json)
        assert mapping.get("0") == "LCSC Part Number"


class TestLoadFile:
    def test_existing_file(self, api, tmp_path):
        test_file = tmp_path / "test.csv"
        test_file.write_text("col1,col2\na,b\n", encoding="utf-8")
        result = api.load_file(str(test_file))
        assert result is not None
        assert result["name"] == "test.csv"
        assert "col1,col2" in result["content"]
        assert result["directory"] == str(tmp_path)
        assert result["path"] == str(test_file)

    def test_missing_file(self, api):
        result = api.load_file("/nonexistent/path/file.csv")
        assert result is None

    def test_empty_path(self, api):
        assert api.load_file("") is None
        assert api.load_file(None) is None

    def test_sidecar_links(self, api, tmp_path):
        test_file = tmp_path / "bom.csv"
        test_file.write_text("h1,h2\n1,2\n", encoding="utf-8")
        links_file = tmp_path / "bom.links.json"
        links_file.write_text('{"manualLinks": [{"bomKey": "a", "invPartKey": "b"}]}', encoding="utf-8")
        result = api.load_file(str(test_file))
        assert result is not None
        assert "links" in result
        assert result["links"]["manualLinks"][0]["bomKey"] == "a"


class TestConfirmClose:
    def test_force_close_flag(self, api):
        assert api._force_close is False

    def test_closing_flag_default(self, api):
        assert api._closing is False

    def test_bom_dirty_flag_default(self, api):
        assert api._bom_dirty is False

    def test_set_bom_dirty(self, api):
        api.set_bom_dirty(True)
        assert api._bom_dirty is True
        api.set_bom_dirty(False)
        assert api._bom_dirty is False

    def test_set_bom_dirty_coerces(self, api):
        api.set_bom_dirty(1)
        assert api._bom_dirty is True
        api.set_bom_dirty(0)
        assert api._bom_dirty is False

    def test_confirm_close_sets_flag(self, api, monkeypatch):
        import types
        mock_win = types.SimpleNamespace(destroy=lambda: None)
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        assert api._force_close is True

    def test_confirm_close_calls_destroy(self, api, monkeypatch):
        import types
        destroyed = []
        mock_win = types.SimpleNamespace(destroy=lambda: destroyed.append(True))
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        assert len(destroyed) == 1

    def test_confirm_close_double_call(self, api, monkeypatch):
        import types
        destroyed = []
        mock_win = types.SimpleNamespace(destroy=lambda: destroyed.append(True))
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        api.confirm_close()
        assert len(destroyed) == 1

    def test_confirm_close_destroy_exception(self, api, monkeypatch):
        import types

        def exploding_destroy():
            raise RuntimeError("window already destroyed")

        mock_win = types.SimpleNamespace(destroy=exploding_destroy)
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()  # should not raise
        assert api._force_close is True
        assert api._closing is True


class TestTruncateCsv:
    def test_remove_last_purchases(self, api):
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
            _make_part(lcsc="C300000", qty=30, desc="LED Red"),
        ])
        result = api.remove_last_purchases(2)
        lcscs = {r["lcsc"] for r in result}
        assert "C100000" in lcscs
        assert "C200000" not in lcscs
        assert "C300000" not in lcscs

    def test_remove_last_adjustments(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.adjust_part("add", "C100000", 5)
        api.adjust_part("add", "C100000", 3)
        result = api.remove_last_adjustments(1)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15  # 10 + 5, last adj removed

    def test_zero_count_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="positive"):
            api.remove_last_purchases(0)

    def test_count_exceeds_rows_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="Cannot remove"):
            api.remove_last_purchases(5)

    def test_missing_file_raises(self, api):
        with pytest.raises(ValueError, match="No purchase ledger file found"):
            api.remove_last_purchases(1)

    def test_missing_adjustments_file_raises(self, api):
        with pytest.raises(ValueError, match="No adjustments file found"):
            api.remove_last_adjustments(1)


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
