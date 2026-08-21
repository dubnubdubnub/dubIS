"""Parametric-attribute normalization: canonical names + value parsing.

Distributor clients (`lcsc_client.py`, `digikey_normalizer.py`,
`mouser_client.py`, `pololu_client.py`) all emit parametrics as free-text
``{"name": ..., "value": ...}`` pairs on `NormalizedProduct.attributes`.
Two things must happen before a predicate ("min VCCA <= 0.9 V",
"resolution >= 13 bit") can be evaluated against them:

1. the *name* must be comparable across distributors — LCSC's
   ``"Voltage - Supply"`` and DigiKey's ``"Voltage - Supply (Vcc/Vdd)"`` are
   the same parametric (`canonical_name`);
2. the *value* must yield a number in a known unit — LCSC bakes the unit into
   the string (``"3.3V"``, ``"0.9V~5.5V"``, ``"-40℃~+125℃"``)
   (`parse_value`).

Both are deliberately conservative:

* An attribute name with no table entry keeps its own (whitespace/case
  normalized) name — it is never force-fitted onto a canonical.
* A value that does not cleanly yield a magnitude stays `KIND_UNPARSED` with
  its raw string intact. **No number is ever invented**: a token is only read
  as a unit when it is a known unit (optionally SI-prefixed), or when it is a
  word separated from the number by whitespace (``"200 Years"``).

The raw string is always preserved by the caller (`domain/attributes.py`
stores `raw_value` next to the parsed fields), so every normalization here is
additive and reversible by inspection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Canonical attribute names ────────────────────────────────────────────────
#
# A pure data table: one line per alias. Keys are matched case- and
# whitespace-insensitively (see `normalize_name`), so a row is only needed when
# the *spelling* differs — "Operating temperature" already collapses onto
# "Operating Temperature" without an entry.
#
# Rule for adding a canonical: it must have at least two genuinely different
# spellings in the wild (usually one LCSC + one DigiKey). A single-spelling
# attribute needs no row — it keys on its own normalized name.

CANONICAL_NAME_ALIASES: dict[str, str] = {
    # temperature
    "operating temperature": "operating_temperature",
    "operating temperature range": "operating_temperature",
    "temperature range": "operating_temperature",
    "temperature coefficient": "temperature_coefficient",
    "temperature coefficient (tc)": "temperature_coefficient",
    # supply
    "voltage - supply": "supply_voltage",
    "voltage - supply (vcc/vdd)": "supply_voltage",
    "voltage - supply, single (v+)": "supply_voltage",
    "supply voltage": "supply_voltage",
    "operating voltage": "supply_voltage",
    "current - supply": "supply_current",
    "current - supply (max)": "supply_current",
    "supply current": "supply_current",
    "quiescent current": "quiescent_current",
    "quiescent current (iq)": "quiescent_current",
    "supply current (iq)": "quiescent_current",
    "current - quiescent (iq)": "quiescent_current",
    # passives
    "voltage rating": "voltage_rating",
    "voltage - rated": "voltage_rating",
    "voltage - rated (dc)": "voltage_rating",
    "power(watts)": "power_dissipation",
    "power (watts)": "power_dissipation",
    "power dissipation": "power_dissipation",
    "pd - power dissipation": "power_dissipation",
    "total power dissipation(pd)": "power_dissipation",
    "equivalent series resistance(esr)": "esr",
    "equivalent series resistance (esr)": "esr",
    "esr (equivalent series resistance)": "esr",
    "load capacitance": "load_capacitance",
    "load capacitance (cl)": "load_capacitance",
    # regulators / references
    "output voltage": "output_voltage",
    "voltage - output": "output_voltage",
    "voltage - output (min/fixed)": "output_voltage",
    "output current": "output_current",
    "current - output": "output_current",
    "current - output (max)": "output_current",
    # diodes / LEDs
    "voltage - forward(vf)": "forward_voltage",
    "voltage - forward(vf@if)": "forward_voltage",
    "voltage - forward (vf) (max) @ if": "forward_voltage",
    "forward current": "forward_current",
    "forward current(if)": "forward_current",
    "current - forward (if)": "forward_current",
    "reverse leakage current (ir)": "reverse_leakage_current",
    "current - reverse leakage @ vr": "reverse_leakage_current",
    "reverse voltage": "reverse_voltage",
    "voltage - dc reverse (vr) (max)": "reverse_voltage",
    "voltage - breakdown": "breakdown_voltage",
    "voltage - breakdown (min)": "breakdown_voltage",
    "zener voltage(nom)": "zener_voltage",
    "voltage - zener (nom) (vz)": "zener_voltage",
    "peak wavelength": "peak_wavelength",
    "wavelength - peak": "peak_wavelength",
    "luminous intensity": "luminous_intensity",
    "millicandela rating": "luminous_intensity",
    "viewing angle": "viewing_angle",
    "viewing angle (2θ 1/2)": "viewing_angle",
    # protection
    "clamping voltage": "clamping_voltage",
    "voltage - clamping (max) (vc)": "clamping_voltage",
    "peak pulse current (ipp)": "peak_pulse_current",
    "current - peak pulse (10/1000µs)": "peak_pulse_current",
    "reverse stand-off voltage (vrwm)": "standoff_voltage",
    "voltage - reverse standoff (typ)": "standoff_voltage",
    "hold current": "hold_current",
    "current - hold (ih) (max)": "hold_current",
    "trip current": "trip_current",
    "current - trip (it)": "trip_current",
    # transistors
    "vce saturation(vce(sat))": "vce_saturation",
    "vce saturation (max) @ ib, ic": "vce_saturation",
    "collector - emitter voltage vceo": "collector_emitter_voltage",
    "voltage - collector emitter breakdown (max)": "collector_emitter_voltage",
    "current - collector(ic)": "collector_current",
    "current - collector (ic) (max)": "collector_current",
    "dc current gain": "dc_current_gain",
    "dc current gain (hfe) (min) @ ic, vce": "dc_current_gain",
    # analog / digital
    "vos - input offset voltage": "input_offset_voltage",
    "input offset voltage": "input_offset_voltage",
    "voltage - input offset": "input_offset_voltage",
    "gain bandwidth product": "gain_bandwidth_product",
    "gain bandwidth product (gbp)": "gain_bandwidth_product",
    "resolution(bits)": "resolution_bits",
    "resolution (bits)": "resolution_bits",
    "resolution": "resolution_bits",
    "number of channels": "number_of_channels",
    "channels": "number_of_channels",
    "clock frequency": "clock_frequency",
    "clock frequency (max)": "clock_frequency",
    "propagation delay": "propagation_delay",
    "propagation delay time": "propagation_delay",
    # opto
    "isolation voltage(vrms)": "isolation_voltage",
    "voltage - isolation": "isolation_voltage",
    "current transfer ratio": "current_transfer_ratio",
    "current transfer ratio (ctr)": "current_transfer_ratio",
    # mechanical
    "mounting type": "mounting_type",
    "mounting style": "mounting_type",
    "installation method": "mounting_type",
    "number of pins": "number_of_pins",
    "number of positions": "number_of_pins",
}

# Every canonical name the table can produce — handy for tests/documentation.
CANONICAL_NAMES: frozenset[str] = frozenset(CANONICAL_NAME_ALIASES.values())

_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Case-fold + collapse whitespace, so spelling drift alone never forks a key."""
    return _WS_RE.sub(" ", (name or "").strip()).lower()


