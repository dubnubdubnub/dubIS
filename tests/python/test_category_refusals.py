"""``category_refusals`` — the shipped list of parts a distributor misfiled.

The map is a decision about a bucket; this is a decision about one part, and it
is the only thing standing between a map row and a microcontroller filed under
*Development Boards, Kits, Programmers*.  So the units here are mostly about
the two ways it fails: a row that does not load (the part goes back to being
misfiled with nothing failing), and a row that loads but matches too much or
too little.
"""
from __future__ import annotations

import pytest

import category_map
import category_refusals
from category_map import DistributorCategory
from category_refusals import RefusalError, Refusals

HEADER = "part_id,distributor,category,subcategory,reason\n"


def _rows(*rows: dict[str, str]) -> Refusals:
    return Refusals([dict(r) for r in rows])


def _refusal(**overrides) -> dict[str, str]:
    row = {"part_id": "C7497415", "distributor": "lcsc",
           "category": "Development Boards & Tools",
           "subcategory": "Programmers, Emulators, And Debuggers",
           "reason": "an MCU; the description is right"}
    row.update(overrides)
    return row


class TestReader:
    def test_an_absent_file_is_no_misfiles_not_an_error(self, tmp_path):
        # A fork with no curated list is a real state; a fork whose file got
        # truncated is caught by the floor below, not by raising here.
        assert not category_refusals.load(str(tmp_path / "none.csv"))

    def test_the_shipped_file_is_where_the_module_says_it_is(self):
        import os
        assert os.path.isfile(category_refusals.refusals_path())

    def test_the_shipped_list_is_not_empty(self):
        """A file emptied by a bad merge silently re-misfiles every part in it."""
        assert len(category_refusals.load()) >= 5

    def test_a_missing_column_is_refused(self, tmp_path):
        path = tmp_path / "r.csv"
        path.write_text("part_id,distributor,reason\nC1,lcsc,x\n", encoding="utf-8")
        with pytest.raises(RefusalError, match="missing column"):
            category_refusals.load(str(path))

    def test_an_unknown_distributor_is_refused(self):
        with pytest.raises(RefusalError, match="unknown distributor"):
            _rows(_refusal(distributor="rs-components"))

    def test_a_refusal_without_a_part_is_refused(self):
        with pytest.raises(RefusalError, match="no part_id"):
            _rows(_refusal(part_id="  "))

    def test_a_refusal_without_a_reason_is_refused(self):
        # The reason is not decoration: it is what tells the next reader the
        # row is still true, and a carve-out nobody can justify gets deleted.
        with pytest.raises(RefusalError, match="no reason"):
            _rows(_refusal(reason=""))

    def test_the_same_refusal_twice_is_refused(self):
        with pytest.raises(RefusalError, match="refused twice"):
            _rows(_refusal(), _refusal(reason="a different sentence"))


class TestMatching:
    """What a row covers, which is the whole behaviour of the file.

    Matching goes through ``CategoryMap.candidates``, the same expansion the
    map itself looks up with, because the distributors store the breadcrumb
    differently: LCSC the bucket and its leaf, DigiKey and Mouser the leaf and
    its parent.  A row written against one level has to cover a part stored at
    the other, or the carve-out depends on which client happened to answer.
    """

    MISFILE = DistributorCategory(
        "lcsc", "Development Boards & Tools", "Programmers, Emulators, And Debuggers")

    def test_the_exact_pair_matches(self):
        assert _rows(_refusal()).refuses("C7497415", self.MISFILE)

    def test_naming_only_the_bucket_matches(self):
        assert _rows(_refusal(subcategory="")).refuses("C7497415", self.MISFILE)

    def test_naming_only_the_leaf_matches(self):
        assert _rows(_refusal(category="", subcategory="Programmers, Emulators, "
                                                     "And Debuggers")).refuses(
            "C7497415", self.MISFILE)

    def test_a_blanket_refusal_matches_anything_from_that_distributor(self):
        assert _rows(_refusal(category="", subcategory="")).refuses(
            "C7497415", DistributorCategory("lcsc", "Capacitors"))

    def test_another_distributor_is_never_touched(self):
        # Refusing LCSC about a part says nothing about DigiKey's answer for it.
        assert not _rows(_refusal()).refuses(
            "C7497415", DistributorCategory("digikey", "Development Boards", "Kits"))

    def test_a_blanket_refusal_still_respects_the_distributor(self):
        assert not _rows(_refusal(category="", subcategory="")).refuses(
            "C7497415", DistributorCategory("mouser", "Development Boards"))

    def test_another_part_is_never_touched(self):
        assert not _rows(_refusal()).refuses("C1525", self.MISFILE)

    def test_an_unrelated_bucket_is_not_matched(self):
        # The point of naming the category: a correct re-filing of the same
        # part is not refused forever by a row about the old misfile.
        assert not _rows(_refusal()).refuses(
            "C7497415", DistributorCategory("lcsc", "Microcontrollers", "MCU"))

    def test_the_refusal_that_matched_comes_back_with_its_reason(self):
        refusals = _rows(_refusal(reason="JLC files an MCU under programmers"))
        found = refusals.refusal_for("C7497415", self.MISFILE)
        assert found is not None and found.reason == "JLC files an MCU under programmers"
        assert refusals.refusal_for("C7497415", DistributorCategory("lcsc", "Capacitors")) is None


