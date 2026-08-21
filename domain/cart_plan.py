"""Turn a cart into a purchase plan: what each line needs, and what to buy.

The arithmetic that makes a cart's board count mean something lives here. A
line's requirement is ``per_board_qty x board_count - on_hand``, so a quantity
stays accountable -- "25 boards x 8 placements, less 112 on hand" -- instead of
being a bare 5,000 nobody can reconstruct once the BOM has moved on.

Lookups arrive injected (``offers_for``, ``on_hand_for``) so the whole plan is
testable without a database, a server, or a CSV on disk. The ranking itself
lives in ``domain/purchase_candidates.py``; this module decides *what each line
needs* and hands that over.

On-hand stock deliberately reduces the requirement to zero rather than being a
separate "use my stock" toggle. A line already covered by inventory is not a
purchase, and modelling it as one -- then subtracting it again at the bottom --
is how a cart total ends up disagreeing with the sum of its rows.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Sequence

from domain.purchase_candidates import (
    PRESET_CUSTOM,
    PRESET_MIN,
    PRESETS,
    Candidate,
    Offer,
    Rejection,
    enumerate_candidates,
    quote,
    select,
)


def requirement(
    per_board_qty: int | None, board_count: int, on_hand: int | None, fallback_qty: int
) -> tuple[int, int, int]:
    """``(gross, covered, net)`` for one line.

    ``gross`` is what the boards consume, ``covered`` how much of that inventory
    already answers, ``net`` what must actually be bought.

    A line with no ``per_board_qty`` is not a per-board line -- a one-off tool,
    a spare, something typed in by hand -- so the board count does not scale it
    and its own quantity stands as the requirement. Silently multiplying it
    would turn "one programmer" into twenty-five.
    """
    boards = max(1, int(board_count or 1))
    if per_board_qty is None or per_board_qty <= 0:
        gross = max(0, int(fallback_qty or 0))
    else:
        gross = int(per_board_qty) * boards
    have = max(0, int(on_hand or 0))
    covered = min(gross, have)
    return gross, covered, gross - covered


def _serialize(value: Candidate | Rejection | None) -> dict[str, Any] | None:
    return None if value is None else asdict(value)


def _effective_preset(item: dict[str, Any], default_preset: str) -> str:
    """The row's own preset, else the cart's default.

    An unrecognised stored preset falls back rather than raising: presets are
    written by clients and read back long afterwards, and one stale string
    should degrade a single row's *recommendation*, not make the whole cart
    unloadable.
    """
    raw = (item.get("preset") or "").strip()
    if raw in PRESETS:
        return raw
    return default_preset if default_preset in PRESETS else PRESET_MIN


def _custom_line(
    offers: Sequence[Offer], item: dict[str, Any], net: int
) -> tuple[Candidate | None, Rejection | None]:
    """Price the quantity the user typed, against the packaging they chose.

    With no packaging pinned this stays ambiguous on purpose -- several
    packagings can quote the same quantity at different prices, and picking one
    silently would attribute a price to a choice nobody made.
    """
    wanted = (item.get("target_packaging") or "").strip()
    qty = int(item.get("qty") or 0)
    distributor = item.get("target_distributor") or ""

    if wanted:
        matching = [o for o in offers if o.packaging == wanted]
        reason, detail = "no_such_packaging", f"no observed prices for packaging {wanted!r}"
    else:
        # No packaging pinned. One offer is unambiguous; several are not, and
        # choosing for the user would attribute a price to a decision nobody
        # made -- cut tape and a reel of the same part quote the same quantity
        # at very different money.
        matching = list(offers)
        reason, detail = "packaging_required", (
            "several packagings quote this quantity at different prices; pick one"
            if len(matching) > 1 else
            "a custom quantity needs a packaging to price it against")
        if len(matching) > 1:
            matching = []

    if not matching:
        return None, Rejection(
            distributor=distributor, packaging=wanted, qty=qty,
            reason=reason, detail=detail, nearest_legal=None,
        )
    priced = quote(matching[0], qty, net)
    if isinstance(priced, Rejection):
        return None, priced
    return priced, None


def plan_cart(
    cart: dict[str, Any],
    *,
    offers_for: Callable[[str, str | None], list[Offer]],
    on_hand_for: Callable[[str], int | None],
    default_preset: str = PRESET_MIN,
    reel_ceiling: float | None = None,
) -> dict[str, Any]:
    """A per-line purchase plan for one cart, plus its totals.

    Every line reports the whole candidate list, not just the winner: the losing
    options are exactly what justifies the pick, and a row that cannot show its
    runner-up cannot be argued with.
    """
    if default_preset not in PRESETS:
        # An unrecognised preset arriving from a *caller* is rejected, while an
        # unrecognised one already stored on a row degrades that row (see
        # _effective_preset). The asymmetry is deliberate: a caller can fix
        # their request, and quietly planning the whole cart under a different
        # rule than the one asked for is worse than saying no.
        raise ValueError(f"unknown preset {default_preset!r}; expected one of {PRESETS}")

    board_count = max(1, int(cart.get("board_count") or 1))
    lines: list[dict[str, Any]] = []
    total = 0.0
    covered_lines = 0
    unbuyable = 0

    for item in cart.get("items") or []:
        part_id = item.get("part_id")
        on_hand = on_hand_for(part_id) if part_id else None
        gross, covered, net = requirement(
            item.get("per_board_qty"), board_count, on_hand, item.get("qty") or 0
        )
        preset = _effective_preset(item, default_preset)
        line: dict[str, Any] = {
            "ref": item["ref"],
            "part_id": part_id,
            "preset": preset,
            "board_count": board_count,
            "per_board_qty": item.get("per_board_qty"),
            "gross_qty": gross,
            "covered_by_stock": covered,
            "required_qty": net,
            "on_hand": on_hand,
            "target_distributor": item.get("target_distributor"),
            "target_packaging": item.get("target_packaging"),
            "candidates": [],
            "selected": None,
            "runner_up": None,
            "rejections": [],
            "reason": "",
            "over_ceiling": False,
            "fell_back": "",
        }

        if net <= 0:
            # Fully answered by stock on the shelf. Not a purchase, and not a
            # $0 purchase either -- there is nothing to buy or to rank.
            line["reason"] = ("covered by stock on hand" if gross
                              else "no quantity required")
            covered_lines += 1
            lines.append(line)
            continue

        offers = offers_for(part_id, item.get("target_distributor")) if part_id else []
        if not offers:
            line["reason"] = "no observed prices for this part"
            unbuyable += 1
            lines.append(line)
            continue

        candidates, rejections = enumerate_candidates(offers, net)
        line["candidates"] = [asdict(c) for c in candidates]
        line["rejections"] = [asdict(r) for r in rejections]

        if preset == PRESET_CUSTOM:
            picked, problem = _custom_line(offers, item, net)
            line["selected"] = _serialize(picked)
            line["reason"] = ("quantity set by hand" if picked
                              else (problem.detail if problem else ""))
            if problem is not None:
                line["rejections"].append(asdict(problem))
        else:
            chosen = select(candidates, preset, required=net, reel_ceiling=reel_ceiling)
            line["selected"] = _serialize(chosen.candidate)
            line["runner_up"] = _serialize(chosen.runner_up)
            line["reason"] = chosen.reason
            line["over_ceiling"] = chosen.over_ceiling
            line["fell_back"] = chosen.fell_back

        if line["selected"] is not None:
            total += float(line["selected"]["spend"])
        else:
            unbuyable += 1
        lines.append(line)

    return {
        "cart_id": cart.get("id"),
        "board_count": board_count,
        "default_preset": default_preset,
        "reel_ceiling": reel_ceiling,
        "lines": lines,
        "totals": {
            "spend": round(total, 4),
            "lines": len(lines),
            "covered_by_stock": covered_lines,
            "unpriced": unbuyable,
        },
    }
