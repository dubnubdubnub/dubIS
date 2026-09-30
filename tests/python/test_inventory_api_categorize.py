"""Tests for categorize.py -- examples and spec parsing.

Every description here is a real one, copied verbatim from a distributor
catalogue or from the inventory it came from; none is written to fit a rule.
The broad check -- thousands of real parts scored against their distributor's
own category -- is tests/python/test_categorize_corpus.py.  These are the
cases worth naming: each rule, and each trap a rule once fell into.
"""

import pytest

from categorize import categorize, parse_capacitance, parse_resistance

REAL_PARTS = [
    # LCSC C7305761
    pytest.param("MR10X1801FTL", "Walsin Tech Corp",
                 "1.8kΩ ±1% 1210 Chip Resistor - Surface Mount ROHS",
                 "Passives - Resistors > Chip Resistors", id="resistor_chip_lcsc"),
    # live inventory (bought from DigiKey)
    pytest.param("", "",
                 "RES SMD 10K OHM 1% 1/10W 0402",
                 "Passives - Resistors > Chip Resistors", id="resistor_chip_digikey_res_prefix"),
    # fixture purchase_ledger.csv (DigiKey)
    pytest.param("3006P-1-100LF", "Bourns Inc.",
                 "TRIMMER 10 OHM 0.75W PC PIN SIDE",
                 "Passives - Resistors > Variable / Trimmers", id="resistor_trimmer_digikey"),
    # LCSC C6674958
    pytest.param("3362W-1-201", "BOURNS",
                 "1 200Ω 500mW ±10% ±100ppm/℃ 插件 Potentiometers, Variable Resistors",
                 "Passives - Resistors > Variable / Trimmers", id="resistor_potentiometer"),
    # live inventory, description blanked (as manufacturer-direct rows arrive)
    pytest.param("0402WGF1002TCE", "UNI-ROYAL",
                 "",
                 "Passives - Resistors", id="resistor_by_manufacturer_blank_description"),
    # fixture inventory.csv C393094
    pytest.param("RLM12FTCMR020", "TA-I Tech",
                 "20mΩ ±1% 1W",
                 "Passives - Resistors > Chip Resistors", id="resistor_ta_i_milliohm_shunt"),
    # LCSC C7226974
    pytest.param("MKT1820447404", "Vishay Intertech",
                 "-55℃~+125℃ 400V 470nF Metallized Polyester ±5% - Film Capacitors ROHS",
                 "Passives - Capacitors", id="capacitor_film_stays_at_parent"),
    # LCSC C7386379
    pytest.param("VJ0805Y822MXBAC", "Vishay Intertech",
                 "100V 8.2nF X7R ±20% 0805 Multilayer Ceramic Capacitors MLCC - SMD/SMT "
                 "ROHS",
                 "Passives - Capacitors > MLCC", id="capacitor_mlcc_lcsc"),
    # live inventory (bought from DigiKey)
    pytest.param("GRM155R61C224KA12D", "",
                 "CAP CER 0.22UF 16V X5R 0402",
                 "Passives - Capacitors > MLCC", id="capacitor_mlcc_digikey_cap_cer"),
    # LCSC C7469982
    pytest.param("FVH010ADA221M0654", "FOSAN",
                 "-55℃~+105℃ 10V 220uF 5.4mm 6.3mm ±20% 6.3x5.4 Aluminum Electrolytic "
                 "Capacitors - SMD ROHS",
                 "Passives - Capacitors > Aluminum Polymer", id="capacitor_aluminum_lcsc"),
    # DigiKey P124837CT-ND
    pytest.param("EEE-FTV151XAV", "Panasonic Industry",
                 "CAP ALUM 150UF 20% 35V SMD",
                 "Passives - Capacitors > Aluminum Polymer", id="capacitor_aluminum_digikey"),
    # LCSC C7062428
    pytest.param("TAP336K006SCS", "Kyocera AVX",
                 "-55℃~+125℃ 33uF 3Ω 6.3V ±10% - Tantalum Capacitors ROHS",
                 "Passives - Capacitors > Tantalum", id="capacitor_tantalum_quoting_esr_ohms"),
    # LCSC C17573699
    pytest.param("ATP203-TL-H", "SANYO DENKI",
                 "13.5mΩ@4.5V 2.6V 2.75nF 265pF 30V 44nC@10V 450pF 50W 75A N-Channel - "
                 "MOSFETs ROHS",
                 "Discrete Semiconductors > MOSFETs", id="mosfet"),
    # LCSC C7085361
    pytest.param("BC846CW RFG", "Taiwan Semiconductor",
                 "-55℃~+150℃ 100MHz 100mA 100nA 200mW 420 600mV 65V NPN SOT-323 Bipolar "
                 "(BJT) ROHS",
                 "Discrete Semiconductors", id="bjt"),
    # LCSC C7450547
    pytest.param("SS56C", "",
                 "5A 60V 700mV@5A SMC Schottky Diodes ROHS",
                 "Diodes", id="schottky_diode"),
    # LCSC C17386447
    pytest.param("CDSV4148-G", "",
                 "1.25V@150mA 150mA 1uA@75V 4ns SOD-323 Switching Diodes ROHS",
                 "Diodes", id="switching_diode_is_not_a_switch"),
    # live inventory C19077518
    pytest.param("SMF30A", "",
                 "48.4VC Clamp 4.1A Ipp TVS DIODE SOD-123FL",
                 "ICs - ESD Protection", id="tvs_diode_is_esd_protection"),
    # fixture inventory.csv C20615829
    pytest.param("SRV05-4A", "R+O",
                 "15VC Clamp 4A@8/20us Ipp ESD DIODE SOT-23-6L",
                 "ICs - ESD Protection", id="esd_diode_is_esd_protection"),
    # live inventory
    pytest.param("TPS7A4700RGWR", "TI",
                 "1.4V~20.5V Positive Adjustable VQFN-20-EP(5x5) Voltage Regulators - "
                 "Linear, Low Drop Out (LDO) Regulators RoHS",
                 "ICs - Power / Voltage Regulators > LDOs", id="ldo"),
    # live inventory
    pytest.param("TPS564242DRLR", "TI",
                 "1.2MHz Buck 4A Adjustable 600mV~7V 1 SOT-563 Voltage Regulators - DC "
                 "DC Switching Regulators ROHS",
                 "ICs - Power / Voltage Regulators > Switchers", id="buck_switching_regulator_is_not_a_switch"),
    # live inventory (bought from DigiKey)
    pytest.param("MP2229GQ-Z", "",
                 "IC REG BUCK ADJ 6A 14QFN",
                 "ICs - Power / Voltage Regulators > Switchers", id="buck_digikey_ic_reg"),
    # LCSC C6671398
    pytest.param("S-8353H33UA-IWST2U", "ABLIC",
                 "-40℃~+85℃@(TA) 1 250kHz 3.3V 300mA 900mV~10V Boost Boost Built-in No "
                 "SOT-89-3 DC-DC Converters ROHS",
                 "ICs - Power / Voltage Regulators > Switchers", id="boost"),
    # LCSC C17514180
    pytest.param("DML3012LDC-7A", "Diodes Incorporated",
                 "-40℃~+85℃ 1 4.8mΩ 500mV~20V Active High Load Switch VDFN3030-12 Power "
                 "Distribution Switches ROHS",
                 "ICs - Power / Voltage Regulators > Load Switches", id="load_switch"),
    # live inventory C2158037
    pytest.param("AP22653W6-7", "",
                 "1 Active High 2.1A High Side Switch SOT-26 Power Distribution "
                 "Switches, Load Drivers RoHS",
                 "ICs - Power / Voltage Regulators", id="high_side_switch_is_not_a_switch"),
    # live inventory
    pytest.param("SM04B-SRSS-TB(LF)(SN)", "JST",
                 "4P 1x4P SH Tin -25℃~+85℃ White 1mm 50V 4 1A 1 Surface Mount, Right "
                 "Angle SMD,P=1mm,Surface Mount,Right Angle Headers, Male Pins ROHS",
                 "Connectors > SMD", id="connector_smd_header"),
    # LCSC C7463258
    pytest.param("TYPE-C-31-D-09", "Korean Hroparts Elec",
                 "-25℃~+85℃ 1 10,000 cycles 16P 5A 6.5mm Female Through Hole Type-C 插件 "
                 "USB Connectors ROHS",
                 "Connectors > High Speed", id="connector_usb_c"),
    # live inventory (bought from DigiKey)
    pytest.param("SFW15R-1STE1LF", "",
                 "CONN FFC FPC BOTTOM 15POS 1MM RA",
                 "Connectors", id="connector_digikey_conn_prefix"),
    # live inventory
    pytest.param("TCPP02-M18", "",
                 "USB TYPE-C PORT PROTECTION FOR S",
                 "ICs - USB", id="usb_c_port_protection_is_not_a_connector"),
    # live inventory
    pytest.param("STM32G474RBT3", "ST",
                 "ARM Cortex-M Series 170MHz 52 LQFP-64(10x10) Microcontrollers ROHS",
                 "ICs - Microcontrollers", id="mcu"),
    # live inventory (bought from DigiKey)
    pytest.param("STM32H7A3VIT6", "",
                 "IC MCU 32BIT 2MB FLASH 100LQFP",
                 "ICs - Microcontrollers", id="mcu_digikey"),
    # DigiKey DG411LDY-T1TR-ND
    pytest.param("DG411LDY-T1", "Vishay Siliconix",
                 "IC SWITCH SPST-NCX4 17OHM 16SOIC",
                 "ICs - Interface", id="analog_switch_digikey_ic_switch"),
    # LCSC C7021756 ("Voltage-Contro-lled" once read as LED)
    pytest.param("SVC53C3B07A2-125.000M", "Suntsu Electronics Inc",
                 "0℃~+70℃ 125MHz 25mA 3.3V CMOS ±25ppm SMD5032-6P Voltage-Controlled "
                 "Crystal Oscillators (VCXOs) ROHS",
                 "Crystals & Oscillators", id="vcxo_is_not_an_led"),
    # LCSC C17487427
    pytest.param("AD404-02E", "NVE",
                 "SOIC-8 Hall Switches ROHS",
                 "ICs - Sensors", id="hall_switch_is_a_sensor"),
    # LCSC C17209540 (Seeed XIAO nRF52840)
    pytest.param("102010448", "Seeed",
                 "Seeed Studio XIAO nRF52840 is an ultra-compact, low-power wireless "
                 "development board powered by the Nordic Semiconductor nRF52840 SoC. "
                 "It features a 64 MHz Arm Cortex-M4F processor with a floating-point "
                 "unit, 256 KB of RAM, 1 MB of on-chip Flash, and an additional 2 MB of "
                 "onboard Flash, providing sufficient processing and storage capacity "
                 "for wireless, sensing, and embedded applications.\nThe board supports "
                 "Bluetooth Low Energy, Bluetooth Mesh, NFC, and 2.4 GHz wireless "
                 "communication through an onboard antenna. It provides UART, I²C, SPI, "
                 "ADC, PWM, NFC, and SWD interfaces, together with USB Type-C "
                 "connectivity and onboard lithium-battery charging management.\nWith a "
                 "compact size of only 21 × 17.8 mm, single-sided component placement, "
                 "and castellated pads, XIAO nRF52840 is suitable for both rapid "
                 "prototyping and surface-mount integration into custom PCBs.",
                 "Development Boards, Kits, Programmers", id="dev_board_listing_its_usb_c_is_a_dev_board"),
    # ST's product title for STLINK-V3SET
    pytest.param("STLINK-V3SET", "STMicroelectronics",
                 "STLINK-V3 modular in-circuit debugger and programmer",
                 "Development Boards, Kits, Programmers", id="stlink_is_dev_tools"),
    # LCSC C7423113
    pytest.param("SK6812MINI-HS-RVA", "OPSCO Optoelectronics",
                 "-40℃~+85℃ 0.25mA 1.95mm 120° 160mcd~320mcd 18ns、22ns 2000V "
                 "240mcd~450mcd 3.5mm 3.7V~5.5V 3.7mm 465nm~475nm 4kHz 520nm~530nm "
                 "620nm~625nm 75ns、110ns 800Kbit/s 815mcd~1275mcd 82ns Built-in "
                 "power-on reset circuit Semi-transparent lens SMD3535-4P RGB "
                 "LEDs(Built-in IC) ROHS",
                 "LEDs", id="addressable_rgb_led"),
    # DigiKey 119-228BDVAAWFLMSD-ND
    pytest.param("228BDVAAWFLMSD", "CTS Electrocomponents",
                 "SWITCH TACTILE SPST-NO 0.05A 12V",
                 "Switches", id="tactile_switch_digikey"),
    # LCSC C18039838
    pytest.param("MPM12A05I12BF02", "NorComp",
                 "- New Arrivals ROHS",
                 "Other", id="uninformative_description_is_other"),
    # LCSC C6953916
    pytest.param("G6K-2F-RF-S DC4.5", "Omron Electronics",
                 "-40℃~+70℃ 1A 2 Form C: 2C (DPDT-CO) 3ms 3ms 4.5V 60V@DC、125V@AC - "
                 "Signal Relays ROHS",
                 "Other", id="relay_is_other"),
    # LCSC C17443318 (a balun: "50Ω:100Ω")
    pytest.param("2450BL14C0100001T", "",
                 "-40℃~+125℃ 1.2dB 1.5dB 180°@±10° 2.4GHz~2.5GHz 50Ω:100Ω 9.5dB 0603 "
                 "Balun ROHS",
                 "Other", id="ohm_figure_alone_is_not_a_resistor"),
]


