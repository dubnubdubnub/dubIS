"""Tests for interchangeability reviews on generic-part membership.

Membership answers "these parts are grouped"; a review answers "WHY is this
one interchangeable, and did anyone sign off". The scenarios below are the real
ones from a sourcing review of a debug board, because they are the cases a
naive model gets wrong:

- ``74AXP1T45GWH``: alternates are only valid if the part translates down to
  0.9 V on the A side (most cheap 1T45s floor at 1.65 V) — a parametric delta.
- ``TLV9001TIDCKR``: same package as the approved ``TLV9001IDCKR`` but with a
  mirrored pinout — a rejection whose value is that it stops the trap being
  re-proposed.
- ``INA226`` for ``INA233AIDGSR``: pin-identical and 4x cheaper, but our
  firmware drives PMBus MFR command codes INA226 doesn't implement — a
  constraint about OUR design, not a property of the part.

Everything here drives the real `domain.generic_parts` functions; nothing
re-implements their logic.
"""

import json
import os

import pytest

import cache_db
from domain.generic_parts import (
    APPROVAL_APPROVED,
    APPROVAL_PROPOSED,
    APPROVAL_REJECTED,
    APPROVAL_UNREVIEWED,
    add_member,
    create_generic_part,
    default_review,
    exclude_member,
    fetch_members,
    get_member_review,
    last_rejection,
    list_generic_parts_with_member_specs,
    list_member_reviews,
    load_into_db,
    remove_member,
    resolve_bom_spec,
    review_member,
    set_preferred,
)
from dubis_errors import AlternateRejectedError, NotFoundError

GENERIC_JSON = "generic_parts.json"


# ── Fixtures / helpers ──────────────────────────────────────────────────────


def _insert_part(db, part_id, description, package, section="Semiconductors"):
    """Insert one part (+ stock row) into the cache. Commits."""
    db.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, description, package, section)"
        " VALUES (?,?,?,?,?,?)",
        (part_id, part_id, part_id, description, package, section),
    )
    db.execute("INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,100,0.05)",
               (part_id,))
    db.commit()


def _seed_translators(db):
    """The 1T45 level-translator family from the debug-board review."""
    _insert_part(db, "C2903325", "74AXP1T45GWH translator 0.65-3.6V", "SOT-363")
    _insert_part(db, "C7420280", "SN74LVC1T45DBVR translator 1.65-5.5V", "SOT-363")


def _seed_opamps(db):
    _insert_part(db, "C2872322", "TLV6001IDCKR opamp", "SOT-23-5")
    _insert_part(db, "C2872323", "TLV9001IDCKR opamp", "SOT-23-5")
    _insert_part(db, "C2872324", "TLV9001TIDCKR opamp mirrored pinout", "SOT-23-5")


def _group(db, events_dir, data_dir, name="1T45 translator, 0.9V-capable",
            package="SOT-363"):
    """A manual generic group that auto-matches on package only.

    Real MPN-level alternates are rarely spec-matchable — which is exactly why
    they get proposed by hand and need a rationale.
    """
    return create_generic_part(
        db, events_dir, data_dir,
        name=name,
        part_type="other",
        spec={"function": "level translator", "min_vcca": "0.9V", "package": package},
        strictness={"required": ["package"]},
    )["generic_part_id"]


def _fresh_db():
    """A brand-new, empty, schema'd cache — simulates cache.db deletion."""
    conn = cache_db.connect(":memory:")
    cache_db.create_schema(conn)
    return conn


MIN_VCCA_DELTA = [{
    "field": "min_vcca",
    "kind": "parametric",
    "reference": "0.9 V",
    "candidate": "1.65 V",
    "blocking": True,
    "note": "low-voltage I/O banks run at 0.9V; a 1.65V floor breaks them",
    "evidence": "SN74LVC1T45 datasheet table 6.3",
}]


# ── The migration default (highest-risk behaviour) ───────────────────────────


