"""Enumerating and ranking the real ways a part can be bought.

The ladders below are the ones actually observed while sourcing a Glasgow
revD0 -- LCSC quoting cut tape and full reels as separate ladders for the same
C-number, DigiKey charging a flat Digi-Reel fee, a regulator sold only in
trays. Invented ladders would not reproduce the two failures this module
exists to prevent: pricing a quantity the distributor never quoted, and
answering "buy a reel" with the biggest reel that fits a budget.
"""

import pytest

from domain.purchase_candidates import (
    BELOW_LADDER,
    BELOW_MOQ,
    INSUFFICIENT_STOCK,
    NO_LADDER,
    NOT_MULTIPLE,
    PRESET_CUSTOM,
    PRESET_MIN,
    PRESET_REEL,
    PRESET_TIER_UP,
    REELING_SUFFIX,
    Candidate,
    Offer,
    Rejection,
    enumerate_candidates,
    offers_from_ladders,
    quote,
    rank,
    select,
    unit_price_at,
)

# ── Fixtures drawn from real observations ────────────────────────────────────

# C1525 -- 100nF 0402 X7R. LCSC publishes two ladders for one C-number.
CUT_TAPE = Offer(
    distributor="lcsc", packaging="Cut Tape", carrier="tape", is_reel=False,
    ladder=((1, 0.0082), (100, 0.0041), (500, 0.0033), (1000, 0.0029)),
    stock=180000,
)
FULL_REEL = Offer(
    distributor="lcsc", packaging="Tape & Reel", carrier="tape", is_reel=True,
    ladder=((5000, 0.0021), (10000, 0.0018)),
    stock=180000, multiple=5000,
)
# DigiKey cuts any quantity onto a reel for a flat handling charge.
DIGI_REEL = Offer(
    distributor="digikey", packaging="Digi-Reel", carrier="tape", is_reel=True,
    ladder=((1, 0.0104), (1000, 0.0058), (4000, 0.0041)),
    stock=52000, fee=7.0,
)
# APS51208N-OBR-BD -- Mouser only, tray, and the one part that blocked the build.
TRAY_ONLY = Offer(
    distributor="mouser", packaging="Tray", carrier="tray", is_reel=False,
    ladder=((1, 2.47), (10, 2.19), (100, 1.94)),
    stock=41, moq=1,
)


def spends(candidates):
    return [(c.distributor, c.packaging, c.qty, c.spend) for c in candidates]


def reasons(rejections):
    return {(r.packaging, r.reason): r.nearest_legal for r in rejections}


# ── unit_price_at ────────────────────────────────────────────────────────────


def test_applicable_break_is_the_one_at_or_below_the_quantity():
    """700 pieces pays the 500 price, not the 1000 price it did not reach."""
    assert unit_price_at(CUT_TAPE.ladder, 700) == 0.0033


def test_quantity_landing_exactly_on_a_break_takes_that_break():
    assert unit_price_at(CUT_TAPE.ladder, 1000) == 0.0029


def test_quantity_below_the_lowest_break_has_no_price_rather_than_the_lowest():
    """A reel-only ladder starts at 5,000; 694 pieces is not 694 x the 5k price."""
    assert unit_price_at(FULL_REEL.ladder, 694) is None


def test_no_ladder_and_non_positive_quantities_have_no_price():
    assert unit_price_at((), 100) is None
    assert unit_price_at(CUT_TAPE.ladder, 0) is None
    assert unit_price_at(CUT_TAPE.ladder, -5) is None


# ── enumeration ──────────────────────────────────────────────────────────────


def test_the_requirement_itself_is_a_candidate_at_its_applicable_price():
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    exact = next(c for c in candidates if c.qty == 694)
    assert exact.unit_price == 0.0033
    assert exact.break_qty == 500
    assert exact.on_break is False
    assert exact.origin == "required"
    assert exact.surplus == 0
    assert exact.spend == pytest.approx(694 * 0.0033)


