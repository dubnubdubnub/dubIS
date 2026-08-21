"""Tests for domain.attributes — the durable part-attribute store and its cache mirror.

Mirrors the style of tests/python/domain/test_pricing.py: the domain functions
are driven directly with a real temp SQLite cache and a real temp data dir, and
the facade/fetch-path integration is driven through a real InventoryApi.
"""

from __future__ import annotations

import csv
import os

import pytest

import domain.attributes as attributes
from domain.attribute_parse import KIND_RANGE, KIND_SCALAR, KIND_TOLERANCE, KIND_UNPARSED
from helpers import lcsc_fixture_products, make_api

# ── fixtures / helpers ───────────────────────────────────────────────────────


def _seed_parts(conn):
    """Insert the same test parts test_pricing.py uses (part_id + distributor PNs)."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "", "", ""),
        ("DRV8316C", "C9000", "DRV8316C", "296-DRV8316CRRGFRCT-ND", "", "595-DRV8316CRRGFR"),
    ]
    for pid, lcsc, mpn, dk, pololu, mouser in parts:
        conn.execute(
            "INSERT INTO parts (part_id, lcsc, mpn, digikey, pololu, mouser, section) "
            "VALUES (?,?,?,?,?,?,'Misc')",
            (pid, lcsc, mpn, dk, pololu, mouser),
        )
        conn.execute("INSERT INTO stock (part_id, quantity) VALUES (?, 10)", (pid,))
    conn.commit()


LCSC_CAP_ATTRS = [
    {"name": "Capacitance", "value": "100nF"},
    {"name": "Tolerance", "value": "±10%"},
    {"name": "Voltage Rating", "value": "16V"},
    {"name": "Operating Temperature", "value": "-55℃~+125℃"},
    {"name": "Temperature Coefficient", "value": "X7R"},
    {"name": "Number of Terminations", "value": "2"},
    {"name": "Ripple Current", "value": "-"},
]


def _rows_by_name(data_dir, part_id=None):
    return {r["name"]: r for r in attributes.read_rows(data_dir, part_id)}


# ── durable CSV ──────────────────────────────────────────────────────────────


class TestRecordAttributes:
    def test_writes_csv_with_the_full_header(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        path = attributes.csv_path(data_dir)
        assert os.path.exists(path)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == attributes.FIELDNAMES
            rows = list(reader)
        assert len(rows) == 6  # the "-" value is not stored

    def test_value_with_unit_keeps_raw_and_parsed_side_by_side(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        row = _rows_by_name(data_dir)["Voltage Rating"]
        assert row["raw_value"] == "16V"
        assert row["kind"] == KIND_SCALAR
        assert row["value_min"] == "16" and row["value_max"] == "16"
        assert row["unit"] == "V"
        assert row["canonical_name"] == "voltage_rating"
        assert row["distributor"] == "lcsc"
        assert row["observed_at"]
        assert row["source"] == "live_fetch"

    def test_range_keeps_both_endpoints(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        row = _rows_by_name(data_dir)["Operating Temperature"]
        assert row["kind"] == KIND_RANGE
        assert row["raw_value"] == "-55℃~+125℃"
        assert (float(row["value_min"]), float(row["value_max"])) == (-55.0, 125.0)
        assert row["unit"] == "°C"

    def test_tolerance_row(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        row = _rows_by_name(data_dir)["Tolerance"]
        assert row["kind"] == KIND_TOLERANCE
        assert (float(row["value_min"]), float(row["value_max"])) == (-10.0, 10.0)
        assert row["unit"] == "%"

    def test_plain_integer_row(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        row = _rows_by_name(data_dir)["Number of Terminations"]
        assert row["kind"] == KIND_SCALAR
        assert row["value_min"] == "2"
        assert row["unit"] == ""

    def test_unparseable_free_text_is_stored_raw_and_flagged(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", [
            {"name": "Features", "value": "Short Circuit Protection;Over Current Protection"},
        ])
        row = _rows_by_name(data_dir)["Features"]
        assert row["kind"] == KIND_UNPARSED
        assert row["raw_value"] == "Short Circuit Protection;Over Current Protection"
        assert row["value_min"] == "" and row["value_max"] == "" and row["unit"] == ""

    def test_absent_values_are_not_stored(self, data_dir):
        """LCSC publishes "-" for an attribute it has no value for."""
        written = attributes.record_attributes(data_dir, "C1525", "lcsc", [
            {"name": "Ripple Current", "value": "-"},
            {"name": "ESR", "value": ""},
            {"name": "Lifetime", "value": None},
        ])
        assert written == 0
        assert attributes.read_rows(data_dir) == []

    def test_nameless_attribute_is_skipped(self, data_dir):
        assert attributes.record_attributes(
            data_dir, "C1525", "lcsc", [{"name": "  ", "value": "5V"}]) == 0

    def test_qualifier_is_preserved(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", [
            {"name": "Voltage - Forward(Vf)", "value": "600mV@1A"},
        ])
        row = _rows_by_name(data_dir)["Voltage - Forward(Vf)"]
        assert row["qualifier"] == "1A"
        assert float(row["value_min"]) == pytest.approx(0.6)

    def test_refetch_is_idempotent(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS,
                                     observed_at="2026-01-01T00:00:00")
        first = attributes.read_rows(data_dir)
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS,
                                     observed_at="2026-02-02T00:00:00")
        second = attributes.read_rows(data_dir)
        assert len(second) == len(first) == 6
        # Same rows, refreshed timestamp — not a second copy.
        assert {r["canonical_name"] for r in second} == {r["canonical_name"] for r in first}
        assert {r["observed_at"] for r in second} == {"2026-02-02T00:00:00"}

    def test_refetch_updates_a_changed_value_in_place(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Voltage Rating", "value": "16V"}])
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Voltage Rating", "value": "25V"}])
        rows = attributes.read_rows(data_dir)
        assert len(rows) == 1
        assert rows[0]["raw_value"] == "25V"

    def test_case_variant_of_the_same_name_does_not_duplicate(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Operating Temperature", "value": "-40℃~+85℃"}])
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Operating temperature", "value": "-40℃~+85℃"}])
        assert len(attributes.read_rows(data_dir)) == 1

    def test_two_distributors_are_two_rows(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Voltage Rating", "value": "16V"}])
        attributes.record_attributes(data_dir, "C1525", "digikey",
                                     [{"name": "Voltage - Rated", "value": "16 V"}])
        rows = attributes.read_rows(data_dir)
        assert len(rows) == 2
        assert {r["distributor"] for r in rows} == {"lcsc", "digikey"}
        # Same canonical key, so a predicate finds both and can pick a source.
        assert {r["canonical_name"] for r in rows} == {"voltage_rating"}
        assert {r["name"] for r in rows} == {"Voltage Rating", "Voltage - Rated"}
        assert {float(r["value_min"]) for r in rows} == {16.0}

    def test_two_parts_do_not_collide(self, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Capacitance", "value": "100nF"}])
        attributes.record_attributes(data_dir, "C3338", "lcsc",
                                     [{"name": "Capacitance", "value": "100uF"}])
        assert len(attributes.read_rows(data_dir)) == 2
        assert len(attributes.read_rows(data_dir, "C1525")) == 1

    def test_duplicate_names_in_one_fetch_collapse(self, data_dir):
        written = attributes.record_attributes(data_dir, "C1525", "lcsc", [
            {"name": "Voltage Rating", "value": "16V"},
            {"name": "Voltage Rating", "value": "25V"},
        ])
        assert written == 1
        assert attributes.read_rows(data_dir)[0]["raw_value"] == "25V"

    def test_non_dict_entries_are_ignored(self, data_dir):
        assert attributes.record_attributes(
            data_dir, "C1525", "lcsc", ["nonsense", None, {"name": "A", "value": "1V"}]) == 1

    def test_missing_file_reads_empty(self, data_dir):
        assert attributes.read_rows(data_dir) == []


class TestCsvMigration:
    def test_legacy_header_without_the_newer_columns_still_loads(self, data_dir):
        """An existing deployment's file must keep loading (csv_io.migrate_csv_header)."""
        legacy_fields = ["part_id", "name", "canonical_name", "distributor",
                         "raw_value", "kind", "value_min", "value_max", "unit"]
        path = attributes.csv_path(data_dir)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=legacy_fields)
            writer.writeheader()
            writer.writerow({
                "part_id": "C1525", "name": "Capacitance",
                "canonical_name": "capacitance", "distributor": "lcsc",
                "raw_value": "100nF", "kind": KIND_SCALAR,
                "value_min": "1e-07", "value_max": "1e-07", "unit": "F",
            })

        rows = attributes.read_rows(data_dir)
        assert len(rows) == 1
        assert rows[0]["raw_value"] == "100nF"
        assert rows[0]["qualifier"] == ""      # new columns read back as ""
        assert rows[0]["observed_at"] == ""
        assert rows[0]["source"] == ""

        # And the file is now on the new header, with the legacy row intact.
        with open(path, newline="", encoding="utf-8") as f:
            assert csv.DictReader(f).fieldnames == attributes.FIELDNAMES

        # A later fetch upserts onto the migrated row rather than duplicating it.
        attributes.record_attributes(data_dir, "C1525", "lcsc",
                                     [{"name": "Capacitance", "value": "100nF"}])
        rows = attributes.read_rows(data_dir)
        assert len(rows) == 1
        assert rows[0]["source"] == "live_fetch"