class TestMigrationDefault:
    """A store whose members predate reviews must load, and must NOT read as
    approved. Getting this wrong silently blesses unreviewed substitutions."""

    def test_default_review_is_unreviewed(self):
        review = default_review()
        assert review["approval"] == APPROVAL_UNREVIEWED
        assert review["rationale"] == ""
        assert review["spec_deltas"] == []
        assert review["asserted_by"] == ""
        assert review["asserted_at"] == ""
        assert review["history"] == []

    def _write_v1_store(self, data_dir):
        """A pre-review (version 1) durable file: bare members, no metadata."""
        with open(os.path.join(data_dir, GENERIC_JSON), "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "groups": [{
                    "generic_part_id": "g_1t45",
                    "name": "1T45 translator",
                    "part_type": "other",
                    "spec": {"function": "level translator"},
                    "strictness": {"required": ["function"]},
                }],
                "members": [
                    {"generic_part_id": "g_1t45", "part_id": "C2903325",
                     "source": "manual"},
                    {"generic_part_id": "g_1t45", "part_id": "C7420280",
                     "source": "manual"},
                ],
                "preferred": [{"generic_part_id": "g_1t45", "part_id": "C2903325"}],
            }, f)

    def test_v1_store_loads_cleanly(self, db, data_dir):
        _seed_translators(db)
        self._write_v1_store(data_dir)

        load_into_db(db, data_dir)

        members = fetch_members(db, "g_1t45")
        assert {m["part_id"] for m in members} == {"C2903325", "C7420280"}
        preferred = [m["part_id"] for m in members if m["preferred"]]
        assert preferred == ["C2903325"]

    def test_v1_members_read_as_unreviewed_never_approved(self, db, data_dir):
        _seed_translators(db)
        self._write_v1_store(data_dir)
        load_into_db(db, data_dir)

        for m in fetch_members(db, "g_1t45"):
            assert m["review"] == default_review()
            assert m["review"]["approval"] == APPROVAL_UNREVIEWED
            assert m["review"]["approval"] != APPROVAL_APPROVED

        assert get_member_review(db, "g_1t45", "C7420280")["approval"] == (
            APPROVAL_UNREVIEWED)
        # ...including via the list-with-specs read path the frontend uses.
        group = next(g for g in list_generic_parts_with_member_specs(db)
                     if g["generic_part_id"] == "g_1t45")
        assert [m["review"]["approval"] for m in group["members"]] == [
            APPROVAL_UNREVIEWED, APPROVAL_UNREVIEWED]

    def test_v1_store_has_no_reviews_at_all(self, db, data_dir):
        """Absent metadata means absent — no rows are invented on load."""
        _seed_translators(db)
        self._write_v1_store(data_dir)
        load_into_db(db, data_dir)
        assert list_member_reviews(db, "g_1t45") == []

    def test_v1_store_is_rewritten_as_v2_on_next_mutation(self, db, events_dir, data_dir):
        _seed_translators(db)
        self._write_v1_store(data_dir)
        load_into_db(db, data_dir)

        add_member(db, events_dir, data_dir, "g_1t45", "C7420280")

        with open(os.path.join(data_dir, GENERIC_JSON), encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == 2
        assert data["reviews"] == []
        # ...and the migrated members are still unreviewed, not approved.
        assert all(m["review"]["approval"] == APPROVAL_UNREVIEWED
                   for m in fetch_members(db, "g_1t45"))


# ── Proposing / approving / rejecting ───────────────────────────────────────


class TestProposeApproveReject:
    def test_propose_records_rationale_and_makes_part_a_member(
            self, db, events_dir, data_dir):
        _seed_translators(db)
        # A candidate the spec matcher would never find (wrong package).
        _insert_part(db, "C7420281", "SN74LVC1T45DCUR translator", "VSSOP-8")
        gid = _group(db, events_dir, data_dir)
        assert all(m["part_id"] != "C7420281" for m in fetch_members(db, gid))

        review = review_member(
            db, events_dir, data_dir, gid, "C7420281", APPROVAL_PROPOSED,
            rationale="same 1T45 function, but different package and VCCA floor",
            spec_deltas=MIN_VCCA_DELTA,
            asserted_by="isaac",
        )

        assert review["approval"] == APPROVAL_PROPOSED
        assert review["asserted_by"] == "isaac"
        assert review["asserted_at"]
        assert review["spec_deltas"][0]["field"] == "min_vcca"
        assert review["spec_deltas"][0]["blocking"] is True
        member = next(m for m in fetch_members(db, gid) if m["part_id"] == "C7420281")
        assert member["source"] == "manual", "proposing pulls the part into the group"
        assert member["review"]["approval"] == APPROVAL_PROPOSED

    def test_proposing_an_auto_matched_member_leaves_its_source_alone(
            self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        assert next(m for m in fetch_members(db, gid)
                    if m["part_id"] == "C7420280")["source"] == "auto"

        review_member(db, events_dir, data_dir, gid, "C7420280", APPROVAL_PROPOSED,
                      rationale="auto-matched on package; VCCA floor still unverified",
                      asserted_by="isaac")

        member = next(m for m in fetch_members(db, gid) if m["part_id"] == "C7420280")
        assert member["source"] == "auto"
        assert member["review"]["approval"] == APPROVAL_PROPOSED

    def test_approve_records_who_and_when(self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        add_member(db, events_dir, data_dir, gid, "C2903325")

        review = review_member(
            db, events_dir, data_dir, gid, "C2903325", APPROVAL_APPROVED,
            rationale="translates down to 0.65V on A side; covers the 0.9V bank",
            asserted_by="isaac",
        )

        assert review["approval"] == APPROVAL_APPROVED
        assert review["asserted_by"] == "isaac"
        assert get_member_review(db, gid, "C2903325")["approval"] == APPROVAL_APPROVED

    def test_any_verdict_requires_a_rationale(self, db, events_dir, data_dir):
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir, name="TLV9001 drop-in")

        for approval in (APPROVAL_PROPOSED, APPROVAL_APPROVED, APPROVAL_REJECTED):
            with pytest.raises(ValueError, match="rationale"):
                review_member(db, events_dir, data_dir, gid, "C2872324", approval)
        assert get_member_review(db, gid, "C2872324") == default_review()

    def test_unknown_approval_state_raises(self, db, events_dir, data_dir):
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir)
        with pytest.raises(ValueError, match="approval"):
            review_member(db, events_dir, data_dir, gid, "C2872324", "blessed",
                          rationale="looks fine to me")

    def test_reject_excludes_the_member_and_clears_preferred(
            self, db, events_dir, data_dir):
        """The TLV9001TIDCKR trap: same package, mirrored pinout."""
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir, name="TLV9001 drop-in")
        add_member(db, events_dir, data_dir, gid, "C2872324")
        set_preferred(db, events_dir, data_dir, gid, "C2872324")

        review_member(
            db, events_dir, data_dir, gid, "C2872324", APPROVAL_REJECTED,
            rationale="mirrored pinout: pin 1 is OUT, not IN+ — shorts output into input",
            spec_deltas=[{"field": "pin 1", "kind": "pinout",
                          "reference": "IN+", "candidate": "OUT", "blocking": True}],
            asserted_by="isaac",
        )

        member = next(m for m in fetch_members(db, gid) if m["part_id"] == "C2872324")
        assert member["source"] == "excluded", "a rejected verdict writes the tombstone"
        assert member["preferred"] == 0, "a rejected part must not stay preferred"
        assert member["review"]["approval"] == APPROVAL_REJECTED

    def test_reviewing_an_unknown_group_raises_not_found(
            self, db, events_dir, data_dir):
        """A review is scoped to a group — an unknown id is a client error,
        not a member-row FK crash."""
        _seed_translators(db)
        with pytest.raises(NotFoundError, match="g_nope"):
            review_member(db, events_dir, data_dir, "g_nope", "C7420280",
                          APPROVAL_PROPOSED, rationale="looks similar")

    def test_reject_creates_no_member_row_for_a_part_we_do_not_stock(
            self, db, events_dir, data_dir):
        """A verdict on a candidate that was never purchased still records."""
        gid = _group(db, events_dir, data_dir)

        review_member(
            db, events_dir, data_dir, gid, "C9999999", APPROVAL_REJECTED,
            rationale="1.65V VCCA floor; would break the 0.9V I/O banks",
            asserted_by="isaac",
        )

        assert get_member_review(db, gid, "C9999999")["approval"] == APPROVAL_REJECTED
        assert db.execute(
            "SELECT 1 FROM generic_part_members WHERE generic_part_id=? AND part_id=?",
            (gid, "C9999999"),
        ).fetchone() is None
        reviews = list_member_reviews(db, gid)
        assert [(r["part_id"], r["is_member"]) for r in reviews] == [("C9999999", False)]


