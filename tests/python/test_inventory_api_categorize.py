"""Tests for InventoryApi — categorization and spec parsing."""

import pytest

from categorize import categorize, parse_capacitance, parse_resistance


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

    def test_tmr_magnetic_sensor_by_description(self):
        row = {"Description": "Low Power Large Range TMR Linear Magnetic Sensor ±500Gs",
               "Manufacture Part Number": ""}
        assert categorize(row) == "ICs - Sensors"

    def test_tmr_sensor_by_mpn_without_description(self):
        """MDT manufacturer-direct parts arrive with no description; MPN must still sort it."""
        row = {"Description": "", "Manufacture Part Number": "TMR2615F-AAC-1.500-500",
               "Manufacturer": "MultiDimension Technology Co., Ltd."}
        assert categorize(row) == "ICs - Sensors"


class TestParseResistance:
    def test_kilo_ohm(self):
        assert parse_resistance("10kΩ") == 10000.0

    def test_fractional_kilo_ohm(self):
        assert parse_resistance("4.7kΩ") == 4700.0

    def test_mega_ohm(self):
        assert parse_resistance("1MΩ") == 1000000.0

    def test_no_match_returns_inf(self):
        assert parse_resistance("no resistor here") == float("inf")


class TestParseCapacitance:
    def test_nanofarad(self):
        assert parse_capacitance("100nF") == pytest.approx(100e-9)

    def test_picofarad(self):
        assert parse_capacitance("22pF") == pytest.approx(22e-12)

    def test_no_match_returns_inf(self):
        assert parse_capacitance("no cap") == float("inf")
