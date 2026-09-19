"""Tests for domain/federation.py — the pure cross-server inventory merge.

Two halves. The first pins `part_key` against the frontend rule it is a port of
(`invPartKey`, js/part-keys.js:67) both by case table and by reading the JS
source, so a future edit to either implementation fails here instead of quietly
splitting one part into two rows in the merged view. The second exercises the
merge semantics from docs/plans/2026-09-19-multi-server-hub-design.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from domain.federation import (
    MergeError,
    MergeRule,
    SourceInfo,
    UnkeyablePartError,
    merge_inventories,
    part_key,
    rule_for,
)
from domain.schema import INVENTORY_FIELDS

REPO_ROOT = Path(__file__).resolve().parents[2]
PART_KEYS_JS = REPO_ROOT / "js" / "part-keys.js"

BENCH = SourceInfo(id="bench", name="bench")
SHOP = SourceInfo(id="shop", name="shop")
LAB = SourceInfo(id="lab", name="lab")


def rec(**kwargs):
    """An inventory record with the schema defaults filled in."""
    base = {f.py_key: (list(f.default) if isinstance(f.default, list) else f.default)
            for f in INVENTORY_FIELDS}
    base.update(kwargs)
    return base


# ── part_key: pinned against js/part-keys.js ──────────────────────────────────

# Each case is (record, expected key) and encodes one branch of the JS:
#   var lcsc = item.lcsc || "";
#   if (lcsc && /^C/i.test(lcsc)) return lcsc;
#   return item.mpn || item.digikey || item.pololu || item.mouser || "";
JS_PART_KEY_CASES = [
    # C-prefixed LCSC wins outright, over every other PN.
    ({"lcsc": "C1000", "mpn": "GRM155", "digikey": "DK-1", "pololu": "P-1", "mouser": "M-1"}, "C1000"),
    ({"lcsc": "C1000"}, "C1000"),
    # /^C/i is case-INsensitive in the test but the value is returned verbatim.
    ({"lcsc": "c1000", "mpn": "GRM155"}, "c1000"),
    # ...and is not anchored to digits: any C-prefixed value is an LCSC key.
    ({"lcsc": "CABC", "mpn": "GRM155"}, "CABC"),
    # A non-C lcsc does not win and does not block — it falls through to mpn.
    ({"lcsc": "X1000", "mpn": "GRM155"}, "GRM155"),
    ({"lcsc": "n/a", "mpn": "GRM155", "digikey": "DK-1"}, "GRM155"),
    # No stripping: the JS tests the raw string, so a leading space is not an LCSC.
    ({"lcsc": " C1000", "mpn": "GRM155"}, "GRM155"),
    # Empty / missing / None lcsc all fall through identically (JS falsiness).
    ({"lcsc": "", "mpn": "GRM155"}, "GRM155"),
    ({"lcsc": None, "mpn": "GRM155"}, "GRM155"),
    ({"mpn": "GRM155"}, "GRM155"),
    # Fallback precedence: mpn > digikey > pololu > mouser.
    ({"mpn": "GRM155", "digikey": "DK-1", "pololu": "P-1", "mouser": "M-1"}, "GRM155"),
    ({"digikey": "DK-1", "pololu": "P-1", "mouser": "M-1"}, "DK-1"),
    ({"pololu": "P-1", "mouser": "M-1"}, "P-1"),
    ({"mouser": "M-1"}, "M-1"),
    # Empty strings in the chain are skipped, exactly as JS `||` does.
    ({"mpn": "", "digikey": "", "pololu": "P-1"}, "P-1"),
    ({"lcsc": "", "mpn": None, "digikey": "", "mouser": "M-1"}, "M-1"),
]


@pytest.mark.parametrize("record,expected", JS_PART_KEY_CASES)
def test_part_key_matches_js_rule(record, expected):
    assert part_key(record) == expected


def _js_inv_part_key_body() -> str:
    source = PART_KEYS_JS.read_text(encoding="utf-8")
    match = re.search(r"export function invPartKey\(item\) \{(.*?)\n\}", source, re.DOTALL)
    assert match, f"invPartKey no longer exists in {PART_KEYS_JS} — the Python port has no referent"
    return match.group(1)


def test_js_inv_part_key_still_has_the_rule_python_ported():
    """Structural pin on the JS source: if the frontend rule changes, this fails.

    Checked rather than eyeballed, because the two implementations key the same
    rows (`rowMap`, `tr.dataset.partKey`) and a silent divergence would show up
    as a part that exists twice in the merged view.
    """
    body = _js_inv_part_key_body()

    # The LCSC prefix test, verbatim, including the /i flag.
    assert "/^C/i.test(lcsc)" in body, "the LCSC prefix test changed in js/part-keys.js"

    # The fallback chain, in precedence order and with nothing inserted.
    chain = re.search(r"return\s+(item\.\w+(?:\s*\|\|\s*item\.\w+)*)", body)
    assert chain, "the fallback chain is no longer a plain `item.x || item.y` expression"
    fields = re.findall(r"item\.(\w+)", chain.group(1))
    assert fields == ["mpn", "digikey", "pololu", "mouser"], (
        f"js/part-keys.js fallback order is now {fields}; update _FALLBACK_FIELDS "
        "in domain/federation.py to match"
    )

    # The JS normalizes nothing. domain/federation.part_key deliberately does not
    # either (unlike domain/part_registry.derive_key, which strips).
    for normalizer in (".trim()", ".toUpperCase()", ".toLowerCase()"):
        assert normalizer not in body, (
            f"js/part-keys.js invPartKey now uses {normalizer}; the Python port must follow"
        )


def test_part_key_precedence_lcsc_beats_mpn():
    assert part_key({"lcsc": "C1000", "mpn": "GRM155C71A106KE11D"}) == "C1000"


def test_part_key_non_c_lcsc_falls_through_to_mpn():
    assert part_key({"lcsc": "12345", "mpn": "GRM155C71A106KE11D"}) == "GRM155C71A106KE11D"


def test_part_key_raises_loudly_when_nothing_identifies_the_part():
    """Unkeyable is a bug in the peer, not a row to drop — dropping it would
    silently subtract that stock from the merged totals."""
    record = {"description": "mystery part", "qty": 12}
    with pytest.raises(UnkeyablePartError) as excinfo:
        part_key(record)
    assert excinfo.value.record == record
    assert "no usable part number" in str(excinfo.value)


def test_merge_names_the_source_of_an_unkeyable_record():
    with pytest.raises(UnkeyablePartError) as excinfo:
        merge_inventories([(BENCH, [rec(description="mystery")])])
    assert excinfo.value.source_id == "bench"
    assert "bench" in str(excinfo.value)


# ── Schema-driven rules ───────────────────────────────────────────────────────

def test_every_schema_field_has_a_merge_rule():
    """The rot guard: a field added to domain/schema.py with a ts_type this
    module has never seen must fail here rather than be guessed at."""
    for field_def in INVENTORY_FIELDS:
        assert isinstance(rule_for(field_def.py_key), MergeRule)


def test_rules_come_from_the_schema_type_with_named_overrides():
    assert rule_for("qty") is MergeRule.SUM
    assert rule_for("ext_price") is MergeRule.SUM
    assert rule_for("unit_price") is MergeRule.WEIGHTED_MEAN_BY_QTY
    assert rule_for("description") is MergeRule.FIRST_NON_EMPTY      # ts_type "string"
    assert rule_for("primary_vendor_id") is MergeRule.FIRST_NON_EMPTY
    assert rule_for("po_history") is MergeRule.UNION                 # ts_type "string[]"


def test_unknown_numeric_fields_are_never_summed():
    """A field this build does not know about gets first-non-empty, not a total:
    summing is a claim about meaning (extensive) that only the overrides make."""
    assert rule_for("reorder_point", 7) is MergeRule.FIRST_NON_EMPTY
    assert rule_for("future_tags", ["a"]) is MergeRule.UNION


# ── Merge: shape and ordering ─────────────────────────────────────────────────

def test_empty_source_list_merges_to_nothing():
    assert merge_inventories([]) == []


def test_source_returning_no_records_contributes_nothing():
    merged = merge_inventories([(BENCH, []), (SHOP, [rec(lcsc="C1", qty=5)])])
    assert [r["lcsc"] for r in merged] == ["C1"]
    assert merged[0]["sources"] == [{"id": "shop", "name": "shop", "qty": 5}]


def test_all_sources_empty():
    assert merge_inventories([(BENCH, []), (SHOP, [])]) == []


def test_single_source_is_passthrough_plus_sources_and_conflicts():
    record = rec(lcsc="C1000", description="100nF 0402", qty=850,
                 unit_price=0.01, ext_price=8.5, po_history=["po_1"])
    merged = merge_inventories([(BENCH, [record])])

    assert len(merged) == 1
    row = merged[0]
    assert row["sources"] == [{"id": "bench", "name": "bench", "qty": 850}]
    assert row["conflicts"] == []
    # Every original field survives untouched.
    for key, value in record.items():
        assert row[key] == value


def test_single_source_row_still_gets_a_one_entry_sources_array():
    """The design doc: 'A single-source row still gets a one-entry sources array
    — the UI never branches.'"""
    merged = merge_inventories([(BENCH, [rec(lcsc="C1", qty=3)])])
    assert merged[0]["sources"] == [{"id": "bench", "name": "bench", "qty": 3}]