@pytest.mark.parametrize(("mpn", "manufacturer", "description", "expected"), REAL_PARTS)
def test_real_part(mpn, manufacturer, description, expected):
    row = {"Description": description, "Manufacture Part Number": mpn, "Manufacturer": manufacturer}
    assert categorize(row) == expected


# Manufacturer-direct rows (MDT, JST, Murata reels bought outright) arrive
# with no description at all; the MPN is the only thing left to sort on.
@pytest.mark.parametrize(("mpn", "manufacturer", "expected"), [
    ("SM04B-GHS-TB", "JST", "Connectors"),
    ("DRV8353RSRGZR", "Texas Instruments", "ICs - Motor Drivers"),
    ("REF3033AIDBZR", "Texas Instruments", "ICs - Voltage References"),
    ("MT6835GT-STD", "MagnTek", "ICs - Sensors"),
    ("TMR2615F-AAC-1.500-500", "MultiDimension Technology Co., Ltd.", "ICs - Sensors"),
    ("WS2812B-V5/W", "Worldsemi", "LEDs"),
    ("GRM21BR61A476ME15L", "Murata", "Passives - Capacitors"),
    ("TCPP02-M18", "STMicroelectronics", "ICs - USB"),
    ("PI3CH3257ZTAEX", "Diodes Incorporated", "ICs - Interface"),
    ("STLINK-V3SET", "STMicroelectronics", "Development Boards, Kits, Programmers"),
])
def test_mpn_fallback_with_blank_description(mpn, manufacturer, expected):
    row = {"Description": "", "Manufacture Part Number": mpn, "Manufacturer": manufacturer}
    assert categorize(row) == expected


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
