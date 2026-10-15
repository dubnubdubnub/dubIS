"""The resolution spec, pinned as data — the Python half.

`tests/fixtures/thresholds/resolution-cases.json` is iterated here AND by
`tests/js/threshold-resolution-parity.test.mjs`; both languages must answer
every case identically. That agreement is the point of the whole fixture
arrangement (the frontend once fell back to $50 and `dubis low-stock` to 0,
so the same part was low for one caller and fine for the other) — the prose
spec lives in domain/thresholds.py's "The resolution spec" section, and this
is what keeps the prose, the Python and the JS from drifting apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.thresholds import resolve

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures" / "thresholds" / "resolution-cases.json"
)


def _cases() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["cases"], "an empty case table pins nothing"
    return data["cases"]


def test_the_fixture_is_readable_and_nonempty():
    assert FIXTURE.exists(), f"missing shared parity fixture: {FIXTURE}"
    assert _cases()


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_resolution_case(case: dict) -> None:
    got = resolve(
        case["section"],
        case["overrides"],
        case["defaults"],
        case["base"],
        preset=case["preset"],
        server=case["server"],
    )
    assert got == case["expected"], (
        f"{case['name']}: resolve() answered {got}, fixture says {case['expected']}. "
        "If the spec genuinely changed, change it in domain/thresholds.py's prose "
        "FIRST, then this fixture, then both languages — in one commit."
    )
