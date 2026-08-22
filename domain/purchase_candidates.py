"""Enumerate and rank the ways one part can actually be bought.

A price ladder answers "what does N cost". This module answers the question a
buyer actually has, which is "what are my options" -- it walks every
distributor x packaging ladder for one requirement and emits the quantities
that are genuinely purchasable, each with the spend it implies. A caller can
then rank them, show the runner-up, and say why the winner won.

``cart_qty.default_qty`` collapses all of that to a single integer, which is
the right answer for "just put something in the cart" and the wrong one for a
buyer comparing options: the losing candidates are exactly the information
needed to justify the winner.

Three rules, mirroring ``domain/predicates.py``:

* **A quantity the distributor never quoted has no price.** Below a ladder's
  lowest break we emit a ``Rejection``, never an extrapolated unit price.
  Guessing there is how you end up promising a 200-piece price for a part sold
  only in thousands.
* **Unknown is not permission.** Unknown stock is neither infinite nor zero.
  The candidate is emitted with ``stock_known=False`` so a caller can say so,
  rather than silently claiming an availability nobody observed.
* **The reel ceiling is a preference, not a filter.** It decides which reel the
  reel preset *prefers*; it never hides a reel that exists. A $90 reel still
  appears, flagged, because a ceiling is not a budget.

Pure: no I/O, no clock, no config lookups. Ladders arrive from
``cart_qty.tier_ladders``, preferences arrive as arguments.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from domain.predicates import DISTRIBUTOR_PREFERENCE

# ── Presets ──────────────────────────────────────────────────────────────────
# Named selections over one candidate list -- NOT separate computations. Each
# preset is a rule for picking a row; all of them see the same rows.

PRESET_MIN = "min"
"""Cheapest total spend that satisfies the requirement."""

PRESET_TIER_UP = "tier_up"
"""Cheapest spend at a price break strictly above the requirement."""

PRESET_REEL = "reel"
"""Cheapest reel-carried candidate, preferring those within the reel ceiling."""

PRESET_CUSTOM = "custom"
"""Caller-supplied quantity; see :func:`quote`."""

PRESETS = (PRESET_MIN, PRESET_TIER_UP, PRESET_REEL, PRESET_CUSTOM)

# ── Rejection reasons ────────────────────────────────────────────────────────

BELOW_MOQ = "below_moq"
NOT_MULTIPLE = "not_multiple"
INSUFFICIENT_STOCK = "insufficient_stock"
BELOW_LADDER = "below_ladder"
NO_LADDER = "no_ladder"
NON_POSITIVE = "non_positive"

# How many decimal places money is compared at. Ladder unit prices routinely
# carry 4-5 significant figures (0.00213/ea), so rounding for *comparison* has
# to be finer than rounding for display or two genuinely different candidates
# collapse into a tie and the tie-break picks arbitrarily.
_MONEY_DP = 6


@dataclass(frozen=True)
class Offer:
    """One distributor x packaging ladder, plus what constrains buying it.

    ``stock``, ``moq`` and ``multiple`` are ``None`` when unobserved, which is
    deliberately distinct from 0/1: an unknown MOQ must not read as "no MOQ"
    in a way that lets us quote an illegal quantity as if it were fine.

    ``multiple`` is the packaging increment -- a full-reel offer of 3,000 sells
    in 3,000s, so 3,500 is not a purchase. Values <= 1 impose nothing.
    """

    distributor: str
    packaging: str = ""
    carrier: str | None = None
    is_reel: bool = False
    ladder: tuple[tuple[int, float], ...] = ()
    stock: int | None = None
    moq: int | None = None
    multiple: int | None = None
    fee: float = 0.0
    """Flat per-order handling charge for this packaging (Digi-Reel, MouseReel)."""

    @property
    def breaks(self) -> tuple[int, ...]:
        return tuple(sorted(q for q, _ in self.ladder))


@dataclass(frozen=True)
class Candidate:
    """One purchasable (quantity, unit price) with its provenance."""

    distributor: str
    packaging: str
    carrier: str | None
    is_reel: bool
    qty: int
    unit_price: float
    fee: float
    spend: float
    break_qty: int
    """The ladder break whose price applies to ``qty``."""
    on_break: bool
    """True when ``qty`` is itself a published break, not a between-breaks quantity."""
    surplus: int
    stock_known: bool
    origin: str
    """``"required"``, ``"break"``, ``"multiple"`` or ``"custom"``."""

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.distributor, self.packaging, self.qty)


@dataclass(frozen=True)
class Rejection:
    """Why a requested quantity is not buyable, and the nearest one that is."""

    distributor: str
    packaging: str
    qty: int
    reason: str
    detail: str
    nearest_legal: int | None = None


@dataclass(frozen=True)
class Selection:
    """A preset's pick, plus the context that makes it defensible."""

    preset: str
    candidate: Candidate | None
    runner_up: Candidate | None = None
    reason: str = ""
    over_ceiling: bool = False
    """Reel preset only: nothing satisfied the ceiling, so this exceeds it."""
    fell_back: str = ""
    """Preset that actually supplied the pick, when the asked-for one had nothing."""
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)

    @property
    def spend(self) -> float:
        return self.candidate.spend if self.candidate else 0.0