class TestShippedList:
    """The rows that ship, held to what makes a row do something."""

    def test_every_row_places_something(self):
        """A refusal of a bucket the map abstains on is a row that does nothing.

        Harmless today, misleading tomorrow: it reads as protection, and the
        next person to trust it gets the misfile. Either the map places the
        bucket (so the refusal is what stops it) or the row should go.
        """
        dead = []
        for row in category_refusals.load().rows():
            if not (row.category or row.subcategory):
                continue  # a blanket refusal is not about one bucket
            if category_map.opinion(row.distributor, row.category, row.subcategory) is None:
                dead.append(f"  {row.part_id} <- {row.category} / {row.subcategory}")
        assert not dead, (
            "these refusals name a category the map has no opinion about, so "
            "nothing needed refusing:\n" + "\n".join(dead))

    def test_every_row_that_matches_a_corpus_misfile_blames_the_distributor(self):
        """A refusal must not contradict the corpus.

        Refusing a part the fixture says the *description* got wrong would
        delete a win the map earned, and the corpus tests would then score the
        part as if the map had never been asked.
        """
        import json
        from pathlib import Path

        corpus = (Path(__file__).resolve().parent.parent / "fixtures"
                  / "categorize-corpus" / "known-misfiles.json")
        known = json.loads(corpus.read_text(encoding="utf-8"))
        wrong = []
        for row in category_refusals.load().rows():
            record = known.get(row.part_id)
            if record and record["fault"] != "distributor":
                wrong.append(f"  {row.part_id}: fixture says {record['fault']!r}")
        assert not wrong, (
            "these parts are refused but the corpus blames the description, "
            "not the distributor:\n" + "\n".join(wrong))

class TestItReachesTheCacheDigest:
    """A deploy that adds a row has to move the part back on the next rebuild.

    ``cache_db`` only does a full rebuild when an input's digest changed, so a
    refusal that is not in the digest is a file edit that changes nothing --
    the part stays misfiled until some unrelated event rebuilds the cache.
    """

    def test_adding_a_row_changes_the_digest(self, tmp_path, monkeypatch):
        import domain.distributor_categories as store

        store.record_categories(str(tmp_path), "C7497415", "lcsc",
                                "Development Boards & Tools",
                                "Programmers, Emulators, And Debuggers")
        mine = tmp_path / "category_refusals.csv"
        monkeypatch.setattr(category_refusals, "refusals_path", lambda: str(mine))
        category_refusals.reset()
        try:
            before = store.content_hash(str(tmp_path))
            mine.write_text(HEADER + "C7497415,lcsc,Development Boards & Tools,"
                            '"Programmers, Emulators, And Debuggers",an MCU\n',
                            encoding="utf-8")
            category_refusals.reset()
            assert store.content_hash(str(tmp_path)) != before
        finally:
            category_refusals.reset()

    def test_an_empty_store_still_hashes_empty(self, tmp_path):
        # Nothing to re-derive when no part has a category at all: the digest
        # stays "" so a first run does not look like a changed input.
        import domain.distributor_categories as store
        assert not store.content_hash(str(tmp_path))
