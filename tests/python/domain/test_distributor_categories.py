"""``domain.distributor_categories`` — the durable "what does each distributor call this part".

Driven the way tests/python/domain/test_attributes.py drives its twin: real
temp data dirs, real SQLite caches, and the fetch path and rebuild path
exercised through a real ``InventoryApi`` rather than a mock of them.
"""
from __future__ import annotations

import os
import stat

import pytest

import category_map
import domain.distributor_categories as store
import inventory_ops
from category_map import DistributorCategory
from helpers import lcsc_fixture_products, make_api, make_part, write_ledger


def _product(category, subcategory="", **extra):
    return {"category": category, "subcategory": subcategory, **extra}


# ── the durable CSV ─────────────────────────────────────────────────────────


class TestDurableCsv:
    def test_writes_the_full_header(self, data_dir):
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors",
                                "Ceramic Capacitors")
        rows = store.read_rows(data_dir)
        assert len(rows) == 1
        assert set(rows[0]) == set(store.FIELDNAMES)
        assert rows[0]["category"] == "Capacitors"
        assert rows[0]["subcategory"] == "Ceramic Capacitors"
        assert rows[0]["observed_at"]

    def test_refetch_updates_in_place_rather_than_appending(self, data_dir):
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors", "Old Leaf")
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors", "New Leaf")
        rows = store.read_rows(data_dir)
        assert len(rows) == 1
        assert rows[0]["subcategory"] == "New Leaf"

    def test_two_distributors_are_two_rows(self, data_dir):
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors")
        store.record_categories(data_dir, "C1525", "digikey", "Aluminum Electrolytic Capacitors")
        assert {r["distributor"] for r in store.read_rows(data_dir)} == {"lcsc", "digikey"}

    def test_nothing_and_empty_is_a_no_op_not_an_erase(self, data_dir):
        """A fetch that returned no category must not blank one already stored.

        LCSC's category capture was empty for as long as anyone can remember
        (the client read a key the API does not send), so a client bug would
        otherwise have wiped every category another distributor had recorded.
        """
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors", "Ceramic")
        assert store.record_categories(data_dir, "C1525", "lcsc", "", "") is False
        rows = store.read_rows(data_dir)
        assert rows[0]["category"] == "Capacitors"

    def test_an_unknown_distributor_is_refused(self, data_dir):
        with pytest.raises(ValueError, match="unknown distributor"):
            store.record_categories(data_dir, "C1525", "farnell", "Resistors")

    def test_the_file_is_owner_only(self, data_dir):
        """Same discipline as every other credential-adjacent store in data/.

        Not secret material, but the file names what this account buys and from
        whom, and csv_io's mkstemp-based write already gives 0600 -- pinned so
        a future writer that swaps in open()/w+ does not quietly widen it.
        """
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors")
        mode = stat.S_IMODE(os.stat(store.csv_path(data_dir)).st_mode)
        assert mode == 0o600

    def test_missing_file_reads_empty(self, tmp_path):
        assert store.read_rows(str(tmp_path / "nowhere")) == []


# ── preference ──────────────────────────────────────────────────────────────


class TestPreference:
    def test_lcsc_wins_when_both_place(self):
        found = {
            "digikey": DistributorCategory("digikey", "Something"),
            "lcsc": DistributorCategory("lcsc", "Capacitors"),
        }
        assert store.resolve(found).distributor == "lcsc"

    def test_a_distributor_the_map_cannot_place_loses_rather_than_winning(self):
        """"No opinion" is not an opinion.

        If the first distributor in preference order simply returned its row,
        an LCSC bucket the map *can* place would be discarded because DigiKey
        filed the part in a bucket too broad to use.
        """
        found = {
            "lcsc": DistributorCategory("lcsc", "Capacitors"),
            "digikey": DistributorCategory("digikey", "Semiconductors", "Whatever"),
        }
        assert store.resolve(found).distributor == "lcsc"

    def test_the_lower_preference_wins_when_the_higher_has_no_row(self):
        found = {"mouser": DistributorCategory("mouser", "Memory")}
        assert store.resolve(found).distributor == "mouser"

    def test_nothing_placeable_resolves_to_none(self):
        found = {"mouser": DistributorCategory("mouser", "Semiconductors")}
        assert store.resolve(found) is None

    def test_preference_covers_every_distributor_the_map_reads(self):
        assert set(store.PREFERENCE) == set(category_map.DISTRIBUTORS)


