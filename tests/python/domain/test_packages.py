"""Tests for domain.packages — the controlled package (land pattern) vocabulary.

The module is pure data mapping, so the tests are tables: (raw -> canonical) and
(a, b -> equivalent?). Every raw string in the tables is a *real* string mined from
one of three corpora, not an invention:

* ``tests/fixtures/generated/distributor-scrapes.json`` — LCSC ``encapStandard``
  values from the committed distributor fixtures.
* ``tests/fixtures/generated/inventory.json`` — the ``package`` column of the
  generated inventory fixture (LCSC-style strings as they land in dubIS).
* ``glasgow_revD0_dubis_bom.csv`` — the ``Footprint`` column of a real KiCad BOM
  (glasgow revD0, 119 rows / 48 distinct footprints). That file lives outside this
  repo, so the distinct strings are inlined below as KICAD_FOOTPRINTS.
* DigiKey ``Package / Case`` strings quoted in the module docstring / issue.
"""

from __future__ import annotations

import json
import os

import pytest

from domain.packages import (
    CHIP_CODES,
    NAMED_CANONICALS,
    PACKAGE_ALIASES,
    normalize_package,
    package_info,
    packages_equivalent,
)
from domain.packages import _build_alias_table, _Named  # noqa: F401  (integrity tests)

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_SCRAPES = os.path.join(_REPO_ROOT, "tests", "fixtures", "generated", "distributor-scrapes.json")


# ── the real-string corpora ──────────────────────────────────────────────────

# All 48 distinct values of the Footprint column in glasgow_revD0_dubis_bom.csv.
KICAD_FOOTPRINTS: tuple[str, ...] = (
    "Automated:D_SOD-123HE",
    "Automated:R_MiniMELF_MMA-0204",
    "Automated:R_Shunt_Alt_1206_3216Metric",
    "Automated:SW_Tactile_SPST_Angled_TC-1109DE",
    "Automated:Texas_RPW0010A_VQFN-HR10_2x2x1mm",
    "Automated:WLCSP-6_1.4x1.0mm_P0.4mm",
    "Capacitor_SMD:CP_Elec_6.3x4.5",
    "Capacitor_SMD:C_0201_0603Metric",
    "Capacitor_SMD:C_0402_1005Metric",
    "Capacitor_SMD:C_0603_1608Metric",
    "Capacitor_SMD:C_0805_2012Metric",
    "Connector_Hirose:Hirose_DF12_DF12C3.0-60DS-0.5V_2x30_P0.50mm_Vertical",
    "Connector_IDC:IDC-Header_2x10_P2.54mm_Vertical_SMD",
    "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD",
    "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
    "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
    "Diode_SMD:D_0201_0603Metric",
    "Diode_SMD:D_0402_1005Metric",
    "Diode_SMD:D_SOD-123",
    "Diode_SMD:D_SOD-323",
    "Diode_SMD:D_SOD-523",
    "Diode_SMD:D_SOD-882",
    "Inductor_SMD:L_0603_1608Metric",
    "Inductor_SMD:L_Changjiang_FTC201212S",
    "Inductor_SMD:L_Changjiang_FTC201612S",
    "LED_SMD:LED_0603_1608Metric",
    "Package_BGA:BGA-24_6x8mm_Layout5x5_P1.0mm",
    "Package_BGA:BGA-256_14.0x14.0mm_Layout16x16_P0.8mm_Ball0.45mm_Pad0.32mm_NSMD",
    "Package_DFN_QFN:QFN-28_4x4mm_P0.5mm",
    "Package_DFN_QFN:QFN-56-1EP_8x8mm_P0.5mm_EP4.5x5.2mm",
    "Package_DFN_QFN:VQFN-64-1EP_9x9mm_P0.5mm_EP7.15x7.15mm",
    "Package_DFN_QFN:WQFN-14-1EP_2.5x2.5mm_P0.5mm_EP1.45x1.45mm",
    "Package_DFN_QFN:WQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm",
    "Package_SO:TSSOP-10_3x3mm_P0.5mm",
    "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm",
    "Package_SO:TSSOP-8_4.4x3mm_P0.65mm",
    "Package_SON:WSON-8-1EP_6x5mm_P1.27mm_EP3.4x4.3mm",
    "Package_TO_SOT_SMD:SOT-143",
    "Package_TO_SOT_SMD:SOT-23",
    "Package_TO_SOT_SMD:SOT-23-5",
    "Package_TO_SOT_SMD:SOT-23-6",
    "Package_TO_SOT_SMD:SOT-323_SC-70",
    "Package_TO_SOT_SMD:SOT-353_SC-70-5",
    "Package_TO_SOT_SMD:SOT-363_SC-70-6",
    "Package_TO_SOT_SMD:SOT-563",
    "Package_TO_SOT_SMD:SOT-583-8",
    "Resistor_SMD:R_0201_0603Metric",
    "Resistor_SMD:R_0402_1005Metric",
)