class TestRejectionIsSticky:
    """A rejection's whole value is that the same bad idea can't come back
    unnoticed."""

    def _reject_the_trap(self, db, events_dir, data_dir):
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir, name="TLV9001 drop-in")
        review_member(
            db, events_dir, data_dir, gid, "C2872324", APPROVAL_REJECTED,
            rationale="mirrored pinout: pin 1 is OUT, not IN+",
            asserted_by="isaac",
        )
        return gid

    def test_reproposing_a_rejected_alternate_surfaces_the_prior_rejection(
            self, db, events_dir, data_dir):
        gid = self._reject_the_trap(db, events_dir, data_dir)

        with pytest.raises(AlternateRejectedError) as exc:
            review_member(
                db, events_dir, data_dir, gid, "C2872324", APPROVAL_PROPOSED,
                rationale="same family, same package, 4c cheaper",
                asserted_by="someone-else",
            )

        assert "mirrored pinout" in str(exc.value)
        assert "isaac" in str(exc.value)
        assert exc.value.part_id == "C2872324"
        assert exc.value.generic_part_id == gid
        assert exc.value.review["approval"] == APPROVAL_REJECTED
        assert last_rejection(exc.value.review)["rationale"].startswith("mirrored pinout")
        # The refused write changed nothing.
        assert get_member_review(db, gid, "C2872324")["approval"] == APPROVAL_REJECTED
        assert get_member_review(db, gid, "C2872324")["asserted_by"] == "isaac"

    def test_acknowledged_override_keeps_the_rejection_in_history(
            self, db, events_dir, data_dir):
        gid = self._reject_the_trap(db, events_dir, data_dir)

        review = review_member(
            db, events_dir, data_dir, gid, "C2872324", APPROVAL_PROPOSED,
            rationale="re-checking against rev B silkscreen",
            asserted_by="isaac", acknowledge_rejection=True,
        )

        assert review["approval"] == APPROVAL_PROPOSED
        assert [h["approval"] for h in review["history"]] == [APPROVAL_REJECTED]
        prior = last_rejection(review)
        assert prior["rationale"] == "mirrored pinout: pin 1 is OUT, not IN+"
        assert prior["asserted_by"] == "isaac"

    def test_refining_a_rejection_needs_no_acknowledgement(
            self, db, events_dir, data_dir):
        gid = self._reject_the_trap(db, events_dir, data_dir)

        review = review_member(
            db, events_dir, data_dir, gid, "C2872324", APPROVAL_REJECTED,
            rationale="mirrored pinout: pin 1 is OUT, not IN+ (confirmed on rev B)",
            asserted_by="isaac",
        )

        assert review["approval"] == APPROVAL_REJECTED
        assert [h["approval"] for h in review["history"]] == [APPROVAL_REJECTED]

    def test_approving_after_an_acknowledged_rejection_lifts_the_exclusion(
            self, db, events_dir, data_dir):
        gid = self._reject_the_trap(db, events_dir, data_dir)
        assert next(m for m in fetch_members(db, gid)
                    if m["part_id"] == "C2872324")["source"] == "excluded"

        review_member(
            db, events_dir, data_dir, gid, "C2872324", APPROVAL_APPROVED,
            rationale="footprint was rotated in rev B; pinout now matches",
            asserted_by="isaac", acknowledge_rejection=True,
        )

        member = next(m for m in fetch_members(db, gid) if m["part_id"] == "C2872324")
        assert member["source"] == "manual"
        assert member["review"]["approval"] == APPROVAL_APPROVED
        assert last_rejection(member["review"])["rationale"].startswith("mirrored pinout")

    def test_removing_the_member_does_not_erase_the_rejection(
            self, db, events_dir, data_dir):
        """Reviews carry no FK precisely so a drag-out (remove + exclude, the
        flyout's own sequence) can't wipe the verdict."""
        gid = self._reject_the_trap(db, events_dir, data_dir)

        remove_member(db, events_dir, data_dir, gid, "C2872324")
        exclude_member(db, events_dir, data_dir, gid, "C2872324")

        assert get_member_review(db, gid, "C2872324")["approval"] == APPROVAL_REJECTED
        with open(os.path.join(data_dir, GENERIC_JSON), encoding="utf-8") as f:
            persisted = json.load(f)
        assert [r["approval"] for r in persisted["reviews"]] == [APPROVAL_REJECTED]


