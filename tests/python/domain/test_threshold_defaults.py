"""The shipped default thresholds, pinned against the numbers this app actually
ran with.

`DEFAULT_THRESHOLDS` in data/constants.json is new; the numbers are not. For
years they lived only in each machine's preferences.json — hand-set per
category, and identical on the two machines that still carry them (the Windows
box at D:\\gehub\\dubIS\\data and the fremont deployment). Those 32 entries are
now tests/fixtures/thresholds/legacy-defaults.json, and this file is the proof
that promoting them to a shipped default changed no answer.

The fixture is deliberately the *effective* table rather than the deltas: it
lists every sub-category at its parent's value, so it also pins the inheritance
rule the delta table relies on. tests/js/threshold-logic.test.mjs reads the
same fixture, which is what keeps js/threshold-logic.js and domain/thresholds.py
from drifting into two answers again — the failure this change closes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from inventory_api import InventoryApi
from domain.thresholds import BASE_THRESHOLD, resolve

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "thresholds" / "legacy-defaults.json"
GENERATOR = REPO_ROOT / "scripts" / "gen-threshold-defaults.py"

LEGACY: dict[str, int] = json.loads(FIXTURE.read_text(encoding="utf-8"))

# The one category the hand-set table never covered, so it keeps the base.
UNCOVERED_CATEGORY = "Development Boards, Kits, Programmers"


def _generator():
    """scripts/gen-threshold-defaults.py, loaded by path (hyphen-free but not a
    package on sys.path — same arrangement as tests/python/test_dubis_cli_commands.py)."""
    spec = importlib.util.spec_from_file_location("gen_threshold_defaults", str(GENERATOR))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_fixture_is_the_effective_table_not_the_deltas():
    """If someone trims the fixture to the five non-base categories it stops
    proving inheritance, which is half of what it is there for."""
    assert len(LEGACY) == 32
    assert any(value != BASE_THRESHOLD for value in LEGACY.values())
    assert any(value == BASE_THRESHOLD for value in LEGACY.values())
    assert "Passives - Resistors > Chip Resistors" in LEGACY


@pytest.mark.parametrize("section", sorted(LEGACY))
def test_every_legacy_answer_is_unchanged(section):
    assert resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS) == LEGACY[section]


def test_the_one_uncovered_category_keeps_the_base():
    assert UNCOVERED_CATEGORY in InventoryApi.FLAT_SECTION_ORDER
    assert UNCOVERED_CATEGORY not in LEGACY
    assert resolve(UNCOVERED_CATEGORY, {}, InventoryApi.DEFAULT_THRESHOLDS) == BASE_THRESHOLD


def test_every_category_the_app_can_produce_has_a_pinned_answer():
    """A new category in SECTION_ORDER lands here until someone decides its
    default — which is the point: an unpinned new category would otherwise
    inherit the base silently, the same way these 32 did."""
    unpinned = [
        section for section in InventoryApi.FLAT_SECTION_ORDER
        if section not in LEGACY and section != UNCOVERED_CATEGORY
    ]
    assert unpinned == []


def test_no_default_names_a_category_the_app_does_not_have():
    unknown = sorted(set(InventoryApi.DEFAULT_THRESHOLDS) - set(InventoryApi.FLAT_SECTION_ORDER))
    assert unknown == []


def test_no_default_repeats_the_base():
    """A default equal to the base makes the base untestable and turns a future
    change into a diff over the whole vocabulary."""
    redundant = {
        section: value for section, value in InventoryApi.DEFAULT_THRESHOLDS.items()
        if value == BASE_THRESHOLD
    }
    assert redundant == {}


def test_the_generated_js_module_matches_the_table_it_is_generated_from():
    """verify.sh and CI's js leg run `--check`; this makes the same statement on
    the Python leg, so a stale module cannot pass a run that only did pytest."""
    module = _generator()
    rendered = module._render()
    on_disk = (REPO_ROOT / "js" / "threshold-defaults.generated.js").read_text(encoding="utf-8")
    assert on_disk == rendered, (
        "js/threshold-defaults.generated.js is stale — run "
        "`python scripts/gen-threshold-defaults.py` and commit."
    )


def test_the_generated_js_module_carries_every_default():
    """Belt to the one above: the file must name each category and its number,
    so a generator that silently emitted less would fail here too."""
    text = (REPO_ROOT / "js" / "threshold-defaults.generated.js").read_text(encoding="utf-8")
    for section, value in InventoryApi.DEFAULT_THRESHOLDS.items():
        assert f'"{section}": {value}' in text, section
    assert f"export const BASE_THRESHOLD = {BASE_THRESHOLD};" in text
