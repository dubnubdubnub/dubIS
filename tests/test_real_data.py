"""Integration tests using real inventory data files.

Verifies the full Python pipeline (read ledger → apply adjustments → rebuild)
produces correct results against known real data.
"""

import csv
import os
import shutil

import pytest

from inventory_api import InventoryApi

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def real_api(tmp_path):
    """InventoryApi pointed at copies of real data files."""
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")

    # Copy real fixtures into tmp_path
    shutil.copy(os.path.join(FIXTURES, "purchase_ledger.csv"), inst.input_csv)
    shutil.copy(os.path.join(FIXTURES, "adjustments.csv"), inst.adjustments_csv)
    return inst


class TestRealPipelineRebuild:
    def test_rebuild_produces_parts(self, real_api):
        inv = real_api.rebuild_inventory()
        assert len(inv) > 100

    def test_every_part_has_section_and_id(self, real_api):
        inv = real_api.rebuild_inventory()
        for item in inv:
            assert item["section"], f"Missing section: {item}"
            assert item["lcsc"] or item["mpn"] or item["digikey"], (
                f"No part ID: {item}"
            )

    def test_quantities_are_non_negative(self, real_api):
        inv = real_api.rebuild_inventory()
        for item in inv:
            assert item["qty"] >= 0, (
                f"Negative qty for {item['lcsc'] or item['mpn']}: {item['qty']}"
            )

    def test_known_parts_present(self, real_api):
        inv = real_api.rebuild_inventory()
        lcscs = {item["lcsc"] for item in inv}
        mpns = {item["mpn"] for item in inv}

        # From LCSC POs
        assert "C2875244" in lcscs   # Crystal 16MHz
        assert "C32949" in lcscs     # 10pF cap
        assert "C879894" in lcscs    # 10uF tantalum

        # From Digikey POs
        assert "DRV8316CRRGFR" in mpns   # Motor driver
        assert "STM32G491CCU6" in mpns    # MCU

    def test_adjustments_applied(self, real_api):
        inv = real_api.rebuild_inventory()
        # C2846801 was set to 1 in adjustments
        part = next((i for i in inv if i["lcsc"] == "C2846801"), None)
        assert part is not None
        assert part["qty"] == 1

    def test_consume_adjustments_reduce_qty(self, real_api):
        inv = real_api.rebuild_inventory()
        # C440198 had consume adjustments from lemon-pepper BOM (-80)
        part = next((i for i in inv if i["lcsc"] == "C440198"), None)
        assert part is not None
        # Total from POs is 3000, consumed 80 → should be 2920
        assert part["qty"] == 2920

    def test_sections_categorized(self, real_api):
        inv = real_api.rebuild_inventory()
        sections = {item["section"] for item in inv}
        assert "Connectors" in sections
        assert "Passives - Resistors" in sections
        assert "Passives - Capacitors" in sections
        assert "ICs - Microcontrollers" in sections

    def test_categorization_snapshot(self, real_api):
        """Every part must categorize identically to the known-good snapshot."""
        inv = real_api.rebuild_inventory()
        actual = {(item["lcsc"] or item["mpn"]): item["section"] for item in inv}
        # Snapshot generated from main branch (pre-refactor)
        expected = {
            "ADP2230ACPZ-1233R7": "ICs - Power / Voltage Regulators",
            "C106231": "Passives - Resistors", "C11702": "Passives - Resistors",
            "C12084": "ICs - Interface", "C12624": "LEDs",
            "C127692": "Passives - Resistors", "C12891": "Passives - Capacitors",
            "C134082": "Connectors", "C136657": "Connectors",
            "C14289": "ICs - Power / Voltage Regulators", "C14996": "Diodes",
            "C15127": "Other", "C1518208": "Passives - Capacitors",
            "C1538": "Passives - Capacitors", "C1554": "Passives - Capacitors",
            "C1555": "Passives - Capacitors", "C1567": "Passives - Capacitors",
            "C15742": "ICs - Microcontrollers", "C15850": "Passives - Capacitors",
            "C160349": "Connectors", "C160404": "Connectors",
            "C160405": "Connectors", "C162274": "Passives - Capacitors",
            "C19077418": "Diodes", "C19077434": "Diodes",
            "C19271996": "Connectors", "C19271997": "Connectors",
            "C19271998": "Connectors", "C19271999": "Connectors",
            "C19272000": "Connectors", "C19272005": "Connectors",
            "C19272006": "Connectors", "C19272007": "Connectors",
            "C19272008": "Connectors", "C20615829": "ICs - ESD Protection",
            "C21189": "Passives - Resistors", "C2128": "Diodes",
            "C2145": "Discrete Semiconductors", "C22375291": "Diodes",
            "C22459526": "Switches", "C22808": "Passives - Resistors",
            "C2286": "LEDs", "C2290": "LEDs",
            "C22936": "Passives - Resistors", "C25076": "Passives - Resistors",
            "C25077": "Passives - Resistors", "C25079": "Passives - Resistors",
            "C25082": "Passives - Resistors", "C25744": "Passives - Resistors",
            "C25752": "Passives - Resistors", "C25756": "Passives - Resistors",
            "C25764": "Passives - Resistors", "C25768": "Passives - Resistors",
            "C25774": "Passives - Resistors", "C25794": "Passives - Resistors",
            "C25890": "Passives - Resistors", "C25897": "Passives - Resistors",
            "C25900": "Passives - Resistors", "C25905": "Passives - Resistors",
            "C272878": "Passives - Capacitors", "C2760486": "Connectors",
            "C2846801": "ICs - Power / Voltage Regulators",
            "C2875244": "Crystals & Oscillators", "C2879839": "Switches",
            "C2887272": "Passives - Capacitors", "C2888932": "Switches",
            "C2906859": "Passives - Resistors", "C2913974": "ICs - Sensors",
            "C2930002": "Passives - Resistors", "C2932578": "ICs - Sensors",
            "C2933103": "Passives - Resistors", "C2984637": "ICs - Sensors",
            "C30170202": "Connectors", "C307331": "Passives - Capacitors",
            "C315248": "Passives - Capacitors", "C327198": "Passives - Capacitors",
            "C32949": "Passives - Capacitors", "C333990": "Passives - Inductors",
            "C36658": "ICs - Voltage References", "C393094": "Passives - Resistors",
            "C41421517": "ICs - Sensors", "C41430893": "Switches",
            "C42387346": "Diodes", "C42438032": "Connectors",
            "C42438034": "Connectors", "C42438041": "Connectors",
            "C42438043": "Connectors", "C424554": "Connectors",
            "C424555": "Connectors", "C428722": "Connectors",
            "C429942": "Connectors", "C440198": "Passives - Capacitors",
            "C49108636": "Switches", "C49140392": "Crystals & Oscillators",
            "C496552": "Connectors", "C5149201": "LEDs",
            "C5159775": "Passives - Capacitors",
            "C529356": "ICs - Microcontrollers", "C529361": "ICs - Microcontrollers",
            "C5301773": "Mechanical & Hardware", "C5334533": "Connectors",
            "C5382546": "ICs - Interface", "C544538": "ICs - Amplifiers",
            "C5446": "ICs - Power / Voltage Regulators",
            "C5942077": "ICs - Power / Voltage Regulators",
            "C602034": "Passives - Inductors",
            "C6119795": "Passives - Capacitors", "C6305267": "Connectors",
            "C633619": "Other", "C7437027": "ICs - Microcontrollers",
            "C7471904": "Other", "C76947": "Passives - Capacitors",
            "C85960": "Passives - Capacitors", "C86295": "Passives - Capacitors",
            "C879894": "Passives - Capacitors", "C88982": "Passives - Resistors",
            "C9002": "Crystals & Oscillators",
            "C96151": "Passives - Inductors", "C962978": "ICs - ESD Protection",
            "C963223": "Connectors", "C963349": "Switches",
            "C964792": "Mechanical & Hardware", "C965891": "LEDs",
            "C98514": "Passives - Inductors", "C98732": "Connectors",
            "C99101": "Connectors", "C99102": "Connectors",
            "CL05A104KA5NNNC": "Passives - Capacitors",
            "CMLDM8005 TR PBFREE": "Discrete Semiconductors",
            "DRV8316CRRGFR": "ICs - Motor Drivers",
            "L6226QTR": "ICs - Motor Drivers",
            "MAX49925XATB+": "ICs - Amplifiers",
            "RC0201FR-071K5L": "Passives - Resistors",
            "RC0402FR-071K5L": "Passives - Resistors",
            "STM32G491CCU6": "ICs - Microcontrollers",
            "TCAN1044AEVDRQ1": "ICs - Interface",
            "TPD2EUSB30DRTR": "Diodes",
            "UCS2114-1-V/LX": "Switches",
            "UTC2000-I/MG": "Other",
        }
        for key, section in expected.items():
            assert actual.get(key) == section, f"{key}: expected {section}, got {actual.get(key)}"

    def test_prices_are_reasonable(self, real_api):
        inv = real_api.rebuild_inventory()
        for item in inv:
            assert item["unit_price"] >= 0, (
                f"Negative unit price for {item['lcsc'] or item['mpn']}"
            )
            assert item["ext_price"] >= 0, (
                f"Negative ext price for {item['lcsc'] or item['mpn']}"
            )

    def test_total_inventory_value_reasonable(self, real_api):
        inv = real_api.rebuild_inventory()
        total = sum(item["qty"] * item["unit_price"] for item in inv)
        # Should be a positive number representing real stock value
        assert total > 0