def test_every_break_above_the_requirement_is_offered_too():
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    assert {c.qty for c in candidates} == {694, 1000}


def test_breaks_below_the_requirement_are_not_offered():
    """100 pieces does not cover a 694-piece requirement, at any price."""
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    assert all(c.qty >= 694 for c in candidates)


def test_a_requirement_on_a_break_is_labelled_by_the_requirement_not_the_break():
    candidates, _ = enumerate_candidates([CUT_TAPE], 1000)
    exact = next(c for c in candidates if c.qty == 1000)
    assert exact.origin == "required"
    assert exact.on_break is True


def test_fixed_increment_packaging_rounds_up_to_whole_units():
    """694 against 5,000-piece reels is one reel, and 6,000 would be two."""
    candidates, _ = enumerate_candidates([FULL_REEL], 694)
    assert {c.qty for c in candidates} == {5000, 10000}
    candidates, _ = enumerate_candidates([FULL_REEL], 6000)
    assert 10000 in {c.qty for c in candidates}


def test_a_flat_handling_fee_lands_in_the_spend():
    candidates, _ = enumerate_candidates([DIGI_REEL], 1000)
    reel = next(c for c in candidates if c.qty == 1000)
    assert reel.fee == 7.0
    assert reel.spend == pytest.approx(1000 * 0.0058 + 7.0)


def test_a_non_positive_requirement_is_a_programming_error():
    with pytest.raises(ValueError, match="required must be positive"):
        enumerate_candidates([CUT_TAPE], 0)


# ── rejections ───────────────────────────────────────────────────────────────


def test_an_off_multiple_quantity_is_rejected_with_the_next_whole_unit():
    _, rejected = enumerate_candidates([FULL_REEL], 694)
    assert reasons(rejected)[("Tape & Reel", NOT_MULTIPLE)] == 5000


def test_below_moq_is_rejected_with_the_moq_as_the_nearest_legal_quantity():
    offer = Offer(distributor="mouser", packaging="Bulk",
                  ladder=((1, 0.5), (100, 0.4)), moq=250)
    _, rejected = enumerate_candidates([offer], 100)
    assert reasons(rejected)[("Bulk", BELOW_MOQ)] == 250


def test_a_requirement_beyond_stock_is_rejected_with_no_nearest_quantity():
    """Buying more cannot fix being short, and quoting the shelf invites
    ordering it bare on a figure that was stale when we recorded it."""
    _, rejected = enumerate_candidates([TRAY_ONLY], 100)
    rejection = next(r for r in rejected if r.reason == INSUFFICIENT_STOCK)
    assert rejection.nearest_legal is None
    assert "41" in rejection.detail


def test_below_ladder_is_rejected_with_the_lowest_break():
    offer = Offer(distributor="lcsc", packaging="Tape & Reel", is_reel=True,
                  ladder=((3000, 0.002),))
    _, rejected = enumerate_candidates([offer], 500)
    assert reasons(rejected)[("Tape & Reel", BELOW_LADDER)] == 3000


def test_off_multiple_beats_below_ladder_because_it_carries_an_actionable_number():
    """A 694-piece ask against 5,000-piece reels violates both; only one of
    them tells the user what to type instead."""
    _, rejected = enumerate_candidates([FULL_REEL], 694)
    assert [r.reason for r in rejected] == [NOT_MULTIPLE]


def test_an_offer_with_no_observed_prices_is_rejected_once_not_per_quantity():
    priceless = Offer(distributor="element14", packaging="Reel",
                      is_reel=True, multiple=2500)
    _, rejected = enumerate_candidates([priceless], 694)
    assert [r.reason for r in rejected] == [NO_LADDER]


def test_rejecting_one_offer_does_not_suppress_the_others():
    candidates, rejected = enumerate_candidates([CUT_TAPE, FULL_REEL], 694)
    assert {c.qty for c in candidates} == {694, 1000, 5000, 10000}
    assert [r.reason for r in rejected] == [NOT_MULTIPLE]