def test_two_sources_with_disjoint_parts_keep_both():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=10), rec(lcsc="C2", qty=20)]),
        (SHOP, [rec(lcsc="C3", qty=30)]),
    ])
    assert [r["lcsc"] for r in merged] == ["C1", "C2", "C3"]
    assert [r["qty"] for r in merged] == [10, 20, 30]
    assert [[s["id"] for s in r["sources"]] for r in merged] == [["bench"], ["bench"], ["shop"]]


def test_row_order_is_first_seen_source_order_then_query_order():
    """Adding a source must not reshuffle the rows an earlier source contributed."""
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C3", qty=1), rec(lcsc="C1", qty=1)]),
        (SHOP, [rec(lcsc="C1", qty=1), rec(lcsc="C2", qty=1)]),
    ])
    assert [r["lcsc"] for r in merged] == ["C3", "C1", "C2"]


def test_duplicate_source_id_is_a_loud_error():
    other_bench = SourceInfo(id="bench", name="bench (copy)")
    with pytest.raises(MergeError, match="appears twice"):
        merge_inventories([(BENCH, [rec(lcsc="C1", qty=1)]),
                           (other_bench, [rec(lcsc="C1", qty=1)])])


# ── Merge: quantities ─────────────────────────────────────────────────────────

def test_same_part_on_two_sources_sums_qty_with_a_breakdown():
    """The worked example from the design doc."""
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1000", qty=850)]),
        (SHOP, [rec(lcsc="C1000", qty=400)]),
    ])
    assert len(merged) == 1
    row = merged[0]
    assert row["lcsc"] == "C1000"
    assert row["qty"] == 1250
    assert row["sources"] == [
        {"id": "bench", "name": "bench", "qty": 850},
        {"id": "shop", "name": "shop", "qty": 400},
    ]