# ── Pricing ──────────────────────────────────────────────────────────────────


def unit_price_at(ladder: Sequence[tuple[int, float]], qty: int) -> float | None:
    """Unit price the distributor's own ladder assigns to ``qty``.

    The applicable break is the highest one at or below ``qty`` -- buy 700
    against breaks of 1/100/500/1000 and you pay the 500 price, not the 1000
    price. Below the lowest break the answer is ``None``, not the lowest
    break's price: the distributor has not quoted that quantity and inventing a
    number there is how a 200-piece order gets promised reel economics.
    """
    if qty <= 0 or not ladder:
        return None
    applicable = [(q, u) for q, u in ladder if q <= qty]
    if not applicable:
        return None
    return max(applicable, key=lambda pair: pair[0])[1]


def _round_money(value: float) -> float:
    return round(value + 0.0, _MONEY_DP)


def _ceil_to(qty: int, multiple: int) -> int:
    return int(math.ceil(qty / float(multiple))) * multiple


def _constrains(value: int | None) -> bool:
    """A multiple only constrains when it is known and greater than one."""
    return isinstance(value, int) and value > 1


def _legality(offer: Offer, qty: int) -> Rejection | None:
    """The first constraint ``qty`` violates for ``offer``, or None if legal.

    Ordered most-actionable-first: a 700-piece ask against a 3,000-piece reel
    is both "not a multiple" and "below the ladder", and only the former comes
    with a number the user can act on.
    """
    if qty <= 0:
        return Rejection(offer.distributor, offer.packaging, qty, NON_POSITIVE,
                         "quantity must be at least 1", None)
    if not offer.ladder:
        return Rejection(offer.distributor, offer.packaging, qty, NO_LADDER,
                         f"no observed prices for {offer.distributor}", None)
    if isinstance(offer.moq, int) and offer.moq > 0 and qty < offer.moq:
        return Rejection(offer.distributor, offer.packaging, qty, BELOW_MOQ,
                         f"minimum order quantity is {offer.moq:,}", offer.moq)
    if _constrains(offer.multiple) and qty % offer.multiple:
        assert offer.multiple is not None
        return Rejection(offer.distributor, offer.packaging, qty, NOT_MULTIPLE,
                         f"sold in multiples of {offer.multiple:,}",
                         _ceil_to(qty, offer.multiple))
    if isinstance(offer.stock, int) and qty > offer.stock:
        # Deliberately no nearest_legal: buying *more* cannot fix this, and
        # suggesting the stock figure invites ordering the shelf bare on a
        # number that was already stale when we recorded it.
        return Rejection(offer.distributor, offer.packaging, qty, INSUFFICIENT_STOCK,
                         f"only {offer.stock:,} in stock", None)
    if unit_price_at(offer.ladder, qty) is None:
        low = offer.breaks[0]
        return Rejection(offer.distributor, offer.packaging, qty, BELOW_LADDER,
                         f"no price quoted below {low:,}", low)
    return None


def _make(offer: Offer, qty: int, required: int, origin: str) -> Candidate:
    unit = unit_price_at(offer.ladder, qty)
    assert unit is not None, "caller must check _legality first"
    applicable = max(q for q, _ in offer.ladder if q <= qty)
    return Candidate(
        distributor=offer.distributor,
        packaging=offer.packaging,
        carrier=offer.carrier,
        is_reel=offer.is_reel,
        qty=qty,
        unit_price=unit,
        fee=offer.fee,
        spend=_round_money(qty * unit + offer.fee),
        break_qty=applicable,
        on_break=qty in offer.breaks,
        surplus=qty - required,
        stock_known=isinstance(offer.stock, int),
        origin=origin,
    )


# ── Enumeration ──────────────────────────────────────────────────────────────


