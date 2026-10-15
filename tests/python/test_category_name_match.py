"""The category-name match, pinned for the Python half against the SAME case
table the JS half is pinned against (tests/js/category-match-cases.test.mjs).

`dubis search --category-hit` and the inventory grid must not hold two different
definitions of "this term names this category", so neither is allowed its own
case list: both read tests/fixtures/category-name/cases.json. Whoever changes the
rule changes both implementations or one suite goes red naming the case.

The case that justifies the duplication is `ic` vs "ICs - Microcontrollers" =
False. A substring implementation passes everything else here and fails that one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.category_match import matches_category_name, name_tokens

REPO = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (REPO / "tests" / "fixtures" / "category-name" / "cases.json").read_text()
)["cases"]


def _case_id(case: dict) -> str:
    return f"{case['query']!r}-vs-{case['section'] or 'none'}-{case['expect']}"


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_case_table(case: dict) -> None:
    assert matches_category_name(case["query"], case["section"]) is case["expect"], case["why"]


def test_case_table_covers_both_verdicts() -> None:
    """A table that only contains hits proves nothing about over-matching."""
    trues = sum(1 for c in CASES if c["expect"])
    assert len(CASES) > 20
    assert 0 < trues < len(CASES)


def test_no_case_contradicts_another() -> None:
    """The JS suite asserts this too; a duplicated (query, section) pair with two
    expectations would make one of the two suites pass by accident."""
    seen: dict[tuple[str, str], bool] = {}
    for case in CASES:
        key = (case["query"], case["section"])
        if key in seen:
            assert seen[key] == case["expect"], f"contradictory pair: {key}"
        seen[key] = case["expect"]


def test_name_tokens_drops_every_section_separator() -> None:
    assert name_tokens("ICs - Power / Voltage Regulators") == ["ics", "power", "voltage", "regulators"]
    assert name_tokens("Connectors > SMD") == ["connectors", "smd"]
    assert name_tokens("Development Boards, Kits, Programmers") == [
        "development", "boards", "kits", "programmers",
    ]


def test_a_non_string_section_is_an_answer_not_an_error() -> None:
    """A section value read out of a CSV cell can be None. That means "no
    category", which is a verdict, not a crash — and never a match-everything."""
    for missing in (None, "", 0):
        assert matches_category_name("diodes", missing) is False
    assert name_tokens(None) == []


def test_the_two_letter_case_the_rule_exists_for() -> None:
    """Restated as a named test because it is the regression, not a case: a
    substring match here would light up nine IC sections for two letters."""
    assert matches_category_name("ic", "ICs - Microcontrollers") is False
    assert matches_category_name("ic", "Switches") is False
    assert matches_category_name("ics", "ICs - Microcontrollers") is True