class TestExcludedIsNotRejected:
    """`excluded` is a mechanism (don't let auto-match re-add this);
    `rejected` is a verdict with a reason. Rejected implies excluded; excluded
    does not imply rejected."""

    def test_excluding_leaves_the_review_unreviewed(self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        add_member(db, events_dir, data_dir, gid, "C7420280")

        exclude_member(db, events_dir, data_dir, gid, "C7420280")

        member = next(m for m in fetch_members(db, gid) if m["part_id"] == "C7420280")
        assert member["source"] == "excluded"
        assert member["review"]["approval"] == APPROVAL_UNREVIEWED
        assert member["review"]["approval"] != APPROVAL_REJECTED

    def test_excluding_a_reviewed_member_keeps_its_rationale(
            self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        review_member(
            db, events_dir, data_dir, gid, "C2903325", APPROVAL_APPROVED,
            rationale="translates down to 0.65V on A side", asserted_by="isaac")

        exclude_member(db, events_dir, data_dir, gid, "C2903325")

        review = get_member_review(db, gid, "C2903325")
        assert review["approval"] == APPROVAL_APPROVED
        assert review["rationale"] == "translates down to 0.65V on A side"


# ── Structured spec deltas ──────────────────────────────────────────────────


class TestSpecDeltas:
    def test_parametric_delta_roundtrips_through_the_durable_file(
            self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        review_member(
            db, events_dir, data_dir, gid, "C7420280", APPROVAL_REJECTED,
            rationale="1.65V VCCA floor breaks the 0.9V I/O banks",
            spec_deltas=MIN_VCCA_DELTA, asserted_by="isaac")

        fresh = _fresh_db()
        _insert_part(fresh, "C7420280", "SN74LVC1T45DBVR", "SOT-363")
        load_into_db(fresh, data_dir)

        restored = get_member_review(fresh, gid, "C7420280")
        assert restored["spec_deltas"] == MIN_VCCA_DELTA
        assert restored["approval"] == APPROVAL_REJECTED
        assert restored["asserted_by"] == "isaac"
        fresh.close()

    def test_design_constraint_delta_needs_no_part_parametrics(
            self, db, events_dir, data_dir):
        """The INA233 -> INA226 case: the deciding fact is about OUR firmware,
        not about either part's spec sheet."""
        _insert_part(db, "C2688581", "INA233AIDGSR current monitor", "VSSOP-10")
        gid = _group(db, events_dir, data_dir, name="INA233 current monitor")

        review = review_member(
            db, events_dir, data_dir, gid, "C1859217", APPROVAL_REJECTED,
            rationale=("pin-identical and 4x cheaper, but our firmware drives PMBus "
                       "MFR command codes 0xD2/0xD4/0xD5"),
            spec_deltas=[{
                "field": "PMBus MFR command codes 0xD2/0xD4/0xD5",
                "kind": "design_constraint",
                "blocking": True,
                "note": "INA226's plain register map does not implement them",
                "evidence": "firmware: drivers/ina233.c",
            }],
            asserted_by="isaac",
        )

        delta = review["spec_deltas"][0]
        assert delta["kind"] == "design_constraint"
        assert delta["reference"] == "" and delta["candidate"] == ""
        assert delta["blocking"] is True
        assert delta["evidence"] == "firmware: drivers/ina233.c"

    def test_delta_needs_a_field_and_a_known_kind(self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        with pytest.raises(ValueError, match="field"):
            review_member(db, events_dir, data_dir, gid, "C7420280",
                          APPROVAL_PROPOSED, rationale="r",
                          spec_deltas=[{"reference": "0.9V"}])
        with pytest.raises(ValueError, match="kind"):
            review_member(db, events_dir, data_dir, gid, "C7420280",
                          APPROVAL_PROPOSED, rationale="r",
                          spec_deltas=[{"field": "min_vcca", "kind": "vibes"}])
        assert get_member_review(db, gid, "C7420280") == default_review()

    def test_deltas_accept_a_json_string(self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        review = review_member(
            db, events_dir, data_dir, gid, "C7420280", APPROVAL_PROPOSED,
            rationale="check the VCCA floor",
            spec_deltas=json.dumps(MIN_VCCA_DELTA))
        assert review["spec_deltas"] == MIN_VCCA_DELTA


# ── Idempotency ─────────────────────────────────────────────────────────────


class TestIdempotence:
    def test_repeated_identical_write_is_a_no_op(self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        kwargs = {"rationale": "translates down to 0.65V on A side",
                  "spec_deltas": MIN_VCCA_DELTA, "asserted_by": "isaac"}

        first = review_member(db, events_dir, data_dir, gid, "C2903325",
                              APPROVAL_APPROVED, **kwargs)
        path = os.path.join(data_dir, GENERIC_JSON)
        with open(path, encoding="utf-8") as f:
            after_first = f.read()

        second = review_member(db, events_dir, data_dir, gid, "C2903325",
                               APPROVAL_APPROVED, **kwargs)
        third = review_member(db, events_dir, data_dir, gid, "C2903325",
                              APPROVAL_APPROVED, **kwargs)

        assert second == first and third == first
        assert second["asserted_at"] == first["asserted_at"]
        assert second["history"] == []
        with open(path, encoding="utf-8") as f:
            assert f.read() == after_first
        assert len(list_member_reviews(db, gid)) == 1

    def test_repeated_loads_do_not_duplicate_or_mutate_reviews(
            self, db, events_dir, data_dir):
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        review_member(db, events_dir, data_dir, gid, "C2903325", APPROVAL_APPROVED,
                      rationale="0.65V A-side floor", asserted_by="isaac")

        load_into_db(db, data_dir)
        load_into_db(db, data_dir)

        reviews = list_member_reviews(db, gid)
        assert len(reviews) == 1
        assert reviews[0]["approval"] == APPROVAL_APPROVED
        assert reviews[0]["history"] == []


# ── Existing behaviour must not move ────────────────────────────────────────


class TestUnreviewedDataBehavesExactlyAsBefore:
    """set_preferred / exclude_member / resolve_bom_spec on data with no
    rationale: byte-for-byte the old behaviour."""

    def _passive_group(self, db, events_dir, data_dir):
        _insert_part(db, "C1525", "100nF 16V 0402 Capacitor MLCC", "0402",
                     section="Passives - Capacitors")
        _insert_part(db, "C9999", "100nF 25V 0402 Capacitor MLCC", "0402",
                     section="Passives - Capacitors")
        return create_generic_part(
            db, events_dir, data_dir, "100nF 0402", "capacitor",
            {"value": "100nF", "package": "0402"},
            {"required": ["value", "package"]},
        )["generic_part_id"]

    def test_set_preferred_unchanged(self, db, events_dir, data_dir):
        gid = self._passive_group(db, events_dir, data_dir)
        set_preferred(db, events_dir, data_dir, gid, "C1525")
        rows = {m["part_id"]: m["preferred"] for m in fetch_members(db, gid)}
        assert rows == {"C1525": 1, "C9999": 0}

    def test_exclude_member_unchanged(self, db, events_dir, data_dir):
        gid = self._passive_group(db, events_dir, data_dir)
        exclude_member(db, events_dir, data_dir, gid, "C9999")
        rows = {m["part_id"]: m["source"] for m in fetch_members(db, gid)}
        assert rows["C9999"] == "excluded"
        assert rows["C1525"] == "auto"

    def test_resolve_bom_spec_unchanged(self, db, events_dir, data_dir):
        gid = self._passive_group(db, events_dir, data_dir)
        set_preferred(db, events_dir, data_dir, gid, "C9999")

        match = resolve_bom_spec(db, "capacitor", 100e-9, "0402")

        assert match["generic_part_id"] == gid
        assert match["best_part_id"] == "C9999"
        assert {m["part_id"] for m in match["members"]} == {"C1525", "C9999"}
        assert all(m["approval"] == APPROVAL_UNREVIEWED for m in match["members"])

    def test_resolve_bom_spec_never_offers_a_rejected_member(
            self, db, events_dir, data_dir):
        gid = self._passive_group(db, events_dir, data_dir)
        set_preferred(db, events_dir, data_dir, gid, "C9999")
        review_member(db, events_dir, data_dir, gid, "C9999", APPROVAL_REJECTED,
                      rationale="25V part is 0.3mm taller; fouls the shield can",
                      asserted_by="isaac")

        match = resolve_bom_spec(db, "capacitor", 100e-9, "0402")

        assert match["best_part_id"] == "C1525"
        assert {m["part_id"] for m in match["members"]} == {"C1525"}

    def test_resolve_bom_spec_returns_none_when_every_member_is_rejected(
            self, db, events_dir, data_dir):
        gid = self._passive_group(db, events_dir, data_dir)
        for part_id in ("C1525", "C9999"):
            review_member(db, events_dir, data_dir, gid, part_id, APPROVAL_REJECTED,
                          rationale="wrong dielectric for this rail",
                          asserted_by="isaac")
        assert resolve_bom_spec(db, "capacitor", 100e-9, "0402") is None


# ── Durability ──────────────────────────────────────────────────────────────


class TestDurability:
    def test_reviews_survive_a_cache_wipe(self, db, events_dir, data_dir):
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir, name="TLV9001 drop-in")
        add_member(db, events_dir, data_dir, gid, "C2872323")
        review_member(db, events_dir, data_dir, gid, "C2872323", APPROVAL_APPROVED,
                      rationale="drop-in for TLV6001: same pinout, same package",
                      asserted_by="isaac")
        review_member(db, events_dir, data_dir, gid, "C2872324", APPROVAL_REJECTED,
                      rationale="mirrored pinout: pin 1 is OUT, not IN+",
                      asserted_by="isaac", acknowledge_rejection=True)

        fresh = _fresh_db()
        _insert_part(fresh, "C2872323", "TLV9001IDCKR opamp", "SOT-23-5")
        _insert_part(fresh, "C2872324", "TLV9001TIDCKR opamp", "SOT-23-5")
        load_into_db(fresh, data_dir)

        assert get_member_review(fresh, gid, "C2872323")["approval"] == APPROVAL_APPROVED
        assert get_member_review(fresh, gid, "C2872324")["approval"] == APPROVAL_REJECTED
        # The rejection still enforces its exclusion tombstone after the wipe.
        members = {m["part_id"]: m["source"] for m in fetch_members(fresh, gid)}
        assert members["C2872324"] == "excluded"
        fresh.close()

    def test_history_survives_a_cache_wipe(self, db, events_dir, data_dir):
        _seed_opamps(db)
        gid = _group(db, events_dir, data_dir, name="TLV9001 drop-in")
        review_member(db, events_dir, data_dir, gid, "C2872324", APPROVAL_REJECTED,
                      rationale="mirrored pinout: pin 1 is OUT, not IN+",
                      asserted_by="isaac")
        review_member(db, events_dir, data_dir, gid, "C2872324", APPROVAL_PROPOSED,
                      rationale="re-checking against rev B silkscreen",
                      asserted_by="isaac", acknowledge_rejection=True)

        fresh = _fresh_db()
        _insert_part(fresh, "C2872324", "TLV9001TIDCKR opamp", "SOT-23-5")
        load_into_db(fresh, data_dir)

        restored = get_member_review(fresh, gid, "C2872324")
        assert restored["approval"] == APPROVAL_PROPOSED
        assert last_rejection(restored)["rationale"].startswith("mirrored pinout")
        fresh.close()

    def test_a_persist_from_a_wiped_cache_retains_reviews(
            self, db, events_dir, data_dir):
        """An unrelated mutation must not snapshot away reviews the DB hasn't
        restored yet — that would silently drop a recorded rejection."""
        _seed_translators(db)
        gid = _group(db, events_dir, data_dir)
        review_member(db, events_dir, data_dir, gid, "C7420280", APPROVAL_REJECTED,
                      rationale="1.65V VCCA floor breaks the 0.9V banks",
                      asserted_by="isaac")

        db.execute("DELETE FROM generic_member_reviews")
        db.commit()
        add_member(db, events_dir, data_dir, gid, "C2903325")

        with open(os.path.join(data_dir, GENERIC_JSON), encoding="utf-8") as f:
            data = json.load(f)
        retained = [r for r in data["reviews"] if r["part_id"] == "C7420280"]
        assert len(retained) == 1
        assert retained[0]["approval"] == APPROVAL_REJECTED
        assert retained[0]["rationale"].startswith("1.65V")

    def test_an_unknown_approval_in_the_file_loads_as_unreviewed(
            self, db, data_dir):
        """A hand-edited or future-written verdict must never read as approved."""
        _seed_translators(db)
        with open(os.path.join(data_dir, GENERIC_JSON), "w", encoding="utf-8") as f:
            json.dump({
                "version": 2, "groups": [], "members": [], "preferred": [],
                "reviews": [{"generic_part_id": "g_x", "part_id": "C7420280",
                             "approval": "probably-fine", "rationale": "hmm"}],
            }, f)

        load_into_db(db, data_dir)

        assert get_member_review(db, "g_x", "C7420280")["approval"] == (
            APPROVAL_UNREVIEWED)
