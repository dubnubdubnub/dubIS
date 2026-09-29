"""Score categorize.py against thousands of real LCSC and DigiKey parts.

Every part in tests/fixtures/categorize-corpus/ is a real catalogue row --
description verbatim, labelled with the *distributor's own* category -- and
categorize_corpus_labels.py translates that category into the dubIS sections
that count as a right answer.  See the README beside the fixtures for where
the rows came from and how to rebuild them.

A part can land in one of three places:
  a section its category allows   -- correct
  Other, though a section fits    -- a miss; harmless, it is just unsorted
  a section its category rules out -- WRONG: the TMUXHS4212 bug, an analog
                                     switch filed as a resistor because its
                                     on-resistance says "8.4OHM"

WRONG is the one that matters, so it is held exactly: the parts that land
wrong today are listed with a reason in known-misfiles.json (mostly the
distributor filing a part under a category its own description contradicts),
and any other part landing wrong fails.  Misses are held by a floor.

To see the whole picture while changing the rules -- including a 100k-part
corpus kept out of git -- run scripts/score-categorize-corpus.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from categorize import categorize
from categorize_corpus_labels import allowed

CORPUS = Path(__file__).resolve().parent.parent / "fixtures" / "categorize-corpus"
DISTRIBUTORS = ("lcsc", "digikey")

# Share of labelled parts that must land in a section their category allows.
# Measured at 94.0% (LCSC) and 96.4% (DigiKey) when this corpus was added;
# raise these when the rules improve, never lower them to make a change pass.
MIN_CORRECT = {"lcsc": 0.935, "digikey": 0.96}


def _load(distributor: str) -> list[dict]:
    with open(CORPUS / f"{distributor}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _section(part: dict) -> str:
    row = {"Description": part["description"], "Manufacture Part Number": part["mpn"],
           "Manufacturer": part["manufacturer"]}
    return categorize(row).split(" > ")[0]


def _scored(distributor: str) -> list[tuple[dict, set[str], str]]:
    """(part, allowed sections, section it lands in) for every labelled part."""
    out = []
    for part in _load(distributor):
        want = allowed(distributor, part["category_path"])
        if want is not None:
            out.append((part, want, _section(part)))
    return out


@pytest.mark.parametrize("distributor", DISTRIBUTORS)
def test_corpus_is_substantial(distributor):
    # A truncated or emptied fixture would make every other check pass.
    parts = _load(distributor)
    assert len(parts) >= 1500
    assert len({tuple(p["category_path"]) for p in parts}) >= 30


@pytest.mark.parametrize("distributor", DISTRIBUTORS)
def test_every_category_has_a_label(distributor):
    # allowed() raises Unmapped for a category nobody has placed; a rebuilt
    # corpus that brings in a new one must be labelled, not silently dropped.
    for part in _load(distributor):
        allowed(distributor, part["category_path"])


def test_no_part_lands_in_a_wrong_section():
    known = json.loads((CORPUS / "known-misfiles.json").read_text(encoding="utf-8"))
    wrong = {}
    for distributor in DISTRIBUTORS:
        for part, want, got in _scored(distributor):
            if got != "Other" and got not in want:
                wrong[part["part_number"]] = (got, part)

    new = {pn: v for pn, v in wrong.items()
           if pn not in known or known[pn]["lands_in"] != v[0]}
    assert not new, "parts now filed in a section their distributor category rules out:\n" + "\n".join(
        f"  {pn}: {got!r} <- {' / '.join(p['category_path'])} | {p['description'][:100]}"
        for pn, (got, p) in sorted(new.items()))

    fixed = sorted(set(known) - set(wrong))
    assert not fixed, (
        f"these known misfiles now land correctly -- remove them from known-misfiles.json: {fixed}")


@pytest.mark.parametrize("distributor", DISTRIBUTORS)
def test_share_correct_does_not_regress(distributor):
    scored = _scored(distributor)
    correct = sum(got in want for _, want, got in scored) / len(scored)
    assert correct >= MIN_CORRECT[distributor], (
        f"{distributor}: {correct:.1%} correct, floor is {MIN_CORRECT[distributor]:.1%}; "
        f"run scripts/score-categorize-corpus.py to see what moved")
