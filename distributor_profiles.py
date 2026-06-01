"""Per-template heuristic OCR/text field extractors for PO scanning.

A "template" is a distributor the user picks before scanning a paper purchase
order ("generic", "lcsc", "digikey", "mouser", "pololu"). Each profile knows:

  * ``pn_column`` — the canonical ledger column its distributor part number maps
    to (e.g. lcsc -> ``"LCSC Part Number"``). ``generic`` has no PN column.
  * ``pn_re`` — a HEURISTIC regex matching that distributor's PN format. These
    are intentionally loose: the user reviews and edits everything before
    import, so partial / fuzzy extraction is acceptable. They are documented per
    profile below.
  * label/keyword hints (qty / price) — only used implicitly via the line regex.

Line-item dict shape (consumed by mfg_direct_import.import_po and later tasks)::

    {
        "mpn": str,            # manufacturer part number (best-effort)
        "manufacturer": str,   # often blank from a distributor invoice
        "package": str,        # usually blank here
        "quantity": int,
        "unit_price": float,
        "distributor": str,    # the template key ("lcsc" / "generic" / ...)
        "distributor_pn": str, # distributor PN (""  for generic / no match)
    }

Public API:
    get_profile(template) -> DistributorProfile
    parse_with_template(template, text) -> list[dict]

PN format notes (heuristic, derived from real-world examples):
    * LCSC:    ``C`` followed by digits, e.g. ``C429942``, ``C1525``.
    * DigiKey: catalogue PNs commonly end in ``-ND`` (or ``-CT``/``-DKR``/
               ``-TR`` cut-tape suffixes), e.g. ``296-1234-1-ND``,
               ``311-1.00KHRCT-ND``.
    * Mouser:  ``<digits>-<mfr-part>``, a numeric vendor prefix then a dash then
               the manufacturer part, e.g. ``595-SN74LVC1G08DBVR``.
    * Pololu:  bare numeric SKU, e.g. ``2820``, ``1182`` (typically 1-5 digits).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _to_qty(raw: str) -> int:
    try:
        return int(str(raw).replace(",", "").strip() or "0")
    except ValueError:
        return 0


def _to_price(raw: str) -> float:
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip() or "0")
    except ValueError:
        return 0.0


def _heuristic_parse_lines(text: str) -> list[dict[str, Any]]:
    """Delegate to the shared generic line parser (lazy import to avoid a cycle:
    mfg_direct_import imports this module)."""
    import mfg_direct_import
    return mfg_direct_import._heuristic_parse_lines(text)


# Trailing "<qty> <unit-price>" pair anchored to end-of-line. Qty is an integer;
# price has a decimal point (optionally currency-prefixed). This lets us pull
# the two rightmost numbers off a noisy invoice line.
_TRAILING_QTY_PRICE = re.compile(
    r"(?P<qty>\d{1,6})\s+(?P<price>\$?\s*\d+\.\d{1,4})\s*$"
)
# An MPN-like token (same shape as mfg_direct_import._LINE_RE group 1).
_MPN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+()\-]{1,40}")


@dataclass
class DistributorProfile:
    """Heuristic field extractor for one distributor template."""

    key: str
    pn_column: str | None
    pn_re: re.Pattern[str] | None

    def match_pn(self, token: str) -> str | None:
        """Return the normalized distributor PN if ``token`` matches this
        profile's PN format, else None. ``generic`` always returns None."""
        if self.pn_re is None:
            return None
        m = self.pn_re.fullmatch((token or "").strip())
        if not m:
            return None
        # LCSC codes are conventionally uppercase (C429942); normalize the
        # leading letter. Other distributors keep their casing.
        pn = m.group(0)
        if self.key == "lcsc":
            return pn.upper()
        return pn

    def parse(self, text: str) -> list[dict[str, Any]]:
        """Extract line items from OCR/PDF text using this profile.

        ``generic`` runs the shared heuristic line parser and tags each row with
        ``distributor="generic"`` / ``distributor_pn=""``. A distributor profile
        that fails to find its PN on any line returns an empty list, signalling
        the caller to fall back to the generic heuristic parser."""
        if self.key == "generic" or self.pn_re is None:
            return [
                {**row, "distributor": "generic", "distributor_pn": ""}
                for row in _heuristic_parse_lines(text)
            ]
        items: list[dict[str, Any]] = []
        for line in text.splitlines():
            item = self._parse_line(line)
            if item is not None:
                items.append(item)
        return items

    def _parse_line(self, line: str) -> dict[str, Any] | None:
        """Parse one noisy OCR line: locate this distributor's PN anywhere in the
        line, then take the trailing two numeric tokens as qty + unit price and
        the token immediately after the PN as the MPN (best-effort)."""
        if self.pn_re is None:
            return None
        pn_match = self.pn_re.search(line)
        if not pn_match:
            return None
        pn = pn_match.group(0).strip()
        if self.key == "lcsc":
            pn = pn.upper()

        # Trailing qty + unit price: the last two number-like tokens.
        price_m = _TRAILING_QTY_PRICE.search(line)
        if not price_m:
            return None
        qty = _to_qty(price_m.group("qty"))
        price = _to_price(price_m.group("price"))

        # MPN: first MPN-like token AFTER the distributor PN, excluding the
        # trailing qty/price region.
        tail_start = price_m.start()
        between = line[pn_match.end():tail_start]
        mpn = ""
        mtok = _MPN_RE.search(between)
        if mtok:
            mpn = mtok.group(0).strip()

        return {
            "mpn": mpn,
            "manufacturer": "",
            "package": "",
            "quantity": qty,
            "unit_price": price,
            "distributor": self.key,
            "distributor_pn": pn,
        }