class TestIndex:
    def test_newest_sighting_wins_among_duplicates(self):
        rows = [
            {"part_id": "C1", "distributor": "lcsc", "category": "Old",
             "subcategory": "", "observed_at": "2026-01-01T00:00:00", "source": "x"},
            {"part_id": "C1", "distributor": "lcsc", "category": "New",
             "subcategory": "", "observed_at": "2026-06-01T00:00:00", "source": "x"},
        ]
        assert store.index(rows)["C1"]["lcsc"].category == "New"

    def test_a_row_for_an_unknown_distributor_is_dropped_not_guessed(self):
        rows = [{"part_id": "C1", "distributor": "farnell", "category": "Resistors",
                 "subcategory": "", "observed_at": "", "source": ""}]
        assert store.index(rows) == {}


# ── the cache mirror ────────────────────────────────────────────────────────


class TestCacheMirror:
    def test_round_trip(self, data_dir, db):
        db.execute("INSERT INTO parts (part_id, section) VALUES ('C1525','Other')")
        db.commit()
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors", "Ceramic")
        store.load_into_db(db, data_dir)
        got = db.execute(
            "SELECT category, subcategory FROM distributor_categories").fetchall()
        assert [tuple(r) for r in got] == [("Capacitors", "Ceramic")]

    def test_a_row_for_a_part_not_in_inventory_is_skipped_not_fatal(self, data_dir, db):
        """The CSV keeps it; the mirror cannot hold it (FK onto parts)."""
        store.record_categories(data_dir, "GHOST", "lcsc", "Capacitors")
        store.load_into_db(db, data_dir)
        assert db.execute(
            "SELECT COUNT(*) FROM distributor_categories").fetchone()[0] == 0
        assert store.read_rows(data_dir)

    def test_reload_replaces_rather_than_accumulates(self, data_dir, db):
        db.execute("INSERT INTO parts (part_id, section) VALUES ('C1525','Other')")
        db.commit()
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors")
        store.load_into_db(db, data_dir)
        store.load_into_db(db, data_dir)
        assert db.execute(
            "SELECT COUNT(*) FROM distributor_categories").fetchone()[0] == 1

    def test_an_empty_store_clears_the_mirror(self, data_dir, db):
        db.execute("INSERT INTO parts (part_id, section) VALUES ('C1525','Other')")
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors")
        store.load_into_db(db, data_dir)
        store._write_rows(data_dir, [])  # noqa: SLF001 - simulate every row edited away
        store.load_into_db(db, data_dir)
        assert db.execute(
            "SELECT COUNT(*) FROM distributor_categories").fetchone()[0] == 0


# ── the fetch path ──────────────────────────────────────────────────────────


class TestFetchPath:
    def test_a_fetch_records_its_category(self, tmp_path):
        api = make_api(tmp_path)
        product = {"productCode": "C1525", "category": "Capacitors",
                   "subcategory": "Ceramic Capacitors", "attributes": []}
        api._attrs.persist_from_product("lcsc", "C1525", product)
        rows = store.read_rows(api.base_dir)
        assert [(r["distributor"], r["category"]) for r in rows] == [("lcsc", "Capacitors")]

    def test_a_product_with_neither_category_nor_attributes_writes_nothing(self, tmp_path):
        api = make_api(tmp_path)
        api._attrs.persist_from_product("lcsc", "C1525", {"productCode": "C1525"})
        assert store.read_rows(api.base_dir) == []

    def test_a_category_alone_is_recorded_even_with_no_attributes(self, tmp_path):
        """The two stores are independent; a client that returns one must still land."""
        api = make_api(tmp_path)
        api._attrs.persist_from_product("mouser", "595-MEM",
                                        {"category": "Memory", "attributes": []})
        assert store.read_rows(api.base_dir)[0]["category"] == "Memory"

    def test_a_store_failure_does_not_eat_the_product(self, tmp_path, monkeypatch):
        """A preview must survive a broken store -- warned, not swallowed."""
        api = make_api(tmp_path)
        monkeypatch.setattr(store, "record_fetched_categories",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))
        product = {"category": "Capacitors"}
        assert api._attrs.persist_from_product("lcsc", "C1525", product) is product

    def test_the_key_is_resolved_to_the_inventory_part_id(self, tmp_path):
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525", mpn="CL05B104KO5NNNC")])
        api.rebuild_inventory()
        api._attrs.record_fetched_categories("CL05B104KO5NNNC", "lcsc",
                                             {"category": "Capacitors"})
        rows = store.read_rows(api.base_dir)
        assert rows[0]["part_id"] == "C1525"

    def test_an_unresolvable_key_is_kept_rather_than_dropped(self, tmp_path):
        """An alternate-part candidate we never bought is exactly what this is for."""
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        api._attrs.record_fetched_categories("C9999999", "lcsc", {"category": "Resistors"})
        assert store.read_rows(api.base_dir)[0]["part_id"] == "C9999999"


