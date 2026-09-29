"""Categorize inventory parts by description, MPN, and manufacturer."""

from __future__ import annotations

import re
from typing import Any


def parse_resistance(desc: str) -> float:
    m = re.search(r"(\d+\.?\d*)\s*(m|k|M)?\s*[\u03a9\u03c9\u2126]", desc)
    if not m:
        return float("inf")
    value = float(m.group(1))
    prefix = m.group(2) or ""
    return value * {"m": 1e-3, "": 1, "k": 1e3, "M": 1e6}[prefix]


def parse_capacitance(desc: str) -> float:
    m = re.search(r"(\d+\.?\d*)\s*(p|n|u|\u00b5|m)?\s*F\b", desc)
    if not m:
        return float("inf")
    value = float(m.group(1))
    prefix = m.group(2) or ""
    return value * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]


def parse_inductance(desc: str) -> float:
    m = re.search(r"(\d+\.?\d*)\s*(n|u|\u00b5|m)?\s*H\b", desc)
    if not m:
        return float("inf")
    value = float(m.group(1))
    prefix = m.group(2) or ""
    return value * {"n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]


# Each rule: first match wins.  Within a rule, keywords in the same field
# are OR'd; different fields (desc + mfr) are AND'd.  exclude_desc vetoes.
# desc_prefix matches only at the start of the description, for DigiKey's
# terse category prefixes ("RES ", "TRIMMER ") that would be noise anywhere
# else in the string.
#
# desc / exclude_desc keywords match at the START of a word, not anywhere:
# as bare substrings "led" matched "contro-lled" (every VCXO filed as an LED)
# and "res" would match "featu-res".  A keyword carrying a unit symbol
# ("m\u03c9") still matches anywhere, since "100m\u03c9" has no word break.
# MPN keywords are part-number prefixes and match the same way ("cl10" is a
# Samsung MLCC; as a substring it also claimed Torex's XCL105 DC-DC module).
# Manufacturer keywords stay plain substrings.
#
# Order carries meaning: a part's description names what it IS alongside a
# list of what it has ("... ESD protection SOIC-8 Operational Amplifier"), so
# the specific identities come before the generic words a feature list uses.
# tests/python/test_categorize_corpus.py scores these rules against real
# LCSC and DigiKey catalogue descriptions; run it after any change here.
CATEGORY_RULES: list[dict[str, Any]] = [
    # Development boards / kits / programmers (dev tools, not components).
    # First: a board lists the connectors and chips it carries ("USB Type-C
    # connectivity"), so any later rule would claim it for a part on it.
    {"category": "Development Boards, Kits, Programmers", "desc": [
        "programmer", "debugger", "debug probe", "in-circuit",
        "evaluation board", "eval board", "development board", "dev board",
        "development kit", "starter kit", "discovery kit", "nucleo",
    ]},
    {"category": "Development Boards, Kits, Programmers", "mpn": [
        "stlink", "st-link", "j-link", "jlink",
    ]},
    # Connectors
    {"category": "Connectors", "desc": [
        "connector", "header", "receptacle", "banana", "xt60", "xt30",
        "ipex", "usb-c", "usb type-c", "crimp", "housing",
        "nv-2a", "nv-4a", "nv-2y", "nv-4y", "df40",
        "terminal block", "d-sub", "edgeboard", "cold-pressed terminal",
        "cold press terminal", "sc terminal", "ic socket", "transistor socket",
    ], "exclude_desc": ["port protect"]},
    # DigiKey's own abbreviations lead its short descriptions.
    {"category": "Connectors", "desc_prefix": ["conn "],
     "exclude_desc": ["port protect"]},
    {"category": "Connectors", "mpn": [
        "xt60", "xt30", "sm04b", "sm05b", "sm06b",
        "svh-21t", "nv-", "df40", "bwipx", "xy-sh", "type-c",
    ]},
    # Optocouplers / isolators -- ahead of Discrete: an optocoupler's
    # description names its "Transistor Output".
    {"category": "ICs - Interface", "desc": [
        "optocoupler", "optoisolator", "photocoupler", "digital isolator",
        "i2c isolator",
    ]},
    # ...and photointerrupters name their "Transistor Output" too.
    {"category": "ICs - Sensors", "desc": ["photointerrupter", "optical interrupter"]},
    # Analog switches / muxes (DigiKey "IC SW ..." / "IC SWITCH ...").  Ahead
    # of Switches and the passives: the on-resistance ("8.4OHM") would
    # otherwise read as a resistor value.
    {"category": "ICs - Interface", "desc": [
        "analog switch", "multiplexer", "bilateral switch",
    ]},
    {"category": "ICs - Interface", "desc_prefix": ["ic sw", "ic mux"]},
    {"category": "ICs - Interface", "mpn": ["tmux"]},
    # Switch-shaped power ICs, ahead of Switches.
    {"category": "ICs - Power / Voltage Regulators", "desc": [
        "pwr switch", "load switch", "power switch", "distribution switch",
    ]},
    # Switches (mechanical/tactile).  "switch" also starts "switching" and
    # "switched", and hall/proximity/RF parts call themselves switches.
    {"category": "Switches", "desc": ["switch", "tactile", "pushbutton", "push button"],
     "exclude_desc": ["switching", "switched", "switchable", "hall", "proximity",
                      "rf switch", "sensor", "amplifier"]},
    # LED (and laser) drivers are power ICs, not LEDs.
    {"category": "ICs - Power / Voltage Regulators", "desc": [
        "led driver", "lighting driver", "laser driver",
    ]},
    # LEDs
    {"category": "LEDs", "desc": ["led", "emitter", "emit", "light source"],
     "exclude_desc": ["driver", "non-led", "led protection", "isolation column"]},
    {"category": "LEDs", "mpn": ["ws2812", "sk6812", "apa102"]},
    # Passives
    {"category": "Passives - Inductors", "desc": [
        "inductor", "ferrite bead", "choke", "common mode filter",
    ]},
    # Resistors are recognised by what they say they are, never by an ohm
    # figure alone: switches quote on-resistance, FETs Rds(on), ferrites
    # impedance and chokes DCR, all in ohms.
    {"category": "Passives - Resistors", "desc": [
        "resistor", "potentiometer", "trimpot", "tube resistance",
    ], "exclude_desc": ["amplifier"]},  # "External resistor gain"
    {"category": "Passives - Resistors", "desc_prefix": ["res ", "trimmer "]},
    {"category": "Passives - Resistors", "mfr": ["uni-royal"]},
    {"category": "Passives - Resistors", "mfr": ["ta-i tech"], "desc": ["m\u03c9"]},
    {"category": "Passives - Capacitors", "desc": ["capacitor", "electrolytic", "cap cer"],
     "exclude_desc": ["switched capacitor"]},
    # Not bare "cap ": DigiKey also sells "CAP NUT"s and "CAP WITH LANYARD".
    {"category": "Passives - Capacitors", "desc_prefix": [
        "cap alum", "cap tant", "cap film", "cap mica", "cap cer", "cap poly",
        "cap hybrid", "cap niob", "cap array", "cap trimmer", "cap feedthru",
    ]},
    {"category": "Passives - Capacitors", "mpn": ["grm", "cl10", "cl21", "cl31"]},
    # Crystals
    {"category": "Crystals & Oscillators", "desc": [
        "crystal", "oscillator", "resonator", "vcxo", "tcxo", "ocxo",
    ]},
    # ESD / TVS, ahead of Diodes.  Not bare "esd": amplifiers and
    # transceivers list "ESD protection" among their features.
    {"category": "ICs - ESD Protection", "desc": [
        "tvs", "esd and surge", "esd suppressor", "esd protection diode",
        "esd diode", "ipp", "surge stopper", "surge suppress", "led protection",
    ]},
    # Gate and bridge drivers name the MOSFET/IGBT they drive, so ahead of
    # Discrete (and of Interface, which "driver" alone would reach).
    {"category": "ICs - Motor Drivers", "desc": [
        "gate driver", "half bridg", "half-bridg", "full bridg", "full-bridg",
        "h-bridg", "h bridg",
    ]},
    # Discrete, ahead of Diodes: thyristor modules list their "2 Diodes".
    {"category": "Discrete Semiconductors", "desc": [
        "transistor", "bjt", "mosfet", "thyristor", "igbt", "jfet", "darlington",
    ]},
    {"category": "Discrete Semiconductors", "mpn": ["ao3"]},
    {"category": "Diodes", "desc": ["diode", "rectifier"], "exclude_desc": ["ideal diode"]},
    # Power (includes load/power switch ICs)
    {"category": "ICs - Power / Voltage Regulators", "desc_prefix": ["ic reg"]},
    {"category": "ICs - Power / Voltage Regulators", "desc": [
        "voltage regulator", "buck", "boost", "ldo", "linear voltage", "switching regulator",
        "ideal diode", "dc-dc", "ac-dc", "battery management", "charger",
        "poe", "supervisor", "hot swap",
    ]},
    # References
    {"category": "ICs - Voltage References", "desc": ["voltage reference"]},
    {"category": "ICs - Voltage References", "mpn": ["ref30"]},
    # Sensors
    {"category": "ICs - Sensors", "desc": ["current sensor"]},
    # Amplifiers
    {"category": "ICs - Amplifiers", "desc": ["amplifier", "csa", "comparator"],
     "exclude_desc": ["digital comparator"]},
    # Motor Drivers
    {"category": "ICs - Motor Drivers", "desc": [
        "motor", "mtr drvr", "mtr drv", "mtr driver", "mtrdrv", "three-phase", "stepper",
    ], "exclude_desc": ["vibration"]},
    {"category": "ICs - Motor Drivers", "mpn": ["drv8", "l6226"]},
    # Interface
    {"category": "ICs - Interface", "desc": [
        "transceiver", "driver", "serializer", "deserializer", "level shifter",
        "translator", "i/o expander", "interface - specialized",
    ]},
    {"category": "ICs - Interface", "mpn": ["pi3ch"]},
    # MCU
    {"category": "ICs - Microcontrollers", "desc": [
        "microcontroller", "mcu", "digital signal processor", "fpga",
    ], "exclude_desc": ["for fpga"]},
    # Sensors.  Not bare "angle" or "position": "Right Angle" is how every
    # other connector describes its mounting.  "sensorless" is a motor
    # driver feature, and DACs/touch controllers list a "built-in temperature
    # sensor".
    {"category": "ICs - Sensors", "desc": [
        "sensor", "thermistor", "accelerometer", "gyroscope",
        "magnetoresistive", "position measuring", "angle measuring",
        "humidity", "moisture", "hall switch", "hall effect",
    ], "exclude_desc": ["sensorless", "built-in temperature sensor"]},
    {"category": "ICs - Sensors", "mpn": ["mt6835", "tmr2615"]},
    # USB
    {"category": "ICs - USB", "desc": ["port ctlr usb", "usb hub", "usb converter"]},
    {"category": "ICs - USB", "mpn": ["usb57", "husb238", "utc2000", "tcpp"]},
    # Mechanical
    {"category": "Mechanical & Hardware", "desc": [
        "spacer", "standoff", "battery holder", "nut", "washer", "rivet", "stud",
    ]},
]

SUBCATEGORY_RULES: dict[str, list[dict[str, Any]]] = {
    "Passives - Resistors": [
        {"subcategory": "Variable / Trimmers", "desc": [
            "trimmer", "potentiometer", "trimpot", "variable resistor",
        ]},
        {"subcategory": "Chip Resistors", "desc": [
            "resistor", "\u03c9", "\u03a9", "\u2126", "ohm",
        ]},
    ],
    "Connectors": [
        {"subcategory": "High Speed", "desc": [
            "usb-c", "usb type-c", "type-c", "board to board", "ipex", "hdmi", "dvi",
        ], "mpn": ["df40", "bm24"]},
        {"subcategory": "Through Hole", "desc": [
            "through hole", "banana", "crimp", "housing",
        ]},
        {"subcategory": "SMD", "desc": ["surface mount", "header"]},
    ],
    "Passives - Capacitors": [
        {"subcategory": "MLCC", "desc": ["mlcc", "cap cer", "ceramic"]},
        {"subcategory": "Aluminum Polymer", "desc": [
            "aluminum", "polymer", "electrolytic", "cap alum",
        ]},
        {"subcategory": "Tantalum", "desc": ["tantalum", "cap tant"]},
    ],
    "Discrete Semiconductors": [
        {"subcategory": "MOSFETs", "desc": ["mosfet"], "mpn": ["ao3"]},
    ],
    "ICs - Power / Voltage Regulators": [
        {"subcategory": "Load Switches", "desc": ["load switch", "pwr switch", "power switch"]},
        {"subcategory": "Switchers", "desc": ["buck", "boost", "switching regulator"]},
        {"subcategory": "LDOs", "desc": ["ldo", "linear voltage", "reg linear"]},
    ],
}


_PATTERNS: dict[tuple[str, ...], re.Pattern[str]] = {}


def _has(text: str, keywords: list[str]) -> bool:
    """True if any keyword starts a word in text (or appears anywhere, for a
    keyword carrying a non-ASCII unit symbol)."""
    key = tuple(keywords)
    pat = _PATTERNS.get(key)
    if pat is None:
        pat = re.compile("|".join(
            ("(?<![a-z0-9])" if kw.isascii() and kw[0].isalnum() else "") + re.escape(kw)
            for kw in keywords
        ))
        _PATTERNS[key] = pat
    return pat.search(text) is not None


def categorize(row: dict[str, str]) -> str:
    desc = (row.get("Description") or "").lower()
    mpn = (row.get("Manufacture Part Number") or "").lower()
    mfr = (row.get("Manufacturer") or "").lower()

    parent = "Other"
    for rule in CATEGORY_RULES:
        if "exclude_desc" in rule and _has(desc, rule["exclude_desc"]):
            continue
        matched = True
        has_condition = False
        for field, text in [("desc", desc), ("desc_prefix", desc), ("mpn", mpn), ("mfr", mfr)]:
            if field in rule:
                has_condition = True
                if field in ("desc", "mpn"):
                    hit = _has(text, rule[field])
                elif field == "desc_prefix":
                    hit = text.startswith(tuple(rule[field]))
                else:
                    hit = any(kw in text for kw in rule[field])
                if not hit:
                    matched = False
                    break
        if has_condition and matched:
            parent = rule["category"]
            break

    # Check subcategory rules
    sub_rules = SUBCATEGORY_RULES.get(parent)
    if sub_rules:
        for sr in sub_rules:
            if "desc" in sr and _has(desc, sr["desc"]):
                return f"{parent} > {sr['subcategory']}"
            if "mpn" in sr and _has(mpn, sr["mpn"]):
                return f"{parent} > {sr['subcategory']}"

    return parent