def test_summed_qty_stays_an_int():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=1)]),
        (SHOP, [rec(lcsc="C1", qty=2)]),
    ])
    assert merged[0]["qty"] == 3
    assert isinstance(merged[0]["qty"], int)


def test_three_sources_sum_and_report_each():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=1)]),
        (SHOP, [rec(lcsc="C1", qty=2)]),
        (LAB, [rec(lcsc="C1", qty=4)]),
    ])
    assert merged[0]["qty"] == 7
    assert [s["qty"] for s in merged[0]["sources"]] == [1, 2, 4]
    assert sum(s["qty"] for s in merged[0]["sources"]) == merged[0]["qty"]


def test_a_source_holding_zero_still_appears_in_the_breakdown():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=0)]),
        (SHOP, [rec(lcsc="C1", qty=5)]),
    ])
    assert merged[0]["qty"] == 5
    assert merged[0]["sources"] == [
        {"id": "bench", "name": "bench", "qty": 0},
        {"id": "shop", "name": "shop", "qty": 5},
    ]


# ── Merge: money ──────────────────────────────────────────────────────────────

def test_ext_price_sums():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=10, ext_price=1.50)]),
        (SHOP, [rec(lcsc="C1", qty=30, ext_price=6.00)]),
    ])
    assert merged[0]["ext_price"] == pytest.approx(7.50)


