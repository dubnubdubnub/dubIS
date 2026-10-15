"""``category_map.csv`` and the reader beside it.

The map is data a human edits, so the tests here are about the two ways a
hand-edited table goes wrong -- a section that does not exist, and a bucket
that says two things at once -- and about the lookup order the three
distributors' different breadcrumb shapes force on it.

The corpus (test_categorize_corpus.py) scores the *decisions*; these score the
machinery that turns a decision into a section.
"""
from __future__ import annotations

import csv
import os

import pytest

import category_map
from category_map import CategoryMap, CategoryMapError, Unmapped

HEADER = ["distributor", "category", "subcategory", "section", "note"]


def _map(rows: list[dict[str, str]]) -> CategoryMap:
    return CategoryMap(rows, "<test>")


def _row(distributor="lcsc", category="", subcategory="", section="", note=""):
    return {"distributor": distributor, "category": category,
            "subcategory": subcategory, "section": section, "note": note}


# ── the shipped file ────────────────────────────────────────────────────────


class TestShippedMap:
    def test_loads_and_is_not_empty(self):
        loaded = category_map.load()
        assert loaded.stats()["keys"] > 100

    def test_every_section_is_a_real_parent_section(self):
        """A section the frontend does not know renders no group header.

        The part is still in the data, still counted, still searchable -- it
        is simply not on screen. Nothing at runtime would notice, so the only
        place this can be caught is the file's own load, which is why
        CategoryMap validates every non-empty section against SECTION_ORDER at
        construction rather than passing the string through.
        """
        loaded = category_map.load()
        with open(category_map.map_path(), newline="", encoding="utf-8") as f:
            named = {(r["section"] or "").strip() for r in csv.DictReader(f)}
        named.discard("")
        unknown = named - loaded.legal_sections
        assert not unknown, f"category_map.csv names sections that do not exist: {unknown}"

    def test_no_section_is_a_compound_one(self):
        """"Passives - Capacitors > MLCC" is a subcategory, not a parent.

        Setting a parent section to a compound string produces a section
        nothing iterates, so the guard is parent-level only even though
        FLAT_SECTION_ORDER contains the compounds.
        """
        with open(category_map.map_path(), newline="", encoding="utf-8") as f:
            named = {(r["section"] or "").strip() for r in csv.DictReader(f)}
        named.discard("")
        assert not [s for s in named if " > " in s], (
            f"these map rows name a compound section: {sorted(named)}")

    def test_every_distributor_is_one_the_clients_can_report(self):
        with open(category_map.map_path(), newline="", encoding="utf-8") as f:
            named = {(r["distributor"] or "").strip() for r in csv.DictReader(f)}
        assert named <= set(category_map.DISTRIBUTORS)

    def test_mouser_abstentions_are_the_majority_of_its_rows(self):
        """Mouser's top-level buckets are mostly too broad to place.

        Pinned because the abstentions are the point of that pass: 21 buckets
        were examined and 8 placed. A future edit that quietly deletes them
        would make the file read as "Mouser was never looked at", which is a
        different and worse claim than "we looked and declined".
        """
        loaded = category_map.load()
        mouser = [k for k in loaded._abstained if k[0] == "mouser"]  # noqa: SLF001
        placed = [k for k in loaded._by_key if k[0] == "mouser"]  # noqa: SLF001
        assert len(mouser) >= 15
        assert len(placed) >= 5


# ── load-time validation ────────────────────────────────────────────────────


