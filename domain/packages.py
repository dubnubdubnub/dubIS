"""Controlled vocabulary for component *packages* — the physical body / land pattern.

Package is the most load-bearing field when judging whether one part can substitute
for another: a different land pattern is an automatic reject. Every source spells the
*same* package differently, though:

    LCSC   encapStandard   "SOT-363-6"  "0402"  "QFN-56-EP(8x8)"  "SC-88"
    DigiKey Package/Case   "6-TSSOP, SC-88, SOT-363"  "0402 (1005 Metric)"
    Mouser                 "SOT-363-6"  "TSSOP-10"
    KiCad  footprint       "Package_TO_SOT_SMD:SOT-363_SC-70-6"
                           "Capacitor_SMD:C_0402_1005Metric"

This module maps those raw strings onto a **canonical token** so callers can compare
packages by identity instead of by spelling.

NOTE: this is about the *body*, not the *carrier*. Reel / tray / tube / cut-tape is a
different concern and is not modelled here.

Design rules
------------
1. **Conservative.** An unrecognised string returns ``None`` — never a guess.
   ``packages_equivalent()`` is ``False`` whenever either side is unknown or the two
   canonical tokens differ. Wrongly equating two packages costs a board respin;
   failing to equate them costs a manual review.
2. **No silent suffix stripping.** ``SOD-123HE`` is a distinct low-profile package and
   never collapses onto ``SOD-123``; ``SOD-882`` never collapses onto ``SOD-523``. The
   whole SOD/SOT/SC/DO/MELF space is modelled as *named* packages resolved by exact
   alias lookup, so there is no stripping code that could erode a suffix.
3. **Aliases are data.** ``_NAMED`` is a table of (canonical, aliases...) records;
   adding a vendor spelling is a one-line change. ``_build_alias_table()`` raises if a
   raw string is ever claimed by two different canonical tokens.
4. **Structure where structure exists.** Regular families (chip passives, QFN/DFN,
   SOIC/TSSOP, BGA, …) are *generated* from a family table plus attributes parsed out
   of the string (pin count, body size, pitch, exposed pad) rather than enumerated.

Canonical token grammar
-----------------------
Tokens are uppercase and self-describing. Canonical family names are *chosen* names,
not any single vendor's spelling::

    CHIP-<imperial>                       CHIP-0402          two-terminal chip passive
    <NAMED>                               SOT-363-6, SOD-882, DO-214AC, MINIMELF-0204
    <FAMILY>-<pins>[-<LxW>][-P<pitch>][-W][-EP]
                                          SOIC-8, TSSOP-10, SOIC-4-P2.54, SOIC-16-W,
                                          QFN-56-8X8-EP, DFN-8-2X2, BGA-256-14X14
    CRYSTAL-<metric>-<pins>               CRYSTAL-3225-4     SMD crystal can
    CAP-CAN-D<dia>XL<height>              CAP-CAN-D6.3XL7.7  SMD electrolytic can

Optional parts appear only when the source string *stated* them:

* body size (``-8X8``) is included for families where it discriminates (QFN, DFN,
  BGA, LGA, WLCSP, QFP). Axes are sorted descending so ``6x5`` and ``5x6`` agree.
* pitch (``-P2.54``) appears only when it deviates from the family's nominal pitch by
  more than ``_PITCH_TOL_MM``, so ``SOP-4-2.54mm`` cannot be confused with ``SOIC-4``.
* ``-W`` marks a wide body (SOIC 0.209"/0.295" vs the 0.154" default).
* ``-EP`` marks a stated exposed thermal pad.

Consequence, deliberate: a size-less ``QFN-28`` (DigiKey's ``28-VQFN Exposed Pad``
carries no size) is *not* equal to ``QFN-28-4X4``. Same for a stated-EP vs unstated-EP
pair. Unproven equality reads as "needs review", which is the cheap failure direction.
Callers that know better can look at :func:`package_info` and decide for themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "PackageInfo",
    "normalize_package",
    "packages_equivalent",
    "package_info",
    "PACKAGE_ALIASES",
    "NAMED_CANONICALS",
    "CHIP_CODES",
    "FAMILY_NAMES",
]

# Two pitches within this many mm are the same pitch (0.635 vs 0.65 are one pitch).
_PITCH_TOL_MM = 0.03


# ── the public record ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PackageInfo:
    """What we know about a package, derived from the raw string.

    ``canonical`` is the only field callers need for identity comparison; the rest is
    surfaced because numeric comparison (pins, body size, pitch) is more robust than
    string matching for anything this vocabulary could not resolve.
    """

    canonical: str
    family: str
    pins: int | None = None
    pitch_mm: float | None = None
    body_mm: tuple[float, float] | None = None   # (long, short), mm
    height_mm: float | None = None
    imperial_code: str | None = None             # chip passives only, e.g. "0402"
    metric_code: str | None = None               # chip passives only, e.g. "1005"
    exposed_pad: bool | None = None              # None = not stated by the source
    ep_mm: tuple[float, float] | None = None
    wide_body: bool | None = None
    mount: str | None = None                     # "smd" | "tht"


# ── data: chip passive codes (imperial -> metric) ────────────────────────────
#
# A bare 4-digit code is read as IMPERIAL, the distributor convention ("0402" is
# 1.0x0.5mm, not 0.4x0.2mm). Metric-only spellings ("1005", "1608", "2012") are
# deliberately NOT accepted bare — 0603 is a valid code in both systems, so guessing
# would be the one mistake this module must not make. A metric code is honoured only
# when it accompanies an imperial one ("0402 (1005 Metric)", "C_0402_1005Metric"),
# where it is used to *verify* rather than to guess.

_CHIP_IMPERIAL_TO_METRIC: dict[str, str] = {
    "01005": "0402",
    "0201": "0603",
    "0402": "1005",
    "0603": "1608",
    "0805": "2012",
    "1008": "2520",
    "1206": "3216",
    "1210": "3225",
    "1218": "3245",
    "1225": "3264",
    "1806": "4516",
    "1812": "4532",
    "1825": "4564",
    "2010": "5025",
    "2020": "5050",
    "2512": "6332",
    "2515": "6438",
    "2725": "6864",
    "2920": "7451",
}

# Imperial code -> (long mm, short mm). Only the common ones; absence is not an error.
_CHIP_BODY_MM: dict[str, tuple[float, float]] = {
    "01005": (0.4, 0.2),
    "0201": (0.6, 0.3),
    "0402": (1.0, 0.5),
    "0603": (1.6, 0.8),
    "0805": (2.0, 1.25),
    "1206": (3.2, 1.6),
    "1210": (3.2, 2.5),
    "1812": (4.5, 3.2),
    "2010": (5.0, 2.5),
    "2512": (6.3, 3.2),
}


# ── data: named packages (exact alias lookup, no suffix logic) ───────────────

@dataclass(frozen=True)
class _Named:
    canonical: str
    aliases: tuple[str, ...] = ()
    pins: int | None = None
    pitch_mm: float | None = None
    body_mm: tuple[float, float] | None = None
    mount: str = "smd"
    family: str | None = None   # defaults to the canonical token itself


_NAMED: tuple[_Named, ...] = (
    # ── SOT / SC (JEDEC + EIAJ/JEITA cross-names) ────────────────────────────
    # Bare "SOT-23" means the 3-lead part, everywhere. The 5/6/8-lead variants are
    # different land patterns and stay separate tokens.
    _Named("SOT-23-3", ("SOT-23", "SOT23", "SOT-23-3", "SOT23-3", "SOT-23-3L",
                        "TO-236", "TO-236-3", "TO-236AB", "SC-59"), pins=3, pitch_mm=0.95),
    _Named("SOT-23-5", ("SOT-23-5", "SOT23-5", "SOT-23-5L", "SOT-25", "SC-74A"),
           pins=5, pitch_mm=0.95),
    _Named("SOT-23-6", ("SOT-23-6", "SOT23-6", "SOT-23-6L", "SOT-26", "SC-74", "SOT-457"),
           pins=6, pitch_mm=0.95),
    _Named("SOT-23-8", ("SOT-23-8", "SOT23-8", "SOT-23-8L"), pins=8, pitch_mm=0.65),
    # SC-70 family. Bare "SC-70" is NOT aliased: vendors use it for the 3-, 5- and
    # 6-lead bodies, so it is genuinely ambiguous and must stay unknown.
    _Named("SOT-323-3", ("SOT-323", "SOT323", "SOT-323-3", "SC-70-3"), pins=3, pitch_mm=0.65),
    _Named("SOT-353-5", ("SOT-353", "SOT353", "SOT-353-5", "SC-70-5", "SC-88A"),
           pins=5, pitch_mm=0.65),
    _Named("SOT-363-6", ("SOT-363", "SOT363", "SOT-363-6", "SC-70-6", "SC-88",
                         # DigiKey files this body under its TSSOP tree and prints all
                         # three names at once; the whole string is one alias.
                         "6-TSSOP, SC-88, SOT-363",
                         "6-TSSOP, SC-70-6, SOT-363"), pins=6, pitch_mm=0.65),
    _Named("SOT-143-4", ("SOT-143", "SOT143", "SOT-143-4"), pins=4),
    _Named("SOT-563-6", ("SOT-563", "SOT563", "SOT-563-6"), pins=6, pitch_mm=0.5),
    _Named("SOT-583-8", ("SOT-583", "SOT583", "SOT-583-8"), pins=8, pitch_mm=0.5),
    _Named("SOT-89-3", ("SOT-89", "SOT89", "SOT-89-3", "SOT-89-3L"), pins=3, pitch_mm=1.5),
    # SOT-223: 3 leads + tab = 4 pads. Vendors write both "-3" and "-4"; same body.
    _Named("SOT-223", ("SOT-223", "SOT223", "SOT-223-3", "SOT-223-4", "SOT-223-3L"), pins=4),

    # ── SOD (every suffix is a different land pattern — nothing is stripped) ──
    _Named("SOD-123", ("SOD-123", "SOD123"), pins=2, body_mm=(2.65, 1.6)),
    _Named("SOD-123F", ("SOD-123F", "SOD123F"), pins=2),
    _Named("SOD-123FL", ("SOD-123FL", "SOD123FL"), pins=2),
    _Named("SOD-123HE", ("SOD-123HE", "SOD123HE"), pins=2),   # low-profile, NOT SOD-123
    _Named("SOD-128", ("SOD-128", "SOD128"), pins=2),
    _Named("SOD-323", ("SOD-323", "SOD323"), pins=2, body_mm=(1.7, 1.25)),
    _Named("SOD-323F", ("SOD-323F", "SOD323F"), pins=2),
    _Named("SOD-323FL", ("SOD-323FL", "SOD323FL"), pins=2),
    _Named("SOD-523", ("SOD-523", "SOD523"), pins=2, body_mm=(1.2, 0.8)),
    _Named("SOD-723", ("SOD-723", "SOD723"), pins=2),
    _Named("SOD-882", ("SOD-882", "SOD882"), pins=2, body_mm=(1.0, 0.6)),
    _Named("SOD-923", ("SOD-923", "SOD923"), pins=2, body_mm=(0.8, 0.6)),

    # ── DO-214 / DO through-hole ─────────────────────────────────────────────
    _Named("DO-214AC", ("DO-214AC", "SMA"), pins=2),
    _Named("DO-214AA", ("DO-214AA", "SMB"), pins=2),
    _Named("DO-214AB", ("DO-214AB", "SMC"), pins=2),
    _Named("DO-35", ("DO-35", "DO-204AH"), pins=2, mount="tht"),
    _Named("DO-41", ("DO-41", "DO-204AL"), pins=2, mount="tht"),
    _Named("DO-201AD", ("DO-201AD",), pins=2, mount="tht"),

    # ── MELF (glass cylinders). LL-34 / SOD-80 / DO-213AA / MMA-0204 are one body. ──
    _Named("MINIMELF-0204", ("MINIMELF", "MINI-MELF", "MINIMELF-0204", "MMA-0204",
                             "LL-34", "SOD-80", "DO-213AA"), pins=2, body_mm=(3.5, 1.6)),
    _Named("MELF-0207", ("MELF", "MELF-0207", "MMB-0207", "LL-41", "DO-213AB"),
           pins=2, body_mm=(5.9, 2.2)),
    _Named("MICROMELF-0102", ("MICROMELF", "MICRO-MELF", "MICROMELF-0102", "MMU-0102"),
           pins=2, body_mm=(2.2, 1.1)),

    # ── power / through-hole bodies where the lead count is conventional ──────
    _Named("TO-92-3", ("TO-92", "TO-92-3", "TO-226"), pins=3, mount="tht"),
    _Named("TO-220-3", ("TO-220", "TO-220-3", "TO-220AB"), pins=3, mount="tht"),
    _Named("TO-220-5", ("TO-220-5",), pins=5, mount="tht"),
    _Named("TO-247-3", ("TO-247", "TO-247-3"), pins=3, mount="tht"),
    # DPAK / D2PAK: leads + tab; vendors count either the leads or the pads.
    _Named("TO-252", ("TO-252", "TO-252-2", "TO-252-3", "TO-252AA", "DPAK"), pins=3),
    _Named("TO-263", ("TO-263", "TO-263-2", "TO-263-3", "TO-263AB", "D2PAK", "DDPAK"), pins=3),

    # ── SOP-family exceptions that the generated families must not swallow ────
    # MSOP-10 is TI's DGS: 3x3mm, 0.5mm pitch — the VSSOP-10 land pattern.
    _Named("VSSOP-10", ("VSSOP-10", "MSOP-10",
                        # KiCad files the 3x3/0.5mm body under "TSSOP-10"; the real
                        # JEDEC TSSOP-10 is 3x4.4mm/0.65mm, so keep them apart and
                        # point KiCad's spelling at the body it actually is.
                        "TSSOP-10_3X3MM_P0.5MM"), pins=10, pitch_mm=0.5, body_mm=(3.0, 3.0),
           family="VSSOP"),
    _Named("MSOP-8", ("MSOP-8", "MSOP8"), pins=8, pitch_mm=0.65, body_mm=(3.0, 3.0),
           family="MSOP"),
)


# ── data: generated families ─────────────────────────────────────────────────

@dataclass(frozen=True)
class _Family:
    """A regular family: ``<pins>-<SPELLING>`` / ``<SPELLING>-<pins>`` / ``<SPELLING><pins>``.

    ``spellings`` is the reviewable list of vendor names that collapse onto
    ``canonical``. Thickness-class prefixes (V/W/U/T…) are listed here on purpose:
    within these families they change the body *height*, not the land pattern.
    ``pins`` is an allow-list — it also stops nonsense like DigiKey's "6-TSSOP"
    (which is really SOT-363) from minting a TSSOP-6 that does not exist.
    """

    canonical: str
    spellings: tuple[str, ...]
    pins: frozenset[int]
    nominal_pitch_mm: float | None = None
    size_in_canonical: bool = False
    ep_in_canonical: bool = False
    wide_threshold_mm: float | None = None
    bare_mm_is_pitch: bool = False
    mount: str = "smd"


_GULLWING_PINS = frozenset({4, 6, 8, 10, 14, 16, 18, 20, 24, 28, 30, 32, 36, 38, 44, 48, 56, 64})
_NOLEAD_PINS = frozenset(range(4, 129))
_BGA_PINS = frozenset(range(4, 1601))

_FAMILIES: tuple[_Family, ...] = (
    # QFN-HR ("hot rod") has staggered/perimeter-broken pads — NOT a plain QFN.
    # Listed before QFN so "VQFN-HR10" cannot be read as a QFN.
    _Family("QFN-HR", ("QFN-HR", "VQFN-HR", "WQFN-HR", "UQFN-HR"), _NOLEAD_PINS,
            size_in_canonical=True, ep_in_canonical=True),
    # UFQFPN is ST's name for a QFN (no leads) — unlike LQFP, which is leaded and
    # lives in the QFP family below, so "UFQFPN-48(7x7)" and "LQFP-48 7x7" stay apart.
    _Family("QFN", ("QFN", "VQFN", "WQFN", "UQFN", "TQFN", "HVQFN", "VFQFN", "WFQFN",
                    "UFQFN", "UFQFPN", "HQFN"), _NOLEAD_PINS,
            size_in_canonical=True, ep_in_canonical=True),
    # DFN and SON are the same dual-row no-lead construction under two naming trees.
    _Family("DFN", ("DFN", "WDFN", "UDFN", "TDFN", "VDFN", "XDFN", "WFDFN",
                    "SON", "WSON", "USON", "VSON", "TSON", "HSON"), _NOLEAD_PINS,
            size_in_canonical=True, ep_in_canonical=True),
    _Family("QFP", ("QFP", "LQFP", "TQFP", "PQFP", "MQFP", "VQFP", "HTQFP", "HLQFP"),
            _NOLEAD_PINS, size_in_canonical=True, ep_in_canonical=True),
    _Family("BGA", ("BGA", "FBGA", "VFBGA", "TFBGA", "UFBGA", "LFBGA", "CSPBGA"),
            _BGA_PINS, size_in_canonical=True),
    _Family("WLCSP", ("WLCSP", "WLB", "DSBGA"), _BGA_PINS, size_in_canonical=True),
    _Family("LGA", ("LGA",), _NOLEAD_PINS, size_in_canonical=True, ep_in_canonical=True),
    # SOIC / SO / SOP: one 1.27mm-pitch gull-wing tree. A stated pitch that is not
    # 1.27 keeps the token apart (LCSC's "SOP-4-2.54mm" optocoupler body), and a body
    # wider than 5mm gets "-W" (DigiKey's 0.209"/0.295" variants).
    _Family("SOIC", ("SOIC", "SO", "SOP", "SOIC-N"), _GULLWING_PINS,
            nominal_pitch_mm=1.27, wide_threshold_mm=5.0, bare_mm_is_pitch=True),
    _Family("TSSOP", ("TSSOP", "HTSSOP"), _GULLWING_PINS - {4, 6},
            nominal_pitch_mm=0.65, bare_mm_is_pitch=True),
    _Family("SSOP", ("SSOP",), _GULLWING_PINS - {4, 6}, nominal_pitch_mm=0.65,
            bare_mm_is_pitch=True),
    _Family("VSSOP", ("VSSOP",), frozenset({8, 10, 12}), nominal_pitch_mm=0.5,
            bare_mm_is_pitch=True),
    _Family("QSOP", ("QSOP",), _GULLWING_PINS - {4, 6}, nominal_pitch_mm=0.635,
            bare_mm_is_pitch=True),
    _Family("TSOP", ("TSOP",), _GULLWING_PINS - {4, 6}, nominal_pitch_mm=0.5,
            bare_mm_is_pitch=True),
    _Family("PLCC", ("PLCC",), frozenset({20, 28, 32, 44, 52, 68, 84})),
    _Family("DIP", ("DIP", "PDIP", "CDIP"), _GULLWING_PINS, nominal_pitch_mm=2.54,
            bare_mm_is_pitch=True, mount="tht"),
)


# ── alias table construction (many-to-one, conflicts are a hard error) ───────

def _key(text: str) -> str:
    """Uppercase + normalise separators/whitespace, without touching meaningful chars."""
    out = text.upper()
    for ch in ("–", "—", "−"):   # en dash, em dash, unicode minus
        out = out.replace(ch, "-")
    out = out.replace("×", "X")            # multiplication sign
    out = re.sub(r"\s+", " ", out)
    return out.strip().strip(".").strip()


def _build_alias_table(named: tuple[_Named, ...]) -> dict[str, str]:
    """Flatten ``named`` into ``{alias_key: canonical}``.

    Every canonical is registered as an alias of itself, so normalisation is
    idempotent. A raw string claimed by two different canonicals is a contradiction in
    the vocabulary and raises — silently keeping the last writer is how a table like
    this rots into wrong answers.
    """
    table: dict[str, str] = {}
    for entry in named:
        for raw in (entry.canonical, *entry.aliases):
            k = _key(raw)
            existing = table.get(k)
            if existing is not None and existing != entry.canonical:
                raise ValueError(
                    f"package alias conflict: {raw!r} maps to both {existing!r} and {entry.canonical!r}"
                )
            table[k] = entry.canonical
    return table


_ALIASES: dict[str, str] = _build_alias_table(_NAMED)
_NAMED_BY_CANONICAL: dict[str, _Named] = {n.canonical: n for n in _NAMED}

# Public, read-only views (handy for UI pickers and for the vocabulary's own tests).
PACKAGE_ALIASES = MappingProxyType(_ALIASES)
NAMED_CANONICALS = frozenset(_NAMED_BY_CANONICAL)
CHIP_CODES = MappingProxyType(_CHIP_IMPERIAL_TO_METRIC)
FAMILY_NAMES = frozenset(f.canonical for f in _FAMILIES) | {"CHIP", "CRYSTAL", "CAP-CAN"}


# ── attribute parsing ────────────────────────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_RE_EP_SIZE = re.compile(rf"^\d*EP\s*({_NUM})\s*X\s*({_NUM})\s*(?:MM)?$")
_RE_LAYOUT = re.compile(r"^LAYOUT\s*\d+\s*X\s*\d+$")
_RE_PITCH = re.compile(rf"^(?:P|PITCH)\s*=?\s*({_NUM})\s*MM$")
_RE_SIZE = re.compile(rf"^({_NUM})\s*X\s*({_NUM})(?:\s*X\s*({_NUM}))?\s*(?:MM)?$")
_RE_BARE_MM = re.compile(rf"^({_NUM})\s*MM$")
_RE_MIL = re.compile(rf"^({_NUM})\s*MIL$")
_RE_WIDTH_MM = re.compile(rf"^({_NUM})\s*MM\s*WIDTH$")
_RE_INCHES = re.compile(rf"^{_NUM}\s*(?:\"|IN|INCH)$")
_RE_METRIC = re.compile(r"^(\d{4})\s*METRIC$")
_RE_EXPOSED = re.compile(r"^(?:EXPOSED\s*PAD|\d*EP)$")
_RE_CAN = re.compile(rf"^D\s*({_NUM})\s*X\s*L?\s*({_NUM})\s*(?:MM)?$")
_RE_CRYSTAL = re.compile(r"^(?:SMD)?(\d{4})-(\d+)P(?:IN)?S?$")
_RE_CHIP = re.compile(r"^(\d{4,5})$")
_RE_BALL_PAD = re.compile(rf"^(?:BALL|PAD)\s*{_NUM}\s*(?:MM)?$")


@dataclass
class _Attrs:
    """Attributes harvested from every segment of the input string."""

    pitch_mm: float | None = None
    bare_mm: float | None = None
    body_mm: tuple[float, float] | None = None      # long axis first
    raw_size: tuple[float, float] | None = None     # as written, order preserved
    height_mm: float | None = None
    width_mm: float | None = None
    ep_mm: tuple[float, float] | None = None
    exposed_pad: bool = False
    metric_code: str | None = None
    markers: set[str] = field(default_factory=set)


# Prose fragments vendors append to a package name. They are attributes, not names,
# and (unlike the bracketed/comma-separated attributes) they are not separated by any
# delimiter — DigiKey writes "8-WFDFN Exposed Pad" — so they are lifted out first.
_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bEXPOSED\s*PAD\b"), "EP"),
    (re.compile(r"\bTHERMAL\s*PAD\b"), "EP"),
    (re.compile(r"\bTHROUGH\s*HOLE\b"), "THT"),
    (re.compile(r"\bSURFACE\s*MOUNT\b"), "SMD"),
    (re.compile(r"\bHAND\s*SOLDER\w*\b"), "IGNORE"),
    (re.compile(r"\bRIGHT\s*ANGLE\b"), "IGNORE"),
)


def _extract_phrases(key: str, attrs: _Attrs) -> str:
    """Lift prose attributes out of the string and return what is left."""
    for pattern, tag in _PHRASES:
        if pattern.search(key):
            key = pattern.sub(" ", key)
            if tag == "EP":
                attrs.exposed_pad = True
            elif tag != "IGNORE":
                attrs.markers.add(tag)
    return re.sub(r"\s+", " ", key).strip()


def _segments(key: str) -> list[str]:
    """Split a keyed string into candidate segments.

    Separators are the ones every source uses for *listing* things — commas (DigiKey
    prints several names for one body), underscores (KiCad footprint fields),
    parentheses and slashes. Hyphens are never separators: they carry meaning
    (``SOD-882``, ``SOT-23-5``).
    """
    parts = re.split(r"[,_()/;]+", key)
    return [p.strip() for p in parts if p.strip()]


def _sorted_body(a: float, b: float) -> tuple[float, float]:
    """Body size with the long axis first, so 6x5 and 5x6 are the same body."""
    return (a, b) if a >= b else (b, a)


def _classify(seg: str, attrs: _Attrs) -> bool:
    """Record ``seg`` as an attribute. Returns True if it was consumed."""
    m = _RE_EP_SIZE.match(seg)
    if m:
        attrs.ep_mm = _sorted_body(float(m.group(1)), float(m.group(2)))
        attrs.exposed_pad = True
        return True
    if _RE_EXPOSED.match(seg):
        attrs.exposed_pad = True
        return True
    if _RE_LAYOUT.match(seg) or _RE_BALL_PAD.match(seg) or _RE_INCHES.match(seg):
        return True
    m = _RE_PITCH.match(seg)
    if m:
        attrs.pitch_mm = float(m.group(1))
        return True
    m = _RE_WIDTH_MM.match(seg)
    if m:
        attrs.width_mm = float(m.group(1))
        return True
    m = _RE_MIL.match(seg)
    if m:
        attrs.width_mm = float(m.group(1)) * 0.0254
        return True
    m = _RE_METRIC.match(seg)
    if m:
        attrs.metric_code = m.group(1)
        return True
    m = _RE_SIZE.match(seg)
    if m:
        attrs.raw_size = (float(m.group(1)), float(m.group(2)))
        attrs.body_mm = _sorted_body(float(m.group(1)), float(m.group(2)))
        if m.group(3):
            attrs.height_mm = float(m.group(3))
        return True
    m = _RE_BARE_MM.match(seg)
    if m:
        attrs.bare_mm = float(m.group(1))
        return True
    if seg in ("CRYSTAL", "XTAL", "ELEC", "SMD", "SMT", "THT", "THROUGH HOLE", "HANDSOLDER"):
        attrs.markers.add(seg)
        return True
    return False


# ── family / named resolution ────────────────────────────────────────────────

@dataclass(frozen=True)
class _Hit:
    """One resolvable package identity found in the string."""

    kind: str                      # "named" | "family" | "chip" | "crystal" | "can"
    name: str                      # canonical family name, or the named canonical
    pins: int | None = None
    exposed_pad: bool = False
    extra: tuple = ()


def _strip_ep_suffix(token: str) -> tuple[str, bool]:
    """Peel a trailing exposed-pad marker: ``QFN-56-EP`` / ``WQFN-14-1EP``."""
    m = re.match(r"^(.*?)[-\s]?(\d*EP)$", token)
    if m and m.group(1):
        return m.group(1), True
    return token, False


def _peel_attributes(token: str, attrs: _Attrs) -> str:
    """Peel hyphen-joined trailing attributes off a token: ``SOP-4-2.54mm`` -> ``SOP-4``.

    Some sources glue an attribute onto the package name with a hyphen instead of
    listing it separately (LCSC's ``SOP-4-2.54mm``, ``SOIC-16-300mil``). Only chunks
    that classify as an *attribute* are peeled, so a meaningful tail is never lost:
    ``SOD-882``, ``SOD-123HE`` and ``SOT-23-5`` have nothing an attribute regex
    recognises, and stop the peel immediately.
    """
    while "-" in token:
        head, _, tail = token.rpartition("-")
        if not head or not _classify(tail, attrs):
            return token
        token = head
    return token


def _family_hit(token: str) -> _Hit | None:
    base, ep = _strip_ep_suffix(token)
    for fam in _FAMILIES:
        for spelling in fam.spellings:
            s = re.escape(spelling)
            m = re.match(rf"^(?:(\d+)\s*-\s*)?{s}(?:\s*-?\s*(\d+)\s*L?)?$", base)
            if not m:
                continue
            pins_txt = m.group(1) or m.group(2)
            if pins_txt is None:
                continue          # a bare family name says nothing — stay unknown
            pins = int(pins_txt)
            if pins not in fam.pins:
                continue          # not a pin count this family comes in
            return _Hit("family", fam.canonical, pins=pins, exposed_pad=ep)
    return None


def _token_hit(token: str, attrs: _Attrs) -> _Hit | None:
    """Resolve one segment to a package identity, or None."""
    canonical = _ALIASES.get(token)
    if canonical is not None:
        return _Hit("named", canonical)

    m = _RE_CHIP.match(token)
    if m and m.group(1) in _CHIP_IMPERIAL_TO_METRIC:
        return _Hit("chip", "CHIP", extra=(m.group(1),))

    m = _RE_CRYSTAL.match(token)
    if m and (token.startswith("SMD") or attrs.markers & {"CRYSTAL", "XTAL"}):
        return _Hit("crystal", "CRYSTAL", pins=int(m.group(2)), extra=(m.group(1),))

    m = _RE_CAN.match(token)
    if m:
        return _Hit("can", "CAP-CAN", pins=2, extra=(float(m.group(1)), float(m.group(2))))

    return _family_hit(token)


# ── canonical assembly ───────────────────────────────────────────────────────

def _fmt(value: float) -> str:
    return f"{value:g}"


def _info_for_hit(hit: _Hit, attrs: _Attrs) -> PackageInfo | None:
    if hit.kind == "named":
        entry = _NAMED_BY_CANONICAL[hit.name]
        return PackageInfo(
            canonical=entry.canonical,
            family=entry.family or entry.canonical,
            pins=entry.pins,
            pitch_mm=entry.pitch_mm if entry.pitch_mm is not None else attrs.pitch_mm,
            body_mm=entry.body_mm or attrs.body_mm,
            height_mm=attrs.height_mm,
            exposed_pad=True if attrs.exposed_pad else None,
            ep_mm=attrs.ep_mm,
            mount=entry.mount,
        )

    if hit.kind == "chip":
        imperial = hit.extra[0]
        metric = _CHIP_IMPERIAL_TO_METRIC[imperial]
        if attrs.metric_code is not None and attrs.metric_code != metric:
            return None    # "0402" + "1608Metric" contradict each other — refuse to guess
        return PackageInfo(
            canonical=f"CHIP-{imperial}",
            family="CHIP",
            pins=2,
            body_mm=_CHIP_BODY_MM.get(imperial),
            imperial_code=imperial,
            metric_code=metric,
            mount="smd",
        )

    if hit.kind == "crystal":
        metric = hit.extra[0]
        body = _sorted_body(int(metric[:2]) / 10.0, int(metric[2:]) / 10.0)
        return PackageInfo(
            canonical=f"CRYSTAL-{metric}-{hit.pins}",
            family="CRYSTAL",
            pins=hit.pins,
            body_mm=attrs.body_mm or body,
            metric_code=metric,
            mount="smd",
        )

    if hit.kind == "can":
        dia, height = hit.extra
        return PackageInfo(
            canonical=f"CAP-CAN-D{_fmt(dia)}XL{_fmt(height)}",
            family="CAP-CAN",
            pins=2,
            body_mm=(dia, dia),
            height_mm=height,
            mount="smd",
        )

    fam = next(f for f in _FAMILIES if f.canonical == hit.name)
    parts = [fam.canonical, str(hit.pins)]

    body = attrs.body_mm
    if fam.size_in_canonical and body is not None:
        parts.append(f"{_fmt(body[0])}X{_fmt(body[1])}")

    pitch = attrs.pitch_mm
    if pitch is None and fam.bare_mm_is_pitch:
        pitch = attrs.bare_mm
    if (
        fam.nominal_pitch_mm is not None
        and pitch is not None
        and abs(pitch - fam.nominal_pitch_mm) > _PITCH_TOL_MM
    ):
        parts.append(f"P{_fmt(pitch)}")

    wide = None
    if fam.wide_threshold_mm is not None and attrs.width_mm is not None:
        wide = attrs.width_mm >= fam.wide_threshold_mm
        if wide:
            parts.append("W")

    ep = hit.exposed_pad or attrs.exposed_pad
    if fam.ep_in_canonical and ep:
        parts.append("EP")

    return PackageInfo(
        canonical="-".join(parts),
        family=fam.canonical,
        pins=hit.pins,
        pitch_mm=pitch if pitch is not None else fam.nominal_pitch_mm,
        body_mm=body,
        height_mm=attrs.height_mm,
        exposed_pad=True if ep else None,
        ep_mm=attrs.ep_mm,
        wide_body=wide,
        mount=fam.mount,
    )


# ── canonical-token re-parsing (keeps normalisation idempotent) ──────────────

_RE_CANON_CHIP = re.compile(r"^CHIP-(\d{4,5})$")
_RE_CANON_CRYSTAL = re.compile(r"^CRYSTAL-(\d{4})-(\d+)$")
_RE_CANON_CAN = re.compile(rf"^CAP-CAN-D({_NUM})XL({_NUM})$")
_RE_CANON_FAMILY = re.compile(
    rf"^(?P<fam>[A-Z]+(?:-HR)?)-(?P<pins>\d+)"
    rf"(?:-(?P<long>{_NUM})X(?P<short>{_NUM}))?"
    rf"(?:-P(?P<pitch>{_NUM}))?"
    rf"(?P<wide>-W)?"
    rf"(?P<ep>-EP)?$"
)


def _parse_canonical(key: str) -> PackageInfo | None:
    """Recognise this module's own output, so ``normalize(normalize(x)) == normalize(x)``."""
    m = _RE_CANON_CHIP.match(key)
    if m and m.group(1) in _CHIP_IMPERIAL_TO_METRIC:
        return _info_for_hit(_Hit("chip", "CHIP", extra=(m.group(1),)), _Attrs())

    m = _RE_CANON_CRYSTAL.match(key)
    if m:
        return _info_for_hit(_Hit("crystal", "CRYSTAL", pins=int(m.group(2)), extra=(m.group(1),)), _Attrs())

    m = _RE_CANON_CAN.match(key)
    if m:
        return _info_for_hit(
            _Hit("can", "CAP-CAN", pins=2, extra=(float(m.group(1)), float(m.group(2)))), _Attrs()
        )

    m = _RE_CANON_FAMILY.match(key)
    if not m:
        return None
    fam = next((f for f in _FAMILIES if f.canonical == m.group("fam")), None)
    if fam is None or int(m.group("pins")) not in fam.pins:
        return None
    attrs = _Attrs()
    if m.group("long"):
        attrs.body_mm = _sorted_body(float(m.group("long")), float(m.group("short")))
    if m.group("pitch"):
        attrs.pitch_mm = float(m.group("pitch"))
    if m.group("wide"):
        attrs.width_mm = fam.wide_threshold_mm
    if m.group("ep"):
        attrs.exposed_pad = True
    info = _info_for_hit(_Hit("family", fam.canonical, pins=int(m.group("pins"))), attrs)
    # Only trust the re-parse if it reproduces the token exactly.
    return info if info is not None and info.canonical == key else None


# ── public API ───────────────────────────────────────────────────────────────

def package_info(raw: str | None) -> PackageInfo | None:
    """Resolve a vendor / KiCad package string to a :class:`PackageInfo`, or None.

    Resolution order:

    1. the whole string as an alias (lets a multi-name vendor string be one table row);
    2. the module's own canonical grammar (idempotence);
    3. segment-wise: split on list separators, harvest attributes, resolve the rest.
       If the resolvable segments disagree, the answer is None — an ambiguous string is
       an unknown string.
    """
    if not raw or not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text:
        return None

    # KiCad carries "Library:Footprint_Name" in BOM rows; the library name is not a
    # package fact ("Package_SO", "Automated"), so drop it.
    if ":" in text:
        text = text.rsplit(":", 1)[1]

    key = _key(text)
    if not key:
        return None

    canonical = _ALIASES.get(key)
    if canonical is not None:
        return _info_for_hit(_Hit("named", canonical), _Attrs())

    own = _parse_canonical(key)
    if own is not None:
        return own

    # Pass 1 — separate attribute segments (size, pitch, width, exposed pad) from
    # segments that might name a package.
    attrs = _Attrs()
    leftovers: list[str] = []
    for seg in _segments(_extract_phrases(key, attrs)):
        if not _classify(seg, attrs):
            leftovers.append(seg)

    # Pass 2 — peel hyphen-glued attributes off the remaining segments, so every
    # attribute is known before any canonical token is assembled.
    reduced = [seg if seg in _ALIASES else _peel_attributes(seg, attrs) for seg in leftovers]

    # Pass 3 — resolve. Every resolvable segment must agree; if two do not, the string
    # is ambiguous and therefore unknown.
    infos: dict[str, PackageInfo] = {}
    for seg in reduced:
        hit = _token_hit(seg, attrs)
        if hit is None:
            continue
        info = _info_for_hit(hit, attrs)
        if info is None:
            return None            # a contradiction inside one segment (e.g. chip metric)
        infos[info.canonical] = info

    # KiCad writes the electrolytic can as "CP_Elec_<dia>x<height>" — a marker plus a
    # bare size, with no token of its own. LCSC writes the same body as
    # "SMD,D6.3xL7.7mm", which _RE_CAN already handles. Diameter comes first in both,
    # so use the size exactly as written (a 6.3x7.7 can is taller than it is wide).
    if not infos and "ELEC" in attrs.markers and attrs.raw_size is not None:
        info = _info_for_hit(_Hit("can", "CAP-CAN", pins=2, extra=attrs.raw_size), attrs)
        if info is not None:
            infos[info.canonical] = info

    if len(infos) != 1:
        return None                # nothing resolved, or resolvable segments disagree
    return next(iter(infos.values()))


def normalize_package(raw: str | None) -> str | None:
    """Map a vendor/KiCad package string onto a canonical token, or None if unknown.

    Never guesses: an unrecognised or internally contradictory string is None, and
    callers must treat None as "unknown", never as "matches".

    >>> normalize_package("Package_TO_SOT_SMD:SOT-363_SC-70-6")
    'SOT-363-6'
    >>> normalize_package("6-TSSOP, SC-88, SOT-363")
    'SOT-363-6'
    >>> normalize_package("0402 (1005 Metric)")
    'CHIP-0402'
    >>> normalize_package("SMD,P=1.27mm") is None
    True
    """
    info = package_info(raw)
    return info.canonical if info is not None else None


def packages_equivalent(a: str | None, b: str | None) -> bool:
    """True only if both strings resolve to the *same* canonical package token.

    Unknown on either side is False, not True: this is the substitution gate, and an
    unproven match must read as "needs review".

    >>> packages_equivalent("6-TSSOP, SC-88, SOT-363", "SOT-363-6")
    True
    >>> packages_equivalent("SOD-123HE", "SOD-123")
    False
    >>> packages_equivalent("whatever", "SOT-23")
    False
    """
    na = normalize_package(a)
    if na is None:
        return False
    return na == normalize_package(b)