# ── unknown is not permission ────────────────────────────────────────────────


def test_unknown_stock_still_yields_candidates_but_flags_itself():
    unobserved = Offer(distributor="lcsc", packaging="Cut Tape",
                       ladder=((1, 0.01), (1000, 0.008)), stock=None)
    candidates, _ = enumerate_candidates([unobserved], 694)
    assert candidates
    assert all(c.stock_known is False for c in candidates)


def test_observed_stock_marks_candidates_as_known():
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    assert all(c.stock_known is True for c in candidates)


def test_unknown_moq_and_multiple_impose_nothing():
    loose = Offer(distributor="lcsc", packaging="Cut Tape",
                  ladder=((1, 0.01), (500, 0.008)), moq=None, multiple=None)
    candidates, rejected = enumerate_candidates([loose], 694)
    assert 694 in {c.qty for c in candidates}
    assert rejected == []


def test_a_multiple_of_one_imposes_nothing():
    singles = Offer(distributor="mouser", packaging="Bulk",
                    ladder=((1, 0.5),), multiple=1)
    candidates, rejected = enumerate_candidates([singles], 37)
    assert 37 in {c.qty for c in candidates}
    assert rejected == []


# ── ranking ──────────────────────────────────────────────────────────────────


def test_candidates_come_back_cheapest_first():
    candidates, _ = enumerate_candidates([CUT_TAPE, FULL_REEL, DIGI_REEL], 694)
    assert [c.spend for c in candidates] == sorted(c.spend for c in candidates)


def test_equal_spend_prefers_the_quantity_that_leaves_less_on_the_shelf():
    """Two offers at the same price are not equivalent when one leaves 4,000
    spare parts behind; surplus must outrank the alphabet."""
    cheap_big = Offer(distributor="aaa", packaging="Reel", is_reel=True,
                      ladder=((5000, 0.002),))
    same_small = Offer(distributor="zzz", packaging="Cut Tape",
                       ladder=((1000, 0.01),))
    candidates, _ = enumerate_candidates([cheap_big, same_small], 1000)
    assert candidates[0].qty == 1000
    assert candidates[0].spend == candidates[1].spend == pytest.approx(10.0)


def test_full_ties_break_toward_the_preferred_distributor():
    a = Offer(distributor="lcsc", packaging="Cut Tape", ladder=((1000, 0.01),))
    b = Offer(distributor="digikey", packaging="Cut Tape", ladder=((1000, 0.01),))
    candidates, _ = enumerate_candidates([a, b], 1000)
    assert candidates[0].distributor == "digikey"


def test_ranking_an_empty_list_is_empty_not_an_error():
    assert rank([]) == []


# ── presets ──────────────────────────────────────────────────────────────────


def test_min_picks_the_cheapest_and_names_the_runner_up():
    candidates, _ = enumerate_candidates([CUT_TAPE, FULL_REEL, DIGI_REEL], 694)
    picked = select(candidates, PRESET_MIN, required=694)
    assert (picked.candidate.distributor, picked.candidate.qty) == ("lcsc", 694)
    assert picked.runner_up.qty == 1000
    assert picked.over_ceiling is False
    assert picked.fell_back == ""


def test_tier_up_climbs_to_the_next_published_break():
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    picked = select(candidates, PRESET_TIER_UP, required=694)
    assert picked.candidate.qty == 1000
    assert picked.candidate.on_break is True


def test_tier_up_never_picks_a_between_breaks_quantity():
    """The point of tiering up is reaching a break; 750 pieces reaches none."""
    candidates, _ = enumerate_candidates([CUT_TAPE], 694)
    picked = select(candidates, PRESET_TIER_UP, required=694)
    assert picked.candidate.qty != 694


def test_tier_up_falls_back_to_min_when_the_requirement_tops_the_ladder():
    candidates, _ = enumerate_candidates([CUT_TAPE], 1000)
    picked = select(candidates, PRESET_TIER_UP, required=1000)
    assert picked.candidate.qty == 1000
    assert picked.fell_back == PRESET_MIN
    assert "no price break above" in picked.reason