# The KiCad footprints that deliberately stay unknown. Each is a manufacturer- or
# connector-specific land pattern with no industry package name to map onto — there is
# nothing a distributor's package field could ever be compared against.
KICAD_UNKNOWN: frozenset[str] = frozenset({
    "Automated:SW_Tactile_SPST_Angled_TC-1109DE",
    "Connector_Hirose:Hirose_DF12_DF12C3.0-60DS-0.5V_2x30_P0.50mm_Vertical",
    "Connector_IDC:IDC-Header_2x10_P2.54mm_Vertical_SMD",
    "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD",
    "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
    "Inductor_SMD:L_Changjiang_FTC201212S",
    "Inductor_SMD:L_Changjiang_FTC201612S",
})


def _vendor_package_strings() -> list[str]:
    """Every distinct LCSC ``encapStandard`` in the committed distributor fixtures."""
    with open(_SCRAPES, encoding="utf-8") as fh:
        data = json.load(fh)

    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if "productCode" in node and isinstance(node.get("encapStandard"), str):
                value = node["encapStandard"].strip()
                if value:
                    found.add(value)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return sorted(found)


# ── table 1: raw string -> canonical token ───────────────────────────────────

NORMALIZE_CASES: tuple[tuple[str, str | None], ...] = (
    # ── the cases from the brief ─────────────────────────────────────────────
    ("6-TSSOP, SC-88, SOT-363", "SOT-363-6"),   # DigiKey prints three names at once
    ("SOT-363-6", "SOT-363-6"),                 # LCSC / Mouser
    ("SC-88", "SOT-363-6"),                     # LCSC fixture (onsemi SMF05CT1G)
    ("SOT-363", "SOT-363-6"),
    ("SC-70-6", "SOT-363-6"),
    ("SOT-353", "SOT-353-5"),
    ("SC-70-5", "SOT-353-5"),
    ("SC-88A", "SOT-353-5"),                    # 5-lead — NOT SC-88
    ("SOT-23-5", "SOT-23-5"),
    ("SOD-882", "SOD-882"),
    ("SOD-523", "SOD-523"),
    ("SOD-923", "SOD-923"),
    ("SOD-123", "SOD-123"),
    ("SOD-123HE", "SOD-123HE"),                 # low-profile variant, never SOD-123
    ("SOD-123F", "SOD-123F"),
    ("WSON-8", "DFN-8"),
    ("8-WFDFN Exposed Pad", "DFN-8-EP"),        # DigiKey
    ("TSSOP-10", "TSSOP-10"),
    ("QFN-56-EP(8x8)", "QFN-56-8X8-EP"),
    ("0402", "CHIP-0402"),
    ("0402 (1005 Metric)", "CHIP-0402"),        # DigiKey
    ("SMD,P=1.27mm", None),                     # LCSC: a pitch is not a package
    ("SMD,P=1mm", None),

    # ── LCSC encapStandard, from the committed distributor fixtures ──────────
    ("0603", "CHIP-0603"),
    ("0805", "CHIP-0805"),
    ("SOIC-8", "SOIC-8"),
    ("SOIC-16", "SOIC-16"),
    ("SOT-23", "SOT-23-3"),                     # bare SOT-23 is the 3-lead body
    ("SOT-23-3L", "SOT-23-3"),
    ("VSSOP-10", "VSSOP-10"),
    ("LL-34", "MINIMELF-0204"),
    ("SMD3225-4P", "CRYSTAL-3225-4"),
    ("SOP-4-2.54mm", "SOIC-4-P2.54"),           # 2.54mm-pitch optocoupler body
    ("SMD,D6.3xL7.7mm", "CAP-CAN-D6.3XL7.7"),
    ("SMD", None),
    ("Through Hole,19.2x15.6mm", None),

    # ── LCSC-style strings from the generated inventory fixture ─────────────
    ("1206", "CHIP-1206"),
    ("2512", "CHIP-2512"),
    ("SOT-23-6L", "SOT-23-6"),
    ("SOT-89-3", "SOT-89-3"),
    ("SOD-323", "SOD-323"),
    ("TSSOP-8", "TSSOP-8"),
    ("TSSOP-16", "TSSOP-16"),
    ("QFN-12(3x3)", "QFN-12-3X3"),
    ("QFN-28(4X4)", "QFN-28-4X4"),
    ("VQFN-56-EP(7x7)", "QFN-56-7X7-EP"),
    ("VQFN-HR-21(3x5)", "QFN-HR-21-5X3"),       # hot-rod QFN: its own family
    ("UFQFPN-48(7x7)", "QFN-48-7X7"),           # ST's name for a QFN
    ("TDFN-8(2x2)", "DFN-8-2X2"),
    ("DFN-10L(3x3)", "DFN-10-3X3"),
    ("USON-14(1.4x3.5)", "DFN-14-3.5X1.4"),
    ("SMA(DO-214AC)", "DO-214AC"),
    ("SMD2520-4P", "CRYSTAL-2520-4"),
    ("SMD,D6.3xL5.4mm", "CAP-CAN-D6.3XL5.4"),
    ("-", None),
    ("P=5mm", None),
    ("SMD,4x4mm", None),
    ("SMD-4P,5.2x5.2mm", None),
    ("SMD,P=1mm,Surface Mount,Right Angle", None),
    ("Through Hole,P=5mm", None),
    ("CASE-P-2012-12(mm)", None),               # tantalum case code, not a land pattern

    # ── DigiKey Package / Case ──────────────────────────────────────────────
    ('8-SOIC (0.154", 3.90mm Width)', "SOIC-8"),
    ('16-SOIC (0.295", 7.50mm Width)', "SOIC-16-W"),
    ("28-VQFN Exposed Pad", "QFN-28-EP"),
    ("10-TSSOP", "TSSOP-10"),
    ("SOT-23-3", "SOT-23-3"),
    ("TO-236-3", "SOT-23-3"),
    ("SC-59", "SOT-23-3"),

    # ── KiCad footprints (Library:Footprint_Name) ───────────────────────────
    ("Package_TO_SOT_SMD:SOT-363_SC-70-6", "SOT-363-6"),
    ("Package_TO_SOT_SMD:SOT-353_SC-70-5", "SOT-353-5"),
    ("Package_TO_SOT_SMD:SOT-323_SC-70", "SOT-323-3"),
    ("Capacitor_SMD:C_0402_1005Metric", "CHIP-0402"),
    ("Resistor_SMD:R_0201_0603Metric", "CHIP-0201"),
    ("Package_SO:TSSOP-10_3x3mm_P0.5mm", "VSSOP-10"),   # 3x3/0.5mm is the VSSOP body
    ("Package_SO:TSSOP-8_4.4x3mm_P0.65mm", "TSSOP-8"),
    ("Package_SON:WSON-8-1EP_6x5mm_P1.27mm_EP3.4x4.3mm", "DFN-8-6X5-EP"),
    ("Package_DFN_QFN:QFN-56-1EP_8x8mm_P0.5mm_EP4.5x5.2mm", "QFN-56-8X8-EP"),
    ("Package_BGA:BGA-256_14.0x14.0mm_Layout16x16_P0.8mm_Ball0.45mm_Pad0.32mm_NSMD",
     "BGA-256-14X14"),
    ("Automated:Texas_RPW0010A_VQFN-HR10_2x2x1mm", "QFN-HR-10-2X2"),
    ("Automated:R_MiniMELF_MMA-0204", "MINIMELF-0204"),
    ("Automated:D_SOD-123HE", "SOD-123HE"),
    ("Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "CRYSTAL-3225-4"),
    ("Capacitor_SMD:CP_Elec_6.3x4.5", "CAP-CAN-D6.3XL4.5"),
    ("Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", None),

    # ── unknown / hostile input ─────────────────────────────────────────────
    ("", None),
    ("   ", None),
    ("no such package", None),
    ("SOD-999", None),                          # unknown SOD suffix is unknown, not SOD
    ("QFN", None),                              # a family with no pin count says nothing
    ("SC-70", None),                            # vendors use it for 3, 5 and 6 leads
    ("1005", None),                             # metric-only code: refuse to guess
    ("1608", None),
    ("0402 (1608 Metric)", None),               # self-contradictory: refuse to guess
)


@pytest.mark.parametrize("raw,expected", NORMALIZE_CASES)
def test_normalize_package(raw, expected):
    assert normalize_package(raw) == expected


@pytest.mark.parametrize("raw,expected", NORMALIZE_CASES)
def test_normalize_is_idempotent(raw, expected):
    """Feeding a canonical token back in must return the same token."""
    once = normalize_package(raw)
    assert normalize_package(once) == once


def test_normalize_none_input():
    assert normalize_package(None) is None


# ── table 2: substitution equivalence ────────────────────────────────────────

EQUIVALENCE_CASES: tuple[tuple[str, str, bool], ...] = (
    # ── same physical package, different vendors ─────────────────────────────
    ("6-TSSOP, SC-88, SOT-363", "SOT-363-6", True),     # DigiKey vs LCSC/Mouser
    ("SC-88", "SOT-363-6", True),
    ("SC-88", "Package_TO_SOT_SMD:SOT-363_SC-70-6", True),
    ("SOT-353", "SC-70-5", True),
    ("SOT-353", "Package_TO_SOT_SMD:SOT-353_SC-70-5", True),
    ("0402", "0402 (1005 Metric)", True),
    ("0402", "Capacitor_SMD:C_0402_1005Metric", True),
    ("0603", "Resistor_SMD:R_0603_1608Metric", True),
    ("SOP-8", ' 8-SOIC (0.154", 3.90mm Width)', True),  # SOP / SO / SOIC, one body
    ("SO-8", "SOIC-8", True),
    ("SMD3225-4P", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", True),
    ("QFN-56-EP(8x8)", "Package_DFN_QFN:QFN-56-1EP_8x8mm_P0.5mm_EP4.5x5.2mm", True),
    ("QFN-28(4X4)", "Package_DFN_QFN:QFN-28_4x4mm_P0.5mm", True),
    ("WSON-8-EP(5x6)", "Package_SON:WSON-8-1EP_6x5mm_P1.27mm_EP3.4x4.3mm", True),  # 5x6 == 6x5
    ("LL-34", "MiniMELF", True),
    ("SMA", "DO-214AC", True),
    ("MSOP-10", "VSSOP-10", True),                      # TI's DGS body, two names
    ("MSOP-10", "Package_SO:TSSOP-10_3x3mm_P0.5mm", True),
    ("SOT-23", "SOT-23-3L", True),
    ("SOT-23", "TO-236-3", True),

    # ── different physical package: must never be equated ────────────────────
    ("SOD-882", "SOD-523", False),
    ("SOD-882", "SOD-923", False),
    ("SOD-123HE", "SOD-123", False),
    ("SOD-123", "SOD-123F", False),
    ("SOT-353", "SOT-23-5", False),
    ("SOT-353", "SOT-363", False),                      # 5 leads vs 6
    ("SOT-23", "SOT-23-5", False),
    ("0402", "0603", False),
    ("0402", "0201", False),
    ("QFN-28(4X4)", "QFN-28(5x5)", False),              # same pins, different body
    ("UFQFPN-48(7x7)", "LQFP-48(7x7)", False),          # no-lead vs leaded
    ("VQFN-HR-21(3x5)", "QFN-21(3x5)", False),          # hot-rod vs standard QFN
    ("TDFN-8(2x2)", "QFN-8(2x2)", False),               # dual-row vs quad-row
    ("SOIC-8", "TSSOP-8", False),
    ("SOIC-16", '16-SOIC (0.295", 7.50mm Width)', False),   # narrow vs wide body
    ("SOP-4-2.54mm", "SOIC-4", False),                  # pitch is load-bearing
    ("TSSOP-10", "VSSOP-10", False),                    # 3x4.4/0.65 vs 3x3/0.5
    ("CAP-CAN-D6.3XL7.7", "SMD,D6.3xL5.4mm", False),    # same diameter, taller can
    ("SMD3225-4P", "SMD2520-4P", False),

    # ── unknown on either side is never a match ──────────────────────────────
    ("SMD", "SMD", False),                              # identical strings, still unknown
    ("SMD,P=1.27mm", "TSSOP-10", False),
    ("no such package", "SOT-23", False),
    ("SOT-23", "no such package", False),
    ("", "0402", False),
    ("0402", "", False),
    ("SC-70", "SOT-323", False),                        # ambiguous side stays unknown
    # An unstated exposed pad is not proof of a match against a stated one.
    ("WSON-8(5x6)", "Package_SON:WSON-8-1EP_6x5mm_P1.27mm_EP3.4x4.3mm", False),
    # A size-less QFN is not proof of a match against a sized one.
    ("28-VQFN Exposed Pad", "QFN-28(4X4)", False),
)


@pytest.mark.parametrize("a,b,expected", EQUIVALENCE_CASES)
def test_packages_equivalent(a, b, expected):
    assert packages_equivalent(a, b) is expected


@pytest.mark.parametrize("a,b,expected", EQUIVALENCE_CASES)
def test_packages_equivalent_is_symmetric(a, b, expected):
    assert packages_equivalent(b, a) is expected


@pytest.mark.parametrize("value", ["0402", "SOT-23", "QFN-56-EP(8x8)"])
def test_packages_equivalent_reflexive_for_known(value):
    assert packages_equivalent(value, value) is True


@pytest.mark.parametrize("value", [None, "", "  ", "SMD", "-", "no such package"])
def test_packages_equivalent_false_when_unknown(value):
    """Unknown must read as 'needs review', never as 'matches' — on either side."""
    assert packages_equivalent(value, "SOT-23") is False
    assert packages_equivalent("SOT-23", value) is False
    assert packages_equivalent(value, value) is False


# ── derived facts (numeric comparison is more robust than string matching) ────

def test_chip_info_exposes_imperial_and_metric():
    info = package_info("0402")
    assert info is not None
    assert (info.family, info.imperial_code, info.metric_code) == ("CHIP", "0402", "1005")
    assert info.body_mm == (1.0, 0.5)
    assert info.pins == 2
    assert info.mount == "smd"


def test_chip_metric_disagreement_is_unknown():
    """An imperial/metric pair that contradicts itself must not resolve."""
    assert package_info("0402 (1608 Metric)") is None


def test_qfn_info_exposes_body_pitch_and_exposed_pad():
    info = package_info("Package_DFN_QFN:QFN-56-1EP_8x8mm_P0.5mm_EP4.5x5.2mm")
    assert info is not None
    assert info.canonical == "QFN-56-8X8-EP"
    assert (info.family, info.pins, info.pitch_mm) == ("QFN", 56, 0.5)
    assert info.body_mm == (8.0, 8.0)
    assert info.exposed_pad is True
    assert info.ep_mm == (5.2, 4.5)


def test_unstated_exposed_pad_is_none_not_false():
    """'not stated' and 'stated absent' are different facts; keep them different."""
    assert package_info("QFN-28(4X4)").exposed_pad is None
    assert package_info("28-VQFN Exposed Pad").exposed_pad is True


def test_named_package_info():
    info = package_info("SC-88")
    assert (info.canonical, info.pins, info.pitch_mm) == ("SOT-363-6", 6, 0.65)


def test_through_hole_mount_is_reported():
    assert package_info("TO-220").mount == "tht"
    assert package_info("DO-41").mount == "tht"


def test_wide_body_flag():
    assert package_info('16-SOIC (0.295", 7.50mm Width)').wide_body is True
    assert package_info('8-SOIC (0.154", 3.90mm Width)').wide_body is False


# ── alias table integrity ────────────────────────────────────────────────────

def test_alias_table_has_no_contradictions():
    """Every alias resolves to exactly one canonical (the table builder enforces it)."""
    for raw, canonical in PACKAGE_ALIASES.items():
        assert canonical in NAMED_CANONICALS, f"{raw} -> unknown canonical {canonical}"


def test_contradictory_alias_table_raises():
    """A raw string claimed by two canonicals is a hard error, not last-writer-wins."""
    good = (_Named("A-1", ("A", "ALPHA")), _Named("B-1", ("B",)))
    assert _build_alias_table(good)["ALPHA"] == "A-1"

    bad = (_Named("A-1", ("SHARED",)), _Named("B-1", ("SHARED",)))
    with pytest.raises(ValueError, match="alias conflict"):
        _build_alias_table(bad)


def test_duplicate_alias_for_same_canonical_is_allowed():
    """Many-to-one is the point; the same alias twice for one canonical is harmless."""
    table = _build_alias_table((_Named("A-1", ("A", "A")),))
    assert table["A"] == "A-1"


@pytest.mark.parametrize("canonical", sorted(NAMED_CANONICALS))
def test_every_named_canonical_normalizes_to_itself(canonical):
    assert normalize_package(canonical) == canonical


@pytest.mark.parametrize("alias,canonical", sorted(PACKAGE_ALIASES.items()))
def test_every_alias_normalizes_to_its_canonical(alias, canonical):
    assert normalize_package(alias) == canonical


@pytest.mark.parametrize("imperial,metric", sorted(CHIP_CODES.items()))
def test_chip_codes_round_trip(imperial, metric):
    assert normalize_package(imperial) == f"CHIP-{imperial}"
    assert package_info(imperial).metric_code == metric
    # The metric spelling alone must stay unknown unless it is also a valid imperial
    # code — 0603 is both, which is exactly why bare codes are read as imperial.
    if metric not in CHIP_CODES:
        assert normalize_package(metric) is None


# ── coverage against the real-string corpora ─────────────────────────────────

@pytest.mark.parametrize("footprint", KICAD_FOOTPRINTS)
def test_kicad_footprint_coverage(footprint):
    """Every real KiCad footprint either normalizes or is a documented unknown."""
    canonical = normalize_package(footprint)
    if footprint in KICAD_UNKNOWN:
        assert canonical is None, f"{footprint} was expected to stay unknown"
    else:
        assert canonical is not None, f"{footprint} no longer normalizes"


def test_kicad_corpus_coverage_ratio():
    resolved = [f for f in KICAD_FOOTPRINTS if normalize_package(f) is not None]
    assert len(resolved) == len(KICAD_FOOTPRINTS) - len(KICAD_UNKNOWN)
    assert len(resolved) / len(KICAD_FOOTPRINTS) >= 0.8


def test_vendor_corpus_coverage():
    """LCSC strings from the committed fixtures: most resolve, none blow up.

    The fixture refreshes weekly in CI, so this asserts a floor rather than an exact
    set. The interesting individual strings are pinned in NORMALIZE_CASES.
    """
    strings = _vendor_package_strings()
    assert len(strings) >= 10, "distributor fixture lost its package strings"
    resolved = [s for s in strings if normalize_package(s) is not None]
    assert len(resolved) / len(strings) >= 0.7


def test_vendor_corpus_normalization_is_stable():
    for raw in _vendor_package_strings():
        canonical = normalize_package(raw)
        assert normalize_package(canonical) == canonical
        assert packages_equivalent(raw, raw) is (canonical is not None)
