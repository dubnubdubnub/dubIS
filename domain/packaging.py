"""Packaging-carrier classification — one vocabulary for all four distributors.

Every distributor names the same physical realities differently. DigiKey says
"Cut Tape (CT)" / "Tape & Reel (TR)" / "Digi-Reel®", Mouser says "Cut Tape" /
"Tape & Reel" / "MouseReel", LCSC publishes no name at all and only implies it
via a package quantity. Downstream code (purchase-quantity strategy, feeder
binding) needs to reason about *carriers*, not vendor prose, so the mapping
lives here rather than being re-guessed per client.

Two orthogonal questions, hence two functions:

``carrier_of``  -- how is the part physically carried? tape / tray / tube /
                   bulk. This is what decides whether a feeder can take it.
``is_reel``     -- is this specific packaging a whole reel rather than a cut
                   length off one? Only meaningful when the carrier is tape.
                   Custom-reeling services (Digi-Reel, MouseReel) count as
                   reels: the buyer receives a reel, just not a factory one.

Both are deliberately tolerant — an unrecognised name returns ``None`` /
``False`` rather than raising, because distributor packaging strings are
scraped and change without notice. Callers treat ``None`` as "unknown", never
as "bulk".
"""

from __future__ import annotations

from typing import Any

# Ordered longest-idea-first: "tape & reel" must beat the bare "tape" and
# "reel" substrings, and "cut tape" must not be read as a reel.
_TAPE_TOKENS = ("cut tape", "tape & reel", "tape and reel", "tape & box",
                "tape &amp; reel", "ammo", "reel", "tape", "digi-reel",
                "mousereel", "custom reel", "strip")
_TRAY_TOKENS = ("tray",)
_TUBE_TOKENS = ("tube", "stick")
_BULK_TOKENS = ("bulk", "bag", "box", "loose", "each")

# A cut length off a reel. Checked before the reel tokens so "cut tape" and
# "tape & box" are never mistaken for whole reels.
_CUT_TOKENS = ("cut tape", "cut-tape", "(ct)", "tape & box", "strip", "cut", "ammo")
_REEL_TOKENS = ("reel", "digi-reel", "digireel", "mousereel", "(tr)")


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def carrier_of(name: str | None) -> str | None:
    """Map a distributor packaging name to ``tape``/``tray``/``tube``/``bulk``.

    Returns ``None`` for an empty or unrecognised name.
    """
    n = _norm(name)
    if not n:
        return None
    # Tray/tube/bulk are checked first: they are unambiguous single words,
    # whereas the tape vocabulary is broad enough to catch stray matches.
    for token in _TRAY_TOKENS:
        if token in n:
            return "tray"
    for token in _TUBE_TOKENS:
        if token in n:
            return "tube"
    for token in _TAPE_TOKENS:
        if token in n:
            return "tape"
    for token in _BULK_TOKENS:
        if token in n:
            return "bulk"
    return None


def is_reel(name: str | None) -> bool:
    """True when this packaging delivers a whole reel (factory or custom-wound).

    ``False`` for cut tape, for non-tape carriers, and for unknown names --
    the conservative answer, since claiming a reel that isn't one would let a
    caller promise feeder-loadable stock it does not have.
    """
    n = _norm(name)
    if not n:
        return False
    if any(token in n for token in _CUT_TOKENS):
        return False
    return any(token in n for token in _REEL_TOKENS)


def clean_reel_qty(value: Any) -> int | None:
    """Coerce a scraped reel quantity to a positive int, else ``None``.

    Distributors variously report this as "3,000", 3000.0, "" or 0; a zero or
    unparseable value means "not published", which is ``None``, not 0 -- a 0
    would read downstream as "reels of zero parts".
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return None
    try:
        qty = int(float(value))
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def clean_reel_fee(value: Any) -> float | None:
    """Coerce a reeling surcharge to a positive float, else ``None``.

    LCSC reports 0 for parts it will not custom-reel; 0 and ``None`` mean the
    same thing to a caller pricing a reel option, so both collapse to ``None``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        if not value:
            return None
    try:
        fee = float(value)
    except (TypeError, ValueError):
        return None
    return fee if fee > 0 else None