def test_unit_price_is_the_quantity_weighted_mean():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=100, unit_price=0.10)]),
        (SHOP, [rec(lcsc="C1", qty=300, unit_price=0.20)]),
    ])
    # (0.10*100 + 0.20*300) / 400
    assert merged[0]["unit_price"] == pytest.approx(0.175)


def test_weighted_unit_price_reconciles_with_the_summed_totals():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=100, unit_price=0.10, ext_price=10.0)]),
        (SHOP, [rec(lcsc="C1", qty=300, unit_price=0.20, ext_price=60.0)]),
    ])
    row = merged[0]
    assert row["unit_price"] * row["qty"] == pytest.approx(row["ext_price"])


def test_single_source_unit_price_is_exact_passthrough():
    """One contribution short-circuits rather than computing p*q/q and its dust."""
    merged = merge_inventories([(BENCH, [rec(lcsc="C1", qty=3, unit_price=0.1)])])
    assert merged[0]["unit_price"] == 0.1


def test_weighted_unit_price_with_zero_total_qty_falls_back_to_the_plain_mean():
    """A part everyone has run out of still has a last known price; the merge
    must not divide by zero and must not lose the price."""
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=0, unit_price=0.10)]),
        (SHOP, [rec(lcsc="C1", qty=0, unit_price=0.30)]),
    ])
    assert merged[0]["qty"] == 0
    assert merged[0]["unit_price"] == pytest.approx(0.20)


def test_zero_total_qty_and_no_prices_anywhere_is_the_schema_default():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=0, unit_price=0)]),
        (SHOP, [rec(lcsc="C1", qty=0, unit_price=0)]),
    ])
    assert merged[0]["unit_price"] == 0.0


def test_a_source_with_no_recorded_price_does_not_drag_the_mean_down():
    """unit_price 0 is 'no price recorded' (the schema default), so it weights
    the total qty but contributes no money — the mean stays the real quote."""
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=100, unit_price=0.10)]),
        (SHOP, [rec(lcsc="C1", qty=0, unit_price=0.0)]),
    ])
    assert merged[0]["unit_price"] == pytest.approx(0.10)


def test_non_numeric_value_in_a_summed_field_is_a_loud_error():
    with pytest.raises(MergeError, match="non-numeric"):
        merge_inventories([
            (BENCH, [rec(lcsc="C1", qty="lots")]),
            (SHOP, [rec(lcsc="C1", qty=1)]),
        ])


# ── Merge: scalar metadata and conflicts ──────────────────────────────────────

def test_first_non_empty_wins_in_source_order():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", description="", package="", manufacturer="Murata")]),
        (SHOP, [rec(lcsc="C1", description="100nF 0402", package="0402", manufacturer="")]),
    ])
    row = merged[0]
    assert row["description"] == "100nF 0402"
    assert row["package"] == "0402"
    assert row["manufacturer"] == "Murata"
    # Filling a gap is a merge, not a disagreement.
    assert row["conflicts"] == []


