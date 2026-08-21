"""Default purchase-quantity computation for cart items.

Reconstructs the price-break ladder from events/price_observations.csv and
applies the cost-stepping rule (see docs/superpowers/specs/2026-07-24-cart-feature-design.md).

A part+distributor has as many ladders as the distributor publishes packagings
for it: DigiKey quotes Cut Tape and Tape & Reel separately, and those are
different ladders that happen to share a part number. Grouping is therefore by
packaging first and price break second -- keying on the break alone silently
overwrites a cut-tape break with the reel break of the same quantity, and a
reel ladder's breaks start at the reel quantity, so picking the wrong ladder
can answer a 1,600-part shortfall with a whole 3,000-part reel.
"""
from __future__ import annotations

import csv
import math
import os
from typing import Any

from domain.packaging import carrier_of, is_reel

# The group key for observations with no packaging recorded: rows written
# before the packaging columns existed, and distributors that publish a single
# unlabelled ladder. Deliberately falsy so "unknown" is never mistaken for a
# real packaging, and deliberately its OWN group so unknown rows neither
# absorb nor get absorbed by a named ladder.
UNKNOWN_PACKAGING = ""


def _round_up_10(n: int) -> int:
    return int(math.ceil(n / 10.0) * 10)


def _row_is_reel(row: dict[str, Any], name: str) -> bool | None:
    """Reel-ness of one observation row: stored column first, else derived.

    None means unknown -- which is NOT the same as False. A row with no
    packaging at all is unknown; a row whose name simply isn't a reel is False.
    """
    raw = (row.get("is_reel") or "").strip()
    if raw:
        return raw not in ("0", "false", "False")
    return is_reel(name) if name else None


def _row_reel_qty(row: dict[str, Any]) -> int | None:
    """The packet/reel quantity this packaging is sold in, or None if unknown.

    Never 0: a reel of zero parts is not a thing, so a stored 0 is treated as
    "not published" rather than as a real multiple that would reject every
    quantity.
    """
    raw = (row.get("reel_qty") or "").strip()
    if not raw:
        return None
    try:
        qty = int(float(raw))
    except ValueError:
        return None
    return qty if qty > 0 else None