def quote(offer: Offer, qty: int, required: int = 0) -> Candidate | Rejection:
    """Price one explicit quantity against one offer -- the custom-qty path.

    Returns a ``Rejection`` rather than a rounded-up ``Candidate`` when the
    quantity is not buyable. Silently rounding a custom quantity up to the next
    legal one spends the user's money on a number they did not type.
    """
    problem = _legality(offer, qty)
    if problem is not None:
        return problem
    return _make(offer, qty, required, PRESET_CUSTOM)


def enumerate_candidates(
    offers: Sequence[Offer], required: int
) -> tuple[list[Candidate], list[Rejection]]:
    """Every purchasable quantity that covers ``required``, across all offers.

    Per offer that is the requirement itself, each published break at or above
    it, and -- where the packaging sells in fixed increments -- the smallest
    whole number of those increments that covers it. Rejections are returned
    alongside rather than dropped, so a part with no viable option can explain
    itself instead of rendering an empty row.
    """
    if required <= 0:
        raise ValueError(f"required must be positive, got {required}")

    candidates: dict[tuple[str, str, int], Candidate] = {}
    rejections: list[Rejection] = []

    for offer in offers:
        if not offer.ladder:
            # One rejection per offer, not one per quantity we would have tried:
            # "no observed prices" is a fact about the offer, and repeating it
            # per candidate quantity buries the offers that failed for a
            # reason the user can actually act on.
            rejections.append(Rejection(
                offer.distributor, offer.packaging, required, NO_LADDER,
                f"no observed prices for {offer.distributor}", None))
            continue

        wanted: list[tuple[int, str]] = [(required, "required")]
        wanted += [(q, "break") for q in offer.breaks if q >= required]
        if _constrains(offer.multiple):
            assert offer.multiple is not None
            wanted.append((_ceil_to(required, offer.multiple), "multiple"))

        seen_here: set[int] = set()
        for qty, origin in wanted:
            if qty in seen_here:
                continue
            seen_here.add(qty)
            problem = _legality(offer, qty)
            if problem is not None:
                rejections.append(problem)
                continue
            candidate = _make(offer, qty, required, origin)
            # First origin wins: "required" and "break" can name the same
            # quantity when the requirement lands exactly on a break, and
            # "required" is the more useful label for a buyer.
            candidates.setdefault(candidate.key, candidate)

    return rank(candidates.values()), rejections


# ── Ranking and selection ────────────────────────────────────────────────────


def _distributor_rank(name: str) -> tuple[int, str]:
    lowered = (name or "").strip().lower()
    try:
        return (DISTRIBUTOR_PREFERENCE.index(lowered), lowered)
    except ValueError:
        return (len(DISTRIBUTOR_PREFERENCE), lowered)


def _order(candidate: Candidate) -> tuple:
    """Cheapest first; ties broken toward less surplus, then a stable order.

    Surplus before distributor matters: two distributors quoting the same spend
    for 1,000 and 5,000 pieces are not equivalent offers, and the one that
    leaves 4,000 spare parts on the shelf should not win on alphabetical luck.
    """
    return (
        round(candidate.spend, _MONEY_DP),
        candidate.surplus,
        _distributor_rank(candidate.distributor),
        candidate.packaging.casefold(),
    )


def rank(candidates) -> list[Candidate]:
    """Candidates cheapest-first. Total and deterministic -- no arbitrary ties."""
    return sorted(candidates, key=_order)


def select(
    candidates: Sequence[Candidate],
    preset: str,
    *,
    required: int,
    reel_ceiling: float | None = None,
) -> Selection:
    """Apply one preset to an already-enumerated candidate list.

    ``reel_ceiling`` is consulted only by :data:`PRESET_REEL`, and only to
    order preferences within it: when no reel fits the ceiling the cheapest
    reel is still returned, with ``over_ceiling`` set. Hiding it would answer
    "buy a reel" with "there are no reels", which is false.
    """
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; expected one of {PRESETS}")
    if preset == PRESET_CUSTOM:
        raise ValueError("PRESET_CUSTOM has no automatic selection; call quote()")

    ordered = rank(candidates)
    if not ordered:
        return Selection(preset=preset, candidate=None,
                         reason="no purchasable quantity covers the requirement")

    if preset == PRESET_MIN:
        return Selection(preset=preset, candidate=ordered[0],
                         runner_up=ordered[1] if len(ordered) > 1 else None,
                         reason="lowest total spend")

    if preset == PRESET_TIER_UP:
        up = [c for c in ordered if c.qty > required and c.on_break]
        if not up:
            return Selection(preset=preset, candidate=ordered[0],
                             runner_up=ordered[1] if len(ordered) > 1 else None,
                             reason="no price break above the requirement",
                             fell_back=PRESET_MIN)
        return Selection(preset=preset, candidate=up[0],
                         runner_up=up[1] if len(up) > 1 else None,
                         reason="cheapest break above the requirement")

    reels = [c for c in ordered if c.is_reel]
    if not reels:
        return Selection(preset=preset, candidate=ordered[0],
                         runner_up=ordered[1] if len(ordered) > 1 else None,
                         reason="no reel packaging offered for this part",
                         fell_back=PRESET_MIN)
    within = ([c for c in reels if c.spend <= reel_ceiling]
              if reel_ceiling is not None else reels)
    if within:
        return Selection(preset=preset, candidate=within[0],
                         runner_up=within[1] if len(within) > 1 else None,
                         reason="cheapest reel within the ceiling")
    return Selection(preset=preset, candidate=reels[0],
                     runner_up=reels[1] if len(reels) > 1 else None,
                     reason="cheapest reel available; exceeds the ceiling",
                     over_ceiling=True)


