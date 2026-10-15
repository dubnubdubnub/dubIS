"""The shipped default thresholds, pinned against the numbers this app actually
ran with, and the derivation that extrapolates them to categories nobody tuned.

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

The fixture also carries DERIVED cases (the entries appended after the legacy
32): categories that exist in neither table and must resolve through the family
tables, plus the categories that land on the base BY DECISION. Same rule as the
legacy half — one fixture, both languages — so a family table that drifts
between Python and JS fails twice.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from inventory_api import InventoryApi
from domain.thresholds import (
    BASE_BY_DECISION,
    BASE_THRESHOLD,
    PRESET_BUNDLES,
    PRESET_IC_HEAVY,
    PRESET_INFO,
    PRESET_SHIPPED,
    PRESETS,
    PRESET_TIGHT,
    bundle_for,
    derive,
    presets_payload,
    resolve,
    stored_preset_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "thresholds" / "legacy-defaults.json"
GENERATOR = REPO_ROOT / "scripts" / "gen-threshold-defaults.py"

FULL: dict[str, int] = json.loads(FIXTURE.read_text(encoding="utf-8"))

# The entries appended for derivation. The legacy 32 are the hand-set table
# from #507 and must never be edited here — a new category gets an entry in
# the DERIVED half, and resolve() must produce exactly that number from the
# family tables alone.
DERIVED_CASES = {
    # IC family (the mode of the operator's tuned IC overrides).
    "ICs - Clock and Timing": 100,
    # The family prefix covers children of a base-by-decision parent too: the
    # decision is about that row, not about categories nobody has seen yet.
    "ICs - Clock and Timing > Programmable Oscillators": 100,
    "ICs - Power / Voltage Regulators > DC-DC": 100,
    # Passives family (median of the tuned passives).
    "Passives - Varistors": 20,
    "Passives - Inductors > Chokes": 20,
    # Connector/discrete family (the tuned connector number).
    "Discrete Semiconductors > IGBTs": 20,
    "Connectors > RF": 20,
    # Base BY DECISION / by explicit no-opinion — pinned so the base is always
    # a stated answer, never a silent fall-through.
    "Development Boards, Kits, Programmers": 50,
    "Mechanical & Hardware > Standoffs": 50,
    "Something Brand New": 50,
}

LEGACY: dict[str, int] = {k: v for k, v in FULL.items() if k not in DERIVED_CASES}

# The one category the hand-set table never covered, kept at the base by
# decision (it is in BASE_BY_DECISION, not derived).
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


def test_the_derived_half_is_appended_not_merged_into_the_legacy_half():
    """The legacy 32 are a historical record; editing one to make a derivation
    pass is rewriting the pin. This names the drift instead of letting a
    parametrised legacy failure hide it."""
    assert set(DERIVED_CASES) <= set(FULL)
    assert len(FULL) == len(LEGACY) + len(DERIVED_CASES)


@pytest.mark.parametrize("section", sorted(LEGACY))
def test_every_legacy_answer_is_unchanged(section):
    assert resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS) == LEGACY[section]


@pytest.mark.parametrize("section", sorted(DERIVED_CASES))
def test_derived_categories_answer_the_family_table(section):
    """A category with neither override nor default resolves through the
    family tables — never through a silent fall to the base. The same fixture
    rows are asserted against js/threshold-logic.js."""
    assert resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS) == DERIVED_CASES[section]


def test_the_one_uncovered_category_keeps_the_base_by_decision():
    """Development Boards is not an oversight: it is NAMED in
    BASE_BY_DECISION, which is what separates "kept at $50 on purpose" from
    "nobody ever looked" — the distinction the whole derivation change turns
    on."""
    assert UNCOVERED_CATEGORY in InventoryApi.FLAT_SECTION_ORDER
    assert UNCOVERED_CATEGORY in LEGACY or UNCOVERED_CATEGORY in DERIVED_CASES
    assert UNCOVERED_CATEGORY in BASE_BY_DECISION
    assert resolve(UNCOVERED_CATEGORY, {}, InventoryApi.DEFAULT_THRESHOLDS) == BASE_THRESHOLD


def test_every_category_the_app_can_produce_has_a_pinned_answer():
    """A new category in SECTION_ORDER lands in the fixture (legacy or derived
    half) until someone decides its default — which is the point: an unpinned
    new category would otherwise inherit the base silently, the same way these
    32 did."""
    unpinned = [
        section for section in InventoryApi.FLAT_SECTION_ORDER
        if section not in FULL
    ]
    assert unpinned == []


def test_no_vocabulary_category_falls_to_the_base_silently():
    """THE guard for "a new category must never silently get $50 again": every
    category the app can produce either resolves to something better than the
    base, or is explicitly listed in BASE_BY_DECISION. A new SECTION_ORDER
    entry that matches no family and joins no list fails here — that is the
    failure mode this test exists to have."""
    silent = [
        section for section in InventoryApi.FLAT_SECTION_ORDER
        if resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS) == BASE_THRESHOLD
        and section not in BASE_BY_DECISION
    ]
    assert silent == []


def test_base_by_decision_members_actually_land_on_the_base():
    """A name in BASE_BY_DECISION that resolves elsewhere is a stale decision:
    either the family table moved it (the bug the list prevents) or a default
    now covers it (the list entry is dead). Both must be noticed."""
    moved = {
        section: resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS)
        for section in BASE_BY_DECISION
        if resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS) != BASE_THRESHOLD
    }
    assert moved == {}


def test_base_by_decision_never_overrides_a_real_answer():
    """The decision list only speaks where the tables are silent — an override
    and a default both still outrank it."""
    assert resolve("Passives - Inductors", {"Passives - Inductors": 12},
                   InventoryApi.DEFAULT_THRESHOLDS) == 12
    assert resolve("Connectors", {}, InventoryApi.DEFAULT_THRESHOLDS) == 20


def test_derivation_is_a_no_opinion_not_a_zero():
    """derive() answers None (not 0, not the base) for a string outside every
    family, so the caller — not the table — owns the base."""
    assert derive("Something Brand New") is None
    assert derive("ICs - Clock and Timing") == 100
    # The decision list answers None too, but the guard tests above are what
    # make that None a stated decision rather than an absence.
    assert derive("Switches") is None


# ── Presets ─────────────────────────────────────────────────────────────────


def test_preset_ids_are_exactly_the_shipped_sentinel_plus_bundles():
    assert PRESETS == (PRESET_SHIPPED, PRESET_IC_HEAVY, PRESET_TIGHT)
    assert set(PRESET_BUNDLES) == set(PRESETS) - {PRESET_SHIPPED}
    assert set(PRESET_INFO) == set(PRESETS)


def test_caller_preset_lookup_raises_on_an_unrecognised_id():
    """The plan_cart(default_preset=...) asymmetry: a caller can fix its
    request, so guessing a different bundle is worse than saying no."""
    with pytest.raises(ValueError, match="unknown threshold preset"):
        bundle_for("no-such-preset")
    assert bundle_for(PRESET_SHIPPED) == {}
    assert bundle_for(PRESET_IC_HEAVY) is PRESET_BUNDLES[PRESET_IC_HEAVY]


def test_stored_preset_id_falls_back_instead_of_raising():
    """The _effective_preset asymmetry: a stale string in preferences.json
    degrades to the shipped order, it does not make the app unreadable."""
    assert stored_preset_id("no-such-preset") == PRESET_SHIPPED
    assert stored_preset_id("") == PRESET_SHIPPED
    assert stored_preset_id(None) == PRESET_SHIPPED
    assert stored_preset_id(41) == PRESET_SHIPPED
    assert stored_preset_id(" ic-heavy ") == PRESET_IC_HEAVY
    assert stored_preset_id(PRESET_SHIPPED) == PRESET_SHIPPED


def test_preset_sits_between_override_and_default():
    """The preset is a choice, so it outranks the shipped table; a per-category
    delta is a more specific choice, so it outranks the preset."""
    bundle = {"Diodes": 90}
    assert resolve("Diodes", {}, InventoryApi.DEFAULT_THRESHOLDS, preset=bundle) == 90
    assert resolve("Diodes", {"Diodes": 7}, InventoryApi.DEFAULT_THRESHOLDS,
                   preset=bundle) == 7
    # A bundle may name a parent; children inherit it like any table.
    assert resolve("Diodes > Zener", {}, {}, preset=bundle) == 90
    # With no preset at all, nothing moves.
    assert resolve("Diodes", {}, InventoryApi.DEFAULT_THRESHOLDS) == 10


def test_preset_beats_default_and_derivation_alone_reaches_new_categories():
    """The ic-heavy bundle moves the tuned IC names; a NEW IC category derives
    the family value whether or not a preset is chosen — the preset names
    categories, derivation extrapolates to the ones nobody has."""
    bundle = bundle_for(PRESET_IC_HEAVY)
    assert resolve("ICs - Microcontrollers", {}, InventoryApi.DEFAULT_THRESHOLDS) == 50
    assert resolve("ICs - Microcontrollers", {}, InventoryApi.DEFAULT_THRESHOLDS,
                   preset=bundle) == 200
    assert resolve("ICs - Clock and Timing", {}, InventoryApi.DEFAULT_THRESHOLDS,
                   preset=bundle) == 100


def test_preset_bundles_are_sparse_and_never_repeat_a_no_op():
    """A bundle entry equal to what the category already answers is noise that
    will outlive its justification; a bundle that materialised the whole table
    would be the resolved-table persistence this design forbids."""
    for preset_id, bundle in PRESET_BUNDLES.items():
        for section, value in bundle.items():
            without = resolve(section, {}, InventoryApi.DEFAULT_THRESHOLDS)
            assert value != without, f"{preset_id}: {section!r} repeats {without}"


def test_preset_bundle_keys_name_categories_the_app_has():
    """Same typo class as a default for a nonexistent category — a bundle entry
    nothing can ever select. (Bundles may name parents; parents are in the
    vocabulary too.)"""
    unknown = sorted(
        section
        for bundle in PRESET_BUNDLES.values()
        for section in bundle
        if section not in InventoryApi.FLAT_SECTION_ORDER
    )
    assert unknown == []


def test_presets_payload_shape_for_the_wire():
    """GET /v1/meta serves this verbatim; the frontend picker and `dubis`
    both read it, so the shape is a contract."""
    payload = presets_payload()
    assert [p["id"] for p in payload] == list(PRESETS)
    for entry in payload:
        assert set(entry) == {"id", "name", "description", "thresholds"}
        assert isinstance(entry["thresholds"], dict)
        assert all(isinstance(v, int) for v in entry["thresholds"].values())
    assert payload[0]["id"] == PRESET_SHIPPED and payload[0]["thresholds"] == {}


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


def test_the_generated_js_module_carries_the_derivation_and_preset_tables():
    """The JS side resolves with THESE tables, not hand-copied ones — so the
    file must name every family prefix, every base-by-decision category, and
    every preset bundle entry."""
    text = (REPO_ROOT / "js" / "threshold-defaults.generated.js").read_text(encoding="utf-8")
    from domain.thresholds import FAMILY_THRESHOLDS
    for prefix, value in FAMILY_THRESHOLDS:
        assert f'["{prefix}", {value}]' in text, prefix
    for section in BASE_BY_DECISION:
        assert f'"{section}"' in text, section
    for preset_id, bundle in PRESET_BUNDLES.items():
        assert f'"{preset_id}"' in text, preset_id
        for section, value in bundle.items():
            assert f'"{section}": {value}' in text, section
