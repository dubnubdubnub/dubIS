"""Tests for scripts/gen-property-schema.py — the property harness's staleness guard.

The generated module is what stops the JS arbitraries from carrying a second,
hand-maintained copy of domain/schema.py. These pin the two things that make
that guarantee real: the committed file matches what the script renders right
now, and a field shape the script has never seen fails loudly instead of being
guessed at.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain.schema import INVENTORY_FIELDS, FieldDef

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen-property-schema.py"
GENERATED = REPO_ROOT / "tests" / "js" / "property" / "inventory-schema.generated.mjs"


def _load():
    """Import the hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location("_gen_property_schema", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_module_is_current():
    """The committed file equals a fresh render.

    verify.sh and CI run `--check` for this; the test makes it fail in a plain
    `pytest` run too, so the guard is not something only the gate knows about.
    """
    assert GENERATED.read_text(encoding="utf-8") == _load().render(), (
        "tests/js/property/inventory-schema.generated.mjs is stale — run "
        "`python scripts/gen-property-schema.py` and commit."
    )


def test_every_schema_field_is_generatable():
    """No field in the schema is missing a JS type name."""
    gen = _load()
    for field in INVENTORY_FIELDS:
        assert gen.js_type_for(field) in {"string", "int", "float", "string[]"}


def test_an_unknown_field_shape_raises_instead_of_guessing():
    """The whole point of the guard: a new shape must stop the build.

    A fallback here would be worse than useless — the JS generators would
    silently produce records the schema rejects, and every property leaning on
    them would go quietly weak.
    """
    gen = _load()
    weird = FieldDef(
        py_key="reorder_enabled",
        sql_col="reorder_enabled",
        csv_col=None,
        table="parts",
        sql_ddl="INTEGER DEFAULT 0",
        ts_type="boolean",
        default=False,
    )
    with pytest.raises(gen.UnknownFieldShape, match="reorder_enabled"):
        gen.js_type_for(weird)


def test_the_two_halves_of_the_harness_agree_on_shapes():
    """gen-property-schema.py and tests/python/strategies.py teach the same shapes.

    They are deliberately separate tables (one emits a name, the other builds a
    strategy), which is exactly the sort of pair that drifts. This is the
    cheapest possible check that it has not.
    """
    import tests.python.strategies as strategies

    gen = _load()
    assert set(gen._JS_TYPE_BY_SHAPE) == set(strategies._STRATEGY_BY_SHAPE)
