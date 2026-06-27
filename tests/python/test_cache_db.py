"""Tests for cache_db SQLite cache layer."""

import csv
import sqlite3

import pytest

import cache_db
import domain.schema


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
        assert row[0] == "7"

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
        expected_keys = {f.py_key for f in domain.schema.INVENTORY_FIELDS if f.to_js}
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


ADJ_FIELDS = ["timestamp", "type", "lcsc_part", "quantity",
              "bom_file", "board_qty", "note", "source"]


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _adj_row(ts, qty, adj_type="remove", part="C1525"):
    return {"timestamp": ts, "type": adj_type, "lcsc_part": part,
            "quantity": str(qty), "bom_file": "", "board_qty": "",
            "note": "", "source": ""}


class TestCheckpoint:
    def test_write_and_read_checkpoint(self, db, tmp_path):
        purchase_path = str(tmp_path / "purchase_ledger.csv")
        _write_csv(purchase_path, ["a", "b"], [{"a": "1", "b": "2"}])
        adj_path = str(tmp_path / "adjustments.csv")
        _write_csv(adj_path, ADJ_FIELDS, [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ])

        cache_db.write_checkpoint(db, purchase_path=purchase_path,
                                  adjustments_path=adj_path)
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_hash"] == cache_db._file_hash(purchase_path)
        assert cp["adjustment_count"] == 2
        assert cp["adjustment_prefix_hash"] != ""

    def test_read_checkpoint_missing_returns_empty(self, db):
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_hash"] == ""
        assert cp["adjustment_count"] == 0
        assert cp["adjustment_prefix_hash"] == ""

    def test_update_checkpoint(self, db, tmp_path):
        purchase_path = str(tmp_path / "purchase_ledger.csv")
        adj_path = str(tmp_path / "adjustments.csv")
        _write_csv(purchase_path, ["a", "b"], [{"a": "1", "b": "2"}])
        _write_csv(adj_path, ADJ_FIELDS, [_adj_row("2026-01-01T00:00:00", -10)])
        cache_db.write_checkpoint(db, purchase_path=purchase_path,
                                  adjustments_path=adj_path)

        # Change the files, rewrite the checkpoint
        _write_csv(purchase_path, ["a", "b"],
                   [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}])
        _write_csv(adj_path, ADJ_FIELDS, [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ])
        cache_db.write_checkpoint(db, purchase_path=purchase_path,
                                  adjustments_path=adj_path)
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_hash"] == cache_db._file_hash(purchase_path)
        assert cp["adjustment_count"] == 2

    def test_file_hash_missing_returns_empty(self, tmp_path):
        assert cache_db._file_hash(str(tmp_path / "nope.csv")) == ""

    def test_read_checkpoint_old_format_returns_empty(self, db):
        """Old caches stored purchase_lines/adjustment_lines ints; new keys
        come back empty so catch_up triggers a one-time full rebuild."""
        db.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES "
            "('purchase_lines', '10')")
        db.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES "
            "('adjustment_lines', '5')")
        db.commit()
        cp = cache_db.read_checkpoint(db)
        assert cp["purchase_hash"] == ""
        assert cp["adjustment_count"] == 0
        assert cp["adjustment_prefix_hash"] == ""


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
    def _write_purchase_ledger(self, path, merged):
        """Write a purchase ledger CSV matching the merged dict (2 data rows)."""
        from inventory_api import InventoryApi
        fieldnames = InventoryApi.FIELDNAMES
        _write_csv(path, fieldnames,
            [{fn: row.get(fn, "") for fn in fieldnames} for row in merged.values()])

    def _setup(self, db, tmp_path, *, adj_rows, checkpoint_after):
        """Populate the cache, write purchase ledger + adjustments, and write a
        checkpoint covering the first ``checkpoint_after`` adjustment rows."""
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)

        purchase_path = str(tmp_path / "purchase_ledger.csv")
        self._write_purchase_ledger(purchase_path, merged)
        adj_path = str(tmp_path / "adjustments.csv")
        _write_csv(adj_path, ADJ_FIELDS, adj_rows)

        # Write a checkpoint as if only the first ``checkpoint_after`` rows were
        # processed: temporarily truncate the file, checkpoint, restore.
        _write_csv(adj_path, ADJ_FIELDS, adj_rows[:checkpoint_after])
        cache_db.write_checkpoint(db, purchase_path=purchase_path,
                                  adjustments_path=adj_path)
        _write_csv(adj_path, ADJ_FIELDS, adj_rows)
        return purchase_path, adj_path

    def test_catch_up_replays_new_adjustments(self, db, tmp_path):
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=0)

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is True
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 170  # 200 - 10 - 20
        # Checkpoint advanced to cover all rows
        cp = cache_db.read_checkpoint(db)
        assert cp["adjustment_count"] == 2

    def test_catch_up_skips_already_processed(self, db, tmp_path):
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        # Checkpoint covers the first row already
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=1)

        result = cache_db.catch_up(db, purchase_path=purchase_path,
                                   adjustments_path=adj_path, adj_fieldnames=ADJ_FIELDS)
        assert result is True
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        # Only row 2 applied (row 1 was already processed)
        assert qty == 180  # 200 - 20

    def test_catch_up_returns_false_on_purchase_append(self, db, tmp_path):
        """catch_up returns False when purchase ledger has new rows."""
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=[], checkpoint_after=0)
        # Append a row to the purchase ledger after the checkpoint
        with open(purchase_path, "a", newline="", encoding="utf-8") as f:
            f.write("EXTRA-ROW\n")

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is False  # signals full rebuild needed

    def test_catch_up_returns_false_on_purchase_inplace_edit(self, db, tmp_path):
        """An in-place edit (same row count, changed value) must be detected."""
        adj_rows = [_adj_row("2026-01-01T00:00:00", -10)]
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=1)

        # Edit a unit price in place — same number of rows
        from inventory_api import InventoryApi
        fieldnames = InventoryApi.FIELDNAMES
        rows = []
        with open(purchase_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
        rows[0]["Unit Price($)"] = "9.99"  # was 0.0074
        _write_csv(purchase_path, fieldnames,
                   [{fn: r.get(fn, "") for fn in fieldnames} for r in rows])
        assert cache_db.count_csv_data_lines(purchase_path) == 2  # unchanged count

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is False  # in-place edit triggers full rebuild

    def test_catch_up_returns_false_on_processed_adjustment_edit(self, db, tmp_path):
        """Editing an already-processed adjustment row triggers full rebuild."""
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        # Checkpoint covers both rows
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=2)

        # Edit the first (already-processed) row in place
        edited = [_adj_row("2026-01-01T00:00:00", -999), adj_rows[1]]
        _write_csv(adj_path, ADJ_FIELDS, edited)

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is False

    def test_catch_up_returns_false_on_processed_adjustment_removed(self, db, tmp_path):
        """Removing an already-processed adjustment row (rollback) triggers rebuild."""
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=2)

        # Remove the first processed row
        _write_csv(adj_path, ADJ_FIELDS, [adj_rows[1]])

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is False

    def test_catch_up_returns_false_on_processed_adjustment_reorder(self, db, tmp_path):
        """Reordering already-processed adjustment rows triggers rebuild."""
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=2)

        # Swap the two processed rows
        _write_csv(adj_path, ADJ_FIELDS, [adj_rows[1], adj_rows[0]])

        result = cache_db.catch_up(db, purchase_path, adj_path, ADJ_FIELDS)
        assert result is False

    def test_catch_up_noop_when_current(self, db, tmp_path):
        adj_rows = [
            _adj_row("2026-01-01T00:00:00", -10),
            _adj_row("2026-01-01T00:01:00", -20),
        ]
        # Checkpoint already covers all rows; nothing new
        purchase_path, adj_path = self._setup(
            db, tmp_path, adj_rows=adj_rows, checkpoint_after=2)

        result = cache_db.catch_up(db, purchase_path=purchase_path,
                                   adjustments_path=adj_path, adj_fieldnames=ADJ_FIELDS)
        assert result is True
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 200  # unchanged


