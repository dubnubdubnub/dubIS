"""domain/thresholds.py — the one resolution order for stock-value thresholds.

The order is the behaviour under test: an override always wins, a parent's
entry covers its children at every level, and the base is the last resort
rather than the first guess.
"""

from __future__ import annotations

from domain.thresholds import BASE_THRESHOLD, parent_of, resolve


class TestParentOf:
    def test_top_level_section_has_no_parent(self):
        assert parent_of("Diodes") == ""

    def test_compound_section_returns_the_parent_half(self):
        assert parent_of("Passives - Resistors > Chip Resistors") == "Passives - Resistors"

    def test_a_hyphen_is_not_a_separator(self):
        # "Passives - Resistors" contains the same " - " the separator opens
        # with; only " > " splits a section from its sub-category.
        assert parent_of("ICs - Power / Voltage Regulators") == ""


class TestResolve:
    def test_nothing_configured_answers_the_base(self):
        assert resolve("Other", {}, {}) == BASE_THRESHOLD

    def test_exact_override_wins(self):
        assert resolve("Diodes", {"Diodes": 7}, {"Diodes": 10}) == 7

    def test_child_inherits_the_parent_override(self):
        assert resolve("Diodes > Zener", {"Diodes": 7}, {}) == 7

    def test_shipped_default_applies_when_no_override_exists(self):
        assert resolve("Diodes", {}, {"Diodes": 10}) == 10

    def test_child_inherits_the_parent_default(self):
        assert resolve("Passives - Capacitors > MLCC", {}, {"Passives - Capacitors": 20}) == 20

    def test_a_parent_override_outranks_a_child_default(self):
        """The user spoke about the family; the shipped table must not overrule
        something they set deliberately one level up."""
        assert resolve("Diodes > Zener", {"Diodes": 3}, {"Diodes > Zener": 90}) == 3

    def test_an_exact_child_override_outranks_a_parent_override(self):
        assert resolve("Diodes > Zener", {"Diodes": 3, "Diodes > Zener": 90}, {}) == 90

    def test_missing_tables_are_not_an_error(self):
        """A preferences file written before thresholds existed has no slice at
        all, and a fresh install has no defaults argument to pass."""
        assert resolve("Diodes", None, None) == BASE_THRESHOLD

    def test_string_values_from_a_preferences_file_still_compare_as_numbers(self):
        assert resolve("Diodes", {"Diodes": "12"}, {}) == 12

    def test_caller_may_name_its_own_base(self):
        """`dubis low-stock` passes the base the server it is talking to
        reports, rather than assuming a remote build agrees with this one."""
        assert resolve("Other", {}, {}, base=120) == 120

    def test_zero_is_a_value_not_an_absence(self):
        """A user who drags a slider to $0 means it: nothing is ever green.
        Falling through to the default there would silently undo them."""
        assert resolve("Diodes", {"Diodes": 0}, {"Diodes": 10}) == 0