class TestValidation:
    def test_unknown_section_is_refused(self):
        with pytest.raises(CategoryMapError, match="not a dubIS section"):
            _map([_row(category="Resistors", section="Passives - Magnetics")])

    def test_compound_section_is_refused(self):
        with pytest.raises(CategoryMapError, match="not a dubIS section"):
            _map([_row(category="Resistors", section="Passives - Capacitors > MLCC")])

    def test_unknown_distributor_is_refused(self):
        with pytest.raises(CategoryMapError, match="unknown distributor"):
            _map([_row(distributor="farnell", category="Resistors",
                       section="Passives - Resistors")])

    def test_a_row_naming_nothing_is_refused(self):
        """A row no part could ever match is a typo, not a decision."""
        with pytest.raises(CategoryMapError, match="neither a category nor a subcategory"):
            _map([_row(section="Other")])

    def test_abstention_and_a_section_for_one_key_is_refused(self):
        rows = [_row(category="Resistors", section="Passives - Resistors"),
                _row(category="Resistors", section="")]
        with pytest.raises(CategoryMapError, match="Say one thing|say one thing"):
            _map(rows)

    def test_missing_column_is_refused(self, tmp_path):
        path = tmp_path / "category_map.csv"
        path.write_text("distributor,category,section\nlcsc,Resistors,Other\n",
                        encoding="utf-8")
        category_map.reset()
        try:
            with pytest.raises(CategoryMapError, match="missing column"):
                category_map.load(str(path))
        finally:
            category_map.reset()

    def test_the_real_file_is_the_one_under_test(self):
        """Guards against a test suite that only ever exercises its own fixtures."""
        assert os.path.basename(category_map.map_path()) == "category_map.csv"
        assert os.path.dirname(category_map.map_path()) == os.path.dirname(
            os.path.abspath(category_map.__file__))


# ── placement vs scoring ────────────────────────────────────────────────────


class TestOpinionVsAllowed:
    def test_one_row_places(self):
        m = _map([_row(category="Resistors", section="Passives - Resistors")])
        assert m.opinion("lcsc", "Resistors", "") == "Passives - Resistors"
        assert m.allowed("lcsc", ["Resistors", ""]) == {"Passives - Resistors"}

    def test_several_rows_score_but_never_place(self):
        """"Wire/Cable/DataCable" really is connectors, hardware or other.

        Committing to one of them would misfile two thirds of the bucket, so a
        multi-section key is a scoring answer and not a placement one.
        """
        m = _map([_row(category="Cables", section="Connectors"),
                  _row(category="Cables", section="Mechanical & Hardware"),
                  _row(category="Cables", section="Other")])
        assert m.allowed("lcsc", ["Cables", ""]) == {"Connectors",
                                                     "Mechanical & Hardware", "Other"}
        assert m.opinion("lcsc", "Cables", "") is None

    def test_abstention_scores_none_and_never_places(self):
        m = _map([_row(category="Other Electronic Components", section="")])
        assert m.allowed("lcsc", ["Other Electronic Components", ""]) is None
        assert m.opinion("lcsc", "Other Electronic Components", "") is None

    def test_abstention_does_not_fall_through_to_a_vaguer_row(self):
        """A subcategory that declines outranks the bucket above it.

        Falling through would let a broad parent row place a part its own more
        specific row said nothing about -- which is how a map ends up placing
        parts nobody decided on.
        """
        m = _map([_row(subcategory="Others", section=""),
                  _row(category="Optoelectronics", section="LEDs")])
        assert m.opinion("lcsc", "Optoelectronics", "Others") is None

    def test_ambiguous_subcategory_does_not_yield_to_the_bucket(self):
        m = _map([_row(subcategory="NTC Thermistors", section="ICs - Sensors"),
                  _row(subcategory="NTC Thermistors", section="Passives - Resistors"),
                  _row(category="Resistors", section="Passives - Resistors")])
        assert m.opinion("lcsc", "Resistors", "NTC Thermistors") is None

    def test_missing_key_is_no_opinion_at_runtime_but_unmapped_in_the_corpus(self):
        m = _map([_row(category="Resistors", section="Passives - Resistors")])
        assert m.opinion("lcsc", "Something New", "") is None
        with pytest.raises(Unmapped):
            m.allowed("lcsc", ["Something New", ""])

    def test_a_part_with_no_category_at_all_is_no_label_not_a_gap(self):
        """The old labels table spelled this as its own `"": None` row.

        It is not a labelling failure, because there is nothing to look up --
        so allowed() answers None instead of raising Unmapped, and the corpus
        skips the part rather than demanding a row for emptiness.
        """
        m = _map([_row(category="Resistors", section="Passives - Resistors")])
        assert m.allowed("lcsc", ["", ""]) is None

    def test_notes_come_back_with_the_decision(self):
        m = _map([_row(category="Memory", section="Other",
                       note="SECTION_ORDER has no memory section.")])
        assert m.notes("mouser", "Memory", "") == []
        assert m.notes("lcsc", "Memory", "") == ["SECTION_ORDER has no memory section."]


# ── the lookup order the distributors force ─────────────────────────────────


