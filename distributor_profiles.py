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
# price is currency-like (optionally currency-prefixed). The decimal portion is
# OPTIONAL so whole-dollar prices ("... 3 6") and OCR that drops the decimal
# point still capture instead of silently dropping the whole line. This lets us
# pull the two rightmost numbers off a noisy invoice line.
_TRAILING_QTY_PRICE = re.compile(
    r"(?P<qty>\d{1,6})\s+(?P<price>\$?\s*\d+(?:\.\d{1,4})?)\s*$"
)
# An MPN-like token (same shape as mfg_direct_import._LINE_RE group 1).
_MPN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+()\-]{1,40}")

# ── Generic free-form heuristic ───────────────────────────────────────────────
# Manufacturer invoices vary wildly and have no distributor PN to anchor on, so
# the generic path is a best-effort heuristic: the user reviews/edits every row
# in the OCR modal, so we prefer NO extraction over a confidently-wrong one.
#
# Trailing "<int qty> <unit price>" region anchored to end-of-line. Unlike the
# legacy MPN-first regex this tolerates the free-form noise real manufacturer
# invoices carry:
#   * the price may be an integer ("... 1000 6") OR decimal ("... 4000 0.0220"),
#     optionally prefixed with a currency symbol ("$3.10");
#   * "Qty"/"Unit Price"/"Price"/"Each" label words may sit immediately before
#     either number (OCR/template noise) and are skipped.
# We grab the two RIGHTMOST numbers so a leading line-index ("1 ...") or label
# text in the head never gets mistaken for qty/price.
_GENERIC_LABEL = r"(?:qty|quantity|unit\s*price|price|each|ea|@|x)?\s*[:.]?\s*"
_GENERIC_QTY_PRICE = re.compile(
    r"(?:^|\s)" + _GENERIC_LABEL + r"(?P<qty>\d{1,7})\s+"
    + _GENERIC_LABEL + r"(?P<price>\$?\s*\d+(?:\.\d{1,4})?)\s*$",
    re.IGNORECASE,
)
# Leading line-index number to strip ("1 ", "12 "), so it isn't taken as the MPN.
_GENERIC_LEADING_INDEX = re.compile(r"^\s*\d{1,3}\s+")
# Common label words that prefix an MPN ("Part:", "P/N", "MPN", "Item") and must
# not themselves be matched as the MPN. Stripped from the head before MPN search.
_GENERIC_HEAD_LABEL = re.compile(
    r"^\s*(?:part|p/?n|mpn|item|no|#|description|desc)\b\s*[:.#]?\s*",
    re.IGNORECASE,
)
# An MPN-like token for the generic path: must contain a digit OR a hyphen so we
# never confuse a plain alphabetic word ("Part", "Green", "Resistor") for a PN.
_GENERIC_MPN_RE = re.compile(
    r"(?P<mpn>[A-Za-z0-9][A-Za-z0-9._/+()\-]{2,40})"
)
# A capitalized alphabetic word (best-effort manufacturer following the MPN).
_GENERIC_MFR_RE = re.compile(r"\s+([A-Z][A-Za-z&.\-]{1,30})\b")


def _looks_like_mpn(token: str) -> bool:
    """A generic MPN candidate must contain a digit or a hyphen (so plain words
    like 'Part' / 'Green' are rejected) — we prefer no match over a wrong one."""
    return bool(re.search(r"\d", token)) or "-" in token


def _parse_generic_line(line: str) -> dict[str, Any] | None:
    """Heuristic free-form line parser for the ``generic`` template.

    Strategy (documented, intentionally conservative):
      1. Find the trailing ``<int qty> <price>`` region (label/currency noise
         tolerated, integer prices allowed). No region -> no row.
      2. Strip a leading line-index number and any leading label word ("Part:")
         from the head.
      3. Take the first MPN-like token (must contain a digit or hyphen) as the
         MPN. No MPN-like token -> no row.
      4. If a capitalized alphabetic word follows the MPN, capture it as the
         manufacturer (best-effort; OK to leave blank).
    """
    qp = _GENERIC_QTY_PRICE.search(line)
    if not qp:
        return None
    qty = _to_qty(qp.group("qty"))
    price = _to_price(qp.group("price"))

    head = line[: qp.start()]
    head = _GENERIC_LEADING_INDEX.sub("", head)
    head = _GENERIC_HEAD_LABEL.sub("", head)

    mtok = _GENERIC_MPN_RE.search(head)
    while mtok and not _looks_like_mpn(mtok.group("mpn")):
        # Skip a non-PN-looking token (e.g. a residual label) and keep scanning.
        nxt = _GENERIC_MPN_RE.search(head, mtok.end())
        if nxt is None:
            mtok = None
            break
        mtok = nxt
    if not mtok:
        return None
    mpn = mtok.group("mpn").strip()

    manufacturer = ""
    mfr = _GENERIC_MFR_RE.match(head, mtok.end())
    if mfr:
        manufacturer = mfr.group(1).strip()

    return {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "package": "",
        "quantity": qty,
        "unit_price": price,
        "distributor": "generic",
        "distributor_pn": "",
    }


