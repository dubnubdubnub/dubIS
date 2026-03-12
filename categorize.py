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
CATEGORY_RULES: list[dict[str, Any]] = [
    # Connectors
    {"category": "Connectors", "desc": [
        "connector", "header", "receptacle", "banana", "xt60", "xt30",
        "ipex", "usb-c", "usb type-c", "crimp", "housing",
        "nv-2a", "nv-4a", "nv-2y", "nv-4y", "df40",
    ]},
    {"category": "Connectors", "mpn": [
        "xt60", "xt30", "sm04b", "sm05b", "sm06b",
        "svh-21t", "nv-", "df40", "bwipx", "xy-sh", "type-c",
    ]},
    # Switches (mechanical/tactile — not power ICs or switching regulators)
    {"category": "Switches", "desc": ["switch", "tactile"],
     "exclude_desc": ["switching regulator", "pwr switch", "load switch",
                      "power switch"]},
    # LEDs
    {"category": "LEDs", "desc": ["led", "emitter", "emit"]},
    # Passives
    {"category": "Passives - Inductors", "desc": ["inductor"]},
    {"category": "Passives - Resistors", "desc": ["resistor"]},
    {"category": "Passives - Resistors", "desc": ["\u03c9", "\u03a9", "\u2126", "ohm"]},
    {"category": "Passives - Resistors", "mfr": ["uni-royal"]},
    {"category": "Passives - Resistors", "mfr": ["ta-i tech"], "desc": ["m\u03c9"]},
    {"category": "Passives - Capacitors", "desc": ["capacitor", "electrolytic", "cap cer"]},
    # Crystals
    {"category": "Crystals & Oscillators", "desc": ["crystal", "oscillator"]},
    # Diodes (not ESD)
    {"category": "Diodes", "desc": ["diode"], "exclude_desc": ["esd"]},
    {"category": "ICs - ESD Protection", "desc": ["esd"]},
    # Discrete
    {"category": "Discrete Semiconductors", "desc": ["transistor", "bjt", "mosfet"]},
    # Power (includes load/power switch ICs)
    {"category": "ICs - Power / Voltage Regulators", "desc": [
        "voltage regulator", "buck", "ldo", "linear voltage", "switching regulator",
        "pwr switch", "load switch", "power switch",
    ]},
    # References
    {"category": "ICs - Voltage References", "desc": ["voltage reference"]},
    {"category": "ICs - Voltage References", "mpn": ["ref30"]},
    # Sensors
    {"category": "ICs - Sensors", "desc": ["current sensor"]},
    # Amplifiers
    {"category": "ICs - Amplifiers", "desc": ["amplifier", "csa"]},
    # Motor Drivers
    {"category": "ICs - Motor Drivers", "desc": ["motor", "mtr drvr", "half-bridge", "three-phase"]},
    {"category": "ICs - Motor Drivers", "mpn": ["drv8", "l6226"]},
    # Interface
    {"category": "ICs - Interface", "desc": ["transceiver", "driver"]},
    # Sensors (position / angle)
    {"category": "ICs - Sensors", "desc": ["position", "angle"]},
    {"category": "ICs - Sensors", "mpn": ["mt6835"]},
    # MCU
    {"category": "ICs - Microcontrollers", "desc": ["microcontroller", "mcu"]},
    # Mechanical
    {"category": "Mechanical & Hardware", "desc": ["spacer", "standoff", "battery holder"]},
]

SUBCATEGORY_RULES: dict[str, list[dict[str, Any]]] = {
    "Connectors": [
        {"subcategory": "High Speed", "desc": [
            "usb-c", "usb type-c", "board to board", "ipex", "hdmi", "dvi",
        ], "mpn": ["df40", "bm24"]},
        {"subcategory": "Through Hole", "desc": [
            "through hole", "banana", "crimp", "housing",
        ]},
        {"subcategory": "SMD", "desc": ["surface mount", "header"]},
    ],
    "Passives - Capacitors": [
        {"subcategory": "MLCC", "desc": ["mlcc", "cap cer"]},
        {"subcategory": "Aluminum Polymer", "desc": ["aluminum", "polymer", "electrolytic"]},
        {"subcategory": "Tantalum", "desc": ["tantalum"]},
    ],
    "Discrete Semiconductors": [
        {"subcategory": "MOSFETs", "desc": ["mosfet"]},
    ],
    "ICs - Power / Voltage Regulators": [
        {"subcategory": "Load Switches", "desc": ["load switch", "pwr switch", "power switch"]},
        {"subcategory": "Switchers", "desc": ["buck", "boost", "switching regulator"]},
        {"subcategory": "LDOs", "desc": ["ldo", "linear voltage"]},
    ],
}


def categorize(row: dict[str, str]) -> str:
    desc = (row.get("Description") or "").lower()
    mpn = (row.get("Manufacture Part Number") or "").lower()
    mfr = (row.get("Manufacturer") or "").lower()

    parent = "Other"
    for rule in CATEGORY_RULES:
        if "exclude_desc" in rule and any(kw in desc for kw in rule["exclude_desc"]):
            continue
        matched = True
        has_condition = False
        for field, text in [("desc", desc), ("mpn", mpn), ("mfr", mfr)]:
            if field in rule:
                has_condition = True
                if not any(kw in text for kw in rule[field]):
                    matched = False
                    break
        if has_condition and matched:
            parent = rule["category"]
            break

    # Check subcategory rules
    sub_rules = SUBCATEGORY_RULES.get(parent)
    if sub_rules:
        for sr in sub_rules:
            if "desc" in sr and any(kw in desc for kw in sr["desc"]):
                return f"{parent} > {sr['subcategory']}"
            if "mpn" in sr and any(kw in mpn for kw in sr["mpn"]):
                return f"{parent} > {sr['subcategory']}"

    return parent
