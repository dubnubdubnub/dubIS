"""Tests for cache_db SQLite cache layer."""

import csv
import os
import sqlite3

import pytest

import cache_db
from inventory_ops import get_part_key


@pytest.fixture
def db(tmp_path):
    """Create an in-memory cache database."""
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    yield conn
    conn.close()


class TestSchema:
    def test_tables_created(self, db):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert tables >= {"cache_meta", "parts", "stock"}

    def test_schema_version_set(self, db):
        row = db.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()
        assert row is not None
        assert row[0] == "1"

    def test_foreign_key_enforced(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO stock (part_id, quantity) VALUES ('nonexistent', 10)"
            )


class TestPopulate:
    def _make_merged(self):
        """Create a minimal merged dict like read_and_merge() returns."""
        return {
            "C1525": {
                "LCSC Part Number": "C1525",
                "Manufacture Part Number": "CL05B104KO5NNNC",
                "Digikey Part Number": "",
                "Pololu Part Number": "",
                "Mouser Part Number": "",
                "Manufacturer": "Samsung Electro-Mechanics",
                "Package": "0402",
                "Description": "100nF 16V 0402 Capacitor MLCC",
                "Quantity": "200",
                "Unit Price($)": "0.0074",
                "Ext.Price($)": "1.48",
                "RoHS": "Yes",
                "Date Code / Lot No.": "",
                "Customer NO.": "",
                "Estimated lead time (business days)": "",
            },
            "C2875244": {
                "LCSC Part Number": "C2875244",
                "Manufacture Part Number": "RC0402FR-074K7L",
                "Digikey Part Number": "",
                "Pololu Part Number": "",
                "Mouser Part Number": "",
                "Manufacturer": "YAGEO",
                "Package": "0402",
                "Description": "4.7kOhm 0402 Resistor",
                "Quantity": "100",
                "Unit Price($)": "0.0023",
                "Ext.Price($)": "0.23",
                "RoHS": "Yes",
                "Date Code / Lot No.": "",
                "Customer NO.": "",
                "Estimated lead time (business days)": "",
            },
        }

    def _make_categorized(self, merged):
        from inventory_ops import categorize_and_sort
        return categorize_and_sort(list(merged.values()))

    def test_populate_creates_parts_and_stock(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        parts = db.execute("SELECT count(*) FROM parts").fetchone()[0]
        stock = db.execute("SELECT count(*) FROM stock").fetchone()[0]
        assert parts == 2
        assert stock == 2

    def test_populate_part_fields(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        part = db.execute("SELECT * FROM parts WHERE part_id='C1525'").fetchone()
        assert part["lcsc"] == "C1525"
        assert part["mpn"] == "CL05B104KO5NNNC"
        assert part["manufacturer"] == "Samsung Electro-Mechanics"
        assert part["package"] == "0402"
        assert part["description"] == "100nF 16V 0402 Capacitor MLCC"
        assert "Capacitor" in part["section"]

    def test_populate_stock_fields(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        stock = db.execute("SELECT * FROM stock WHERE part_id='C1525'").fetchone()
        assert stock["quantity"] == 200
        assert abs(stock["unit_price"] - 0.0074) < 0.0001
        assert abs(stock["ext_price"] - 1.48) < 0.01

    def test_populate_sort_key(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        cap = db.execute("SELECT sort_key FROM parts WHERE part_id='C1525'").fetchone()
        res = db.execute("SELECT sort_key FROM parts WHERE part_id='C2875244'").fetchone()
        assert cap["sort_key"] is not None
        assert cap["sort_key"] < 1.0  # farads
        assert res["sort_key"] is not None
        assert res["sort_key"] > 1000  # ohms

    def test_populate_clears_old_data(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        small = {"C1525": merged["C1525"]}
        from inventory_ops import categorize_and_sort
        small_cat = categorize_and_sort(list(small.values()))
        cache_db.populate_full(db, small, small_cat)
        assert db.execute("SELECT count(*) FROM parts").fetchone()[0] == 1


class TestQuery:
    def _populate(self, db):
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)

    def test_returns_list_of_dicts(self, db):
        self._populate(db)
        result = cache_db.query_inventory(db)
        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], dict)

    def test_dict_keys_match_load_organized(self, db):
        self._populate(db)
        result = cache_db.query_inventory(db)
        expected_keys = {
            "section", "lcsc", "mpn", "digikey", "pololu", "mouser",
            "manufacturer", "package", "description",
            "qty", "unit_price", "ext_price",
        }
        assert set(result[0].keys()) == expected_keys

    def test_values_correct(self, db):
        self._populate(db)
        result = cache_db.query_inventory(db)
        cap = next(r for r in result if r["lcsc"] == "C1525")
        assert cap["qty"] == 200
        assert abs(cap["unit_price"] - 0.0074) < 0.0001
        assert cap["mpn"] == "CL05B104KO5NNNC"
        assert "Capacitor" in cap["section"]

    def test_ordered_by_section_then_sort_key(self, db):
        self._populate(db)
        result = cache_db.query_inventory(db)
        sections = [r["section"] for r in result]
        assert all(s for s in sections)

    def test_empty_db_returns_empty_list(self, db):
        result = cache_db.query_inventory(db)
        assert result == []


class TestIncrementalOps:
    def _populate(self, db):
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)

    def test_apply_stock_delta_decrease(self, db):
        self._populate(db)
        cache_db.apply_stock_delta(db, "C1525", -50)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 150

    def test_apply_stock_delta_increase(self, db):
        self._populate(db)
        cache_db.apply_stock_delta(db, "C1525", 30)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 230

    def test_apply_stock_delta_floors_at_zero(self, db):
        self._populate(db)
        cache_db.apply_stock_delta(db, "C1525", -9999)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 0

    def test_set_stock_quantity(self, db):
        self._populate(db)
        cache_db.set_stock_quantity(db, "C1525", 42)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 42

    def test_upsert_part_new(self, db):
        cache_db.upsert_part(db, "C999999", {
            "LCSC Part Number": "C999999",
            "Manufacture Part Number": "NEW-PART",
            "Digikey Part Number": "",
            "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "TestCorp",
            "Description": "Test part",
            "Package": "0805",
            "Quantity": "50",
            "Unit Price($)": "0.10",
            "Ext.Price($)": "5.00",
            "RoHS": "",
            "Date Code / Lot No.": "",
        }, section="Other")
        part = db.execute("SELECT * FROM parts WHERE part_id='C999999'").fetchone()
        assert part["mpn"] == "NEW-PART"
        stock = db.execute("SELECT * FROM stock WHERE part_id='C999999'").fetchone()
        assert stock["quantity"] == 50

    def test_upsert_part_existing_updates(self, db):
        self._populate(db)
        cache_db.upsert_part(db, "C1525", {
            "LCSC Part Number": "C1525",
            "Manufacture Part Number": "CL05B104KO5NNNC",
            "Digikey Part Number": "",
            "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "Samsung Updated",
            "Description": "100nF 16V 0402 Capacitor MLCC",
            "Package": "0402",
            "Quantity": "300",
            "Unit Price($)": "0.005",
            "Ext.Price($)": "1.50",
            "RoHS": "Yes",
            "Date Code / Lot No.": "",
        }, section="Passives - Capacitors")
        part = db.execute("SELECT * FROM parts WHERE part_id='C1525'").fetchone()
        assert part["manufacturer"] == "Samsung Updated"
        stock = db.execute("SELECT * FROM stock WHERE part_id='C1525'").fetchone()
        assert stock["quantity"] == 300

    def test_update_stock_price(self, db):
        self._populate(db)
        cache_db.update_stock_price(db, "C1525", unit_price=0.01, ext_price=2.00)
        stock = db.execute("SELECT * FROM stock WHERE part_id='C1525'").fetchone()
        assert abs(stock["unit_price"] - 0.01) < 0.0001
        assert abs(stock["ext_price"] - 2.00) < 0.01


class TestCheckpoint:
    def test_write_and_read_checkpoint(self, db):
        cache_db.write_checkpoint(db, purchase_lines=10, adjustment_lines=5)
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_lines"] == 10
        assert cp["adjustment_lines"] == 5

    def test_read_checkpoint_missing_returns_zeros(self, db):
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_lines"] == 0
        assert cp["adjustment_lines"] == 0

    def test_update_checkpoint(self, db):
        cache_db.write_checkpoint(db, purchase_lines=10, adjustment_lines=5)
        cache_db.write_checkpoint(db, purchase_lines=20, adjustment_lines=12)
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_lines"] == 20
        assert cp["adjustment_lines"] == 12


class TestCountLines:
    def test_count_csv_lines(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["a", "b"])  # header
            writer.writerow(["1", "2"])
            writer.writerow(["3", "4"])
        assert cache_db.count_csv_data_lines(csv_path) == 2

    def test_count_csv_lines_empty(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["a", "b"])  # header only
        assert cache_db.count_csv_data_lines(csv_path) == 0

    def test_count_csv_lines_missing_file(self, tmp_path):
        csv_path = str(tmp_path / "nonexistent.csv")
        assert cache_db.count_csv_data_lines(csv_path) == 0


class TestCatchUp:
    def _write_adjustments(self, path, adj_fieldnames, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=adj_fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_catch_up_replays_new_adjustments(self, db, tmp_path):
        # Populate cache with a part
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)
        cache_db.write_checkpoint(db, purchase_lines=2, adjustment_lines=0)

        # Write adjustments file with 2 rows
        adj_path = str(tmp_path / "adjustments.csv")
        adj_fields = ["timestamp", "type", "lcsc_part", "quantity",
                       "bom_file", "board_qty", "note", "source"]
        self._write_adjustments(adj_path, adj_fields, [
            {"timestamp": "2026-01-01T00:00:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-10",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
            {"timestamp": "2026-01-01T00:01:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-20",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
        ])

        # Catch up — should apply both new adjustment rows
        purchase_path = str(tmp_path / "purchase_ledger.csv")
        cache_db.catch_up(db, purchase_path, adj_path, adj_fields)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 170  # 200 - 10 - 20

    def test_catch_up_skips_already_processed(self, db, tmp_path):
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)
        # Mark 1 adjustment as already processed
        cache_db.write_checkpoint(db, purchase_lines=2, adjustment_lines=1)

        adj_path = str(tmp_path / "adjustments.csv")
        adj_fields = ["timestamp", "type", "lcsc_part", "quantity",
                       "bom_file", "board_qty", "note", "source"]
        self._write_adjustments(adj_path, adj_fields, [
            {"timestamp": "2026-01-01T00:00:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-10",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
            {"timestamp": "2026-01-01T00:01:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-20",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
        ])

        cache_db.catch_up(db, purchase_path=str(tmp_path / "purchase_ledger.csv"),
                          adjustments_path=adj_path, adj_fieldnames=adj_fields)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        # Only row 2 applied (row 1 was already processed)
        assert qty == 180  # 200 - 20

    def test_catch_up_noop_when_current(self, db, tmp_path):
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)
        cache_db.write_checkpoint(db, purchase_lines=2, adjustment_lines=2)

        adj_path = str(tmp_path / "adjustments.csv")
        adj_fields = ["timestamp", "type", "lcsc_part", "quantity",
                       "bom_file", "board_qty", "note", "source"]
        self._write_adjustments(adj_path, adj_fields, [
            {"timestamp": "2026-01-01T00:00:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-10",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
            {"timestamp": "2026-01-01T00:01:00", "type": "remove",
             "lcsc_part": "C1525", "quantity": "-20",
             "bom_file": "", "board_qty": "", "note": "", "source": ""},
        ])

        cache_db.catch_up(db, purchase_path=str(tmp_path / "purchase_ledger.csv"),
                          adjustments_path=adj_path, adj_fieldnames=adj_fields)
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 200  # unchanged