# ── SQLite mirror ────────────────────────────────────────────────────────────


class TestLoadIntoDb:
    def test_mirrors_the_csv(self, db, data_dir):
        _seed_parts(db)
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        attributes.load_into_db(db, data_dir)
        rows = db.execute(
            "SELECT * FROM part_attributes WHERE part_id='C1525' ORDER BY canonical_name"
        ).fetchall()
        assert len(rows) == 6
        by_key = {r["canonical_name"]: r for r in rows}
        assert by_key["capacitance"]["value_min"] == pytest.approx(1e-7)
        assert by_key["capacitance"]["unit"] == "F"
        assert by_key["operating_temperature"]["value_max"] == pytest.approx(125.0)
        assert by_key["temperature_coefficient"]["kind"] == KIND_UNPARSED
        assert by_key["temperature_coefficient"]["value_min"] is None

    def test_is_idempotent(self, db, data_dir):
        _seed_parts(db)
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        attributes.load_into_db(db, data_dir)
        attributes.load_into_db(db, data_dir)
        assert db.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 6

    def test_drops_rows_deleted_from_the_csv(self, db, data_dir):
        _seed_parts(db)
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        attributes.load_into_db(db, data_dir)
        os.remove(attributes.csv_path(data_dir))
        attributes.load_into_db(db, data_dir)
        assert db.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 0

    def test_skips_parts_absent_from_inventory(self, db, data_dir):
        """The cache table has an FK onto parts; the CSV keeps the row anyway."""
        _seed_parts(db)
        attributes.record_attributes(data_dir, "NOT-IN-INVENTORY", "digikey",
                                     [{"name": "Capacitance", "value": "1uF"}])
        attributes.load_into_db(db, data_dir)
        assert db.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 0
        assert len(attributes.read_rows(data_dir, "NOT-IN-INVENTORY")) == 1

    def test_empty_parts_table_is_not_a_crash(self, db, data_dir):
        attributes.record_attributes(data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        attributes.load_into_db(db, data_dir)
        assert db.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 0


# ── record_fetched_attributes / get_attributes ───────────────────────────────


class TestRecordFetchedAttributes:
    def test_records_and_mirrors(self, db, data_dir):
        _seed_parts(db)
        written = attributes.record_fetched_attributes(
            db, data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        assert written == 6
        assert db.execute(
            "SELECT COUNT(*) FROM part_attributes WHERE part_id='C1525'").fetchone()[0] == 6

    def test_resolves_a_distributor_pn_to_the_inventory_part_id(self, db, data_dir):
        _seed_parts(db)
        attributes.record_fetched_attributes(
            db, data_dir, "296-DRV8316CRRGFRCT-ND", "digikey",
            [{"name": "Voltage - Supply (Vcc/Vdd)", "value": "4.5V ~ 24V"}])
        rows = attributes.read_rows(data_dir)
        assert [r["part_id"] for r in rows] == ["DRV8316C"]
        assert rows[0]["canonical_name"] == "supply_voltage"

    def test_lcsc_and_digikey_rows_land_on_one_part(self, db, data_dir):
        _seed_parts(db)
        attributes.record_fetched_attributes(
            db, data_dir, "C9000", "lcsc",
            [{"name": "Voltage - Supply", "value": "4.5V~24V"}])
        attributes.record_fetched_attributes(
            db, data_dir, "296-DRV8316CRRGFRCT-ND", "digikey",
            [{"name": "Voltage - Supply (Vcc/Vdd)", "value": "4.5 V ~ 24 V"}])
        rows = db.execute(
            "SELECT * FROM part_attributes WHERE canonical_name='supply_voltage'"
        ).fetchall()
        assert {r["distributor"] for r in rows} == {"lcsc", "digikey"}
        assert {r["part_id"] for r in rows} == {"DRV8316C"}
        assert {r["value_max"] for r in rows} == {24.0}

    def test_unknown_part_key_is_kept_under_its_raw_key(self, db, data_dir):
        """An alternate-part candidate we never purchased is the whole point."""
        _seed_parts(db)
        written = attributes.record_fetched_attributes(
            db, data_dir, "C999999", "lcsc", [{"name": "Capacitance", "value": "1uF"}])
        assert written == 1
        assert [r["part_id"] for r in attributes.read_rows(data_dir)] == ["C999999"]

    def test_no_attributes_writes_nothing(self, db, data_dir):
        _seed_parts(db)
        assert attributes.record_fetched_attributes(db, data_dir, "C1525", "lcsc", []) == 0
        assert attributes.record_fetched_attributes(db, data_dir, "C1525", "lcsc", None) == 0
        assert not os.path.exists(attributes.csv_path(data_dir))

    def test_repeated_fetch_does_not_duplicate_db_rows(self, db, data_dir):
        _seed_parts(db)
        for _ in range(3):
            attributes.record_fetched_attributes(db, data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        assert db.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 6
        assert len(attributes.read_rows(data_dir)) == 6


class TestGetAttributes:
    def test_returns_typed_rows_sorted_by_canonical_name(self, db, data_dir):
        _seed_parts(db)
        attributes.record_fetched_attributes(db, data_dir, "C1525", "lcsc", LCSC_CAP_ATTRS)
        out = attributes.get_attributes(db, data_dir, "C1525")
        assert [r["canonical_name"] for r in out] == sorted(
            r["canonical_name"] for r in out)
        capacitance = next(r for r in out if r["canonical_name"] == "capacitance")
        assert isinstance(capacitance["value_min"], float)
        assert capacitance["value_min"] == pytest.approx(1e-7)
        unparsed = next(r for r in out if r["kind"] == KIND_UNPARSED)
        assert unparsed["value_min"] is None

    def test_resolves_a_distributor_pn(self, db, data_dir):
        _seed_parts(db)
        attributes.record_fetched_attributes(
            db, data_dir, "C1525", "lcsc", [{"name": "Capacitance", "value": "100nF"}])
        assert len(attributes.get_attributes(db, data_dir, "CL05B104KO5NNNC")) == 1

    def test_unknown_part_returns_empty(self, db, data_dir):
        _seed_parts(db)
        assert attributes.get_attributes(db, data_dir, "NOPE") == []


# ── real corpus, end to end through the real client ─────────────────────────


class TestRealCorpusRoundTrip:
    def test_every_captured_lcsc_part_stores_its_parametrics(self, tmp_path, capsys):
        """Fetch -> normalize -> persist, using the real LcscClient and real store."""
        api = make_api(tmp_path)
        conn = api._get_cache()
        products = lcsc_fixture_products()
        for code in products:
            conn.execute("INSERT INTO parts (part_id, lcsc, section) VALUES (?,?,'Misc')",
                         (code, code))
            conn.execute("INSERT INTO stock (part_id, quantity) VALUES (?, 1)", (code,))
        conn.commit()

        total = 0
        for code, product in products.items():
            total += api.record_fetched_attributes(code, "lcsc", product["attributes"])

        stored = attributes.read_rows(api.base_dir)
        assert len(stored) == total
        assert {r["part_id"] for r in stored} == set(products)
        # Every part in the corpus contributes at least a few parametrics.
        per_part = {code: len(attributes.read_rows(api.base_dir, code)) for code in products}
        assert min(per_part.values()) >= 4, per_part

        kinds = {}
        for row in stored:
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        with capsys.disabled():
            print(f"\n[attribute store round-trip] {len(products)} LCSC parts -> "
                  f"{len(stored)} stored rows, median "
                  f"{sorted(per_part.values())[len(per_part) // 2]} per part, kinds={kinds}")

        # The SQLite mirror agrees with the durable CSV.
        assert conn.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == len(stored)

        # Re-running the whole corpus adds nothing.
        for code, product in products.items():
            api.record_fetched_attributes(code, "lcsc", product["attributes"])
        assert len(attributes.read_rows(api.base_dir)) == len(stored)
        api.shutdown()


# ── fetch-path integration ───────────────────────────────────────────────────


class TestFetchPathPersistence:
    def _cached_product(self, api, code, attrs):
        product = {"productCode": code, "provider": "lcsc", "attributes": attrs,
                   "prices": [], "stock": 1}
        api._distributors._lcsc._cache[code] = product
        return product

    def test_fetching_a_product_persists_its_attributes(self, tmp_path):
        api = make_api(tmp_path)
        conn = api._get_cache()
        conn.execute("INSERT INTO parts (part_id, lcsc, section) VALUES ('C1525','C1525','Misc')")
        conn.execute("INSERT INTO stock (part_id, quantity) VALUES ('C1525', 1)")
        conn.commit()
        product = self._cached_product(api, "C1525", LCSC_CAP_ATTRS)

        # The product preview route calls exactly this.
        assert api.fetch_lcsc_product("C1525") is product  # returned unchanged
        assert len(attributes.read_rows(api.base_dir, "C1525")) == 6
        assert conn.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 6
        api.shutdown()

    def test_hovering_twice_does_not_duplicate(self, tmp_path):
        api = make_api(tmp_path)
        self._cached_product(api, "C1525", LCSC_CAP_ATTRS)
        api.fetch_lcsc_product("C1525")
        api.fetch_lcsc_product("C1525")
        assert len(attributes.read_rows(api.base_dir, "C1525")) == 6
        api.shutdown()

    def test_product_without_attributes_writes_nothing(self, tmp_path):
        api = make_api(tmp_path)
        api._distributors._lcsc._cache["C2040"] = {"productCode": "C2040", "provider": "lcsc"}
        assert api.fetch_lcsc_product("C2040") is not None
        assert not os.path.exists(attributes.csv_path(api.base_dir))
        api.shutdown()

    def test_store_failure_does_not_break_the_preview(self, tmp_path, monkeypatch):
        """A preview must survive a store failure — warned, not fatal."""
        api = make_api(tmp_path)
        product = self._cached_product(api, "C1525", LCSC_CAP_ATTRS)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(attributes, "record_fetched_attributes", boom)
        assert api.fetch_lcsc_product("C1525") is product
        api.shutdown()

    def test_recording_fetcher_wraps_a_manager_fetch(self, tmp_path):
        """The bulk fetch_missing_descriptions path uses this wrapper."""
        api = make_api(tmp_path)
        self._cached_product(api, "C1525", LCSC_CAP_ATTRS)
        fetch = api._attrs.recording_fetcher("lcsc", api._distributors.fetch_lcsc_product)
        assert fetch("C1525")["productCode"] == "C1525"
        assert len(attributes.read_rows(api.base_dir, "C1525")) == 6
        api.shutdown()

    def test_get_part_attributes_reads_back_through_the_api(self, tmp_path):
        api = make_api(tmp_path)
        self._cached_product(api, "C1525", LCSC_CAP_ATTRS)
        api.fetch_lcsc_product("C1525")
        out = api.get_part_attributes("C1525")
        assert {r["distributor"] for r in out} == {"lcsc"}
        assert any(r["unit"] == "F" for r in out)
        api.shutdown()


# ── rebuild integration ──────────────────────────────────────────────────────


class TestRebuildRestore:
    def test_rebuild_restores_the_table_from_the_csv(self, tmp_path):
        """cache.db is deletable: a rebuild must bring the attributes back."""
        from helpers import make_part, write_ledger

        api = make_api(tmp_path)
        write_ledger(api, [make_part(lcsc="C1525", qty=10)])
        api.rebuild_inventory()
        api.record_fetched_attributes("C1525", "lcsc", LCSC_CAP_ATTRS)
        conn = api._get_cache()
        assert conn.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 6

        conn.execute("DELETE FROM part_attributes")
        conn.commit()
        api.rebuild_inventory()
        assert conn.execute("SELECT COUNT(*) FROM part_attributes").fetchone()[0] == 6
        api.shutdown()