# ── PN format regexes (heuristic) ─────────────────────────────────────────────

_LCSC_PN = re.compile(r"C\d{2,9}", re.IGNORECASE)
# DigiKey catalogue PNs commonly end in -ND / -CT / -DKR / -TR. Require that
# suffix so we don't grab bare manufacturer parts.
_DIGIKEY_PN = re.compile(r"[A-Z0-9][A-Z0-9.\-]{2,40}-(?:ND|CT|DKR|TR)", re.IGNORECASE)
# Mouser: numeric vendor prefix, dash, then the manufacturer part.
_MOUSER_PN = re.compile(r"\d{2,4}-[A-Z0-9][A-Z0-9./\-]{2,40}", re.IGNORECASE)
# Pololu: bare numeric SKU (1-5 digits). Anchored so we don't match a stray
# digit inside a longer token; used with fullmatch in match_pn and as a
# standalone token via \b in line parsing.
_POLOLU_PN = re.compile(r"\b\d{1,5}\b")


# ── Profile registry ──────────────────────────────────────────────────────────

def _make_profiles() -> dict[str, DistributorProfile]:
    return {
        "generic": DistributorProfile(
            key="generic", pn_column=None, pn_re=None,
        ),
        "lcsc": DistributorProfile(
            key="lcsc", pn_column="LCSC Part Number", pn_re=_LCSC_PN,
        ),
        "digikey": DistributorProfile(
            key="digikey", pn_column="Digikey Part Number", pn_re=_DIGIKEY_PN,
        ),
        "mouser": DistributorProfile(
            key="mouser", pn_column="Mouser Part Number", pn_re=_MOUSER_PN,
        ),
        "pololu": DistributorProfile(
            key="pololu", pn_column="Pololu Part Number", pn_re=_POLOLU_PN,
        ),
    }


_PROFILES = _make_profiles()


def get_profile(template: str) -> DistributorProfile:
    """Return the profile for a template key. Raises ValueError if unknown."""
    prof = _PROFILES.get((template or "").strip().lower())
    if prof is None:
        raise ValueError(
            f"unknown distributor template {template!r}; "
            f"expected one of {sorted(_PROFILES)}"
        )
    return prof


def parse_with_template(template: str, text: str) -> list[dict[str, Any]]:
    """Parse OCR/PDF text using the given template profile.

    Returns a list of line-item dicts (see module docstring for shape). An empty
    list means the profile found nothing — the caller should fall back to the
    shared generic heuristic parser.
    """
    return get_profile(template).parse(text or "")


# Exposed so mfg_direct_import can list valid template keys without importing
# the private dict.
def template_keys() -> list[str]:
    return sorted(_PROFILES)