def canonical_name(name: str) -> str:
    """Canonical name for a distributor attribute label.

    Unmapped names fall back to their own normalized form — conservative by
    design: an unknown parametric is stored under its raw name rather than
    being force-fitted onto a canonical it may not mean.
    """
    normalized = normalize_name(name)
    return CANONICAL_NAME_ALIASES.get(normalized, normalized)


# ── Value parsing ────────────────────────────────────────────────────────────

KIND_SCALAR = "scalar"        # one magnitude: value_min == value_max
KIND_RANGE = "range"          # "0.9V~5.5V" — both endpoints preserved
KIND_TOLERANCE = "tolerance"  # "±10%" — symmetric, value_min = -value_max
KIND_UNPARSED = "unparsed"    # free text; raw_value is all there is
KIND_EMPTY = "empty"          # "-" / "" — the distributor published no value

# Values that mean "no value published" rather than a value we failed to read.
_ABSENT = frozenset({"-", "--", "–", "—", "n/a", "n.a.", "n/a.", "not applicable"})

# SI prefixes. Both "k" and "K" read as kilo (LCSC uses either); "u"/"µ"/"μ"
# all read as micro. Case matters elsewhere: "m" is milli, "M" is mega.
_SI_PREFIXES: dict[str, float] = {
    "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3,
    "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}

# Units an SI prefix may be applied to. Deliberately does NOT include "bit":
# "2Kbit" is 2048 bits in memory-part vocabulary, not 2000, so scaling it would
# invent a wrong number — it stays unparsed instead.
_SCALED_UNITS: frozenset[str] = frozenset({
    "V", "A", "W", "Ω", "F", "H", "Hz", "s", "m", "g", "J", "cd", "lm", "lx", "T", "Wh", "Ah", "VA",
})

# Units that carry no SI prefix. Matched before prefix stripping.
_UNSCALED_UNITS: frozenset[str] = frozenset({
    "%", "°", "°C", "°F", "K", "ppm", "ppb", "dB", "dBm", "dBc",
    "bit", "bits", "h", "min", "AWG", "mil",
})

# Exact unit-token spelling fixes applied before lookup (never partial).
# "℃" is the single-codepoint ℃ LCSC publishes; "Ω" is the legacy
# OHM SIGN, which must fold onto GREEK CAPITAL OMEGA ("Ω").
_UNIT_ALIASES: dict[str, str] = {
    "℃": "°C", "degC": "°C", "C°": "°C",
    "℉": "°F", "degF": "°F",
    "Ω": "Ω", "ohm": "Ω", "ohms": "Ω",
    "Ohm": "Ω", "Ohms": "Ω", "R": "Ω",
    "hrs": "h", "hr": "h", "hour": "h", "hours": "h",
    "sec": "s", "secs": "s", "seconds": "s",
    "Bit": "bit", "Bits": "bit", "bits": "bit",
    "Hertz": "Hz",
}

_NUM_RE = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+")
# A whitespace-separated trailing word ("200 Years", "1 Independent") is taken
# verbatim as the unit: the magnitude is unambiguous, the unit is not ours to
# interpret.
_WORD_UNIT_RE = re.compile(r"[A-Za-z][A-Za-z\- ]*")
_RANGE_SEPARATORS = ("~", "～")
_TOLERANCE_PREFIXES = ("±", "+/-", "+-")


@dataclass(frozen=True)
class ParsedValue:
    """One attribute value, parsed as far as is safe.

    `value_min`/`value_max` are expressed in `unit`, which is always an SI base
    unit for prefixed inputs ("100nF" -> 1e-07 "F"). `qualifier` holds the
    measurement condition that followed an "@" ("600mV@1A" -> "1A"), preserved
    verbatim and never parsed.
    """

    raw: str
    kind: str
    value_min: float | None = None
    value_max: float | None = None
    unit: str = ""
    qualifier: str = ""

    @property
    def parsed(self) -> bool:
        """True when a usable magnitude was extracted."""
        return self.kind in (KIND_SCALAR, KIND_RANGE, KIND_TOLERANCE)


def _resolve_unit_atom(token: str) -> tuple[float, str] | None:
    """Resolve a single unit token to (scale, base_unit), or None if unknown."""
    token = _UNIT_ALIASES.get(token, token)
    if token in _UNSCALED_UNITS or token in _SCALED_UNITS:
        return 1.0, token
    for prefix, multiplier in _SI_PREFIXES.items():
        if len(token) > len(prefix) and token.startswith(prefix):
            base = token[len(prefix):]
            base = _UNIT_ALIASES.get(base, base)
            if base in _SCALED_UNITS:
                return multiplier, base
    return None


def _resolve_unit(token: str) -> tuple[float, str] | None:
    """Resolve a unit token, including one level of "per" ("uV/℃", "V/us")."""
    token = token.strip()
    if not token:
        return 1.0, ""
    if "/" in token:
        head, _, tail = token.partition("/")
        numerator = _resolve_unit_atom(head.strip())
        if numerator is None:
            return None
        denominator = _resolve_unit_atom(tail.strip())
        if denominator is None:
            tail_unit = _UNIT_ALIASES.get(tail.strip(), tail.strip())
            return numerator[0], f"{numerator[1]}/{tail_unit}"
        return numerator[0] / denominator[0], f"{numerator[1]}/{denominator[1]}"
    return _resolve_unit_atom(token)


def _parse_magnitude(text: str) -> tuple[float, str, str] | None:
    """Parse "<number><unit>" into (value_in_base_unit, base_unit, raw_unit_token).

    Returns None unless the string *starts* with a number and whatever follows
    is a recognizable unit (or a whitespace-separated word). "74HC" therefore
    does not become 74 "HC".
    """
    text = text.strip()
    match = _NUM_RE.match(text)
    if not match:
        return None
    number = float(match.group(0).replace(",", ""))
    rest = text[match.end():]
    token = rest.strip()
    if not token:
        return number, "", ""
    resolved = _resolve_unit(token)
    if resolved is not None:
        scale, unit = resolved
        return number * scale, unit, token
    # Whitespace-separated word: keep it verbatim as the unit, unscaled.
    if rest != token and _WORD_UNIT_RE.fullmatch(token):
        return number, token, token
    return None


def _unparsed(raw: str, qualifier: str = "") -> ParsedValue:
    return ParsedValue(raw=raw, kind=KIND_UNPARSED, qualifier=qualifier)


def _parse_range(body: str, raw: str, qualifier: str) -> ParsedValue:
    separator = next(s for s in _RANGE_SEPARATORS if s in body)
    low_text, _, high_text = body.partition(separator)
    low = _parse_magnitude(low_text)
    high = _parse_magnitude(high_text)
    if low is None or high is None:
        return _unparsed(raw, qualifier)
    # One-sided unit ("0.9~5.5V"): the bare endpoint inherits the other's unit,
    # which is the SI reading of the notation.
    if not low[2] and high[2]:
        low = _parse_magnitude(low_text.strip() + high[2])
    elif not high[2] and low[2]:
        high = _parse_magnitude(high_text.strip() + low[2])
    if low is None or high is None or low[1] != high[1]:
        return _unparsed(raw, qualifier)
    return ParsedValue(
        raw=raw,
        kind=KIND_RANGE,
        value_min=min(low[0], high[0]),
        value_max=max(low[0], high[0]),
        unit=low[1],
        qualifier=qualifier,
    )


def parse_value(raw: str) -> ParsedValue:
    """Parse a distributor attribute value string.

    Recognized shapes, in order: absent markers ("-"), an "@" measurement
    condition split off as `qualifier`, semicolon lists (left unparsed — a
    list is not one number), "±x" tolerances, "a~b" ranges, and finally a
    plain magnitude. Anything else stays `KIND_UNPARSED` with `raw` intact.
    """
    raw = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    text = raw.strip()
    if not text or text.lower() in _ABSENT:
        return ParsedValue(raw=raw, kind=KIND_EMPTY)

    qualifier = ""
    if "@" in text:
        text, _, qualifier = text.partition("@")
        text = text.strip()
        qualifier = qualifier.strip()
        if not text:
            return _unparsed(raw, qualifier)

    # A ";" list ("130%;260%", "ESD protection;Short circuit protection") holds
    # several values or free-text features — never flatten it into one number.
    if ";" in text:
        return _unparsed(raw, qualifier)

    for prefix in _TOLERANCE_PREFIXES:
        if text.startswith(prefix):
            parsed = _parse_magnitude(text[len(prefix):])
            if parsed is None:
                return _unparsed(raw, qualifier)
            magnitude = abs(parsed[0])
            return ParsedValue(
                raw=raw,
                kind=KIND_TOLERANCE,
                value_min=-magnitude,
                value_max=magnitude,
                unit=parsed[1],
                qualifier=qualifier,
            )

    if any(separator in text for separator in _RANGE_SEPARATORS):
        return _parse_range(text, raw, qualifier)

    parsed = _parse_magnitude(text)
    if parsed is None:
        return _unparsed(raw, qualifier)
    return ParsedValue(
        raw=raw,
        kind=KIND_SCALAR,
        value_min=parsed[0],
        value_max=parsed[0],
        unit=parsed[1],
        qualifier=qualifier,
    )