def test_reel_picks_the_cheapest_reel_not_the_biggest_one_under_the_ceiling():
    """The whole-reel preference minimises spend. Maximising reel size subject
    to a ceiling is what puts every line just under $80 -- explicitly not the
    rule, and the bug this test exists to hold down."""
    candidates, _ = enumerate_candidates([CUT_TAPE, FULL_REEL, DIGI_REEL], 694)
    picked = select(candidates, PRESET_REEL, required=694, reel_ceiling=80.0)
    assert (picked.candidate.packaging, picked.candidate.qty) == ("Tape & Reel", 5000)
    assert picked.candidate.spend == pytest.approx(10.50)
    assert picked.over_ceiling is False


def test_reel_prefers_a_reel_over_a_cheaper_cut_tape_because_that_is_the_ask():
    candidates, _ = enumerate_candidates([CUT_TAPE, FULL_REEL], 694)
    picked = select(candidates, PRESET_REEL, required=694, reel_ceiling=80.0)
    assert picked.candidate.is_reel is True
    assert picked.candidate.spend > min(c.spend for c in candidates)


def test_a_reel_over_the_ceiling_is_shown_and_flagged_not_hidden():
    """A ceiling is not a budget: answering "buy a reel" with "there are no
    reels" would be false."""
    candidates, _ = enumerate_candidates([FULL_REEL], 694)
    picked = select(candidates, PRESET_REEL, required=694, reel_ceiling=5.0)
    assert picked.candidate.qty == 5000
    assert picked.over_ceiling is True
    assert "exceeds the ceiling" in picked.reason


def test_no_ceiling_at_all_still_picks_the_cheapest_reel():
    candidates, _ = enumerate_candidates([FULL_REEL, DIGI_REEL], 694)
    picked = select(candidates, PRESET_REEL, required=694, reel_ceiling=None)
    assert picked.over_ceiling is False
    assert picked.candidate.spend == min(c.spend for c in candidates if c.is_reel)


def test_reel_falls_back_when_the_part_has_no_reel_packaging():
    candidates, _ = enumerate_candidates([TRAY_ONLY], 10)
    picked = select(candidates, PRESET_REEL, required=10, reel_ceiling=80.0)
    assert picked.candidate.packaging == "Tray"
    assert picked.fell_back == PRESET_MIN
    assert "no reel packaging" in picked.reason


def test_every_preset_survives_having_nothing_to_pick():
    for preset in (PRESET_MIN, PRESET_TIER_UP, PRESET_REEL):
        picked = select([], preset, required=694, reel_ceiling=80.0)
        assert picked.candidate is None
        assert picked.spend == 0.0
        assert "no purchasable quantity" in picked.reason


def test_a_single_candidate_has_no_runner_up():
    candidates, _ = enumerate_candidates([TRAY_ONLY], 10)
    picked = select(candidates, PRESET_MIN, required=10)
    assert picked.runner_up is None


def test_an_unknown_preset_is_rejected_rather_than_defaulted():
    with pytest.raises(ValueError, match="unknown preset"):
        select([], "cheapest", required=1)


def test_custom_has_no_automatic_selection():
    with pytest.raises(ValueError, match="no automatic selection"):
        select([], PRESET_CUSTOM, required=1)


# ── custom quantities ────────────────────────────────────────────────────────


def test_a_custom_quantity_is_priced_at_its_applicable_break():
    priced = quote(CUT_TAPE, 700, required=694)
    assert isinstance(priced, Candidate)
    assert priced.unit_price == 0.0033
    assert priced.break_qty == 500
    assert priced.on_break is False
    assert priced.origin == PRESET_CUSTOM


def test_a_custom_quantity_is_rejected_rather_than_rounded_up():
    """Rounding 700 up to a 5,000-piece reel spends money on a number the user
    did not type."""
    rejected = quote(FULL_REEL, 700, required=694)
    assert isinstance(rejected, Rejection)
    assert rejected.reason == NOT_MULTIPLE
    assert rejected.nearest_legal == 5000