class TestLookupOrder:
    def test_lcsc_subcategory_beats_top_level(self):
        """JLC spells one tree several ways at the top; only the leaf is stable."""
        m = _map([_row(category="Photoelectric Devices", section="LEDs"),
                  _row(subcategory="Photodiodes", section="Diodes")])
        assert m.opinion("lcsc", "Photoelectric Devices", "Photodiodes") == "Diodes"

    def test_lcsc_falls_back_to_the_top_level(self):
        m = _map([_row(category="Photoelectric Devices", section="LEDs")])
        assert m.opinion("lcsc", "Photoelectric Devices", "Laser Diodes") == "LEDs"

    def test_digikey_matches_the_pair_the_client_stores(self):
        """The normalizer keeps the leaf as `category` and its parent above it."""
        m = _map([_row(distributor="digikey", category="Motor Drivers, Controllers",
                       subcategory="Power Management (PMIC)",
                       section="ICs - Motor Drivers")])
        assert m.opinion("digikey", "Motor Drivers, Controllers",
                         "Power Management (PMIC)") == "ICs - Motor Drivers"

    def test_digikey_falls_back_to_the_leaf_alone(self):
        """A two-deep DigiKey breadcrumb stores the *top* level in subcategory.

        The table keys DigiKey below the top level, so the pair cannot match
        and the leaf alone has to.
        """
        m = _map([_row(distributor="digikey", category="Aluminum Electrolytic Capacitors",
                       section="Passives - Capacitors")])
        assert m.opinion("digikey", "Aluminum Electrolytic Capacitors",
                         "Capacitors") == "Passives - Capacitors"

    def test_a_top_level_bucket_reached_only_through_subcategory(self):
        """Mouser's rows are its top-level catalogue; its client keeps the last
        two crumbs, so the bucket only ever arrives in `subcategory`."""
        m = _map([_row(distributor="mouser", category="Connectors",
                       section="Connectors")])
        assert m.opinion("mouser", "Terminal Blocks", "Connectors") == "Connectors"

    def test_distributor_is_case_insensitive(self):
        m = _map([_row(category="Resistors", section="Passives - Resistors")])
        assert m.opinion("LCSC", "Resistors", "") == "Passives - Resistors"

    def test_whitespace_does_not_defeat_a_match(self):
        m = _map([_row(category=" Resistors ", section="Passives - Resistors")])
        assert m.opinion("lcsc", "Resistors", "  ") == "Passives - Resistors"


# ── the corpus dialect ──────────────────────────────────────────────────────


class TestPathToKey:
    def test_lcsc_keeps_both_positions_of_a_two_element_path(self):
        # Filtering empties would make ["", "NTC Thermistors"] indistinguishable
        # from ["NTC Thermistors", ""], and the table keys those two differently.
        assert CategoryMap._path_to_key("lcsc", ["", "NTC Thermistors"]) == (  # noqa: SLF001
            "", "NTC Thermistors")
        assert CategoryMap._path_to_key("lcsc", ["Resistors", ""]) == (  # noqa: SLF001
            "Resistors", "")

    def test_digikey_drops_its_redundant_top_level(self):
        assert CategoryMap._path_to_key(  # noqa: SLF001
            "digikey", ["Integrated Circuits (ICs)", "Interface", "I/O Expanders"]
        ) == ("I/O Expanders", "Interface")
        assert CategoryMap._path_to_key(  # noqa: SLF001
            "digikey", ["Capacitors", "Aluminum Electrolytic Capacitors"]
        ) == ("Aluminum Electrolytic Capacitors", "")

    def test_the_shipped_map_answers_every_labelled_corpus_path(self):
        """The map and the corpus labels are one file; this is the proof.

        Reads the real fixtures and asserts every category path in them
        resolves -- which is the same property test_every_category_has_a_label
        holds, checked here against the reader rather than the test module, so
        a change to either half is caught in both places.
        """
        import json
        from pathlib import Path

        corpus = Path(__file__).resolve().parents[1] / "fixtures" / "categorize-corpus"
        for distributor in ("lcsc", "digikey"):
            seen = set()
            for line in open(corpus / f"{distributor}.jsonl", encoding="utf-8"):
                path = json.loads(line)["category_path"]
                key = tuple(path)
                if key in seen:
                    continue
                seen.add(key)
                category_map.allowed(distributor, list(path))
            assert len(seen) >= 30, f"{distributor}: fixture looks truncated"
