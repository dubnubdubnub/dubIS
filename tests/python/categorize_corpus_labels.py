"""Distributor category -> the dubIS sections that count as a correct answer.

Labels for the real-part corpus in tests/fixtures/categorize-corpus/, which
test_categorize_corpus.py scores categorize.py against.  The ground truth is
the *distributor's* own category for each part; this module only translates
that taxonomy into dubIS's.

Each answer is a set, because the two taxonomies genuinely overlap (an NTC
thermistor is a sensor to one person and a resistor to another).  None means
no label: a distributor's catch-all bucket says nothing about what a part is.
"Other" in a set means dubIS has no better home for the part.
"""
from __future__ import annotations

R, C, L = "Passives - Resistors", "Passives - Capacitors", "Passives - Inductors"
CON, SW, LED, XTAL, D = "Connectors", "Switches", "LEDs", "Crystals & Oscillators", "Diodes"
Q, MCU, PWR = "Discrete Semiconductors", "ICs - Microcontrollers", "ICs - Power / Voltage Regulators"
REF, SEN, AMP, MOT = "ICs - Voltage References", "ICs - Sensors", "ICs - Amplifiers", "ICs - Motor Drivers"
USB, IF, ESD, MECH = "ICs - USB", "ICs - Interface", "ICs - ESD Protection", "Mechanical & Hardware"
DEV, OTH = "Development Boards, Kits, Programmers", "Other"

LCSC_CATEGORY: dict[str, set[str] | None] = {
    "Resistors": {R},
    "Capacitors": {C},
    "Inductors, Coils, Chokes": {L}, "Inductors/Coils/Transformers": {L},
    "Inductors & Chokes & Transformers": {L},
    "Filters": {L, OTH}, "Bead/Filter/EMI Optimization": {L, OTH},
    "Connectors": {CON}, "Terminal": {CON},
    "Wire/Cable/DataCable": {OTH, CON, MECH}, "Wires And Cables": {OTH, CON, MECH},
    "Wires and cables": {OTH, CON, MECH},
    "Switches": {SW}, "Key/Switch": {SW}, "switches": {SW}, "Pushbutton Switches & Relays": {SW},
    "Crystals, Oscillators, Resonators": {XTAL}, "Crystal Oscillator/Oscillator/Resonator": {XTAL},
    "Resonators/Oscillators": {XTAL},
    "Clock/Timing": {OTH, IF, XTAL},
    "Diodes": {D},
    "Circuit Protection": {OTH, ESD},
    "TVS/Fuse/Board Level Protection": {ESD, D, OTH},
    "Transistors/Thyristors": {Q}, "Triode/MOS Tube/Transistor": {Q},
    "Silicon Carbide (SiC) Devices": {Q}, "Gallium Nitride (GaN) Devices": {Q},
    "Optoelectronics": {LED}, "Photoelectric Devices": {LED, SEN, OTH},
    "Optocoupler/LED/Digital Tube/Photoelectric Device": {LED, IF, OTH},
    "Optocouplers & LEDs & Infrared": {LED, OTH},
    "Optoisolators": {IF, OTH}, "Optocoupler": {IF, OTH}, "Optocouplers/Photocouplers": {IF, OTH},
    "Signal Isolation Devices": {IF},
    "Power Management (PMIC)": {PWR}, "Power Management ICs": {PWR}, "Power Supply Chip": {PWR},
    "Power Modules": {PWR, OTH},
    "Amplifiers/Comparators": {AMP}, "Operational Amplifier/Comparator": {AMP},
    "Data Acquisition": {OTH, IF}, "ADC/DAC/Data Conversion": {OTH, IF},
    "Interface": {IF}, "Interface ICs": {IF}, "Communication Interface Chip/UART/485/232": {IF},
    "Logic": {OTH, IF}, "Logic ICs": {OTH, IF},
    "Memory": {OTH},
    "Embedded Processors & Controllers": {MCU}, "Single Chip Microcomputer/Microcontroller": {MCU},
    "Motor Driver ICs": {MOT},
    "LED Drivers": {PWR, OTH}, "Nixie Tube Driver/LED Driver": {PWR, IF, OTH},
    "Sensors": {SEN}, "Magnetic Sensors": {SEN},
    "Relays": {OTH},
    "RF And Wireless": {OTH, IF, AMP}, "RF & Radio": {OTH, IF, AMP},
    "Radio Frequency Chip/Antenna": {OTH, IF},
    "IoT/Communication Modules": {OTH, DEV, IF}, "Functional Modules": {OTH, DEV, IF},
    "Educational Kits": {DEV, OTH}, "Development Boards & Tools": {DEV},
    "Displays": {OTH, LED},
    "Audio Products / Vibration Motors": {OTH},
    "Industrial Control Electrical": {OTH},
    "Hardware/Fasteners/Sealing": {MECH}, "Hardware Fasteners": {MECH},
    "Hardwares / Sealings / Machinings": {MECH},
    "Consumables": {OTH, MECH},
    "Battery Products": {MECH, CON, OTH},
    # JLC's catch-alls: no label.
    "Other": None, "Others": None, "Global Sourcing Parts": None, "Test": None, "": None,
}

