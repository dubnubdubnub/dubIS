"""Predicate evaluation for alternate-part approval.

Every case here is a real substitution question from an actual sourcing review,
because the failure mode this module exists to prevent is a *plausible* wrong
answer, and invented parametrics do not exercise that.
"""

import pytest

from domain.attribute_parse import canonical_name, parse_value
from domain.predicates import (
    FAIL,
    PASS,
    STATUS_FAIL,
    STATUS_INDETERMINATE,
    STATUS_PASS,
    UNKNOWN,
    Predicate,
    Report,
    evaluate,
    evaluate_all,
)


def attr(name, raw, distributor="lcsc", observed_at="2026-08-01"):
    """Build a stored-attribute row the way domain/attributes.py does."""
    parsed = parse_value(raw)
    return {
        "canonical_name": canonical_name(name),
        "name": name,
        "distributor": distributor,
        "raw_value": raw,
        "kind": parsed.kind,
        "value_min": parsed.value_min,
        "value_max": parsed.value_max,
        "unit": parsed.unit,
        "qualifier": parsed.qualifier,
        "observed_at": observed_at,
    }


class TestPredicateConstruction:
    def test_attribute_is_canonicalized_so_vendor_spellings_work(self):
        assert Predicate("Voltage - Supply", "lte", value=1).attribute == canonical_name(
            "Voltage - Supply")

    @pytest.mark.parametrize("kwargs,message", [
        ({"op": "nonsense", "value": 1}, "unknown op"),
        ({"op": "lte", "value": 1, "bound": "sideways"}, "unknown bound"),
        ({"op": "lte"}, "requires a numeric value"),
        ({"op": "enum"}, "requires a non-empty values"),
        ({"op": "package_equivalent"}, "requires a reference package"),
    ])
    def test_malformed_predicates_are_refused_at_construction(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            Predicate("x", **kwargs)


class TestLevelTranslatorFloor:
    """74AXP1T45GWH: the A side must reach 0.9 V or the low-voltage IO banks break.

    Most cheaper 1T45 parts floor at 1.65 V. This is the case where a naive
    check on the *upper* bound (both parts reach 5.5 V) would approve a part
    that cannot drive the board's lowest rail.
    """

    REQ = Predicate("Voltage - Supply", "lte", bound="lower", value=0.9, unit="V",
                    label="min VCCA")

    def test_original_passes(self):
        r = evaluate_all([self.REQ], [attr("Voltage - Supply", "0.9V~5.5V")])
        assert r.status == STATUS_PASS and not r.blockers

    def test_higher_floor_fails_with_a_legible_reason(self):
        r = evaluate_all([self.REQ], [attr("Voltage - Supply", "1.65V~5.5V")])
        assert r.status == STATUS_FAIL
        assert "1.65" in r.verdicts[0].note and "0.9" in r.verdicts[0].note

    def test_matching_upper_bound_does_not_rescue_a_bad_floor(self):
        """Both parts reach 5.5 V; only the floor distinguishes them."""
        upper_only = Predicate("Voltage - Supply", "gte", bound="upper", value=5.5, unit="V")
        rows = [attr("Voltage - Supply", "1.65V~5.5V")]
        assert evaluate_all([upper_only], rows).status == STATUS_PASS
        assert evaluate_all([self.REQ], rows).status == STATUS_FAIL


class TestUnknownIsNeverApproval:
    """The central rule: absent data must not read as a pass."""

    REQ = Predicate("capacitance", "lte", value=0.5, unit="pF")

    def test_absent_attribute_is_indeterminate_not_pass(self):
        r = evaluate_all([self.REQ], [])
        assert r.verdicts[0].status == UNKNOWN
        assert r.status == STATUS_INDETERMINATE
        assert r.status != STATUS_PASS

    def test_unit_mismatch_is_indeterminate_not_a_verdict(self):
        """Volts where farads were meant is confusion, not a measurement."""
        r = evaluate_all([self.REQ], [attr("Capacitance", "10V")])
        assert r.verdicts[0].status == UNKNOWN
        assert "unit mismatch" in r.verdicts[0].note

    def test_unparseable_value_is_indeterminate(self):
        r = evaluate_all([self.REQ], [attr("Capacitance", "see datasheet")])
        assert r.verdicts[0].status == UNKNOWN

    def test_range_without_an_explicit_bound_refuses_to_guess(self):
        r = evaluate_all([Predicate("Voltage - Supply", "lte", value=5.5, unit="V")],
                         [attr("Voltage - Supply", "0.9V~5.5V")])
        assert r.verdicts[0].status == UNKNOWN
        assert "bound" in r.verdicts[0].note

    def test_indeterminate_blocks_only_when_the_predicate_blocks(self):
        advisory = Predicate("capacitance", "lte", value=0.5, unit="pF", blocking=False)
        assert evaluate_all([advisory], []).status == STATUS_PASS


class TestEsdCapacitance:
    """ESD441DPLR sits on high-speed IO; general-purpose parts are 10-40 pF."""

    REQ = Predicate("capacitance", "lte", value=0.5, unit="pF")

    @pytest.mark.parametrize("raw,expected", [
        ("0.5pF", PASS),
        ("0.4pF", PASS),
        ("1pF", FAIL),
        ("15pF", FAIL),
    ])
    def test_capacitance_ceiling(self, raw, expected):
        assert evaluate(self.REQ, [attr("Capacitance", raw)]).status == expected

    def test_prefix_scaling_is_not_fooled_by_bare_farads(self):
        """5e-13 F is 0.5 pF -- the store holds SI base units, not vendor prefixes."""
        assert evaluate(self.REQ, [attr("Capacitance", "0.0000000000005F")]).status == PASS


class TestFeedbackReferenceEquality:
    """A buck's divider is already on the board, so Vref must match exactly."""

    REQ = Predicate("Feedback Reference Voltage", "eq", value=0.6, unit="V")

    @pytest.mark.parametrize("raw,expected", [
        ("0.6V", PASS),
        ("600mV", PASS),          # same value, different spelling
        ("0.8V", FAIL),
        ("0.5V", FAIL),
    ])
    def test_vref_equality(self, raw, expected):
        rows = [attr("Feedback Reference Voltage", raw)]
        assert evaluate(self.REQ, rows).status == expected


class TestResolutionFloor:
    """TMP112 is 13-bit; TMP102 is pin-compatible but 12-bit -- a downgrade."""

    REQ = Predicate("resolution_bits", "gte", value=13, unit="bit")

    @pytest.mark.parametrize("raw,expected", [("13bit", PASS), ("16bit", PASS), ("12bit", FAIL)])
    def test_resolution(self, raw, expected):
        assert evaluate(self.REQ, [attr("Resolution(Bits)", raw)]).status == expected


class TestEnum:
    """SD20C is bidirectional; a unidirectional part changes clamp behaviour."""

    REQ = Predicate("Diode Configuration", "enum", values=("Bidirectional",))

    @pytest.mark.parametrize("raw,expected", [
        ("Bidirectional", PASS),
        ("bidirectional", PASS),      # case-insensitive
        ("Unidirectional", FAIL),
    ])
    def test_configuration(self, raw, expected):
        assert evaluate(self.REQ, [attr("Diode Configuration", raw)]).status == expected


class TestPackageEquivalence:
    """Delegates to domain/packages so one vocabulary governs land patterns."""

    REQ = Predicate("", "package_equivalent", package="SOT-363-6", label="package")

    def test_differently_spelled_same_package_passes(self):
        assert evaluate(self.REQ, [], package="6-TSSOP, SC-88, SOT-363").status == PASS

    @pytest.mark.parametrize("candidate", ["SOD-882", "SOT-23-6", "QFN-56-EP(8x8)"])
    def test_different_package_fails(self, candidate):
        assert evaluate(self.REQ, [], package=candidate).status == FAIL

    def test_absent_package_is_unknown_not_fail(self):
        """We could not check, which is different from having checked and refused."""
        assert evaluate(self.REQ, [], package=None).status == UNKNOWN


class TestQualifiedMeasurements:
    """Rds(on) is only meaningful with its gate voltage attached."""

    REQ = Predicate("Rds On", "lte", value=0.05, unit="ohm", qualifier="10V")

    def test_matching_qualifier_is_used(self):
        assert evaluate(self.REQ, [attr("Rds On", "47mohm@10V")]).status == PASS

    def test_reading_at_a_different_condition_is_not_borrowed(self):
        """A 2.5 V figure is not evidence about the 10 V requirement."""
        v = evaluate(self.REQ, [attr("Rds On", "85mohm@2.5V")])
        assert v.status == UNKNOWN
        assert "10V" in v.note


class TestDistributorPreference:
    def test_a_parsed_reading_beats_a_preferred_but_unparsed_one(self):
        rows = [attr("Capacitance", "see datasheet", distributor="digikey"),
                attr("Capacitance", "0.4pF", distributor="lcsc")]
        v = evaluate(Predicate("capacitance", "lte", value=0.5, unit="pF"), rows)
        assert v.status == PASS and v.distributor == "lcsc"

    def test_preference_decides_between_two_parsed_readings(self):
        rows = [attr("Capacitance", "0.9pF", distributor="lcsc"),
                attr("Capacitance", "0.4pF", distributor="digikey")]
        v = evaluate(Predicate("capacitance", "lte", value=0.5, unit="pF"), rows)
        assert v.distributor == "digikey" and v.status == PASS

    def test_preference_is_overridable(self):
        rows = [attr("Capacitance", "0.9pF", distributor="lcsc"),
                attr("Capacitance", "0.4pF", distributor="digikey")]
        v = evaluate(Predicate("capacitance", "lte", value=0.5, unit="pF"),
                     rows, prefer=("lcsc",))
        assert v.distributor == "lcsc" and v.status == FAIL


class TestReportAggregation:
    def test_fail_outranks_indeterminate(self):
        r = evaluate_all(
            [Predicate("capacitance", "lte", value=0.5, unit="pF"),
             Predicate("resolution_bits", "gte", value=13, unit="bit")],
            [attr("Capacitance", "15pF")],
        )
        assert r.status == STATUS_FAIL

    def test_all_pass_is_pass(self):
        r = evaluate_all(
            [Predicate("capacitance", "lte", value=0.5, unit="pF"),
             Predicate("resolution_bits", "gte", value=13, unit="bit")],
            [attr("Capacitance", "0.4pF"), attr("Resolution(Bits)", "16bit")],
        )
        assert r.status == STATUS_PASS and r.spec_deltas() == []

    def test_empty_report_passes_vacuously(self):
        assert Report().status == STATUS_PASS

    def test_blockers_lists_only_blocking_non_passes(self):
        r = evaluate_all(
            [Predicate("capacitance", "lte", value=0.5, unit="pF"),
             Predicate("resolution_bits", "gte", value=13, unit="bit", blocking=False)],
            [attr("Capacitance", "15pF")],
        )
        assert [v.predicate.attribute for v in r.blockers] == [canonical_name("capacitance")]


class TestSpecDeltaHandoff:
    """The output must be recordable as a review's spec_deltas without translation."""

    def test_delta_shape_matches_generic_parts(self):
        from domain.generic_parts import _normalize_spec_deltas

        r = evaluate_all(
            [Predicate("Voltage - Supply", "lte", bound="lower", value=0.9, unit="V",
                       label="min VCCA"),
             Predicate("", "package_equivalent", package="SOT-363-6")],
            [attr("Voltage - Supply", "1.65V~5.5V")],
            package="SOD-882",
        )
        deltas = r.spec_deltas()
        assert len(deltas) == 2
        # Round-trips through the real normalizer -- no shape drift.
        normalized = _normalize_spec_deltas(deltas)
        assert {d["kind"] for d in normalized} == {"parametric", "package"}
        assert all(d["blocking"] for d in normalized)
        by_field = {d["field"]: d for d in normalized}
        assert by_field["min VCCA"]["reference"] == "≤ 0.9V"
        assert "1.65" in by_field["min VCCA"]["candidate"]

    def test_passing_predicates_produce_no_deltas(self):
        r = evaluate_all([Predicate("capacitance", "lte", value=0.5, unit="pF")],
                         [attr("Capacitance", "0.4pF")])
        assert r.spec_deltas() == []


class TestAbsenceIsExplained:
    """"Not published" and "you named it wrong" are the same silence otherwise.

    Attribute names canonicalize against a table covering a few dozen spellings
    and pass everything else through normalized, so a predicate written as
    `feedback_reference_voltage` finds nothing when the store holds
    `feedback reference voltage`. That is a predicate bug, not missing data, and
    the two must not look alike.
    """

    def test_no_attributes_at_all_says_so(self):
        v = evaluate(Predicate("capacitance", "lte", value=1, unit="pF"), [])
        assert v.status == UNKNOWN and "no stored attributes" in v.note

    def test_separator_mismatch_is_named_as_such(self):
        rows = [attr("Feedback Reference Voltage", "0.6V")]
        v = evaluate(Predicate("feedback_reference_voltage", "eq", value=0.6, unit="V"), rows)
        assert v.status == UNKNOWN
        assert "did you mean" in v.note and "feedback reference voltage" in v.note

    def test_genuinely_absent_attribute_is_reported_as_absent(self):
        rows = [attr("Capacitance", "0.4pF")]
        v = evaluate(Predicate("resolution_bits", "gte", value=13, unit="bit"), rows)
        assert v.status == UNKNOWN
        assert "not published" in v.note and "did you mean" not in v.note

    def test_qualifier_absence_is_distinguished_from_attribute_absence(self):
        rows = [attr("Rds On", "85mohm@2.5V")]
        v = evaluate(Predicate("Rds On", "lte", value=0.05, unit="ohm", qualifier="10V"), rows)
        assert v.status == UNKNOWN and "no reading at 10V" in v.note