def test_a_custom_quantity_beyond_stock_is_rejected():
    rejected = quote(TRAY_ONLY, 60, required=60)
    assert isinstance(rejected, Rejection)
    assert rejected.reason == INSUFFICIENT_STOCK


def test_a_custom_quantity_may_deliberately_undershoot_the_requirement():
    """Buying part of a shortfall now and the rest later is a real decision;
    the surplus simply goes negative."""
    priced = quote(CUT_TAPE, 500, required=694)
    assert isinstance(priced, Candidate)
    assert priced.surplus == -194


def test_zero_and_negative_custom_quantities_are_rejected():
    for bad in (0, -1):
        rejected = quote(CUT_TAPE, bad, required=694)
        assert isinstance(rejected, Rejection)
        assert rejected.reason == "non_positive"


# ── offers_from_ladders ──────────────────────────────────────────────────────


def group(name, ladder, *, is_reel=False, carrier="tape", reel_qty=None, reel_fee=None):
    """One packaging group shaped the way cart_qty.tier_ladders returns them."""
    return {"name": name, "carrier": carrier, "is_reel": is_reel,
            "reel_qty": reel_qty, "reel_fee": reel_fee,
            "latest_ts": "2026-08-01T00:00:00", "ladder": ladder}


def by_packaging(offers):
    return {o.packaging: o for o in offers}


def test_each_stored_packaging_becomes_its_own_offer():
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082), (500, 0.0033)]),
        "tape & reel": group("Tape & Reel", [(5000, 0.0021)], is_reel=True, reel_qty=5000),
    }, "lcsc")
    assert set(by_packaging(offers)) == {"Cut Tape", "Tape & Reel"}
    assert all(o.distributor == "lcsc" for o in offers)


def test_reel_quantity_constrains_a_reel_packaging():
    offers = offers_from_ladders({
        "tape & reel": group("Tape & Reel", [(5000, 0.0021)], is_reel=True, reel_qty=5000),
    }, "lcsc")
    assert by_packaging(offers)["Tape & Reel"].multiple == 5000


def test_a_reel_whose_ladder_quotes_less_than_a_reel_is_not_a_multiple():
    """LCSC's real shape: one packaging called "Reel", quoted from 20 pieces up.

    Observed on Glasgow revD0's C13533. Treating the 10,000-piece reel as an
    order multiple rejected 20, 50, 500 and 1,500 -- every quantity LCSC had
    just published a price for -- and made the cheapest way to cover a need of
    20 a $40 reel of 10,000.
    """
    offers = offers_from_ladders({
        "reel": group("Reel", [(20, 0.0063), (50, 0.0055), (500, 0.0045),
                               (1500, 0.0042), (10000, 0.0040)],
                      is_reel=True, reel_qty=10000),
    }, "lcsc")
    assert by_packaging(offers)["Reel"].multiple is None
    candidates, rejected = enumerate_candidates(offers, 20)
    assert 20 in {c.qty for c in candidates}
    assert rejected == []
    assert min(c.spend for c in candidates) == pytest.approx(20 * 0.0063)


def test_a_reel_whose_ladder_starts_at_the_reel_stays_a_multiple():
    """DigiKey's Tape & Reel still cannot be bought in part."""
    offers = offers_from_ladders({
        "tape & reel": group("Tape & Reel", [(5000, 0.0021), (10000, 0.0019)],
                             is_reel=True, reel_qty=5000),
    }, "lcsc")
    assert by_packaging(offers)["Tape & Reel"].multiple == 5000
    _c, rejected = enumerate_candidates(offers, 5000)
    assert [r.reason for r in rejected] == []


def test_a_nonsense_reel_quantity_is_ignored_rather_than_dividing_by_zero():
    offers = offers_from_ladders({
        "reel": group("Reel", [(100, 0.01)], is_reel=True, reel_qty=0),
    }, "lcsc")
    assert by_packaging(offers)["Reel"].multiple is None


