"""Turning a cart into a purchase plan.

The scenario throughout is a real one: a Glasgow revD0 run where C1525 (100nF
0402) is placed eight times a board and 112 are already on the shelf. That
combination is what exercises the arithmetic the board count exists for.
"""

import pytest

from domain.cart_plan import plan_cart, requirement
from domain.purchase_candidates import (
    PRESET_CUSTOM,
    PRESET_MIN,
    PRESET_REEL,
    PRESET_TIER_UP,
    offers_from_ladders,
)

CUT_TAPE_AND_REEL = {
    "cut tape": {
        "name": "Cut Tape", "carrier": "tape", "is_reel": False,
        "reel_qty": 5000, "reel_fee": 3.0, "latest_ts": "2026-08-01",
        "ladder": [(1, 0.0082), (100, 0.0041), (500, 0.0033), (1000, 0.0029)],
    },
    "tape & reel": {
        "name": "Tape & Reel", "carrier": "tape", "is_reel": True,
        "reel_qty": 5000, "reel_fee": None, "latest_ts": "2026-08-01",
        "ladder": [(5000, 0.0021), (10000, 0.0018)],
    },
}


def offers(_part_id=None, _distributor=None):
    return offers_from_ladders(CUT_TAPE_AND_REEL, "lcsc")


def cart(items, board_count=1, cart_id="cart_x"):
    return {"id": cart_id, "board_count": board_count, "items": items}


def item(ref="C1525", **overrides):
    base = {"ref": ref, "part_id": ref, "qty": 0, "per_board_qty": None,
            "preset": None, "target_distributor": None, "target_packaging": None}
    base.update(overrides)
    return base


def build(items, *, board_count=1, on_hand=0, **kwargs):
    return plan_cart(cart(items, board_count),
                     offers_for=offers,
                     on_hand_for=lambda _pid: on_hand,
                     **kwargs)


def only(plan):
    assert len(plan["lines"]) == 1
    return plan["lines"][0]


# ── requirement arithmetic ───────────────────────────────────────────────────


def test_boards_multiply_the_per_board_placement_count():
    assert requirement(8, 25, 0, 0) == (200, 0, 200)


def test_stock_on_hand_reduces_what_must_be_bought():
    """25 boards x 8 placements, less 112 on hand, is 88 to buy."""
    assert requirement(8, 25, 112, 0) == (200, 112, 88)


def test_stock_beyond_the_requirement_covers_it_without_going_negative():
    gross, covered, net = requirement(8, 1, 500, 0)
    assert (gross, covered, net) == (8, 8, 0)


def test_a_line_with_no_per_board_count_is_not_scaled_by_the_boards():
    """A one-off programmer is one programmer, not twenty-five of them."""
    assert requirement(None, 25, 0, 1) == (1, 0, 1)


def test_a_zero_per_board_count_is_treated_as_unrecorded():
    assert requirement(0, 25, 0, 3) == (3, 0, 3)


def test_a_missing_board_count_means_one_board_not_zero():
    assert requirement(8, 0, 0, 0) == (8, 0, 8)
    assert requirement(8, None, 0, 0) == (8, 0, 8)


def test_negative_stock_is_treated_as_none_on_hand():
    assert requirement(8, 1, -20, 0) == (8, 0, 8)


# ── the plan ─────────────────────────────────────────────────────────────────


def test_the_plan_reports_the_whole_derivation_not_just_a_quantity():
    """A row has to be able to explain itself weeks later."""
    line = only(build([item(per_board_qty=8)], board_count=25, on_hand=112))
    assert (line["per_board_qty"], line["board_count"]) == (8, 25)
    assert (line["gross_qty"], line["covered_by_stock"], line["required_qty"]) == (200, 112, 88)
    assert line["on_hand"] == 112


def test_a_line_fully_covered_by_stock_is_not_a_purchase():
    line = only(build([item(per_board_qty=8)], board_count=1, on_hand=500))
    assert line["required_qty"] == 0
    assert line["selected"] is None
    assert line["candidates"] == []
    assert "covered by stock" in line["reason"]