# JLC/LCSC subcategory overrides, matched on the subcategory name alone
# (they are unambiguous across JLC's duplicated top-level spellings).
LCSC_SUBCATEGORY: dict[str, set[str] | None] = {
    # resistors
    "NTC Thermistors": {SEN, R, OTH}, "PTC Thermistors": {SEN, R, OTH}, "Others": None,
    "Photoresistors": {SEN, R, OTH},
    # inductors / filters
    "Audio Transformers": {L, OTH}, "Current Sense Transformers": {L, OTH, SEN},
    "Current Transformers": {L, OTH, SEN}, "Power Transformer": {L, OTH}, "Power Transformers": {L, OTH},
    "Pulse Transformers": {L, OTH}, "Pulse Transformers(LAN)": {L, OTH}, "RJ45 Transformer": {L, OTH},
    "Accessories - Inductors/Transformers": {L, OTH, MECH}, "Wireless Charging Coils": {L, OTH},
    "Ferrite Beads": {L}, "Common Mode Filters": {L}, "Common Mode Chokes / Filters": {L},
    "Feed Through Capacitors": {C, OTH}, "Ceramic Filters": {OTH, XTAL}, "Crystal Filters": {OTH, XTAL},
    "Switched Capacitor Filters": {OTH, AMP},
    # connectors
    "Connector Accessories": {CON, MECH, OTH}, "FFC Cable (Flexible Flat Cable)": {CON, OTH},
    # switches
    "Rotary Encoders": {SW, SEN}, "Switch Accessories / Caps": {SW, MECH, OTH},
    "Switch Accessories Or Caps": {SW, MECH, OTH},
    # clocks
    "Real Time Clocks": {OTH, IF}, "Real-Time Clocks(RTC)": {OTH, IF}, "Time Delays": {OTH},
    "Timers / Counters": {OTH, IF, XTAL},  # JLC files programmable oscillators here
    # protection
    "ESD And Surge Protection (TVS/ESD)": {ESD, D},
    "Electrostatic And Surge Protection (TVS/ESD)": {ESD, D},
    "Fuseholders": {OTH, MECH, CON}, "Automotive Fuses": {OTH}, "Disposable Fuses": {OTH},
    "Resettable Fuses": {OTH, ESD}, "Thermal Fuses (TCO)": {OTH},
    # discretes
    "SiC Diodes": {D}, "Intelligent Power Modules (IPM)": {Q, MOT, PWR},
    # optoelectronics
    "Laser Diodes": {LED, D}, "Photodiodes": {D, SEN, OTH}, "Phototransistors": {SEN, Q, OTH},
    "Infrared Remote Receiver (IRM)": {SEN, OTH},
    "Photointerrupters - Slot Type - Transistor Output": {SEN, OTH},
    "Photointerrupters - Slot Type - Logic Output": {SEN, OTH},
    "Reflective Optical Interrupters": {SEN, OTH},
    "Optoelectronics Accessories": {OTH, MECH, LED}, "Photoelectric Accessories": {OTH, MECH, LED},
    "Fiber Optical Transceivers": {IF, OTH}, "IrDA Transceiver Modules": {IF, OTH},
    "Optocouplers - Phototransistor Output": {IF, OTH},
    "Solid State Relays - MOS Output (PhotoMOS)": {OTH, IF},
    "Solid State Relays (MOS Output)": {OTH, IF}, "Solid State Relays (Triac Output)": {OTH, IF},
    "Gate Drive Optocoupler": {IF, MOT, OTH},
    "Isolation Amplifiers": {AMP, IF}, "Isolated ADCs": {IF, OTH}, "Isolated ADCs With Power": {IF, OTH},
    "Isolated Comparators": {AMP, IF}, "Isolated USB ICs": {IF, USB},
    # power
    "Voltage Reference": {REF}, "Motor Driver ICs": {MOT},
    "Isolators - Gate Drivers": {MOT, IF, PWR}, "Gate Drive ICs": {MOT, PWR},
    "Supervisor And Reset ICs": {PWR, OTH}, "Monitors & Reset Circuits": {PWR, OTH},
    "Current Source / Constant Current Source": {PWR, OTH}, "Power Management - Specialized": {PWR, OTH},
    "Leakage Protection ICs": {PWR, OTH}, "Power Over Ethernet (PoE) Controllers": {PWR, IF},
    "Linear - Analog Multipliers, Dividers": {AMP, OTH},
    # data acquisition / interface
    "Digital Potentiometers": {OTH, R, IF}, "Analog Front End (AFE)": {OTH, IF, AMP},
    "Touch Screen Controllers": {IF, OTH},
    "USB Converters": {USB, IF}, "Audio Interface ICs": {IF, OTH},
    "Security Verification / Encryption ICs": {OTH, IF},
    "Translators, Level Shifters": {IF, OTH}, "Signal Switches, Multiplexers, Decoders": {IF, OTH},
    "Buffers, Drivers, Receivers, Transceivers": {IF, OTH},
    "Programmable Logic Device (CPLDs/FPGAs)": {MCU, OTH},
    "Relay / Coil Drivers": {MOT, IF, OTH}, "Gate Drivers": {MOT, PWR},
    "LCD Drivers": {IF, OTH}, "Digital Tube Drivers": {IF, OTH}, "Laser Drivers": {PWR, OTH},
    # sensors
    "Thermostat Switches": {SEN, SW, OTH}, "Sensor Modules": {SEN, DEV, OTH},
    "Reed Switches": {SEN, SW},
    # rf
    "Antennas": {OTH, CON}, "RF Cables": {OTH, CON}, "RF Amplifiers": {AMP, OTH},
    "Low Noise Amplifiers (LNA) - RF": {AMP, OTH}, "SAW Filters": {OTH, XTAL},
    # modules / kits
    "Motor Driver Boards, Modules": {DEV, MOT, OTH},
    "LED Segment Displays": {LED, OTH}, "LED Dot Matrix And Cluster": {LED, OTH},
    "LED Displays Modules": {LED, OTH},
    "Microphones": {OTH, SEN}, "MEMS Microphones": {OTH, SEN},
    # industrial control
    "Limit Switchs,Travel Switchs": {SW}, "Tact Switch": {SW}, "Tact Switchs,Keypad Switches": {SW},
    "Tactile Switch/Push Button Switch": {SW}, "Encoders": {SW, SEN},
    "Proximity Sensors,Proximity Switches": {SEN, SW, OTH}, "PhotoelectricSensor": {SEN, OTH},
    "Power Module/Power Supply": {PWR, OTH}, "Power Supply Module": {PWR, OTH},
    "Power Supply Module,Power Supplies": {PWR, OTH}, "Waterproof LED Driver Power Supply": {PWR, OTH},
    "Battery Connector": {CON}, "BatteryConnector": {CON}, "Terminal Strips": {CON},
    "Power Connector / Plug Connector": {CON}, "Antenna Spring": {CON, MECH, OTH},
    "Heat Sink / Cooling Fin": {MECH, OTH}, "Heat Sink/Heatsink": {MECH, OTH},
    "Global Sourcing Parts": None, "New Arrivals": None, "Comprehensive Electronic": None,
    "": None,
}


