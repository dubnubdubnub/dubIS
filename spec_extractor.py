"""Extract structured specs from part descriptions and match against generic part specs."""

from __future__ import annotations

import re

from categorize import parse_capacitance, parse_inductance, parse_resistance


def extract_spec(description: str = "", package: str = "") -> dict:
    """Extract structured spec from a part's description and package.

    Returns dict with: type, value (float), value_display (str), package (str),
    and optional: voltage, tolerance, dielectric.
    """
    desc = description.lower()
    spec: dict = {"type": "other", "package": (package or "").strip()}

    # Determine component type
    if any(kw in desc for kw in ("capacitor", "cap ", "mlcc", "electrolytic", "tantalum")):
        spec["type"] = "capacitor"
    elif any(kw in desc for kw in ("resistor", "\u03c9", "\u03a9", "\u2126", "ohm")):
        spec["type"] = "resistor"
    elif "inductor" in desc:
        spec["type"] = "inductor"

    # Extract value
    if spec["type"] == "capacitor":
        val = parse_capacitance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "F")
    elif spec["type"] == "resistor":
        val = parse_resistance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "\u03a9")
    elif spec["type"] == "inductor":
        val = parse_inductance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "H")

    # Extract voltage (e.g., "16V", "25V")
    m = re.search(r"(\d+\.?\d*)\s*V\b", description)
    if m:
        spec["voltage"] = float(m.group(1))

    # Extract tolerance (e.g., "\u00b11%", "5%", "10%")
    m = re.search(r"[\u00b1]?(\d+\.?\d*)%", description)
    if m:
        spec["tolerance"] = m.group(1) + "%"

    # Extract dielectric (C0G/NP0, X5R, X7R, Y5V)
    m = re.search(r"\b(C0G|NP0|X[457][RSPTUVW]|Y5V)\b", description, re.IGNORECASE)
    if m:
        spec["dielectric"] = m.group(1).upper()

    return spec


def _format_value(val: float, unit: str) -> str:
    """Format a numeric value with SI prefix for display."""
    if val == 0:
        return f"0{unit}"
    prefixes = [
        (1e-12, "p"), (1e-9, "n"), (1e-6, "\u00b5"), (1e-3, "m"),
        (1, ""), (1e3, "k"), (1e6, "M"),
    ]
    for scale, prefix in reversed(prefixes):
        if abs(val) >= scale:
            display = val / scale
            if display == int(display):
                return f"{int(display)}{prefix}{unit}"
            return f"{display:g}{prefix}{unit}"
    return f"{val:g}{unit}"


def spec_matches(
    part_spec: dict,
    generic_spec: dict,
    strictness: dict,
) -> bool:
    """Check if a real part's spec matches a generic part's spec + strictness.

    Args:
        part_spec: extracted spec from a real part (from extract_spec)
        generic_spec: the generic part's spec_json (parsed)
        strictness: the generic part's strictness_json (parsed)
    """
    required = strictness.get("required", [])
    for field in required:
        if field == "value":
            # Compare parsed values with tolerance
            generic_val = _parse_spec_value(generic_spec.get("value", ""))
            part_val = part_spec.get("value")
            if generic_val is None or part_val is None:
                return False
            if generic_val == 0 and part_val == 0:
                continue
            if generic_val == 0 or part_val == 0:
                return False
            if abs(generic_val - part_val) / max(abs(generic_val), abs(part_val)) > 0.001:
                return False
        elif field == "package":
            gp = (generic_spec.get("package") or "").upper()
            pp = (part_spec.get("package") or "").upper()
            if gp and pp and gp != pp:
                return False
        elif field == "voltage_min":
            min_v = generic_spec.get("voltage_min", 0)
            part_v = part_spec.get("voltage")
            if part_v is None or part_v < min_v:
                return False
        elif field == "tolerance":
            gt = generic_spec.get("tolerance", "")
            pt = part_spec.get("tolerance", "")
            if gt and pt and gt != pt:
                return False
        elif field == "dielectric":
            gd = (generic_spec.get("dielectric") or "").upper()
            pd = (part_spec.get("dielectric") or "").upper()
            if gd and pd and gd != pd:
                return False
    return True


def _parse_spec_value(value_str: str) -> float | None:
    """Parse a value string like '100nF', '4.7k\u03a9', '10\u00b5H' to float."""
    if not value_str:
        return None
    # Try as capacitance
    val = parse_capacitance(value_str + " F" if "F" not in value_str else value_str)
    if val != float("inf"):
        return val
    # Try as resistance
    val = parse_resistance(value_str + " \u03a9" if "\u03a9" not in value_str and "ohm" not in value_str.lower() else value_str)
    if val != float("inf"):
        return val
    # Try as inductance
    val = parse_inductance(value_str + " H" if "H" not in value_str else value_str)
    if val != float("inf"):
        return val
    # Try as plain float
    try:
        return float(value_str)
    except ValueError:
        return None


def generate_generic_id(part_type: str, spec: dict) -> str:
    """Generate a stable, human-readable generic part ID from type + spec."""
    prefix = {"capacitor": "cap", "resistor": "res", "inductor": "ind"}.get(part_type, part_type[:3])
    value = (spec.get("value") or "").lower()
    # Normalize unicode
    value = value.replace("\u00b5", "u").replace("\u03a9", "ohm").replace("\u03c9", "ohm").replace("\u2126", "ohm")
    value = re.sub(r"[^a-z0-9._]", "", value)
    package = (spec.get("package") or "").lower()
    package = re.sub(r"[^a-z0-9]", "", package)
    parts = [prefix]
    if value:
        parts.append(value)
    if package:
        parts.append(package)
    return "_".join(parts)
