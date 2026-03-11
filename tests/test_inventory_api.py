"""Smoke tests for InventoryApi static methods."""

import pytest

from inventory_api import InventoryApi


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


class TestCategorize:
    def test_resistor_by_description(self):
        row = {"Description": "Resistor 10k\u03a9 \u00b11%", "Package": "0402", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Resistors"

    def test_capacitor_by_description(self):
        row = {"Description": "Capacitor 100nF 25V", "Package": "0402", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Passives - Capacitors"

    def test_connector_by_keyword(self):
        row = {"Description": "USB-C Connector", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Connectors"

    def test_mcu(self):
        row = {"Description": "Microcontroller ARM Cortex-M4", "Package": "LQFP-64", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "ICs - Microcontrollers"

    def test_other_fallback(self):
        row = {"Description": "Something unknown", "Package": "", "Manufacture Part Number": ""}
        assert InventoryApi.categorize(row) == "Other"


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
