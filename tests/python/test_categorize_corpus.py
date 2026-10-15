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

import category_map
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


# ── with the distributor category fed in ────────────────────────────────────
#
# Everything above scores the description rules alone, which is how those
# rules have always been held. The tests below score what categorize() actually
# does now: the distributor's own category first, the description second.
#
# Careful reading, because the obvious property here is a trap. The map's
# opinion is a member of the allowed set by construction -- both come from the
# same rows -- so "feeding the map never lands a part somewhere its category
# rules out" is true forever and proves nothing. What is NOT true by
# construction, and so is worth asserting, is the relationship between the two
# *independent* signals: what the distributor's taxonomy says, and what the
# description rules say. They are written by different people from different
# artifacts, and a part where they disagree is a bug in one of them.


def _with_category(distributor: str, part: dict) -> str:
    """The section categorize() gives this part now, categories wired in."""
    distributor_category = category_map.DistributorCategory(
        distributor, *category_map.CategoryMap._path_to_key(  # noqa: SLF001
            distributor, part["category_path"]))
    known = _known_misfiles()
    misfile = known.get(part["part_number"])
    refuse = (lambda section: section != misfile["lands_in"]) if misfile else None
    return categorize(
        {"Description": part["description"],
         "Manufacture Part Number": part["mpn"],
         "Manufacturer": part["manufacturer"]},
        distributor_category=distributor_category,
        accept=refuse,
    ).split(" > ")[0]


def _known_misfiles() -> dict:
    return json.loads((CORPUS / "known-misfiles.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("distributor", DISTRIBUTORS)
def test_feeding_categories_does_not_lose_ground(distributor):
    """Every part the description rules got right must still be right.

    The map is allowed to move a part to a *different* correct section -- two
    taxonomies overlap that way -- but a part the rules placed and the map
    un-places is a map row overriding a description rule for no reason.
    """
    lost = []
    for part, want, got in _scored(distributor):
        now = _with_category(distributor, part)
        if got in want and now not in want and now != "Other":
            lost.append((part["mpn"], got, now, " / ".join(part["category_path"])))
    assert not lost, "the distributor category un-placed parts the rules had right:\n" + "\n".join(
        f"  {mpn}: {was} -> {now} <- {cat}" for mpn, was, now, cat in lost)


@pytest.mark.parametrize("distributor", DISTRIBUTORS)
def test_the_two_signals_agree_where_both_commit(distributor):
    """Taxonomy and description, measured against each other.

    Held at 99%, where the corpus measures 99.6% (LCSC) and 99.9% (DigiKey).
    A collapse here means one of the two broke independently of the other --
    a distributor that reorganised its catalogue tree, or a description rule
    that started claiming a bucket it never used to -- which neither half's own
    tests can see, because each agrees with itself.
    """
    both = agree = 0
    disagreements = []
    for part, _want, rules_section in _scored(distributor):
        proposed = category_map.opinion(
            distributor, *category_map.CategoryMap._path_to_key(  # noqa: SLF001
                distributor, part["category_path"]))
        if proposed is None or rules_section == "Other":
            continue
        both += 1
        if proposed == rules_section:
            agree += 1
        else:
            disagreements.append((part["mpn"], proposed, rules_section,
                                  " / ".join(part["category_path"])))
    assert both >= 500, f"{distributor}: only {both} parts where both commit"
    rate = agree / both
    assert rate >= 0.99, (
        f"{distributor}: distributor category and description rules agree on "
        f"{rate:.1%} of the {both} parts where both commit (floor 99%). "
        "Disagreements:\n" + "\n".join(
            f"  {mpn}: map={a} rules={b} <- {cat}"
            for mpn, a, b, cat in disagreements[:15])
    )


def test_the_map_clears_the_misfiles_it_covers():
    """known-misfiles.json is the corpus's list of parts the rules get wrong.

    Feeding the distributor category is supposed to fix exactly those -- that
    is what the feature is for -- so every listed part whose category the map
    can place must now land somewhere its category allows. The ones the map
    cannot place stay wrong and stay listed, which is the honest reading of the
    file: it is a list of what the *description* still gets wrong, and it
    shrinks as the map grows.
    """
    known = _known_misfiles()
    index = {}
    for distributor in DISTRIBUTORS:
        for part in _load(distributor):
            index[part["part_number"]] = (distributor, part)

    cleared, still_wrong, unplaceable = [], [], []
    for part_number, record in known.items():
        distributor, part = index[part_number]
        proposed = category_map.opinion(
            distributor, *category_map.CategoryMap._path_to_key(  # noqa: SLF001
                distributor, part["category_path"]))
        if proposed is None:
            unplaceable.append(part_number)
        if _with_category(distributor, part) == record["lands_in"]:
            still_wrong.append(part_number)
        else:
            cleared.append(part_number)

    assert len(cleared) >= 8, (
        f"only {len(cleared)} of {len(known)} known misfiles cleared; this was "
        f"8 when the feature landed. Still wrong: {still_wrong}")
    assert set(still_wrong) == set(unplaceable), (
        "the map places these but the part still lands where its category "
        f"rules it out: {sorted(set(still_wrong) - set(unplaceable))}")
