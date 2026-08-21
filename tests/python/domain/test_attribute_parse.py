"""Tests for domain.attribute_parse — canonical names + attribute value parsing.

The corpus tests at the bottom run against the committed real distributor
capture (tests/fixtures/generated/distributor-scrapes.json) and print the
measured parse coverage, so the number in the PR description stays honest.
"""

from __future__ import annotations

import collections

import pytest

from domain.attribute_parse import (
    CANONICAL_NAME_ALIASES,
    CANONICAL_NAMES,
    KIND_EMPTY,
    KIND_RANGE,
    KIND_SCALAR,
    KIND_TOLERANCE,
    KIND_UNPARSED,
    canonical_name,
    normalize_name,
    parse_value,
)
from helpers import lcsc_fixture_param_values, lcsc_fixture_products

# ── canonical names ──────────────────────────────────────────────────────────


class TestCanonicalName:
    def test_maps_lcsc_and_digikey_spellings_together(self):
        assert canonical_name("Voltage - Supply") == "supply_voltage"
        assert canonical_name("Voltage - Supply (Vcc/Vdd)") == "supply_voltage"

    def test_case_and_whitespace_insensitive(self):
        assert canonical_name("Operating temperature") == "operating_temperature"
        assert canonical_name("  OPERATING   TEMPERATURE ") == "operating_temperature"

    def test_unmapped_name_keeps_its_own_name(self):
        """Unknown parametrics are stored under their own name, never force-fitted."""
        assert canonical_name("Lamp Holder Type") == "lamp holder type"
        assert canonical_name("Some Brand New Parametric") == "some brand new parametric"

    def test_case_variants_of_an_unmapped_name_share_one_key(self):
        assert canonical_name("type") == canonical_name("Type")

    def test_empty_name(self):
        assert canonical_name("") == ""
        assert normalize_name(None) == ""

    def test_alias_table_keys_are_already_normalized(self):
        """A row with a non-normalized key could never match — catch it here."""
        for alias in CANONICAL_NAME_ALIASES:
            assert alias == normalize_name(alias), alias

    def test_canonical_names_are_snake_case_identifiers(self):
        for name in CANONICAL_NAMES:
            assert name.replace("_", "a").isalnum() and name.islower(), name

    def test_no_canonical_is_also_an_alias_of_something_else(self):
        for canonical in CANONICAL_NAMES:
            mapped = CANONICAL_NAME_ALIASES.get(canonical)
            assert mapped in (None, canonical), (canonical, mapped)


# ── value parsing ────────────────────────────────────────────────────────────


class TestParseScalar:
    def test_value_with_unit(self):
        parsed = parse_value("3.3V")
        assert parsed.kind == KIND_SCALAR
        assert parsed.value_min == parsed.value_max == pytest.approx(3.3)
        assert parsed.unit == "V"
        assert parsed.raw == "3.3V"

    def test_si_prefix_scales_to_base_unit(self):
        assert parse_value("100nF").value_min == pytest.approx(1e-7)
        assert parse_value("0.5pF").value_min == pytest.approx(5e-13)
        assert parse_value("16MHz").value_min == pytest.approx(16e6)
        assert parse_value("100kΩ").value_min == pytest.approx(1e5)
        assert parse_value("650mΩ").value_min == pytest.approx(0.65)

    def test_uppercase_k_is_also_kilo(self):
        assert parse_value("100KΩ").value_min == pytest.approx(1e5)

    def test_micro_spellings_agree(self):
        assert parse_value("13uA").value_min == pytest.approx(13e-6)
        assert parse_value("13µA").value_min == pytest.approx(13e-6)

    def test_plain_integer_has_no_unit(self):
        parsed = parse_value("10000")
        assert parsed.kind == KIND_SCALAR
        assert parsed.value_min == parsed.value_max == 10000.0
        assert parsed.unit == ""

    def test_thousands_separators(self):
        parsed = parse_value("4,000,000 Cycles")
        assert parsed.value_min == 4000000.0
        assert parsed.unit == "Cycles"

    def test_unicode_degree_celsius_folds_to_ascii_unit(self):
        assert parse_value("±2℃").unit == "°C"

    def test_per_unit(self):
        parsed = parse_value("7uV/℃")
        assert parsed.value_min == pytest.approx(7e-6)
        assert parsed.unit == "V/°C"

    def test_per_unit_scales_the_denominator_too(self):
        parsed = parse_value("1V/us")
        assert parsed.value_min == pytest.approx(1e6)
        assert parsed.unit == "V/s"

    def test_whitespace_separated_word_is_taken_verbatim_as_unit(self):
        parsed = parse_value("200 Years")
        assert parsed.value_min == 200.0
        assert parsed.unit == "Years"

    def test_bit_is_not_si_prefixed(self):
        """"11bit" is a resolution; "2Kbit" is 2048 bits, not 2000 — refuse it."""
        assert parse_value("11bit").value_min == 11.0
        assert parse_value("2Kbit").kind == KIND_UNPARSED


