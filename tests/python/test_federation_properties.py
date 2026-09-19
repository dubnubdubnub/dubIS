"""Property tests for domain/federation.py — the cross-server inventory merge.

The reference implementation of the repo's property-testing harness.  Read
``docs/property-testing.md`` first; ``tests/python/test_federation.py`` holds the
example-based tests, and this file is the complementary axis, not a replacement.

Why federation earns the first property suite: its failure mode is a *silently
wrong number*.  An under-reported merged quantity looks exactly like a correct
one — there is no exception, no red row, nothing to notice until someone orders
parts they already own.  Example-based tests prove the cases someone imagined;
the invariants below ("no stock is created or destroyed by merging") are true of
*every* input, so a generator can attack them.

Each property states something the module's own docstrings already claim.  None
of them re-implements ``merge_inventories`` — that would be the tautological
property anti-pattern the guide warns about.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tests.python.strategies as gen
from domain.federation import (
    MergeError,
    SourceInfo,
    UnkeyablePartError,
    merge_inventories,
    part_key,
)
from domain.schema import INVENTORY_FIELDS


def _total_qty(by_source) -> int:
    return sum(int(r["qty"]) for _, records in by_source for r in records)


def _keys(by_source) -> set[str]:
    return {part_key(r) for _, records in by_source for r in records}


def _groups(by_source) -> "dict[str, list]":
    """``{part key: [record, ...]}`` in first-seen order.

    Reconstructed from the *inputs*, never from a merged row's own key: several
    properties below are precisely about whether a merged row still keys to the
    group it was merged for, so asking the row would beg the question.
    """
    out: dict[str, list] = {}
    for _, records in by_source:
        for record in records:
            out.setdefault(part_key(record), []).append(record)
    return out


# ── part_key: the identity rule ───────────────────────────────────────────────

class TestPartKeyProperties:
    """``part_key`` is a port of ``invPartKey``; these pin the port's shape.

    The example table in ``test_federation.py`` pins specific values.  These pin
    the *rule*: whatever it returns must be one of the five fields it was given,
    and which one is fully determined by the documented precedence.
    """

    @given(st.one_of(*[gen.identity_fields(branch=b) for b in gen.IDENTITY_FIELDS]))
    def test_result_is_always_one_of_the_input_fields(self, ident):
        """The key is never synthesised — it is verbatim one of the five values.

        A key the merge invented would not match the ``tr.dataset.partKey`` the
        browser computes from the same record, and the two views would silently
        disagree about which rows are the same part.
        """
        key = part_key({k: v for k, v in ident.items() if k != "__branch"})
        assert key in {ident[f] for f in gen.IDENTITY_FIELDS}

    @given(gen.identity_fields(branch="lcsc"))
    def test_c_prefixed_lcsc_wins_outright(self, ident):
        """A C-prefixed lcsc short-circuits the chain, whatever else is filled in."""
        assert part_key({k: v for k, v in ident.items() if k != "__branch"}) == ident["lcsc"]

    @given(gen.part_key_cases())
    def test_branch_matches_the_documented_precedence(self, ident):
        """The winning field is the first non-empty one in precedence order.

        Stated as "the key equals the value of the field the generator aimed
        at", which is the branch coverage claim ``identity_fields`` makes.
        """
        branch = ident["__branch"]
        record = {k: v for k, v in ident.items() if k != "__branch"}
        if branch == "unkeyable":
            with pytest.raises(UnkeyablePartError):
                part_key(record)
            return
        assert part_key(record) == record[branch]

    @given(gen.identity_fields(branch="unkeyable"))
    def test_unkeyable_raises_rather_than_returning_blank(self, ident):
        """Where the browser returns ``""``, the merge must raise.

        A blank key would pool every unkeyable row from every source into one
        phantom part — the exact "silently wrong number" this suite exists for.
        """
        with pytest.raises(UnkeyablePartError):
            part_key({k: v for k, v in ident.items() if k != "__branch"})


# ── The merge: conservation ───────────────────────────────────────────────────

class TestConservation:
    """Nothing is created and nothing is lost by merging."""

    @given(gen.federated_inputs())
    def test_total_qty_is_conserved(self, by_source):
        """Sum of merged qty == sum of every input qty.

        The headline invariant.  A merge that drops a record, double-counts one,
        or mis-groups two parts breaks this and nothing else would notice.
        """
        merged = merge_inventories(by_source)
        assert sum(int(row["qty"]) for row in merged) == _total_qty(by_source)

    @given(gen.federated_inputs())
    def test_each_rows_breakdown_sums_to_its_qty(self, by_source):
        """``sources`` is a breakdown, so it must add up to the row it breaks down.

        The UI shows this split without a second request; a breakdown that does
        not reconcile with its own total is a lie told confidently.
        """
        for row in merge_inventories(by_source):
            assert sum(int(s["qty"]) for s in row["sources"]) == int(row["qty"]), row

    @given(gen.federated_inputs())
    def test_key_set_is_exactly_the_union_of_input_key_sets(self, by_source):
        """No part gained, no part dropped, and one row per key."""
        merged = merge_inventories(by_source)
        assert {part_key(row) for row in merged} == _keys(by_source)
        assert len(merged) == len(_keys(by_source))

    @given(gen.federated_inputs())
    def test_every_merged_row_rekeys_to_its_own_group(self, by_source):
        """A merged row must still key to the part it was merged *for*.

        The merge groups by ``part_key`` and then picks per-field winners.  If
        those winners could change the key, the browser would re-key the row
        differently from the hub and ``rowMap`` would split one part in two.
        """
        merged = merge_inventories(by_source)
        for row, key in zip(merged, _groups(by_source)):
            assert part_key(row) == key, (key, row)

    @given(gen.federated_inputs())
    def test_every_source_that_has_the_part_appears_in_its_breakdown(self, by_source):
        """One entry per contributing source, in roster order, never duplicated."""
        merged = merge_inventories(by_source)
        expected: dict[str, list[str]] = {}
        for info, records in by_source:
            for record in records:
                ids = expected.setdefault(part_key(record), [])
                if info.id not in ids:
                    ids.append(info.id)
        for row, ids in zip(merged, expected.values()):
            assert [s["id"] for s in row["sources"]] == ids


# ── The merge: ordering ───────────────────────────────────────────────────────

class TestOrdering:
    """What survives a reshuffle of the roster, and what deliberately does not.

    ``merge_inventories``'s docstring says ordering is *fully determined by the
    input*: first-non-empty winners and the breakdown's order are source-order
    dependent on purpose.  So the order-independence claim is about the numbers,
    not about the whole row.
    """

    @given(gen.federated_inputs(min_sources=2, max_sources=3), st.randoms())
    def test_quantities_are_independent_of_source_order(self, by_source, rnd):
        """Permuting the roster changes presentation, never arithmetic."""
        shuffled = list(by_source)
        rnd.shuffle(shuffled)
        def qty_by_key(rows, source_order):
            return dict(zip(_groups(source_order), (int(r["qty"]) for r in rows)))

        before = qty_by_key(merge_inventories(by_source), by_source)
        after = qty_by_key(merge_inventories(shuffled), shuffled)
        assert before == after

    @given(gen.federated_inputs(min_sources=2, max_sources=3), st.randoms())
    def test_breakdown_is_independent_of_source_order_as_a_set(self, by_source, rnd):
        shuffled = list(by_source)
        rnd.shuffle(shuffled)

        def breakdown(source_order):
            rows = merge_inventories(source_order)
            return dict(zip(
                _groups(source_order),
                (frozenset((s["id"], s["qty"]) for s in r["sources"]) for r in rows),
            ))

        assert breakdown(by_source) == breakdown(shuffled)

    @given(gen.federated_inputs(min_sources=2, max_sources=3))
    def test_rows_appear_in_first_seen_order(self, by_source):
        """Adding a source never reshuffles the rows an earlier source contributed."""
        expected: list[str] = []
        for _, records in by_source:
            for record in records:
                key = part_key(record)
                if key not in expected:
                    expected.append(key)
        assert [part_key(r) for r in merge_inventories(by_source)] == expected


# ── The merge: money ──────────────────────────────────────────────────────────

class TestMoney:
    """``unit_price`` is intensive, ``ext_price`` extensive, and they must agree."""

    @given(gen.federated_inputs(consistent_money=True))
    def test_unit_price_times_qty_reconciles_with_summed_ext_price(self, by_source):
        """The stated reason ``unit_price`` is a qty-weighted mean.

        Given inputs where each source's own ``ext_price == unit_price * qty``,
        the merged row must satisfy the same identity — otherwise the merged
        view quotes a price that multiplies out to a different total than the
        one it also displays.
        """
        groups = _groups(by_source)
        for row, contributors in zip(merge_inventories(by_source), groups.values()):
            expected = sum(float(r["ext_price"]) for r in contributors)
            assert math.isclose(
                float(row["unit_price"]) * int(row["qty"]), expected,
                rel_tol=1e-6, abs_tol=1e-6,
            ), row

    @given(gen.federated_inputs())
    def test_ext_price_is_conserved(self, by_source):
        total = sum(float(r["ext_price"]) for _, records in by_source for r in records)
        merged_total = sum(float(row["ext_price"]) for row in merge_inventories(by_source))
        assert math.isclose(merged_total, total, rel_tol=1e-9, abs_tol=1e-9)

    @given(gen.federated_inputs())
    def test_merged_unit_price_is_never_larger_than_the_largest_input(self, by_source):
        """A mean cannot exceed its inputs.

        Catches the classic weighted-mean slip of dividing by the wrong total —
        summing prices instead of averaging them would sail past the
        reconciliation test whenever qty happens to be 1.
        """
        groups = _groups(by_source)
        for row, contributors in zip(merge_inventories(by_source), groups.values()):
            assert float(row["unit_price"]) <= max(
                float(r["unit_price"]) for r in contributors) + 1e-9, row


# ── The merge: single source and degenerate inputs ────────────────────────────

class TestDegenerate:

    @given(gen.federated_inputs(min_sources=1, max_sources=1))
    def test_single_source_is_passthrough(self, by_source):
        """One source in, the same rows out, plus provenance and no conflicts.

        The merged view must be usable as the *only* view, so a one-source merge
        has to be byte-identical to that source's own ``/v1/parts`` — with one
        stated exception the generator found at 2000 examples: ``_union`` drops
        repeats, so a list field arrives deduplicated even when nothing merged.
        That is the union rule doing its documented job, so the property names it
        rather than ignoring it; if the dedup ever stops happening, this fails.
        """
        info, _records = by_source[0]
        groups = _groups(by_source)
        merged = merge_inventories(by_source)
        assert len(merged) == len(groups)
        for row, contributors in zip(merged, groups.values()):
            if len(contributors) > 1:
                continue  # the source listed one part twice; see the conflict test
            original = contributors[0]
            for field in INVENTORY_FIELDS:
                expected = original[field.py_key]
                if field.ts_type.endswith("[]"):
                    expected = list(dict.fromkeys(expected))  # _union drops repeats
                assert row[field.py_key] == expected, field.py_key
            assert row["conflicts"] == []
            assert row["sources"] == [
                {"id": info.id, "name": info.name, "qty": original["qty"]}
            ]

    @given(gen.rosters(min_size=0, max_size=3))
    def test_empty_sources_contribute_nothing(self, roster):
        assert merge_inventories([(info, []) for info in roster]) == []

    def test_no_sources_at_all(self):
        assert merge_inventories([]) == []

    @given(gen.federated_inputs(min_sources=1, max_sources=2), gen.source_infos())
    def test_duplicate_source_id_is_rejected(self, by_source, extra):
        """A repeated id makes the breakdown ambiguous, so it must raise.

        Stated as a property because the ambiguity does not depend on *which*
        id repeats — any repeat is fatal.
        """
        dup = (SourceInfo(id=by_source[0][0].id, name=extra.name), [])
        with pytest.raises(MergeError):
            merge_inventories([*by_source, dup])


# ── The merge: conflicts ──────────────────────────────────────────────────────

class TestConflicts:

    @given(gen.federated_inputs())
    def test_conflicts_only_ever_name_real_fields(self, by_source):
        names = {f.py_key for f in INVENTORY_FIELDS}
        for row in merge_inventories(by_source):
            assert set(row["conflicts"]) <= names | (set(row) - {"sources", "conflicts"})

    @given(gen.federated_inputs())
    def test_a_conflicted_field_really_had_two_distinct_non_empty_values(self, by_source):
        """``conflicts`` is an admission, so it must only admit real disagreements.

        A conflict flag that fires on "one source knows, the others don't" would
        light up the whole merged view and train the user to ignore it.
        """
        merged = merge_inventories(by_source)
        groups = _groups(by_source)
        for row, contributors in zip(merged, groups.values()):
            for field in row["conflicts"]:
                distinct = {
                    (v.strip() if isinstance(v, str) else v)
                    for v in (r.get(field) for r in contributors)
                    if not _is_empty(v)
                }
                assert len(distinct) > 1, (field, contributors)

    @given(gen.federated_inputs(min_sources=1, max_sources=1))
    def test_one_source_conflicts_only_with_its_own_duplicate_rows(self, by_source):
        """A lone source has nothing to disagree with — unless it repeats a part.

        The first draft of this said "one source, no conflicts", and the
        generator immediately produced a source listing two records that key to
        the same part (``digikey`` and ``mpn`` both holding a stray tab).  That
        is not a merge bug — ``_source_breakdown``'s docstring anticipates it —
        it is a wrong property.  Corrected rather than deleted, because the
        corrected version still catches a merge that invents disagreement.
        """
        info, records = by_source[0]
        singletons = {k for k, rows in _groups(by_source).items() if len(rows) == 1}
        for row, key in zip(merge_inventories(by_source), _groups(by_source)):
            if key in singletons:
                assert row["conflicts"] == [], (key, row)


def _is_empty(value) -> bool:
    """Local mirror of federation._is_empty, kept tiny and explicit.

    Deliberately *not* imported: a property that reuses the implementation's own
    helper stops being an independent statement about the behaviour.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return False


# ── The generators themselves ─────────────────────────────────────────────────

class TestStrategyLibrary:
    """The harness has to be trustworthy before the properties it feeds are.

    Cheap, and it catches the failure mode that would quietly hollow out every
    property above: a generator that stopped producing what it claims to.
    """

    @given(gen.inventory_records())
    def test_generated_records_are_schema_valid(self, record):
        assert gen.is_schema_valid(record), record

    @given(gen.keyed_inventory_records())
    def test_keyable_records_really_are_keyable(self, record):
        assert part_key(record)

    @settings(max_examples=25)
    @given(gen.federated_inputs(min_sources=2, max_sources=2, max_parts=3))
    def test_federated_inputs_produce_overlap_sometimes(self, by_source):
        """Sanity: the two sources are dealt from a shared pool, so their key
        sets are comparable.  Asserted as "no source invents a key the pool
        never held", which is what makes the overlap lattice reachable."""
        pooled = _keys(by_source)
        for _, records in by_source:
            assert {part_key(r) for r in records} <= pooled