def test_a_covered_line_contributes_nothing_to_the_total():
    plan = build([item("C1525", per_board_qty=8), item("C0402", per_board_qty=2)],
                 board_count=1, on_hand=500)
    assert plan["totals"]["spend"] == 0.0
    assert plan["totals"]["covered_by_stock"] == 2


def test_stock_reduces_a_requirement_rather_than_discounting_a_purchase():
    """Modelling covered stock as a $0 line item is how a cart total stops
    agreeing with the sum of its rows."""
    partly = only(build([item(per_board_qty=8)], board_count=25, on_hand=112))
    assert partly["required_qty"] == 88
    assert partly["selected"]["qty"] >= 88


def test_buying_past_the_requirement_wins_when_it_is_genuinely_cheaper():
    """88 pieces at the 1-piece price costs more than 100 at the 100 price.
    The cheaper row wins even though it buys more -- that is the point."""
    line = only(build([item(per_board_qty=8)], board_count=25, on_hand=112))
    assert line["selected"]["qty"] == 100
    assert line["selected"]["spend"] == pytest.approx(0.41)


def test_every_candidate_is_returned_so_the_pick_can_be_argued_with():
    line = only(build([item(per_board_qty=8)], board_count=25))
    assert len(line["candidates"]) > 1
    assert line["runner_up"] is not None
    assert line["runner_up"]["spend"] >= line["selected"]["spend"]


def test_candidates_are_plain_dicts_ready_to_serialize():
    line = only(build([item(per_board_qty=8)], board_count=25))
    assert isinstance(line["candidates"][0], dict)
    assert {"qty", "unit_price", "spend", "packaging", "is_reel"} <= set(line["candidates"][0])


def test_the_total_is_the_sum_of_the_selected_rows():
    plan = build([item("C1525", per_board_qty=8), item("C0402", per_board_qty=2)],
                 board_count=25)
    assert plan["totals"]["spend"] == pytest.approx(
        sum(line["selected"]["spend"] for line in plan["lines"]))


# ── presets ──────────────────────────────────────────────────────────────────


def test_a_row_preset_overrides_the_cart_default():
    plan = build([item(preset=PRESET_REEL, per_board_qty=8)],
                 board_count=25, default_preset=PRESET_MIN, reel_ceiling=80.0)
    line = only(plan)
    assert line["preset"] == PRESET_REEL
    assert line["selected"]["is_reel"] is True


def test_a_row_with_no_preset_follows_the_cart_default():
    line = only(build([item(per_board_qty=8)], board_count=25,
                      default_preset=PRESET_TIER_UP))
    assert line["preset"] == PRESET_TIER_UP


def test_an_unrecognised_stored_preset_degrades_one_row_not_the_whole_cart():
    """Presets are written by clients and read back much later; one stale
    string must not make the cart unloadable."""
    line = only(build([item(preset="cheapest", per_board_qty=8)], board_count=25))
    assert line["preset"] == PRESET_MIN
    assert line["selected"] is not None


def test_the_reel_ceiling_reaches_the_selection():
    line = only(build([item(preset=PRESET_REEL, per_board_qty=8)],
                      board_count=25, reel_ceiling=0.5))
    assert line["over_ceiling"] is True


def test_a_preset_that_finds_nothing_reports_what_it_fell_back_to():
    line = only(build([item(preset=PRESET_TIER_UP, per_board_qty=1000)],
                      board_count=10))
    assert line["fell_back"] == PRESET_MIN
    assert line["selected"] is not None


# ── custom quantities ────────────────────────────────────────────────────────


def test_a_custom_row_is_priced_at_the_quantity_that_was_typed():
    line = only(build([item(preset=PRESET_CUSTOM, qty=700, per_board_qty=8,
                            target_packaging="Cut Tape")], board_count=25))
    assert line["selected"]["qty"] == 700
    assert line["selected"]["unit_price"] == 0.0033
    assert line["reason"] == "quantity set by hand"