class TestParseRange:
    def test_both_endpoints_preserved(self):
        parsed = parse_value("0.9V ~ 5.5V")
        assert parsed.kind == KIND_RANGE
        assert parsed.value_min == pytest.approx(0.9)
        assert parsed.value_max == pytest.approx(5.5)
        assert parsed.unit == "V"

    def test_negative_temperature_range(self):
        parsed = parse_value("-40℃~+125℃")
        assert parsed.kind == KIND_RANGE
        assert (parsed.value_min, parsed.value_max) == (-40.0, 125.0)
        assert parsed.unit == "°C"

    def test_symmetric_range(self):
        parsed = parse_value("-16V~16V")
        assert (parsed.value_min, parsed.value_max) == (-16.0, 16.0)

    def test_bare_endpoint_inherits_the_other_unit(self):
        parsed = parse_value("0.9~5.5V")
        assert parsed.kind == KIND_RANGE
        assert parsed.unit == "V"
        assert parsed.value_max == pytest.approx(5.5)

    def test_mismatched_units_are_not_flattened(self):
        assert parse_value("1V~2A").kind == KIND_UNPARSED

    def test_unparseable_endpoint_leaves_the_whole_value_raw(self):
        assert parse_value("low~high").kind == KIND_UNPARSED


class TestParseTolerance:
    def test_percent_tolerance_is_symmetric(self):
        parsed = parse_value("±10%")
        assert parsed.kind == KIND_TOLERANCE
        assert (parsed.value_min, parsed.value_max) == (-10.0, 10.0)
        assert parsed.unit == "%"

    def test_ppm_per_degree(self):
        parsed = parse_value("±100ppm/℃")
        assert (parsed.value_min, parsed.value_max) == (-100.0, 100.0)
        assert parsed.unit == "ppm/°C"

    def test_ascii_plus_minus(self):
        assert parse_value("+/-1%").kind == KIND_TOLERANCE

    def test_unparseable_tolerance_body(self):
        assert parse_value("±lots").kind == KIND_UNPARSED


class TestParseQualifier:
    def test_condition_after_at_is_preserved_verbatim(self):
        parsed = parse_value("600mV@1A")
        assert parsed.kind == KIND_SCALAR
        assert parsed.value_min == pytest.approx(0.6)
        assert parsed.unit == "V"
        assert parsed.qualifier == "1A"

    def test_condition_on_a_range(self):
        parsed = parse_value("-40℃~+85℃@(Ta)")
        assert parsed.kind == KIND_RANGE
        assert (parsed.value_min, parsed.value_max) == (-40.0, 85.0)
        assert parsed.qualifier == "(Ta)"

    def test_multi_part_condition(self):
        parsed = parse_value("19ns@4.5V,50pF")
        assert parsed.value_min == pytest.approx(19e-9)
        assert parsed.qualifier == "4.5V,50pF"

    def test_non_numeric_condition(self):
        assert parse_value("250V@AC").qualifier == "AC"


class TestParseUnparsed:
    def test_free_text_feature_string(self):
        """The `Features` attribute is prose — it must stay raw, not become a number."""
        raw = "Short Circuit Protection;Over Current Protection"
        parsed = parse_value(raw)
        assert parsed.kind == KIND_UNPARSED
        assert parsed.raw == raw
        assert parsed.value_min is None and parsed.value_max is None
        assert parsed.unit == ""

    def test_dielectric_code_is_not_a_number(self):
        assert parse_value("X7R").kind == KIND_UNPARSED

    def test_leading_digits_of_a_part_family_are_not_a_magnitude(self):
        """"74HC" must not be read as 74 "HC" — that would invent a number."""
        assert parse_value("74HC").kind == KIND_UNPARSED

    def test_semicolon_list_is_not_flattened(self):
        assert parse_value("130%;260%").kind == KIND_UNPARSED

    def test_enumerated_text(self):
        for raw in ("Water Clear", "Push-pull", "I2C", "Surface Mount, Right Angle"):
            assert parse_value(raw).kind == KIND_UNPARSED, raw

    def test_parsed_flag(self):
        assert parse_value("3.3V").parsed is True
        assert parse_value("X7R").parsed is False


class TestParseEmpty:
    def test_lcsc_dash_means_absent(self):
        parsed = parse_value("-")
        assert parsed.kind == KIND_EMPTY
        assert parsed.value_min is None

    def test_empty_and_whitespace(self):
        assert parse_value("").kind == KIND_EMPTY
        assert parse_value("   ").kind == KIND_EMPTY

    def test_none(self):
        assert parse_value(None).kind == KIND_EMPTY

    def test_n_a(self):
        assert parse_value("N/A").kind == KIND_EMPTY