def _parse_generic(text: str) -> list[dict[str, Any]]:
    """Parse all lines of free-form text for the generic template, falling back
    to the legacy MPN-first heuristic for any line the free-form parser can't
    handle (so behaviour never regresses vs. the original generic pipeline)."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = _parse_generic_line(line)
        if row is not None:
            rows.append(row)
            continue
        # Fall back to the legacy MPN-first parser for this single line so any
        # row the original generic pipeline extracted still extracts.
        for lrow in _heuristic_parse_lines(line):
            rows.append(
                {**lrow, "distributor": "generic", "distributor_pn": ""}
            )
    return rows


@dataclass
class DistributorProfile:
    """Heuristic field extractor for one distributor template."""

    key: str
    pn_column: str | None
    pn_re: re.Pattern[str] | None
    # Optional line-parsing override. When set, ``_parse_line`` uses this to
    # locate the PN in the line head (and reads capture group 1, if present)
    # instead of ``pn_re``. ``match_pn`` always uses ``pn_re`` (token validation).
    pn_line_re: re.Pattern[str] | None = None

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
            rows = _parse_generic(text)
            # Rescue a DigiKey packing list dropped on the default "generic"
            # template: its labels/PNs are unmistakable, so extract them rather
            # than autofilling nothing.
            if not rows and _looks_like_digikey_packlist(text):
                return _parse_digikey_packlist(text)
            return rows
        # DigiKey packing lists / certificates of compliance are label-prefixed
        # (PART:/MFG#:/MFG:/QTY:) with no prices — the per-line tabular parser
        # finds nothing. Detect that layout by its labels and extract column-wise.
        if self.key == "digikey" and _DK_PACKLIST_MARKER.search(text):
            rows = _parse_digikey_packlist(text)
            if rows:
                return rows
            # Labels present but nothing extracted — fall through to the line
            # parser (e.g. an invoice that merely mentions "PART:" in prose).
        items: list[dict[str, Any]] = []
        for line in text.splitlines():
            item = self._parse_line(line)
            if item is not None:
                items.append(item)
        return items

    def _parse_line(self, line: str) -> dict[str, Any] | None:
        """Parse one noisy OCR line: take the trailing two numeric tokens as
        qty + unit price, then locate this distributor's PN in the HEAD of the
        line (before that trailing region) and the token after the PN as the
        MPN (best-effort).

        Searching the PN only in the head matters for bare-numeric SKUs (Pololu):
        a whole-line ``search`` would otherwise grab a leading year / order number
        or the trailing qty as the SKU. Constraining to the head and excluding the
        qty/price span makes a wrong match much less likely — and we prefer NO
        match over a confidently-wrong one, since the user reviews each row."""
        if self.pn_re is None:
            return None

        # Trailing qty + unit price: the last two number-like tokens. Locate this
        # first so the PN search can exclude it (otherwise a bare-numeric SKU
        # regex matches the qty).
        price_m = _TRAILING_QTY_PRICE.search(line)
        if not price_m:
            return None
        qty = _to_qty(price_m.group("qty"))
        price = _to_price(price_m.group("price"))

        # Search for the distributor PN only in the head of the line, before the
        # trailing qty/price region.
        head = line[: price_m.start()]
        line_re = self.pn_line_re or self.pn_re
        pn_match = line_re.search(head)
        if not pn_match:
            return None
        # If the line regex captures the PN in group 1 (e.g. Pololu strips
        # leading whitespace), prefer it; otherwise use the whole match.
        pn = (pn_match.group(1) if pn_match.lastindex else pn_match.group(0)).strip()
        if self.key == "lcsc":
            pn = pn.upper()

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
# Pololu: bare numeric SKU (1-5 digits). A bare numeric SKU is dangerously easy
# to confuse with a year, order number, or quantity, so line parsing only trusts
# it at the START of the line's head region (Pololu invoices lead each row with
# the item number) — see ``_POLOLU_LINE_PN``. ``match_pn`` (token validation,
# used by callers that already isolated a token) keeps the loose 1-5 digit shape.
_POLOLU_PN = re.compile(r"\d{1,5}")
# Line-parsing variant: anchored to the start of the head (optional leading
# whitespace), and the token must be a standalone 1-5 digit run (so an 8-digit
# leading token like "12345678" does NOT match a 5-digit prefix of it). Prefers
# NO match over grabbing a leading year / order number further into the line.
_POLOLU_LINE_PN = re.compile(r"^\s*(\d{1,5})\b")


# ── DigiKey packing-list / certificate-of-compliance (label-prefixed) ─────────
# A DigiKey packing list is NOT a tabular invoice: it carries no prices and lays
# each field on its own labelled line (PART:/MFG#:/MFG:/QTY:). Photographed scans
# also scramble word order within a line ("490-9961-1-ND PART:"), so we can't rely
# on line structure. Instead we collect each field type across the whole document
# in order of appearance and zip them into rows (item N = Nth PN + Nth MFG# + …).
# We prefer leaving a field blank over a confidently-wrong value — the user
# reviews and drag-corrects every row in the OCR overlay before import.

# Detects the packing-list layout: an "MFG#" or a "PART:" label anywhere.
_DK_PACKLIST_MARKER = re.compile(r"MFG\s*#|PART\s*:", re.IGNORECASE)
# "MFG#:" (M or N — OCR misreads 'M' as 'N') → first token after it is the MPN.
_DK_MFG_NUM = re.compile(
    r"[MN]FG\s*#\s*:?\s*([A-Za-z0-9][A-Za-z0-9._/+\-]{1,40})", re.IGNORECASE)
# "MFG :" / "NFG :" (a colon NOT preceded by '#') introduces the manufacturer
# name; capture the rest of that line. The negative-lookbehind-free "[MN]FG\s*:"
# can't match "MFG#:" because '#' is neither whitespace nor ':'.
_DK_MFG_NAME = re.compile(r"(?:^|\s)[MN]FG\s*:\s*([^\n]+)", re.IGNORECASE)
# "QTY:" then (optionally past a "LOT" word) the integer quantity on the SAME line.
_DK_QTY = re.compile(r"QTY\s*:[^\d\n]*(\d+)", re.IGNORECASE)


def _looks_like_digikey_packlist(text: str) -> bool:
    """True only for the unmistakable DigiKey packing-list shape: an ``MFG#``
    label AND at least one DigiKey-format (``-ND``/``-CT``/…) part number. Used to
    rescue autofill when the user left the template on "generic". Deliberately
    strict so ordinary generic invoices are never hijacked."""
    return bool(re.search(r"MFG\s*#", text, re.IGNORECASE)) and bool(_DIGIKEY_PN.search(text))


def _parse_digikey_packlist(text: str) -> list[dict[str, Any]]:
    """Column-wise extractor for a DigiKey packing list (see notes above).

    Anchors on the DigiKey part numbers (one per line item) and zips the other
    label columns by appearance order. Quantity comes from the "QTY:" label;
    unit price is always 0.0 (packing lists have no prices).
    """
    pns = list(dict.fromkeys(_DIGIKEY_PN.findall(text)))
    mpns = _DK_MFG_NUM.findall(text)
    names = [m.strip() for m in _DK_MFG_NAME.findall(text)]
    qtys = _DK_QTY.findall(text)
    rows: list[dict[str, Any]] = []
    for i, pn in enumerate(pns):
        rows.append({
            "mpn": mpns[i].strip() if i < len(mpns) else "",
            "manufacturer": names[i] if i < len(names) else "",
            "package": "",
            "quantity": _to_qty(qtys[i]) if i < len(qtys) else 0,
            "unit_price": 0.0,
            "distributor": "digikey",
            "distributor_pn": pn,
        })
    return rows


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
            pn_line_re=_POLOLU_LINE_PN,
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