class TestGetPartCategories:
    def test_reports_the_verdict_and_which_row_won(self, tmp_path):
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        api._attrs.record_fetched_categories("C1525", "lcsc",
                                             {"category": "Capacitors",
                                              "subcategory": "Ceramic Capacitors"})
        api._attrs.record_fetched_categories("C1525", "mouser",
                                             {"category": "Passive Components"})
        got = api.get_part_categories("C1525")
        assert [g["distributor"] for g in got] == ["lcsc", "mouser"]
        assert got[0]["section"] == "Passives - Capacitors"
        assert got[0]["preferred"] is True
        # An ambiguous bucket is reported as no opinion, not as a blank section.
        assert got[1]["section"] is None
        assert got[1]["preferred"] is False

    def test_the_note_that_was_decided_comes_back_with_the_row(self, tmp_path):
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        api._attrs.record_fetched_categories("C1525", "mouser", {"category": "Memory"})
        got = api.get_part_categories("C1525")
        assert got[0]["section"] == "Other"
        assert "no memory section" in got[0]["note"]

    def test_a_part_nobody_fetched_returns_an_empty_list(self, tmp_path):
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        assert api.get_part_categories("C1525") == []


# ── the rebuild path ────────────────────────────────────────────────────────


class TestRebuildPath:
    def _ledger_with(self, api, desc, digikey=""):
        write_ledger(api, [make_part(lcsc="C1525", digikey=digikey, desc=desc)])
        api.rebuild_inventory()

    def test_a_recorded_category_moves_the_part_on_rebuild(self, tmp_path):
        """DG2750: a DigiKey breadcrumb rescues a part the rules misfile.

        The description is the corpus's verbatim "LOW VOLTAGE DUAL ANALAOG
        SWITCH" -- misspelled, which is exactly why the rules reach for
        Switches and why the breadcrumb is the only thing that knows.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "LOW VOLTAGE DUAL ANALAOG SWITCH")
        assert api.rebuild_inventory()[0]["section"] == "Switches"

        api._attrs.record_fetched_categories(
            "C1525", "digikey",
            {"category": "Analog Switches, Multiplexers, Demultiplexers",
             "subcategory": "Interface"})
        inventory = api.rebuild_inventory()
        assert inventory[0]["section"] == "ICs - Interface"

    def test_rebuild_never_reaches_the_network(self, tmp_path, monkeypatch):
        """The whole reason the store exists.

        rebuild() runs on every mutation; a rebuild that fetched categories
        would make adding five capacitors five HTTP round trips, and would
        fail all five the moment a distributor blocked the scraper.
        """
        import urllib.request

        api = make_api(tmp_path)
        self._ledger_with(api, "100nF 0402 MLCC")
        store.record_categories(api.base_dir, "C1525", "lcsc", "Capacitors")

        def _no_network(*a, **k):
            raise AssertionError("rebuild() reached the network for a category")

        monkeypatch.setattr(urllib.request, "urlopen", _no_network)
        inventory = api.rebuild_inventory()
        assert inventory[0]["section"] == "Passives - Capacitors > MLCC"

    def test_an_empty_store_leaves_the_old_answer(self, tmp_path):
        api = make_api(tmp_path)
        self._ledger_with(api, "LOW VOLTAGE DUAL ANALAOG SWITCH")
        assert api.rebuild_inventory()[0]["section"] == "Switches"

    def test_blank_the_row_and_the_distributor_stops_being_believed(self, tmp_path):
        """The per-part override, which is what makes the store editable.

        known-misfiles.json is the corpus's version of this sentence: we know
        this distributor files this part wrong. On a live inventory the user's
        move is to delete the row, and the part falls back to its description.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "LOW VOLTAGE DUAL ANALAOG SWITCH")
        api._attrs.record_fetched_categories(
            "C1525", "digikey",
            {"category": "Analog Switches, Multiplexers, Demultiplexers",
             "subcategory": "Interface"})
        inventory = api.rebuild_inventory()
        assert inventory[0]["section"] == "ICs - Interface"

        store._write_rows(api.base_dir, [])  # noqa: SLF001 - the user deleted the row
        inventory = api.rebuild_inventory()
        assert inventory[0]["section"] == "Switches"

    def test_the_mirror_is_repopulated_by_a_rebuild(self, tmp_path):
        api = make_api(tmp_path)
        self._ledger_with(api, "100nF MLCC")
        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Capacitors"})
        conn = api._get_cache()
        assert conn.execute(
            "SELECT COUNT(*) FROM distributor_categories").fetchone()[0] == 1

    def test_a_changed_category_forces_a_full_rebuild_not_a_catch_up(self, tmp_path):
        """The part of this feature that is easy to get wrong and impossible to see.

        A rebuild takes the catch-up path whenever the ledger and adjustments
        are unchanged, and catch-up replays quantity events without
        re-categorizing anything. So a recorded category that only lands in the
        CSV moves the part on no screen -- and nothing errors, because the CSV
        and the cache are both perfectly consistent with what they were told.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "100nF 16V X7R 0402")
        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Capacitors"})
        api.rebuild_inventory()
        before = api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0]
        assert before, "a full rebuild must record the category store it read"

        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Memory"})
        after = api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0]
        assert after == before, (
            "the checkpoint must keep describing the store the cache actually "
            "reflects; stamping the new digest is what hides the change")
        assert api.rebuild_inventory()[0]["section"] == "Other"

    def test_an_unchanged_refetch_does_not_force_a_rebuild(self, tmp_path):
        """A preview is a read.

        Re-fetching a part whose category did not move must leave the
        checkpoint alone, or hovering product cards would each cost a full
        rebuild of the whole inventory.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "100nF 16V X7R 0402")
        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Capacitors"})
        api.rebuild_inventory()
        stamped = api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0]
        assert api._attrs.record_fetched_categories(
            "C1525", "lcsc", {"category": "Capacitors"}) is False
        assert api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0] == stamped

    def test_an_adjustment_does_not_swallow_a_pending_category(self, tmp_path):
        """The subtle half of the checkpoint rule.

        adjust_part applies a stock delta and leaves every section as it was,
        so the cache still describes the category store the last *full* rebuild
        read. Stamping today's digest there would mark a pending
        re-categorization as done, and the part would sit in the wrong section
        forever with a checkpoint that insists it is current.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "100nF 16V X7R 0402")
        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Capacitors"})
        api.rebuild_inventory()
        stamped = api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0]

        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Memory"})
        api.adjust_part("add", "C1525", 5)
        assert api._get_cache().execute(
            "SELECT value FROM cache_meta WHERE key='category_hash'").fetchone()[0] == stamped
        # ...and the pending move still happens on the next load.
        assert api.rebuild_inventory()[0]["section"] == "Other"

    def test_editing_the_store_by_hand_takes_effect(self, tmp_path):
        """The file is the interface, so editing it has to be enough.

        Nothing in the app writes "this distributor is wrong about this part"
        -- a person does, in a spreadsheet, and then reopens the app. If only
        the app's own writes could invalidate the cache, the editable file
        would be a display copy rather than the store it claims to be.
        """
        api = make_api(tmp_path)
        self._ledger_with(api, "100nF 16V X7R 0402")
        api._attrs.record_fetched_categories("C1525", "lcsc", {"category": "Capacitors"})
        assert api.rebuild_inventory()[0]["section"] == "Passives - Capacitors"

        store._write_rows(api.base_dir, [  # noqa: SLF001 - the user re-labelled the row
            {**row, "category": "Memory"}
            for row in store.read_rows(api.base_dir)
        ])
        assert api.rebuild_inventory()[0]["section"] == "Other"


# ── the captured LCSC fixture ───────────────────────────────────────────────


class TestLcscCapture:
    """The captured LCSC payload carries its category under a key the client
    used to not read: ``parentCatalogList`` entries hold ``catalogNameEn``, so
    reading ``catalogName`` returned "" for every part, forever, with a green
    test suite. These replay the real fixture through the real client."""

    def test_every_captured_part_yields_a_category(self):
        products = lcsc_fixture_products()
        assert len(products) >= 20
        empty = [code for code, p in products.items()
                 if not (p.get("category") or "").strip()]
        assert not empty, f"LCSC captured no category for: {sorted(empty)}"

    def test_the_category_is_the_bucket_the_map_is_keyed_on(self):
        """Not the deeper DigiKey-flavoured crumb trail.

        "Passives" names no bucket anybody has labelled; "Capacitors" is the
        row in category_map.csv.
        """
        products = lcsc_fixture_products()
        buckets = {p["category"] for p in products.values()}
        assert "Passives" not in buckets
        assert "Capacitors" in buckets

    def test_the_captured_categories_all_reach_a_section(self):
        """Every bucket the real API returns is one the map knows.

        Not every one places -- some are deliberately ambiguous -- but a bucket
        that resolves to Unmapped would mean LCSC's live vocabulary drifted
        from the table, which is the drift this feature cannot detect any other
        way from a laptop.
        """
        products = lcsc_fixture_products()
        unmapped = set()
        for product in products.values():
            try:
                category_map.allowed("lcsc", [product["category"],
                                              product.get("subcategory") or ""])
            except category_map.Unmapped:
                unmapped.add(product["category"])
        assert not unmapped, f"category_map.csv does not know: {sorted(unmapped)}"

    def test_the_captured_parts_are_placed_by_their_own_category(self):
        products = lcsc_fixture_products()
        placed = {
            code: category_map.opinion("lcsc", p["category"], p.get("subcategory") or "")
            for code, p in products.items()
        }
        assert sum(1 for v in placed.values() if v) >= len(placed) * 0.8, (
            f"the map places too few captured parts: {placed}")

    def test_a_recorded_lcsc_category_moves_a_real_part(self, tmp_path):
        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525", desc="CL05B104KO5NNNC 0402")])
        api.rebuild_inventory()
        products = lcsc_fixture_products()
        product = products["C1525"]
        api._attrs.record_fetched_categories("C1525", "lcsc", product)
        inventory = api.rebuild_inventory()
        assert inventory[0]["section"] == "Passives - Capacitors"


# ── the /v1 route ───────────────────────────────────────────────────────────


class TestRoute:
    def test_returns_the_stored_rows(self, tmp_path):
        from fastapi.testclient import TestClient

        from server.app import create_app

        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        api._attrs.record_fetched_categories("C1525", "lcsc",
                                             {"category": "Capacitors",
                                              "subcategory": "Ceramic Capacitors"})
        client = TestClient(create_app(api))
        resp = client.get("/v1/parts/C1525/categories")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["distributor"] == "lcsc"
        assert body[0]["section"] == "Passives - Capacitors"

    def test_an_unknown_part_is_an_empty_list_not_a_404(self, tmp_path):
        """Same shape as /attributes: "nobody fetched this" is data, not an error."""
        from fastapi.testclient import TestClient

        from server.app import create_app

        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525")])
        api.rebuild_inventory()
        client = TestClient(create_app(api))
        assert client.get("/v1/parts/C1525/categories").json() == []


# ── the resolver used directly ──────────────────────────────────────────────


class TestResolver:
    def test_no_store_means_a_resolver_that_never_answers(self, tmp_path):
        resolver = store.resolver(str(tmp_path), inventory_ops.get_part_key)
        assert resolver({"LCSC Part Number": "C1525"}) is None

    def test_a_category_is_found_through_the_key_the_ledger_would_use(self, data_dir):
        """One identity, computed the same way on both sides.

        The write path resolves a fetch key to the inventory part_id and the
        rebuild reads ``inventory_ops.get_part_key``, so a category recorded
        against an LCSC code turns up for the ledger row -- and a row with no
        LCSC code resolves to its MPN and correctly does *not* match, because
        that is a different part as far as the ledger is concerned.
        """
        store.record_categories(data_dir, "C1525", "lcsc", "Capacitors")
        resolver = store.resolver(data_dir, inventory_ops.get_part_key)
        assert resolver({"LCSC Part Number": "C1525"}) is not None
        assert resolver({"Manufacture Part Number": "CL05B104KO5NNNC"}) is None

    def test_a_row_for_a_part_that_is_not_in_the_ledger_never_matches(self, data_dir):
        store.record_categories(data_dir, "GHOST", "lcsc", "Capacitors")
        resolver = store.resolver(data_dir, inventory_ops.get_part_key)
        assert resolver({"Description": "Resistor 10k"}) is None