# DigiKey, keyed by its breadcrumb below the top level (the top level is
# redundant and leaf names alone repeat across branches).
DIGIKEY_PATH: dict[str, set[str] | None] = {
    "Aluminum Electrolytic Capacitors": {C},
    "Through Hole Resistors": {R},
    "Arrays, Signal Transformers": {L, OTH},
    "Inrush Current Limiters (ICL)": {OTH, SEN, R},
    "Surge Suppression ICs": {ESD, PWR, OTH},
    "Thermal Cutoffs (Thermal Fuses)": {OTH},
    # Protection thyristors: "THYRISTOR 58V 150A" is all the description
    # says, so reading it as a discrete thyristor is fair.
    "Transient Voltage Suppressors (TVS) / Thyristors": {ESD, D, Q, OTH},
    "Barrel Connectors / Barrel Connector Accessories": {CON, MECH, OTH},
    "Card Edge Connectors / Edgeboard Connectors": {CON},
    "Fiber Optic Connectors / Fiber Optic Connector Housings": {CON},
    "Modular/Ethernet Connectors / Modular/Ethernet Connector (RJ45, RJ11) Plugs": {CON},
    "Terminals / Quick Connects, Quick Disconnect Connectors": {CON},
    "Nuts": {MECH},
    "Clock/Timing / Clock Generators, PLLs, Frequency Synthesizers": {OTH, IF, XTAL},
    "Embedded / System On Chip (SoC)": {MCU},
    "Interface / Analog Switches, Multiplexers, Demultiplexers": {IF},
    "Linear / Comparators": {AMP},
    "Logic / Gates and Inverters": {OTH, IF},
    "Power Management (PMIC) / Full, Half-Bridge (H Bridge) Drivers": {MOT, PWR},
    "Power Management (PMIC) / Hot Swap Controllers": {PWR},
    "Power Management (PMIC) / Motor Drivers, Controllers": {MOT},
    "Power Management (PMIC) / Power Management - Specialized": {PWR, MOT, OTH},
    "Optocouplers, Optoisolators / Transistor, Photovoltaic Output Optoisolators": {IF, OTH},
    "Circuit Board Indicators, Arrays, Light Bars, Bar Graphs": {LED},
    "LED Indication - Discrete": {LED},
    "RFI and EMI - Shielding and Absorbing Materials": {OTH, MECH},
    "Signal Relays, Up to 2 Amps": {OTH},
    "Humidity, Moisture Sensors": {SEN},
    "Optical Sensors / Ambient Light, IR, UV Sensors": {SEN},
    "Solder Stencils, Templates": {OTH, MECH},
    "Pushbutton Switches": {SW},
    "Tactile Switches": {SW},
}


class Unmapped(KeyError):
    """A distributor category nobody has decided a dubIS home for yet."""


def allowed(distributor: str, category_path: list[str]) -> set[str] | None:
    """The dubIS sections a part in this distributor category may land in."""
    if distributor == "lcsc":
        top, sub = category_path
        if sub in LCSC_SUBCATEGORY:
            return LCSC_SUBCATEGORY[sub]
        if top in LCSC_CATEGORY:
            return LCSC_CATEGORY[top]
    elif distributor == "digikey":
        key = " / ".join(category_path[1:])
        if key in DIGIKEY_PATH:
            return DIGIKEY_PATH[key]
    raise Unmapped(f"{distributor}: {' / '.join(category_path)}")