def test_a_custom_row_is_never_silently_rounded_up():
    line = only(build([item(preset=PRESET_CUSTOM, qty=700, per_board_qty=8,
                            target_packaging="Tape & Reel")], board_count=25))
    assert line["selected"] is None
    assert any(r["reason"] == "not_multiple" and r["nearest_legal"] == 5000
               for r in line["rejections"])


def test_a_custom_row_with_no_packaging_says_so_rather_than_guessing():
    """Several packagings quote the same quantity at different prices; picking
    one silently attributes a price to a choice nobody made."""
    line = only(build([item(preset=PRESET_CUSTOM, qty=700, per_board_qty=8)],
                      board_count=25))
    assert line["selected"] is None
    assert any(r["reason"] == "packaging_required" for r in line["rejections"])


def test_a_custom_row_needs_no_packaging_when_there_is_only_one():
    """One offer is not an ambiguity, and demanding a choice there would be
    bureaucracy rather than safety."""
    single = {"cut tape": {**CUT_TAPE_AND_REEL["cut tape"], "reel_fee": None}}
    plan = plan_cart(
        cart([item(preset=PRESET_CUSTOM, qty=700, per_board_qty=8)], 25),
        offers_for=lambda *_: offers_from_ladders(single, "lcsc"),
        on_hand_for=lambda _p: 0,
    )
    line = only(plan)
    assert line["selected"]["qty"] == 700


def test_a_custom_row_naming_an_unknown_packaging_is_rejected_clearly():
    line = only(build([item(preset=PRESET_CUSTOM, qty=700,
                            target_packaging="Ammo Pack")], board_count=1))
    assert line["selected"] is None
    assert any(r["reason"] == "no_such_packaging" for r in line["rejections"])


def test_an_unpriceable_custom_row_still_leaves_the_alternatives_visible():
    line = only(build([item(preset=PRESET_CUSTOM, qty=700, per_board_qty=8,
                            target_packaging="Tape & Reel")], board_count=25))
    assert line["candidates"]


# ── lines that cannot be priced ──────────────────────────────────────────────


def test_a_line_with_no_part_id_is_reported_not_dropped():
    plan = plan_cart(cart([item("raw:tool", part_id=None, qty=1)]),
                     offers_for=offers, on_hand_for=lambda _p: 0)
    line = only(plan)
    assert line["selected"] is None
    assert "no observed prices" in line["reason"]
    assert plan["totals"]["unpriced"] == 1


def test_a_part_with_no_observed_prices_is_reported_not_dropped():
    plan = plan_cart(cart([item(per_board_qty=8)], board_count=25),
                     offers_for=lambda *_: [], on_hand_for=lambda _p: 0)
    line = only(plan)
    assert line["candidates"] == []
    assert line["reason"] == "no observed prices for this part"
    assert plan["totals"]["unpriced"] == 1


def test_an_unpriceable_line_does_not_poison_the_others():
    plan = plan_cart(
        cart([item("C1525", per_board_qty=8), item("raw:tool", part_id=None, qty=1)],
             board_count=25),
        offers_for=lambda pid, _d: offers() if pid else [],
        on_hand_for=lambda _p: 0,
    )
    assert plan["totals"]["spend"] > 0
    assert plan["totals"]["unpriced"] == 1
    assert plan["totals"]["lines"] == 2


def test_an_empty_cart_plans_to_nothing_without_erroring():
    plan = plan_cart(cart([]), offers_for=offers, on_hand_for=lambda _p: 0)
    assert plan["lines"] == []
    assert plan["totals"] == {"spend": 0.0, "lines": 0, "covered_by_stock": 0, "unpriced": 0}


def test_the_target_distributor_is_passed_through_to_the_offer_lookup():
    """A row pinned to one distributor must not be ranked against another's
    prices."""
    seen = []

    def spy(part_id, distributor):
        seen.append((part_id, distributor))
        return offers()

    plan_cart(cart([item(per_board_qty=8, target_distributor="mouser")], 25),
              offers_for=spy, on_hand_for=lambda _p: 0)
    assert seen == [("C1525", "mouser")]