def _row_reel_fee(row: dict[str, Any]) -> float | None:
    """The custom-reeling surcharge, or None if the distributor publishes none.

    0.0 is kept as a real value distinct from None -- a distributor that reels
    for free has said something, and it is not "unknown".
    """
    raw = (row.get("reel_fee") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _row_carrier(row: dict[str, Any], name: str) -> str | None:
    carrier = (row.get("carrier") or "").strip().lower()
    if carrier:
        return carrier
    return carrier_of(name) if name else None


def tier_ladders(
    events_dir: str, part_id: str, distributor: str
) -> dict[str, dict[str, Any]]:
    """Every price-break ladder for one part+distributor, keyed by packaging.

    The key is the distributor's packaging name, casefolded, or
    ``UNKNOWN_PACKAGING`` for observations that carry no packaging. Within one
    group the latest observation per price break wins, exactly as before --
    which is why a file of legacy rows (all in the one unknown group) yields
    byte-identical ladders to the pre-packaging implementation.

    Each value is ``{"name", "carrier", "is_reel", "reel_qty", "reel_fee",
    "latest_ts", "ladder"}``, where every descriptive field comes from that
    group's most recent row. ``reel_qty``/``reel_fee`` are None when the
    distributor publishes neither -- which is not the same as a reel of 0 parts
    or a free reeling service; see ``domain.purchase_candidates``, which turns
    these groups into purchasable offers.
    """
    path = os.path.join(events_dir, "price_observations.csv")
    if not os.path.exists(path):
        return {}
    groups: dict[str, dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("part_id") != part_id or row.get("distributor") != distributor:
                continue
            moq_raw = (row.get("moq") or "").strip()
            price_raw = (row.get("unit_price") or "").strip()
            if not moq_raw or not price_raw:
                continue
            try:
                moq = int(float(moq_raw))
                price = float(price_raw)
            except ValueError:
                continue
            ts = row.get("timestamp", "")
            name = (row.get("packaging") or "").strip()
            key = name.casefold() if name else UNKNOWN_PACKAGING
            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                    "name": name,
                    "carrier": _row_carrier(row, name),
                    "is_reel": _row_is_reel(row, name),
                    "reel_qty": _row_reel_qty(row),
                    "reel_fee": _row_reel_fee(row),
                    "latest_ts": ts,
                    "_breaks": {},
                }
            breaks = group["_breaks"]
            if moq not in breaks or ts >= breaks[moq][0]:
                breaks[moq] = (ts, price)
            if ts >= group["latest_ts"]:
                group["latest_ts"] = ts
                group["name"] = name
                group["carrier"] = _row_carrier(row, name)
                group["is_reel"] = _row_is_reel(row, name)
                group["reel_qty"] = _row_reel_qty(row)
                group["reel_fee"] = _row_reel_fee(row)

    for group in groups.values():
        breaks = group.pop("_breaks")
        group["ladder"] = sorted((q, p) for q, (_ts, p) in breaks.items())
    return groups


def _pick(groups: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """Most recently observed of several candidate groups (name breaks ties)."""
    if not groups:
        return []
    best = max(groups, key=lambda g: (g["latest_ts"], g["name"].casefold()))
    return best["ladder"]


def tier_ladder(
    events_dir: str,
    part_id: str,
    distributor: str,
    packaging: str | None = None,
) -> list[tuple[int, float]]:
    """The price-break ladder for one part+distributor+packaging.

    `packaging` is additive and optional so every existing call site keeps its
    signature. It takes the distributor's packaging name ("Cut Tape (CT)"),
    matched case-insensitively; failing an exact match it falls back to
    matching by normalized carrier + reel-ness, so a caller can pass the
    generic "cut tape" or "tape & reel" and still hit the vendor's own prose.
    An explicit "" asks for the unknown-packaging ladder specifically. No
    matching ladder returns [] rather than quietly substituting another
    packaging's prices.

    With `packaging` omitted the ladder is chosen, not merged: the most
    recently observed packaging wins, but a known reel ladder is only ever
    chosen when it is the *only* thing on offer. A reel ladder's lowest break
    is the reel quantity, so handing one to `default_qty` for an unspecified
    packaging buys a whole reel nobody asked for. A file of legacy rows has
    exactly one (unknown) group, so this reduces to returning it -- the
    pre-packaging behaviour, unchanged.
    """
    groups = tier_ladders(events_dir, part_id, distributor)
    if not groups:
        return []

    if packaging is None:
        not_reel = [g for g in groups.values() if g["is_reel"] is not True]
        return _pick(not_reel or list(groups.values()))

    wanted = packaging.strip()
    exact = groups.get(wanted.casefold())
    if exact is not None:
        return exact["ladder"]
    if not wanted:
        return []
    want_carrier, want_reel = carrier_of(wanted), is_reel(wanted)
    if want_carrier is None:
        return []
    return _pick([
        g for g in groups.values()
        if g["carrier"] == want_carrier and bool(g["is_reel"]) == want_reel
    ])


def default_qty(shortfall: int | None, ladder: list[tuple[int, float]]) -> int:
    has_shortfall = isinstance(shortfall, int) and shortfall > 0
    base = shortfall if has_shortfall else 1
    if not ladder:
        return base
    breaks = [q for q, _ in ladder]
    if has_shortfall:
        candidates = [q for q in breaks if q >= base]
        if not candidates:
            return max(breaks)
        step = min(candidates)
        return step if step <= 2 * base else _round_up_10(base)
    # no shortfall: lowest break, unless its extended price > $30 -> 5
    low_q, low_price = min(ladder)
    return 5 if low_q * low_price > 30 else low_q