def test_reel_quantity_does_not_constrain_a_cut_tape_ladder():
    """A vendor mentioning its 5,000-piece reel must not make 694 pieces of cut
    tape unbuyable."""
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082), (500, 0.0033)], reel_qty=5000),
    }, "lcsc")
    assert by_packaging(offers)["Cut Tape"].multiple is None
    candidates, rejected = enumerate_candidates(offers, 694)
    assert 694 in {c.qty for c in candidates}
    assert rejected == []


def test_a_reeling_fee_on_cut_tape_yields_a_buyable_part_reel():
    """The stated preference is that a half reel plus the fee is fine; without
    this derived offer there is nothing for the reel preset to pick."""
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082), (500, 0.0033)], reel_fee=3.0),
    }, "lcsc")
    derived = by_packaging(offers)["Cut Tape" + REELING_SUFFIX]
    assert derived.is_reel is True
    assert derived.fee == 3.0
    assert derived.multiple is None
    priced = quote(derived, 694, required=694)
    assert priced.spend == pytest.approx(694 * 0.0033 + 3.0)


def test_a_part_reel_can_beat_a_whole_reel_and_the_preset_says_so():
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082), (500, 0.0033)], reel_fee=3.0),
        "tape & reel": group("Tape & Reel", [(5000, 0.0021)], is_reel=True, reel_qty=5000),
    }, "lcsc")
    candidates, _ = enumerate_candidates(offers, 694)
    picked = select(candidates, PRESET_REEL, required=694, reel_ceiling=80.0)
    assert picked.candidate.packaging == "Cut Tape" + REELING_SUFFIX
    assert picked.candidate.spend == pytest.approx(5.2902)


def test_no_reeling_fee_means_no_derived_offer():
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082)], reel_fee=None),
    }, "lcsc")
    assert set(by_packaging(offers)) == {"Cut Tape"}


def test_a_free_reeling_service_is_not_a_missing_one_but_adds_no_priced_option():
    """0.0 is a real published value, distinct from None -- but a zero-cost
    derived reel would just duplicate the cut-tape row, so it is not emitted."""
    offers = offers_from_ladders({
        "cut tape": group("Cut Tape", [(1, 0.0082)], reel_fee=0.0),
    }, "lcsc")
    assert set(by_packaging(offers)) == {"Cut Tape"}


def test_a_reeling_fee_on_a_reel_is_not_charged_twice():
    offers = offers_from_ladders({
        "tape & reel": group("Tape & Reel", [(5000, 0.0021)], is_reel=True,
                             reel_qty=5000, reel_fee=3.0),
    }, "lcsc")
    assert len(offers) == 1
    assert offers[0].fee == 0.0


def test_groups_with_no_ladder_are_skipped_entirely():
    offers = offers_from_ladders({"cut tape": group("Cut Tape", [])}, "lcsc")
    assert offers == []


def test_stock_is_unknown_unless_the_caller_supplies_it():
    """Price observations do not record stock, and a ladder is not evidence of
    availability."""
    groups = {"cut tape": group("Cut Tape", [(1, 0.0082), (500, 0.0033)])}
    unknown = offers_from_ladders(groups, "lcsc")
    assert all(o.stock is None for o in unknown)
    candidates, _ = enumerate_candidates(unknown, 694)
    assert all(c.stock_known is False for c in candidates)

    known = offers_from_ladders(groups, "lcsc", stock=180000)
    candidates, _ = enumerate_candidates(known, 694)
    assert all(c.stock_known is True for c in candidates)


def test_an_unnamed_packaging_still_produces_a_labelled_derived_reel():
    """Legacy observations carry no packaging name; the derived option still
    needs to be distinguishable from the ladder it came from."""
    offers = offers_from_ladders({"": group("", [(1, 0.5)], reel_fee=7.0)}, "digikey")
    assert set(by_packaging(offers)) == {"", "reeled"}