def test_conflicting_descriptions_are_named_in_conflicts():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", description="100nF 0402 X7R")]),
        (SHOP, [rec(lcsc="C1", description="100nF 0402 X5R")]),
    ])
    row = merged[0]
    assert row["description"] == "100nF 0402 X7R"   # first source still wins
    assert row["conflicts"] == ["description"]


def test_several_conflicting_fields_are_all_named_in_schema_order():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", section="Capacitors", description="A", manufacturer="Murata")]),
        (SHOP, [rec(lcsc="C1", section="Passives", description="B", manufacturer="Murata")]),
    ])
    assert merged[0]["conflicts"] == ["section", "description"]


def test_whitespace_only_difference_is_not_a_conflict():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", description="100nF 0402")]),
        (SHOP, [rec(lcsc="C1", description="  100nF 0402  ")]),
    ])
    assert merged[0]["conflicts"] == []
    assert merged[0]["description"] == "100nF 0402"


def test_summed_and_unioned_fields_never_report_a_conflict():
    """Disagreement over qty/prices/po_history has a defined answer, so it is not
    a conflict the UI should flag."""
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=1, unit_price=0.1, ext_price=0.1, po_history=["po_1"])]),
        (SHOP, [rec(lcsc="C1", qty=2, unit_price=0.2, ext_price=0.4, po_history=["po_2"])]),
    ])
    assert merged[0]["conflicts"] == []


def test_a_third_source_agreeing_with_the_first_still_leaves_the_conflict_named():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", description="A")]),
        (SHOP, [rec(lcsc="C1", description="B")]),
        (LAB, [rec(lcsc="C1", description="A")]),
    ])
    assert merged[0]["conflicts"] == ["description"]


# ── Merge: list fields ────────────────────────────────────────────────────────

def test_list_fields_union_in_source_order_without_duplicates():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", po_history=["po_1", "po_2"])]),
        (SHOP, [rec(lcsc="C1", po_history=["po_2", "po_3"])]),
    ])
    assert merged[0]["po_history"] == ["po_1", "po_2", "po_3"]


def test_list_union_tolerates_an_empty_list_from_one_source():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", po_history=[])]),
        (SHOP, [rec(lcsc="C1", po_history=["po_9"])]),
    ])
    assert merged[0]["po_history"] == ["po_9"]


def test_scalar_where_a_list_belongs_is_a_loud_error():
    with pytest.raises(MergeError, match="cannot be unioned with a scalar"):
        merge_inventories([
            (BENCH, [rec(lcsc="C1", po_history="po_1")]),
            (SHOP, [rec(lcsc="C1", po_history=["po_2"])]),
        ])


# ── Merge: reserved keys and unknown fields ───────────────────────────────────

def test_sources_and_conflicts_on_an_input_record_are_recomputed_not_merged():
    """Re-merging an already-merged row must not inherit a stale breakdown."""
    already_merged = rec(lcsc="C1", qty=5, sources=[{"id": "ghost", "name": "ghost", "qty": 5}],
                         conflicts=["description"])
    merged = merge_inventories([(BENCH, [already_merged])])
    assert merged[0]["sources"] == [{"id": "bench", "name": "bench", "qty": 5}]
    assert merged[0]["conflicts"] == []


def test_unknown_fields_from_a_newer_peer_survive_the_merge():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=1, shelf="A3")]),
        (SHOP, [rec(lcsc="C1", qty=1, shelf="")]),
    ])
    assert merged[0]["shelf"] == "A3"


def test_a_field_only_one_source_has_is_carried_through():
    merged = merge_inventories([
        (BENCH, [rec(lcsc="C1", qty=1)]),
        (SHOP, [{"lcsc": "C1", "qty": 2, "description": "from the shop only"}]),
    ])
    assert merged[0]["description"] == "from the shop only"
    assert merged[0]["qty"] == 3