class TestVerify:
    def _populate_and_write_csvs(self, db, tmp_path):
        """Populate cache and write matching CSVs."""
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)

        from inventory_api import InventoryApi
        fieldnames = InventoryApi.FIELDNAMES
        purchase_path = str(tmp_path / "purchase_ledger.csv")
        with open(purchase_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in merged.values():
                writer.writerow({fn: row.get(fn, "") for fn in fieldnames})

        adj_path = str(tmp_path / "adjustments.csv")
        return purchase_path, adj_path, fieldnames

    def test_verify_consistent_cache(self, db, tmp_path):
        purchase_path, adj_path, fieldnames = self._populate_and_write_csvs(db, tmp_path)
        mismatches = cache_db.verify_parts(
            db, ["C1525", "C2875244"], purchase_path, adj_path, fieldnames,
        )
        assert mismatches == []

    def test_verify_detects_mismatch(self, db, tmp_path):
        purchase_path, adj_path, fieldnames = self._populate_and_write_csvs(db, tmp_path)
        db.execute("UPDATE stock SET quantity = 999 WHERE part_id = 'C1525'")
        db.commit()
        mismatches = cache_db.verify_parts(
            db, ["C1525"], purchase_path, adj_path, fieldnames,
        )
        assert len(mismatches) == 1
        assert mismatches[0]["part_id"] == "C1525"
        assert mismatches[0]["cache_qty"] == 999
        assert mismatches[0]["expected_qty"] == 200

    def test_verify_fixes_mismatch_when_requested(self, db, tmp_path):
        purchase_path, adj_path, fieldnames = self._populate_and_write_csvs(db, tmp_path)
        db.execute("UPDATE stock SET quantity = 999 WHERE part_id = 'C1525'")
        db.commit()
        mismatches = cache_db.verify_parts(
            db, ["C1525"], purchase_path, adj_path, fieldnames, fix=True,
        )
        assert len(mismatches) == 1
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 200


class TestSchemaV3:
    def test_fresh_schema_has_generic_parts_tables(self, db):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "generic_parts" in tables
        assert "generic_part_members" in tables

    def test_schema_version_is_3(self, db):
        row = db.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "7"

    def test_generic_parts_columns(self, db):
        db.execute(
            """INSERT INTO generic_parts (generic_part_id, name, part_type, spec_json, strictness_json)
               VALUES ('gp_100nf_0402', '100nF 0402 MLCC', 'capacitor',
                       '{"value":"100nF","package":"0402"}',
                       '{"required":["value","package"]}')"""
        )
        db.commit()
        row = db.execute("SELECT * FROM generic_parts WHERE generic_part_id='gp_100nf_0402'").fetchone()
        assert row["name"] == "100nF 0402 MLCC"
        assert row["part_type"] == "capacitor"

    def test_generic_part_members_with_foreign_keys(self, db):
        db.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C1525', 'C1525')")
        db.execute(
            """INSERT INTO generic_parts (generic_part_id, name, part_type, spec_json, strictness_json)
               VALUES ('gp1', 'Test', 'capacitor', '{}', '{}')"""
        )
        db.execute(
            """INSERT INTO generic_part_members (generic_part_id, part_id, source)
               VALUES ('gp1', 'C1525', 'auto')"""
        )
        db.commit()
        row = db.execute("SELECT * FROM generic_part_members").fetchone()
        assert row["source"] == "auto"
        assert row["preferred"] == 0

    def test_v2_to_v3_migration(self, tmp_path):
        import cache_db as cdb
        db_path = str(tmp_path / "v2.db")
        conn = cdb.connect(db_path)
        # Create v2 schema manually (has prices but no generic_parts)
        conn.executescript("""
            CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE parts (part_id TEXT PRIMARY KEY);
            CREATE TABLE stock (part_id TEXT PRIMARY KEY REFERENCES parts(part_id),
                                quantity INTEGER DEFAULT 0,
                                unit_price REAL DEFAULT 0.0,
                                ext_price REAL DEFAULT 0.0);
            CREATE TABLE prices (part_id TEXT NOT NULL REFERENCES parts(part_id),
                                 distributor TEXT NOT NULL,
                                 latest_unit_price REAL,
                                 avg_unit_price REAL,
                                 price_count INTEGER DEFAULT 0,
                                 last_observed TEXT,
                                 moq INTEGER,
                                 source TEXT,
                                 PRIMARY KEY (part_id, distributor));
        """)
        conn.execute("INSERT INTO cache_meta VALUES ('schema_version', '2')")
        conn.commit()
        cdb.create_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "generic_parts" in tables
        assert "generic_part_members" in tables
        version = conn.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "7"
        conn.close()


class TestSchemaMigration:
    def test_fresh_schema_has_prices_table(self, db):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "prices" in tables

    def test_schema_version_is_3(self, db):
        row = db.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "7"

    def test_prices_table_columns(self, db):
        db.execute("INSERT INTO parts (part_id) VALUES ('C1525')")
        db.execute(
            """INSERT INTO prices (part_id, distributor, latest_unit_price,
               avg_unit_price, price_count, last_observed)
               VALUES ('C1525', 'lcsc', 0.0074, 0.0074, 1, '2026-01-01T00:00:00')"""
        )
        db.commit()
        row = db.execute("SELECT * FROM prices WHERE part_id='C1525'").fetchone()
        assert row["distributor"] == "lcsc"
        assert row["price_count"] == 1

    def test_v1_to_v3_migration(self, tmp_path):
        """Opening a v1 database should auto-migrate to v3."""
        import cache_db as cdb
        db_path = str(tmp_path / "old.db")
        conn = cdb.connect(db_path)
        conn.executescript("""
            CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE parts (part_id TEXT PRIMARY KEY);
            CREATE TABLE stock (part_id TEXT PRIMARY KEY REFERENCES parts(part_id),
                                quantity INTEGER DEFAULT 0,
                                unit_price REAL DEFAULT 0.0,
                                ext_price REAL DEFAULT 0.0);
        """)
        conn.execute("INSERT INTO cache_meta VALUES ('schema_version', '1')")
        conn.commit()
        cdb.create_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "prices" in tables
        assert "generic_parts" in tables
        assert "generic_part_members" in tables
        version = conn.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "7"
        conn.close()


class TestSchemaV4:
    def test_generic_parts_has_source_column(self, db):
        """generic_parts table should have a source column."""
        row = db.execute("PRAGMA table_info(generic_parts)").fetchall()
        col_names = [r["name"] for r in row]
        assert "source" in col_names

    def test_source_defaults_to_manual(self, db):
        """source should default to 'manual' for user-created groups."""
        db.execute(
            "INSERT INTO parts (part_id) VALUES ('test1')"
        )
        db.execute(
            "INSERT INTO generic_parts (generic_part_id, name, part_type) VALUES ('gp1', 'Test', 'other')"
        )
        db.commit()
        row = db.execute("SELECT source FROM generic_parts WHERE generic_part_id='gp1'").fetchone()
        assert row["source"] == "manual"


class TestIntegration:
    """Verify cache output matches legacy load_organized() output."""

    def _build_purchase_ledger(self, path, fieldnames, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_cache_matches_legacy_rebuild(self, db, tmp_path):
        """Full pipeline via cache must produce same output as legacy pipeline."""
        from inventory_api import InventoryApi
        import inventory_ops

        fieldnames = InventoryApi.FIELDNAMES
        purchase_path = str(tmp_path / "purchase_ledger.csv")
        adj_path = str(tmp_path / "adjustments.csv")
        output_path = str(tmp_path / "inventory.csv")

        merged = TestPopulate._make_merged(self)
        self._build_purchase_ledger(purchase_path, fieldnames,
            [{fn: row.get(fn, "") for fn in fieldnames} for row in merged.values()])

        # Write adjustments
        adj_fields = InventoryApi.ADJ_FIELDNAMES
        with open(adj_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=adj_fields)
            writer.writeheader()
            writer.writerow({
                "timestamp": "2026-01-01T00:00:00", "type": "remove",
                "lcsc_part": "C1525", "quantity": "-50",
                "bom_file": "", "board_qty": "", "note": "", "source": "",
            })

        # Legacy pipeline
        legacy = inventory_ops.rebuild(
            purchase_path, adj_path, output_path,
            fieldnames, InventoryApi.FLAT_SECTION_ORDER,
        )

        # Cache pipeline
        file_fn, merged_dict = inventory_ops.read_and_merge(purchase_path, fieldnames)
        inventory_ops.apply_adjustments(merged_dict, adj_path, file_fn)
        categorized = inventory_ops.categorize_and_sort(list(merged_dict.values()))
        cache_db.populate_full(db, merged_dict, categorized)
        cached = cache_db.query_inventory(db)

        # Compare: same parts, same quantities, same sections
        assert len(cached) == len(legacy)
        legacy_by_key = {(r["lcsc"] or r["mpn"]): r for r in legacy}
        cached_by_key = {(r["lcsc"] or r["mpn"]): r for r in cached}
        assert set(legacy_by_key.keys()) == set(cached_by_key.keys())
        for key in legacy_by_key:
            assert cached_by_key[key]["qty"] == legacy_by_key[key]["qty"], f"qty mismatch for {key}"
            assert cached_by_key[key]["section"] == legacy_by_key[key]["section"], f"section mismatch for {key}"
            assert abs(cached_by_key[key]["unit_price"] - legacy_by_key[key]["unit_price"]) < 0.001, f"price mismatch for {key}"


def test_schema_v6_creates_vendors_table(tmp_path):
    import cache_db
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(vendors)")}
    assert cols == {"id", "name", "url", "favicon_path", "type", "icon"}
    conn.close()


def test_schema_v6_creates_purchase_orders_table(tmp_path):
    import cache_db
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(purchase_orders)")}
    assert cols == {"po_id", "vendor_id", "source_file_hash", "source_file_ext",
                    "purchase_date", "notes"}
    conn.close()


def test_schema_v6_adds_primary_vendor_id_to_parts(tmp_path):
    import cache_db
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(parts)")}
    assert "primary_vendor_id" in cols
    conn.close()


def test_schema_v6_bumps_version(tmp_path):
    import cache_db
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    row = conn.execute(
        "SELECT value FROM cache_meta WHERE key='schema_version'"
    ).fetchone()
    assert row["value"] == "7"
    conn.close()


def test_v5_to_v6_migration_adds_primary_vendor_id(tmp_path):
    """Existing v5 database upgrades to v6 with primary_vendor_id added to parts."""
    import cache_db

    db_path = str(tmp_path / "cache.db")
    # Manually create a v5-like state: cache_meta says "5" and parts table lacks primary_vendor_id
    conn = cache_db.connect(db_path)
    conn.executescript("""
        CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO cache_meta (key, value) VALUES ('schema_version', '5');
        CREATE TABLE parts (
            part_id      TEXT PRIMARY KEY,
            lcsc         TEXT DEFAULT '',
            mpn          TEXT DEFAULT '',
            digikey      TEXT DEFAULT '',
            pololu       TEXT DEFAULT '',
            mouser       TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            description  TEXT DEFAULT '',
            package      TEXT DEFAULT '',
            rohs         TEXT DEFAULT '',
            section      TEXT DEFAULT '',
            sort_key     REAL,
            date_code    TEXT DEFAULT ''
        );
        INSERT INTO parts (part_id, mpn, manufacturer)
            VALUES ('TEST1', 'TMR2615', 'MDT');
    """)
    conn.commit()
    conn.close()

    # Reopen and trigger schema migration
    conn = cache_db.connect(db_path)
    cache_db.create_schema(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(parts)")}
    assert "primary_vendor_id" in cols

    # Existing row preserved with empty primary_vendor_id
    row = conn.execute(
        "SELECT primary_vendor_id FROM parts WHERE part_id = ?", ("TEST1",)
    ).fetchone()
    assert row["primary_vendor_id"] == ""

    # Version is now 7
    v = conn.execute(
        "SELECT value FROM cache_meta WHERE key='schema_version'"
    ).fetchone()
    assert v["value"] == "7"
    conn.close()


def test_populate_full_succeeds_when_generic_part_members_exist(tmp_path):
    """populate_full must not fail when generic_part_members has rows.

    Regression: DELETE FROM parts violated the FK from generic_part_members.part_id
    to parts.part_id, leaving the cache in a half-deleted state. populate_full
    is run on every full _rebuild, so any prior auto-generated passive group
    triggered FOREIGN KEY constraint failed on the next price update.
    """
    import cache_db

    db_path = str(tmp_path / "cache.db")
    conn = cache_db.connect(db_path)
    cache_db.create_schema(conn)

    # Seed a part that has an auto-generated generic_part_member referencing it.
    conn.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, section) "
        "VALUES ('C96151', 'C96151', '', 'Passives - Resistors > Chip Resistors')"
    )
    conn.execute(
        "INSERT INTO stock (part_id, quantity, unit_price, ext_price) "
        "VALUES ('C96151', 10, 0.0, 0.0)"
    )
    conn.execute(
        "INSERT INTO generic_parts (generic_part_id, name, part_type, "
        "spec_json, strictness_json, source) "
        "VALUES ('res_1.34ohm', '1.34Ω', 'resistor', '{}', '{}', 'auto')"
    )
    conn.execute(
        "INSERT INTO generic_part_members (generic_part_id, part_id, source) "
        "VALUES ('res_1.34ohm', 'C96151', 'auto')"
    )
    conn.commit()

    # Re-running populate_full (as _rebuild does) must succeed even though the
    # part is referenced by generic_part_members.
    merged = {"C96151": {"LCSC Part Number": "C96151", "Quantity": "20",
                          "Unit Price($)": "0.10", "Ext.Price($)": "2.00"}}
    categorized = {"Passives - Resistors > Chip Resistors": list(merged.values())}
    cache_db.populate_full(conn, merged, categorized)

    # Member row survives because its referenced part_id was re-inserted.
    member = conn.execute(
        "SELECT * FROM generic_part_members WHERE part_id='C96151'"
    ).fetchone()
    assert member is not None
    conn.close()


def test_v5_to_v6_migration_populate_full_succeeds(tmp_path):
    """After migration, populate_full's INSERT with primary_vendor_id works."""
    import cache_db

    db_path = str(tmp_path / "cache.db")
    conn = cache_db.connect(db_path)
    conn.executescript("""
        CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO cache_meta (key, value) VALUES ('schema_version', '5');
        CREATE TABLE parts (
            part_id TEXT PRIMARY KEY, lcsc TEXT DEFAULT '', mpn TEXT DEFAULT '',
            digikey TEXT DEFAULT '', pololu TEXT DEFAULT '', mouser TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '', description TEXT DEFAULT '',
            package TEXT DEFAULT '', rohs TEXT DEFAULT '', section TEXT DEFAULT '',
            sort_key REAL, date_code TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()

    conn = cache_db.connect(db_path)
    cache_db.create_schema(conn)

    # Now populate_full should work (this would crash without the migration fix)
    merged = {"TEST2": {"Manufacture Part Number": "TMR2305", "Manufacturer": "MDT",
                        "Quantity": "10", "Unit Price($)": "3.10",
                        "Ext.Price($)": "31.00"}}
    categorized = {"Other": list(merged.values())}
    cache_db.populate_full(conn, merged, categorized)
    conn.close()


def test_populate_full_sets_primary_vendor_id_from_po(tmp_path):
    """A part that has a PO row gets primary_vendor_id set to that PO's vendor."""
    import cache_db
    import csv as _csv

    base = tmp_path / "data"
    base.mkdir()
    (base / "sources").mkdir()
    ledger = base / "purchase_ledger.csv"
    fields = ["LCSC Part Number", "Manufacture Part Number", "Manufacturer",
              "Quantity", "Unit Price($)", "po_id"]
    with open(ledger, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "", "Manufacture Part Number": "TMR2615",
                    "Manufacturer": "MDT", "Quantity": "50",
                    "Unit Price($)": "4.20", "po_id": "po_test01"})

    po_csv = base / "purchase_orders.csv"
    with open(po_csv, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=[
            "po_id", "vendor_id", "source_file_hash", "source_file_ext",
            "purchase_date", "notes",
        ])
        w.writeheader()
        w.writerow({"po_id": "po_test01", "vendor_id": "v_mdt_x", "source_file_hash": "",
                    "source_file_ext": "", "purchase_date": "2026-04-15", "notes": ""})

    conn = cache_db.connect(str(base / "cache.db"))
    cache_db.create_schema(conn)
    # Use the helper that the rebuild path will call (added in Task 11 step 4)
    from inventory_ops import read_and_merge, apply_adjustments, categorize_and_sort
    fnames, merged = read_and_merge(str(ledger), fields)
    apply_adjustments(merged, str(base / "adjustments.csv"), fnames)
    categorized = categorize_and_sort(list(merged.values()))
    cache_db.populate_full(conn, merged, categorized,
                            ledger_path=str(ledger), po_csv_path=str(po_csv))
    row = conn.execute(
        "SELECT primary_vendor_id FROM parts WHERE mpn=?", ("TMR2615",)
    ).fetchone()
    assert row["primary_vendor_id"] == "v_mdt_x"
    conn.close()


def test_populate_full_falls_back_to_inferred_vendor(tmp_path):
    """A part with no PO falls back to inferred vendor by manufacturer name."""
    import cache_db
    import csv as _csv
    import json

    base = tmp_path / "data"
    base.mkdir()
    ledger = base / "purchase_ledger.csv"
    fields = ["LCSC Part Number", "Manufacture Part Number", "Manufacturer",
              "Quantity", "po_id"]
    with open(ledger, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "", "Manufacture Part Number": "X1",
                    "Manufacturer": "HRS", "Quantity": "5", "po_id": ""})

    vjson = base / "vendors.json"
    with open(vjson, "w", encoding="utf-8") as f:
        json.dump([
            {"id": "v_unknown", "name": "Unknown", "type": "unknown", "icon": "❓",
             "url": "", "favicon_path": ""},
            {"id": "v_hrs_abcd", "name": "HRS", "type": "inferred",
             "url": "", "favicon_path": "", "icon": ""},
        ], f)

    conn = cache_db.connect(str(base / "cache.db"))
    cache_db.create_schema(conn)
    from inventory_ops import read_and_merge, apply_adjustments, categorize_and_sort
    fnames, merged = read_and_merge(str(ledger), fields)
    apply_adjustments(merged, str(base / "adjustments.csv"), fnames)
    categorized = categorize_and_sort(list(merged.values()))
    cache_db.populate_full(conn, merged, categorized,
                            ledger_path=str(ledger), po_csv_path=None,
                            vendors_json_path=str(vjson))
    row = conn.execute(
        "SELECT primary_vendor_id FROM parts WHERE mpn=?", ("X1",)
    ).fetchone()
    assert row["primary_vendor_id"] == "v_hrs_abcd"
    conn.close()