# ── Building offers from stored observations ─────────────────────────────────

REELING_SUFFIX = " + reeling"
"""Marks a packaging derived by paying a distributor to reel a cut-tape buy."""


def _reel_multiple(
    is_reel: bool, reel_qty: Any, ladder: tuple[tuple[int, float], ...]
) -> int | None:
    """The reel size as an order multiple, when the ladder does not contradict it.

    See ``offers_from_ladders``. A quoted break below the reel size is the
    vendor saying that quantity is purchasable, which a whole-reel multiple
    would deny.
    """
    if not is_reel or not reel_qty:
        return None
    qty = int(reel_qty)
    if qty <= 0:
        return None
    return qty if ladder[0][0] >= qty else None


def offers_from_ladders(
    groups: dict[str, dict],
    distributor: str,
    *,
    stock: int | None = None,
) -> list[Offer]:
    """Turn one ``cart_qty.tier_ladders`` result into purchasable offers.

    Mostly one offer per stored packaging, with two deliberate asymmetries.

    ``reel_qty`` becomes a purchase ``multiple`` only for packagings that are
    already reels. On a cut-tape ladder it is describing the reel you *could*
    order, not a constraint on cutting tape -- reading it as a multiple there
    would reject every ordinary cut-tape quantity for a part whose vendor
    happens to mention its reel size.

    Even on a reel it is only a multiple when the ladder agrees. A vendor that
    publishes a price for 20 pieces of a part it calls "Reel" (LCSC labels its
    single packaging that way and quotes from 20 up, with the 10,000 reel as
    just another break) is telling us plainly that 20 is buyable. Taking the
    reel size as a multiple there rejects the vendor's own quoted quantities
    and makes the cheapest way to cover a need of 20 a full 10,000-piece reel
    -- an answer that is not merely suboptimal but contradicts the quote it was
    derived from. So the multiple is dropped whenever the ladder starts below
    it, and kept when the ladder starts at the reel (DigiKey's Tape & Reel,
    whose first break IS the reel, still cannot be bought in part).

    A stored ``reel_fee`` on a non-reel packaging is what makes a part-reel
    buyable, so it yields a second, derived offer: the same ladder, any
    quantity, plus the fee, carried on a reel. Without it ``PRESET_REEL``
    cannot express "reel this for me" for the many parts whose vendor publishes
    only a cut-tape ladder, and a paid-for half reel would be invisible.

    ``stock`` is a caller-supplied override because price observations do not
    record it. Left as None every candidate reports ``stock_known=False``,
    which is honest -- claiming availability from a ladder would be inventing
    it.
    """
    offers: list[Offer] = []
    for group in groups.values():
        ladder = tuple((int(q), float(u)) for q, u in group.get("ladder") or ())
        if not ladder:
            continue
        name = group.get("name") or ""
        is_reel = bool(group.get("is_reel"))
        reel_qty = group.get("reel_qty")
        reel_fee = group.get("reel_fee")

        offers.append(Offer(
            distributor=distributor,
            packaging=name,
            carrier=group.get("carrier"),
            is_reel=is_reel,
            ladder=ladder,
            stock=stock,
            multiple=_reel_multiple(is_reel, reel_qty, ladder),
            fee=0.0,
        ))

        if not is_reel and isinstance(reel_fee, (int, float)) and reel_fee > 0:
            offers.append(Offer(
                distributor=distributor,
                packaging=(name + REELING_SUFFIX) if name else "reeled",
                carrier=group.get("carrier"),
                is_reel=True,
                ladder=ladder,
                stock=stock,
                multiple=None,
                fee=float(reel_fee),
            ))
    return offers
