"""Tests for InventoryApi."""

import json
import os

import pytest

from categorize import categorize, parse_capacitance, parse_resistance
from inventory_api import InventoryApi


@pytest.fixture
def api(tmp_path):
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    inst.events_dir = str(tmp_path / "events")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    inst.cache_db_path = str(data_dir / "cache.db")
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


class TestCategorize:
    def test_resistor_chip_by_description(self):
        row = {"Description": "Resistor 10kΩ ±1%", "Package": "0402", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Resistors > Chip Resistors"

    def test_resistor_chip_by_ohm(self):
        row = {"Description": "RES 1.5K OHM 1% 1/16W 0402", "Package": "0402", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Resistors > Chip Resistors"

    def test_resistor_trimmer(self):
        row = {"Description": "TRIMMER 10 OHM 0.75W PC PIN SIDE", "Manufacture Part Number": "3006P-1-100LF", "Manufacturer": "Bourns Inc."}
        assert categorize(row) == "Passives - Resistors > Variable / Trimmers"

    def test_resistor_potentiometer(self):
        row = {"Description": "Potentiometer 10kΩ Linear", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Resistors > Variable / Trimmers"

    def test_resistor_by_manufacturer_no_subcategory(self):
        """Resistor matched by manufacturer alone without subcategory keywords stays at parent."""
        row = {"Description": "Chip component", "Manufacture Part Number": "", "Manufacturer": "UNI-ROYAL"}
        assert categorize(row) == "Passives - Resistors"

    def test_capacitor_by_description(self):
        """Generic capacitor without subcategory keywords stays at parent level."""
        row = {"Description": "Capacitor 100nF 25V", "Package": "0402", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors"

    def test_capacitor_mlcc(self):
        row = {"Description": "Cap Cer 100nF 25V X7R", "Package": "0402", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors > MLCC"

    def test_capacitor_mlcc_keyword(self):
        row = {"Description": "MLCC Capacitor 10uF", "Package": "0805", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors > MLCC"

    def test_capacitor_ceramic_is_mlcc(self):
        row = {"Description": "100nF ±10% 50V Ceramic Capacitor X7R 0402", "Package": "0402", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors > MLCC"

    def test_capacitor_aluminum_polymer(self):
        row = {"Description": "Aluminum Electrolytic Capacitor 100uF 25V", "Package": "", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors > Aluminum Polymer"

    def test_capacitor_tantalum(self):
        row = {"Description": "Tantalum Capacitor 10uF 16V", "Package": "", "Manufacture Part Number": ""}
        assert categorize(row) == "Passives - Capacitors > Tantalum"

    def test_mosfet_subcategory(self):
        row = {"Description": "N-Channel MOSFET 30V 5A", "Manufacture Part Number": ""}
        assert categorize(row) == "Discrete Semiconductors > MOSFETs"

    def test_discrete_without_mosfet(self):
        row = {"Description": "NPN Transistor BJT 40V", "Manufacture Part Number": ""}
        assert categorize(row) == "Discrete Semiconductors"

    def test_ldo_subcategory(self):
        row = {"Description": "LDO Voltage Regulator 3.3V 500mA", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > LDOs"

    def test_buck_switcher_subcategory(self):
        row = {"Description": "Buck Switching Regulator IC 5V 2A", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_boost_switcher_subcategory(self):
        row = {"Description": "Boost Voltage Regulator IC 12V", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_load_switch_subcategory(self):
        row = {"Description": "Load Switch IC 3.3V 1A", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > Load Switches"

    def test_pwr_switch_subcategory(self):
        row = {"Description": "IC PWR SWITCH 1:1 20VQFN", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > Load Switches"

    def test_generic_voltage_regulator(self):
        """Voltage regulator without subcategory keywords stays at parent."""
        row = {"Description": "Voltage Regulator IC 3.3V", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators"

    def test_connector_by_keyword(self):
        row = {"Description": "USB-C Connector", "Package": "", "Manufacture Part Number": ""}
        assert categorize(row) == "Connectors > High Speed"

    def test_mcu(self):
        row = {"Description": "Microcontroller ARM Cortex-M4", "Package": "LQFP-64", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Microcontrollers"

    def test_other_fallback(self):
        row = {"Description": "Something unknown", "Package": "", "Manufacture Part Number": ""}
        assert categorize(row) == "Other"

    def test_switching_regulator_not_switch(self):
        row = {"Description": "Switching Regulator IC", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Power / Voltage Regulators > Switchers"

    def test_tactile_switch(self):
        row = {"Description": "Tactile switch 6x6mm", "Manufacture Part Number": ""}
        assert categorize(row) == "Switches"

    def test_esd_diode_not_diodes(self):
        row = {"Description": "ESD Protection Diode", "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - ESD Protection"

    def test_diode_without_esd(self):
        row = {"Description": "Schottky Diode 40V", "Manufacture Part Number": ""}
        assert categorize(row) == "Diodes"

    def test_connector_by_mpn(self):
        row = {"Description": "something", "Manufacture Part Number": "SM04B-GHS"}
        assert categorize(row) == "Connectors"

    def test_motor_driver_by_mpn(self):
        row = {"Description": "IC chip", "Manufacture Part Number": "DRV8353"}
        assert categorize(row) == "ICs - Motor Drivers"

    def test_resistor_by_mfr_and_desc(self):
        row = {"Description": "100mω shunt", "Manufacture Part Number": "", "Manufacturer": "TA-I Tech"}
        assert categorize(row) == "Passives - Resistors > Chip Resistors"

    def test_voltage_reference_by_mpn(self):
        row = {"Description": "Voltage ref IC", "Manufacture Part Number": "REF3033"}
        assert categorize(row) == "ICs - Voltage References"

    def test_sensor_by_mpn(self):
        row = {"Description": "Magnetic encoder", "Manufacture Part Number": "MT6835"}
        assert categorize(row) == "ICs - Sensors"


class TestParseResistance:
    def test_kilo_ohm(self):
        assert parse_resistance("10k\u03a9") == 10000.0

    def test_fractional_kilo_ohm(self):
        assert parse_resistance("4.7k\u03a9") == 4700.0

    def test_mega_ohm(self):
        assert parse_resistance("1M\u03a9") == 1000000.0

    def test_no_match_returns_inf(self):
        assert parse_resistance("no resistor here") == float("inf")


class TestParseCapacitance:
    def test_nanofarad(self):
        assert parse_capacitance("100nF") == pytest.approx(100e-9)

    def test_picofarad(self):
        assert parse_capacitance("22pF") == pytest.approx(22e-12)

    def test_no_match_returns_inf(self):
        assert parse_capacitance("no cap") == float("inf")


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
        with pytest.raises(ValueError, match="No rows to import"):
            api.import_purchases([])

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
        with pytest.raises(ValueError, match="Unknown adjustment type"):
            api.adjust_part("delete", "C100000", 1)

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
        assert any(r["lcsc"] == "C999999" for r in result), "New part C999999 should appear in result"

    def test_no_ledger_error(self, api):
        with pytest.raises(ValueError, match="No purchase ledger found"):
            api.update_part_price("C100000", unit_price=0.01)


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

    def test_mouser_headers(self, api):
        """Mouser cart XLS headers are detected correctly."""
        headers = ["", "Mouser #", "Mfr. #", "Manufacturer", "Customer #",
                    "Description", "RoHS", "Lifecycle", "Order Qty.",
                    "Price (USD)", "Ext.: (USD)"]
        mapping = api.detect_columns(headers)
        assert mapping.get("1") == "Mouser Part Number"
        assert mapping.get("2") == "Manufacture Part Number"
        assert mapping.get("3") == "Manufacturer"
        assert mapping.get("8") == "Quantity"
        assert mapping.get("9") == "Unit Price($)"
        assert mapping.get("10") == "Ext.Price($)"

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


class TestConvertXls:
    def test_mouser_cart_xls(self, api):
        """Convert real Mouser cart XLS file to CSV."""
        xls_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "Cart_Mar25_0912PM.xls",
        )
        if not os.path.exists(xls_path):
            pytest.skip("Mouser XLS test file not available")
        result = api.convert_xls_to_csv(xls_path)
        assert result is not None
        assert result["row_count"] >= 1
        assert any("mouser" in h.lower() for h in result["headers"])
        assert result["csv_text"]  # non-empty


class TestInferDistributor:
    def test_lcsc(self):
        row = {"LCSC Part Number": "C1525", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert InventoryApi._infer_distributor(row) == "lcsc"

    def test_digikey(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "DK-123", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert InventoryApi._infer_distributor(row) == "digikey"

    def test_mouser(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "M-123", "Pololu Part Number": ""}
        assert InventoryApi._infer_distributor(row) == "mouser"

    def test_pololu(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": "1992"}
        assert InventoryApi._infer_distributor(row) == "pololu"

    def test_unknown(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert InventoryApi._infer_distributor(row) == "unknown"


class TestPriceHistoryOnImport:
    def test_import_records_price_observations(self, api, tmp_path):
        import csv as csv_mod
        rows = [
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "",
             "Digikey Part Number": "", "Pololu Part Number": "",
             "Mouser Part Number": "",
             "Manufacturer": "", "Quantity": "100",
             "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
             "Description": "", "Package": "", "RoHS": "",
             "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ]
        api.import_purchases(rows)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        assert os.path.exists(obs_path)
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"
        assert obs[0]["distributor"] == "lcsc"
        assert float(obs[0]["unit_price"]) == pytest.approx(0.0074)
        assert obs[0]["source"] == "import"


class TestPriceHistoryOnManualEdit:
    def _setup_part(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_price_update_records_observation(self, api, tmp_path):
        import csv as csv_mod
        self._setup_part(api)
        api.update_part_price("C1525", unit_price=0.01)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        manual = [o for o in obs if o["source"] == "manual"]
        assert len(manual) == 1
        assert manual[0]["part_id"] == "C1525"
        assert float(manual[0]["unit_price"]) == pytest.approx(0.01)


class TestRecordFetchedPrices:
    def _setup_part(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_record_fetched_prices(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
            {"qty": 10, "price": 0.0070},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0070)
        assert summary["lcsc"]["price_count"] >= 2

    def test_get_price_summary_empty(self, api):
        summary = api.get_price_summary("NONEXISTENT")
        assert summary == {}

    def test_get_price_summary_multiple_distributors(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "digikey", [
            {"qty": 1, "price": 0.012},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert "digikey" in summary


class TestPricesFKResolution:
    """Test that distributor-specific PNs resolve to inventory part_ids."""

    def _setup_part_with_digikey(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "DRV8316C",
            "Digikey Part Number": "296-DRV8316CRRGFRCT-ND",
            "Pololu Part Number": "", "Mouser Part Number": "",
            "Manufacturer": "TI", "Quantity": "10",
            "Unit Price($)": "2.50", "Ext.Price($)": "25.00",
            "Description": "Motor driver", "Package": "QFN",
            "RoHS": "Yes", "Customer NO.": "",
            "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_record_fetched_prices_with_digikey_pn(self, api):
        """record_fetched_prices resolves Digikey PN to inventory part_id."""
        self._setup_part_with_digikey(api)
        # Pass the Digikey PN, not the inventory part_id (C1525)
        api.record_fetched_prices("296-DRV8316CRRGFRCT-ND", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Should be queryable by either key
        summary = api.get_price_summary("C1525")
        assert "digikey" in summary
        assert summary["digikey"]["latest_unit_price"] == pytest.approx(2.80)

    def test_get_price_summary_with_digikey_pn(self, api):
        """get_price_summary resolves Digikey PN to inventory part_id."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("C1525", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Query using the Digikey PN
        summary = api.get_price_summary("296-DRV8316CRRGFRCT-ND")
        assert "digikey" in summary

    def test_record_fetched_prices_with_mpn(self, api):
        """record_fetched_prices resolves MPN to inventory part_id."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("DRV8316C", "mouser", [
            {"qty": 1, "price": 2.70},
        ])
        summary = api.get_price_summary("C1525")
        assert "mouser" in summary

    def test_record_fetched_prices_unknown_part_skipped(self, api):
        """record_fetched_prices silently skips unknown part keys."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("TOTALLY-UNKNOWN-PN", "digikey", [
            {"qty": 1, "price": 1.00},
        ])
        # No crash, no data written
        summary = api.get_price_summary("C1525")
        assert "digikey" not in summary

    def test_populate_prices_cache_resolves_distributor_pn(self, api):
        """populate_prices_cache resolves distributor PNs in historical data."""
        import price_history
        self._setup_part_with_digikey(api)
        # Write observation with Digikey PN directly to the log
        events_dir = os.path.join(api.base_dir, "events")
        os.makedirs(events_dir, exist_ok=True)
        price_history.record_observations(events_dir, [{
            "part_id": "296-DRV8316CRRGFRCT-ND",
            "distributor": "digikey",
            "unit_price": 2.80,
            "source": "live_fetch",
        }])
        # Rebuild cache — should resolve the Digikey PN
        conn = api._get_cache()
        price_history.populate_prices_cache(conn, events_dir)
        rows = conn.execute(
            "SELECT * FROM prices WHERE part_id = ? AND distributor = ?",
            ("C1525", "digikey"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["latest_unit_price"] == pytest.approx(2.80)


class TestPricesCacheOnRebuild:
    def test_rebuild_populates_prices_cache(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0074)


class TestGenericPartsAPI:
    def _import_parts(self, api):
        api.import_purchases([
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "CL05B104KO5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "200",
             "Unit Price($)": "0.0074", "Ext.Price($)": "1.48",
             "Description": "100nF 16V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
            {"LCSC Part Number": "C9999", "Manufacture Part Number": "CL05B104KA5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "50",
             "Unit Price($)": "0.006", "Ext.Price($)": "0.30",
             "Description": "100nF 25V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ])

    def test_create_generic_part(self, api):
        self._import_parts(api)
        result = api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["generic_part_id"].startswith("cap_")
        assert len(result["members"]) == 2  # C1525 and C9999

    def test_resolve_bom_spec(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = api.resolve_bom_spec("capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_list_generic_parts(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = api.list_generic_parts()
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"