class TestRealPipelineMatchesSnapshot:
    """Compare rebuilt inventory against the known-good inventory.csv snapshot."""

    def test_part_count_matches_snapshot(self, real_api):
        inv = real_api.rebuild_inventory()

        # Load the snapshot for comparison
        snapshot_path = os.path.join(FIXTURES, "inventory.csv")
        snapshot_parts = []
        with open(snapshot_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                section = (row.get("Section") or "").strip()
                lcsc = (row.get("LCSC Part Number") or "").strip()
                mpn = (row.get("Manufacture Part Number") or "").strip()
                if section.startswith("=") or lcsc.startswith("="):
                    continue
                if not lcsc and not mpn:
                    continue
                snapshot_parts.append(row)

        assert len(inv) == len(snapshot_parts)

    def test_quantities_match_snapshot(self, real_api):
        inv = real_api.rebuild_inventory()
        inv_by_key = {}
        for item in inv:
            key = item["lcsc"] or item["mpn"]
            inv_by_key[key] = item

        snapshot_path = os.path.join(FIXTURES, "inventory.csv")
        with open(snapshot_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                section = (row.get("Section") or "").strip()
                lcsc = (row.get("LCSC Part Number") or "").strip()
                mpn = (row.get("Manufacture Part Number") or "").strip()
                if section.startswith("=") or lcsc.startswith("="):
                    continue
                if not lcsc and not mpn:
                    continue
                key = lcsc or mpn
                snap_qty = int(
                    (row.get("Quantity") or "0").replace(",", "") or "0"
                )
                assert key in inv_by_key, f"Part {key} missing from rebuild"
                assert inv_by_key[key]["qty"] == snap_qty, (
                    f"Qty mismatch for {key}: "
                    f"rebuild={inv_by_key[key]['qty']}, snapshot={snap_qty}"
                )


class TestRealDetectColumns:
    """Test column detection with real PO/BOM header formats."""

    def test_detects_lcsc_po_columns(self, real_api):
        headers = [
            "LCSC Part Number", "Manufacture Part Number", "Manufacturer",
            "Customer NO.", "Package", "Description", "RoHS", "Quantity",
            "Unit Price($)", "Ext.Price($)",
            "Estimated lead time (business days)", "Date Code / Lot No.",
        ]
        mapping = real_api.detect_columns(headers)
        assert mapping.get("0") == "LCSC Part Number"
        assert mapping.get("1") == "Manufacture Part Number"
        assert mapping.get("7") == "Quantity"

    def test_detects_digikey_po_columns(self, real_api):
        # Real Digikey PO headers (from 97746939.csv / 97970162.csv)
        headers = [
            "#", "QUANTITY", "PART NUMBER", "MANUFACTURER PART NUMBER",
            "DESCRIPTION", "CUSTOMER REFERENCE", "BACKORDER",
            "UNIT PRICE", "EXTENDED PRICE",
        ]
        mapping = real_api.detect_columns(headers)
        # "PART NUMBER" is too generic for Digikey detection (needs "digikey"
        # in the header), but MPN, qty, description, and prices are detected
        assert mapping.get("3") == "Manufacture Part Number"
        assert mapping.get("1") == "Quantity"
        assert mapping.get("4") == "Description"
        assert mapping.get("7") == "Unit Price($)"
        assert mapping.get("8") == "Ext.Price($)"


class TestRealAdjustAfterRebuild:
    """Test mutations on top of real rebuilt inventory."""

    def test_adjust_part_on_real_data(self, real_api):
        inv = real_api.rebuild_inventory()
        # Add 5 to a known part
        result = real_api.adjust_part("add", "C32949", 5)
        part = next(r for r in result if r["lcsc"] == "C32949")
        orig = next(i for i in inv if i["lcsc"] == "C32949")
        assert part["qty"] == orig["qty"] + 5

    def test_consume_bom_on_real_data(self, real_api):
        inv = real_api.rebuild_inventory()
        # Consume 1 board's worth of a few parts
        matches = [
            {"part_key": "C32949", "bom_qty": 2},    # 10pF cap
            {"part_key": "C2875244", "bom_qty": 1},   # Crystal
        ]
        result = real_api.consume_bom(matches, 1, "test-consume.csv")
        cap = next(r for r in result if r["lcsc"] == "C32949")
        crystal = next(r for r in result if r["lcsc"] == "C2875244")
        cap_orig = next(i for i in inv if i["lcsc"] == "C32949")
        crystal_orig = next(i for i in inv if i["lcsc"] == "C2875244")
        assert cap["qty"] == cap_orig["qty"] - 2
        assert crystal["qty"] == crystal_orig["qty"] - 1