# ── real corpus ──────────────────────────────────────────────────────────────


class TestRealCorpusCoverage:
    """Measured against the committed LCSC capture — 21 parts, 214 parametrics.

    At the time of writing: 147/214 values (68.7%) yield a magnitude, 23 are
    published as absent ("-"), and 44 are genuinely categorical (enum text or
    ';'-joined `Features` prose). Excluding the absent ones, 147/191 = 77.0%
    of published values parse. The assertions below are floors well under the
    measured value — they guard against regression, they are not the target.
    """

    def test_reports_parse_coverage(self, capsys):
        values = lcsc_fixture_param_values()
        kinds = collections.Counter(parse_value(value).kind for _, _, value in values)
        total = len(values)
        published = total - kinds[KIND_EMPTY]
        numeric = kinds[KIND_SCALAR] + kinds[KIND_RANGE] + kinds[KIND_TOLERANCE]
        with capsys.disabled():
            print(f"\n[attribute parse coverage] {total} captured LCSC values across "
                  f"{len({code for code, _, _ in values})} parts: "
                  f"{numeric} numeric ({numeric / total:.1%}), "
                  f"{kinds[KIND_UNPARSED]} unparsed, {kinds[KIND_EMPTY]} absent — "
                  f"{numeric / published:.1%} of published values parse "
                  f"({dict(kinds)})")
        assert total >= 200
        assert numeric / published >= 0.60

    def test_every_value_keeps_its_raw_string(self):
        for _code, _name, value in lcsc_fixture_param_values():
            assert parse_value(value).raw == value

    def test_unparsed_values_never_carry_numbers(self):
        for _code, _name, value in lcsc_fixture_param_values():
            parsed = parse_value(value)
            if not parsed.parsed:
                assert parsed.value_min is None and parsed.value_max is None, value

    def test_parsed_values_always_carry_both_bounds_and_ordered(self):
        for _code, _name, value in lcsc_fixture_param_values():
            parsed = parse_value(value)
            if parsed.parsed:
                assert parsed.value_min is not None and parsed.value_max is not None, value
                assert parsed.value_min <= parsed.value_max, value

    def test_real_client_attributes_parse_the_same_way(self):
        """Same corpus, but through the real LcscClient's attribute extraction."""
        products = lcsc_fixture_products()
        seen = 0
        for product in products.values():
            for attribute in product["attributes"]:
                seen += 1
                # The client filters "-" itself, so nothing reaching us is empty.
                assert parse_value(attribute["value"]).kind != KIND_EMPTY
        assert seen >= 150

    def test_every_captured_name_gets_a_non_empty_key(self):
        """No captured parametric may key on "" — that would merge unrelated rows."""
        for _code, name, _value in lcsc_fixture_param_values():
            if name:
                assert canonical_name(name), name

    def test_spelling_drift_in_the_corpus_collapses(self):
        """The corpus really does contain case-only duplicates; they must merge."""
        names = {name for _code, name, _value in lcsc_fixture_param_values() if name}
        assert {"Operating Temperature", "Operating temperature"} <= names
        assert {"Type", "type"} <= names
        assert canonical_name("Operating Temperature") == canonical_name("Operating temperature")
        assert canonical_name("Type") == canonical_name("type")
        assert len({canonical_name(n) for n in names}) < len(names)

    def test_captured_names_reaching_the_table_land_on_a_canonical(self):
        mapped = {canonical_name(name)
                  for _code, name, _value in lcsc_fixture_param_values()
                  if normalize_name(name) in CANONICAL_NAME_ALIASES}
        # The captured corpus exercises a real slice of the table, not one row.
        assert len(mapped) >= 10
        assert mapped <= CANONICAL_NAMES

    @pytest.mark.parametrize(("lcsc", "digikey"), [
        ("Voltage - Supply", "Voltage - Supply (Vcc/Vdd)"),
        ("Voltage Rating", "Voltage - Rated"),
        ("Power(Watts)", "Power (Watts)"),
        ("Quiescent Current", "Current - Quiescent (Iq)"),
        ("Reverse Leakage Current (Ir)", "Current - Reverse Leakage @ Vr"),
        ("Voltage - Forward(Vf)", "Voltage - Forward (Vf) (Max) @ If"),
        ("Resolution(Bits)", "Resolution (Bits)"),
        ("Equivalent Series Resistance(ESR)", "ESR (Equivalent Series Resistance)"),
        ("Installation method", "Mounting Type"),
        ("Isolation Voltage(Vrms)", "Voltage - Isolation"),
    ])
    def test_cross_distributor_spellings_share_a_canonical(self, lcsc, digikey):
        """The point of the table: one predicate can read either distributor's row."""
        assert canonical_name(lcsc) == canonical_name(digikey)
        assert canonical_name(lcsc) in CANONICAL_NAMES
