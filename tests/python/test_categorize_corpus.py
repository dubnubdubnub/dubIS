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
wrong today are listed with a reason in known-misfiles.json, and each says who
is at fault -- ``distributor`` (the distributor filed a part under a category
its own description contradicts, so the category must not be used) or
``description`` (the description is the unreliable half, so it must be).
Any other part landing wrong fails.  Misses are held by a floor.

To see the whole picture while changing the rules -- including a 100k-part
corpus kept out of git -- run scripts/score-categorize-corpus.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import category_map
import category_refusals
import domain.distributor_categories
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
# rules have always been held. The tests below score what the app actually
# does now: the distributor's own category first, the description second.
#
# Careful reading, because two properties here look meaningful and are not.
#
# The map's opinion is a member of the allowed set by construction -- both
# come from the same rows -- so "feeding the map never lands a part somewhere
# its category rules out" is true forever and proves nothing.
#
# And the same construction makes "the category fixes the misfiles" circular
# for the parts known-misfiles.json blames on the *distributor*: scoring those
# against the distributor's own category measures the very thing the fixture
# records as wrong. That is why every part below is scored through the
# production resolver, misfile list included -- a refused part is scored with
# its description, which is the half the fixture trusts.
#
# What is NOT true by construction, and so is worth asserting, is the
# relationship between the two *independent* signals: what the distributor's
# taxonomy says, and what the description rules say. They are written by
# different people from different artifacts, and a part where they disagree is
# a bug in one of them.


def _known_misfiles() -> dict:
    return json.loads((CORPUS / "known-misfiles.json").read_text(encoding="utf-8"))


def _entry(distributor: str, part: dict) -> category_map.DistributorCategory:
    return category_map.DistributorCategory(
        distributor, *category_map.CategoryMap._path_to_key(  # noqa: SLF001
            distributor, part["category_path"]))


def _corpus_index() -> dict[str, tuple[str, dict]]:
    index = {}
    for dist in DISTRIBUTORS:
        for part in _load(dist):
            index[part["part_number"]] = (dist, part)
    return index


def _with_category(distributor: str, part: dict) -> str:
    """The section the app gives this part now, categories wired in.

    Through ``domain.distributor_categories.resolve`` rather than straight to
    ``categorize``: that is where the shipped misfile list is applied, and a
    corpus that skipped it would be scoring a decision production never makes.
    """
    entry = _entry(distributor, part)
    resolved = domain.distributor_categories.resolve(
        {distributor: entry}, part_id=part["part_number"],
        refusals=category_refusals.load())
    return categorize(
        {"Description": part["description"],
         "Manufacture Part Number": part["mpn"],
         "Manufacturer": part["manufacturer"]},
        distributor_category=resolved,
    ).split(" > ")[0]


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


def test_the_map_clears_the_misfiles_the_description_caused():
    """The half of the file the feature exists for: the *description* was wrong.

    An Ethernet controller the rules file as an LED because its feature list
    mentions one; an analog switch whose description has a typo. Every listed
    part the map can place must now land in a section its own distributor
    category allows. The ones the map cannot place stay listed, which is the
    honest reading of the file: it shrinks as the map grows. The floor is the
    count measured today (3), so a map row that quietly stops placing them
    cannot pass unnoticed.
    """
    index = _corpus_index()
    cleared, unplaceable, still_wrong = [], [], []
    for part_number, record in _known_misfiles().items():
        if record["fault"] != "description":
            continue
        distributor, part = index[part_number]
        entry = _entry(distributor, part)
        if category_map.opinion(*entry) is None:
            unplaceable.append(part_number)
            continue
        got = _with_category(distributor, part)
        if got in allowed(distributor, part["category_path"]):
            cleared.append(part_number)
        else:
            still_wrong.append((part_number, got))

    assert not still_wrong, (
        "the category is used for these, yet they still land where that "
        f"category rules them out: {sorted(still_wrong)}")
    assert len(cleared) >= 3, (
        f"only {len(cleared)} of the description-caused misfiles are cleared "
        f"(3 when this was written) -- the map stopped placing them. "
        f"Cleared: {sorted(cleared)}; unplaceable: {sorted(unplaceable)}")


def test_every_known_misfile_says_who_is_at_fault():
    """``fault`` is the fixture's judgement, and the two tests above obey it.

    The difference between "the distributor misfiled this" and "the
    description misled the rules" decides whether the category may be used at
    all, and a sentence in ``why`` is not something a test can hold -- so the
    fixture states it as a field, and an entry that does not is a gap.
    """
    for part_number, record in _known_misfiles().items():
        assert record["fault"] in ("distributor", "description"), (
            f"{part_number}: fault must name which signal the fixture blames")


def test_a_part_the_distributor_misfiled_is_never_moved_by_its_category():
    """The parts the carve-out exists for: JLC files an MCU under Programmers.

    known-misfiles.json lists these as ``fault: distributor`` -- the
    description is right and the category is the misfile. Wiring the category
    in must not move one of them: the shipped misfile list refuses it, the
    part falls back to its description, and it stays in ``lands_in``. A map row
    added over one of these parts files it in the bucket the fixture documents
    as wrong, and this is what catches it.
    """
    index = _corpus_index()
    moved = []
    for part_number, record in _known_misfiles().items():
        if record["fault"] != "distributor":
            continue
        distributor, part = index[part_number]
        got = _with_category(distributor, part)
        if got != record["lands_in"]:
            moved.append(f"  {part_number}: {record['lands_in']} -> {got}"
                         f" <- {' / '.join(part['category_path'])}")
    assert not moved, (
        "the distributor category moved parts the fixture records as the "
        "distributor's own misfiles. Add a row to category_refusals.csv for "
        "each, or show the fixture is wrong about it:\n" + "\n".join(moved))


def test_every_misfile_the_map_could_act_on_is_refused():
    """Which protection each distributor-fault part has, named rather than implied.

    The test above passes for two different reasons -- a refusal, or a bucket
    the map happens to have no opinion about -- and only the first is a
    decision someone made. A part whose bucket the map later learns to place
    needs a refusal row written for it; this says so at the moment the map row
    lands, not after the parts are already misfiled.
    """
    index = _corpus_index()
    refusals = category_refusals.load()
    undefended = []
    for part_number, record in _known_misfiles().items():
        if record["fault"] != "distributor":
            continue
        distributor, part = index[part_number]
        entry = _entry(distributor, part)
        proposed = category_map.opinion(*entry)
        if proposed is None or refusals.refusal_for(part_number, entry):
            continue
        undefended.append(f"  {part_number}: {' / '.join(part['category_path'])}"
                          f" -> {proposed}")
    assert not undefended, (
        "the map can now place these parts, which the fixture says its "
        "distributor misfiled, and nothing refuses it. Add each to "
        "category_refusals.csv:\n" + "\n".join(undefended))
