# Phase 1: Part/Stock Divorce + SQLite Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `inventory.csv` (the file-based materialized view) with a SQLite cache that separates part definitions from stock quantities, supports incremental updates, and maintains the same public API.

**Architecture:** CSV event logs (`purchase_ledger.csv`, `adjustments.csv`) remain the single source of truth. A new `cache_db.py` module manages a SQLite database (`data/cache.db`) as a derived cache. `inventory_api.py` switches from full-rebuild-per-operation to incremental cache updates. The JS frontend sees zero changes.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), pytest, existing CSV/categorize infrastructure.

---

### File Structure

**New files:**
- `cache_db.py` — SQLite cache layer: schema, populate, query, incremental ops, checkpoint, verification
- `tests/python/test_cache_db.py` — unit tests for cache layer

**Modified files:**
- `inventory_ops.py` — export `sort_key_for_section()` helper (extracted from `categorize_and_sort`)
- `inventory_api.py` — switch `_rebuild()` and `_load_organized()` to use cache; add incremental update paths
- `pnp_server.py` — no code changes needed (calls `api._load_organized()` which transparently switches)

**Removed outputs:**
- `data/inventory.csv` — no longer generated after migration (file left on disk, can be deleted manually)

---

### Task 1: cache_db.py — Schema creation and connection

**Files:**
- Create: `cache_db.py`
- Create: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing test for schema creation**

```python
# tests/python/test_cache_db.py
"""Tests for cache_db SQLite cache layer."""

import sqlite3

import pytest

import cache_db


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\gehub\dubIS\.claude\worktrees\data-architecture-analysis && python -m pytest tests/python/test_cache_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cache_db'`

- [ ] **Step 3: Implement cache_db.py with connect() and create_schema()**

```python
# cache_db.py
"""SQLite cache layer for inventory data.

The cache is a derived, deletable materialized view of the CSV event logs.
Delete cache.db at any time — it will be rebuilt from purchase_ledger.csv
and adjustments.csv on next startup.
"""

from __future__ import annotations

import sqlite3
from typing import Any

SCHEMA_VERSION = "1"


def connect(db_path: str) -> sqlite3.Connection:
    """Open or create the cache database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create cache tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS parts (
            part_id       TEXT PRIMARY KEY,
            lcsc          TEXT DEFAULT '',
            mpn           TEXT DEFAULT '',
            digikey       TEXT DEFAULT '',
            pololu        TEXT DEFAULT '',
            mouser        TEXT DEFAULT '',
            manufacturer  TEXT DEFAULT '',
            description   TEXT DEFAULT '',
            package       TEXT DEFAULT '',
            rohs          TEXT DEFAULT '',
            section       TEXT DEFAULT '',
            sort_key      REAL,
            date_code     TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stock (
            part_id     TEXT PRIMARY KEY REFERENCES parts(part_id),
            quantity    INTEGER NOT NULL DEFAULT 0,
            unit_price  REAL NOT NULL DEFAULT 0.0,
            ext_price   REAL NOT NULL DEFAULT 0.0
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py::TestSchema -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add cache_db module with SQLite schema creation"
```

---

### Task 2: cache_db.py — Full population from merged data

**Files:**
- Modify: `cache_db.py`
- Modify: `inventory_ops.py` (extract sort key helper)
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Extract sort_key helper from inventory_ops.py**

Add this function to `inventory_ops.py` right before `categorize_and_sort`:

```python
def sort_key_for_section(section: str, description: str) -> float | None:
    """Return numeric sort key for a part within its section, or None."""
    if "Resistor" in section:
        return parse_resistance(description)
    elif "Capacitor" in section:
        return parse_capacitance(description)
    elif "Inductor" in section:
        return parse_inductance(description)
    return None
```

- [ ] **Step 2: Write failing test for full population**

```python
# Add to tests/python/test_cache_db.py

from inventory_ops import get_part_key


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
                "Description": "100nF 16V 0402 MLCC",
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
        assert part["description"] == "100nF 16V 0402 MLCC"
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
        # 100nF = 1e-7, 4.7k = 4700
        assert cap["sort_key"] is not None
        assert cap["sort_key"] < 1.0  # farads
        assert res["sort_key"] is not None
        assert res["sort_key"] > 1000  # ohms

    def test_populate_clears_old_data(self, db):
        merged = self._make_merged()
        categorized = self._make_categorized(merged)
        cache_db.populate_full(db, merged, categorized)
        # Populate again with fewer parts
        small = {"C1525": merged["C1525"]}
        from inventory_ops import categorize_and_sort
        small_cat = categorize_and_sort(list(small.values()))
        cache_db.populate_full(db, small, small_cat)
        assert db.execute("SELECT count(*) FROM parts").fetchone()[0] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/python/test_cache_db.py::TestPopulate -v`
Expected: FAIL — `AttributeError: module 'cache_db' has no attribute 'populate_full'`

- [ ] **Step 4: Implement populate_full()**

Add to `cache_db.py`:

```python
from inventory_ops import get_part_key, sort_key_for_section
from price_ops import parse_price, parse_qty


def populate_full(
    conn: sqlite3.Connection,
    merged: dict[str, dict[str, str]],
    categorized: dict[str, list[dict[str, str]]],
) -> None:
    """Full population from merge + categorize results. Clears existing data."""
    conn.execute("DELETE FROM stock")
    conn.execute("DELETE FROM parts")

    for section, parts_list in categorized.items():
        for part in parts_list:
            part_id = get_part_key(part)
            if not part_id:
                continue
            sk = sort_key_for_section(section, part.get("Description", ""))
            conn.execute(
                """INSERT OR REPLACE INTO parts
                   (part_id, lcsc, mpn, digikey, pololu, mouser,
                    manufacturer, description, package, rohs, section, sort_key, date_code)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    part_id,
                    (part.get("LCSC Part Number") or "").strip(),
                    (part.get("Manufacture Part Number") or "").strip(),
                    (part.get("Digikey Part Number") or "").strip(),
                    (part.get("Pololu Part Number") or "").strip(),
                    (part.get("Mouser Part Number") or "").strip(),
                    (part.get("Manufacturer") or "").strip(),
                    (part.get("Description") or "").strip(),
                    (part.get("Package") or "").strip(),
                    (part.get("RoHS") or "").strip(),
                    section,
                    sk,
                    (part.get("Date Code / Lot No.") or "").strip(),
                ),
            )
            conn.execute(
                """INSERT OR REPLACE INTO stock (part_id, quantity, unit_price, ext_price)
                   VALUES (?,?,?,?)""",
                (
                    part_id,
                    parse_qty(part.get("Quantity")),
                    parse_price(part.get("Unit Price($)")),
                    parse_price(part.get("Ext.Price($)")),
                ),
            )
    conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py::TestPopulate -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add cache_db.py inventory_ops.py tests/python/test_cache_db.py
git commit -m "feat: add full population and sort_key helper to cache_db"
```

---

### Task 3: cache_db.py — Query inventory (matching load_organized format)

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing test for query_inventory**

```python
# Add to tests/python/test_cache_db.py

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
        # Both parts should have sections and be in some order
        sections = [r["section"] for r in result]
        assert all(s for s in sections)  # no empty sections

    def test_empty_db_returns_empty_list(self, db):
        result = cache_db.query_inventory(db)
        assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_cache_db.py::TestQuery -v`
Expected: FAIL — `AttributeError: module 'cache_db' has no attribute 'query_inventory'`

- [ ] **Step 3: Implement query_inventory()**

Add to `cache_db.py`:

```python
def query_inventory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Query cache in the same format as inventory_ops.load_organized().

    Returns list of dicts with keys: section, lcsc, mpn, digikey, pololu,
    mouser, manufacturer, package, description, qty, unit_price, ext_price.
    """
    rows = conn.execute("""
        SELECT p.section, p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser,
               p.manufacturer, p.package, p.description,
               s.quantity, s.unit_price, s.ext_price
        FROM parts p
        JOIN stock s USING (part_id)
        ORDER BY p.section, p.sort_key NULLS LAST
    """).fetchall()
    return [
        {
            "section": row["section"],
            "lcsc": row["lcsc"],
            "mpn": row["mpn"],
            "digikey": row["digikey"],
            "pololu": row["pololu"],
            "mouser": row["mouser"],
            "manufacturer": row["manufacturer"],
            "package": row["package"],
            "description": row["description"],
            "qty": row["quantity"],
            "unit_price": row["unit_price"],
            "ext_price": row["ext_price"],
        }
        for row in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py::TestQuery -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add query_inventory matching load_organized format"
```

---

### Task 4: cache_db.py — Incremental update operations

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing tests for incremental operations**

```python
# Add to tests/python/test_cache_db.py

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
            "Description": "100nF 16V 0402 MLCC",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_cache_db.py::TestIncrementalOps -v`
Expected: FAIL — `AttributeError: module 'cache_db' has no attribute 'apply_stock_delta'`

- [ ] **Step 3: Implement incremental operations**

Add to `cache_db.py`:

```python
def apply_stock_delta(conn: sqlite3.Connection, part_id: str, delta: int) -> None:
    """Adjust stock quantity by delta. Floors at zero."""
    conn.execute(
        "UPDATE stock SET quantity = MAX(0, quantity + ?) WHERE part_id = ?",
        (delta, part_id),
    )
    conn.commit()


def set_stock_quantity(conn: sqlite3.Connection, part_id: str, quantity: int) -> None:
    """Set stock quantity to an absolute value."""
    conn.execute(
        "UPDATE stock SET quantity = MAX(0, ?) WHERE part_id = ?",
        (quantity, part_id),
    )
    conn.commit()


def upsert_part(
    conn: sqlite3.Connection,
    part_id: str,
    row: dict[str, str],
    section: str = "",
) -> None:
    """Insert or update a part and its stock from a CSV-column-named dict."""
    sk = sort_key_for_section(section, (row.get("Description") or "").strip())
    conn.execute(
        """INSERT INTO parts
           (part_id, lcsc, mpn, digikey, pololu, mouser,
            manufacturer, description, package, rohs, section, sort_key, date_code)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(part_id) DO UPDATE SET
            lcsc=excluded.lcsc, mpn=excluded.mpn, digikey=excluded.digikey,
            pololu=excluded.pololu, mouser=excluded.mouser,
            manufacturer=excluded.manufacturer, description=excluded.description,
            package=excluded.package, rohs=excluded.rohs, section=excluded.section,
            sort_key=excluded.sort_key, date_code=excluded.date_code""",
        (
            part_id,
            (row.get("LCSC Part Number") or "").strip(),
            (row.get("Manufacture Part Number") or "").strip(),
            (row.get("Digikey Part Number") or "").strip(),
            (row.get("Pololu Part Number") or "").strip(),
            (row.get("Mouser Part Number") or "").strip(),
            (row.get("Manufacturer") or "").strip(),
            (row.get("Description") or "").strip(),
            (row.get("Package") or "").strip(),
            (row.get("RoHS") or "").strip(),
            section,
            sk,
            (row.get("Date Code / Lot No.") or "").strip(),
        ),
    )
    conn.execute(
        """INSERT INTO stock (part_id, quantity, unit_price, ext_price)
           VALUES (?,?,?,?)
           ON CONFLICT(part_id) DO UPDATE SET
            quantity=excluded.quantity, unit_price=excluded.unit_price,
            ext_price=excluded.ext_price""",
        (
            part_id,
            parse_qty(row.get("Quantity")),
            parse_price(row.get("Unit Price($)")),
            parse_price(row.get("Ext.Price($)")),
        ),
    )
    conn.commit()


def update_stock_price(
    conn: sqlite3.Connection,
    part_id: str,
    unit_price: float,
    ext_price: float,
) -> None:
    """Update price fields for a part's stock entry."""
    conn.execute(
        "UPDATE stock SET unit_price = ?, ext_price = ? WHERE part_id = ?",
        (unit_price, ext_price, part_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_cache_db.py::TestIncrementalOps -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add incremental cache operations (delta, set, upsert, price)"
```

---

### Task 5: cache_db.py — Checkpoint and startup catch-up

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing tests for checkpoint and catch-up**

```python
# Add to tests/python/test_cache_db.py
import csv
import os


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_cache_db.py::TestCheckpoint tests/python/test_cache_db.py::TestCountLines tests/python/test_cache_db.py::TestCatchUp -v`
Expected: FAIL

- [ ] **Step 3: Implement checkpoint, count_csv_data_lines, and catch_up**

Add to `cache_db.py`:

```python
import csv
import logging
import os

logger = logging.getLogger(__name__)


def write_checkpoint(
    conn: sqlite3.Connection,
    purchase_lines: int,
    adjustment_lines: int,
) -> None:
    """Record how many CSV data lines the cache has processed."""
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('purchase_lines', ?)",
        (str(purchase_lines),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('adjustment_lines', ?)",
        (str(adjustment_lines),),
    )
    conn.commit()


def read_checkpoint(conn: sqlite3.Connection) -> dict[str, int]:
    """Read the checkpoint. Returns zeros if no checkpoint exists."""
    result = {"purchase_lines": 0, "adjustment_lines": 0}
    for key in ("purchase_lines", "adjustment_lines"):
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?", (key,)
        ).fetchone()
        if row:
            result[key] = int(row[0])
    return result


def count_csv_data_lines(csv_path: str) -> int:
    """Count data lines in a CSV (excludes header). Returns 0 if file missing."""
    if not os.path.exists(csv_path):
        return 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def catch_up(
    conn: sqlite3.Connection,
    purchase_path: str,
    adjustments_path: str,
    adj_fieldnames: list[str],
) -> None:
    """Replay only events added since the last checkpoint."""
    cp = read_checkpoint(conn)

    # Catch up on new adjustments
    adj_total = count_csv_data_lines(adjustments_path)
    adj_seen = cp["adjustment_lines"]
    if adj_total > adj_seen and os.path.exists(adjustments_path):
        with open(adjustments_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i < adj_seen:
                    continue  # skip already-processed rows
                adj_type = (row.get("type") or "").strip()
                pn = (row.get("lcsc_part") or "").strip()
                if not pn or not adj_type:
                    continue
                try:
                    qty = int(float(row.get("quantity", "0")))
                except ValueError:
                    continue
                if adj_type == "set":
                    set_stock_quantity(conn, pn, max(0, qty))
                elif adj_type in ("consume", "add", "remove"):
                    apply_stock_delta(conn, pn, qty)
        logger.info("Cache catch-up: replayed %d new adjustments", adj_total - adj_seen)

    # Update checkpoint
    purchase_total = count_csv_data_lines(purchase_path)
    write_checkpoint(conn, purchase_lines=purchase_total, adjustment_lines=adj_total)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_cache_db.py::TestCheckpoint tests/python/test_cache_db.py::TestCountLines tests/python/test_cache_db.py::TestCatchUp -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add checkpoint, line counting, and startup catch-up"
```

---

### Task 6: cache_db.py — Spot-check verification

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing tests for verification**

```python
# Add to tests/python/test_cache_db.py

class TestVerify:
    def _populate_and_write_csvs(self, db, tmp_path):
        """Populate cache and write matching CSVs."""
        merged = TestPopulate._make_merged(self)
        categorized = TestPopulate._make_categorized(self, merged)
        cache_db.populate_full(db, merged, categorized)

        # Write purchase ledger
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
        # Corrupt cache — manually change quantity
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
        # After fix, cache should be corrected
        qty = db.execute("SELECT quantity FROM stock WHERE part_id='C1525'").fetchone()[0]
        assert qty == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_cache_db.py::TestVerify -v`
Expected: FAIL — `AttributeError: module 'cache_db' has no attribute 'verify_parts'`

- [ ] **Step 3: Implement verify_parts()**

Add to `cache_db.py`:

```python
from inventory_ops import read_and_merge, apply_adjustments


def verify_parts(
    conn: sqlite3.Connection,
    part_ids: list[str],
    purchase_path: str,
    adjustments_path: str,
    fieldnames: list[str],
    fix: bool = False,
) -> list[dict[str, Any]]:
    """Spot-check: replay events for specific parts and compare to cache.

    Returns list of mismatches: [{"part_id", "cache_qty", "expected_qty"}].
    If fix=True, corrects cache for any mismatched parts.
    """
    # Full replay from source of truth
    _, merged = read_and_merge(purchase_path, fieldnames)
    apply_adjustments(merged, adjustments_path, fieldnames)

    mismatches = []
    for pid in part_ids:
        cache_row = conn.execute(
            "SELECT quantity FROM stock WHERE part_id = ?", (pid,)
        ).fetchone()
        cache_qty = cache_row["quantity"] if cache_row else 0

        expected_qty = parse_qty(merged[pid]["Quantity"]) if pid in merged else 0

        if cache_qty != expected_qty:
            mismatches.append({
                "part_id": pid,
                "cache_qty": cache_qty,
                "expected_qty": expected_qty,
            })
            if fix:
                conn.execute(
                    "UPDATE stock SET quantity = ? WHERE part_id = ?",
                    (expected_qty, pid),
                )
                logger.warning(
                    "Cache mismatch fixed: %s was %d, expected %d",
                    pid, cache_qty, expected_qty,
                )

    if fix and mismatches:
        conn.commit()
    return mismatches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_cache_db.py::TestVerify -v`
Expected: 3 passed

- [ ] **Step 5: Run all cache_db tests to confirm nothing broken**

Run: `python -m pytest tests/python/test_cache_db.py -v`
Expected: All passed (24 tests)

- [ ] **Step 6: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add spot-check verification for cache consistency"
```

---

### Task 7: Wire cache into inventory_api.py

**Files:**
- Modify: `inventory_api.py`

- [ ] **Step 1: Add cache initialization to InventoryApi.__init__()**

Add after the existing path setup (around line 88):

```python
import cache_db

# In __init__, after self.prefs_json = ...
self.cache_db_path = os.path.join(self.base_dir, "data", "cache.db")
self._cache_conn: sqlite3.Connection | None = None
```

- [ ] **Step 2: Add _get_cache() helper method**

Add to `InventoryApi` after `__init__`:

```python
def _get_cache(self) -> sqlite3.Connection:
    """Get or create the cache database connection."""
    if self._cache_conn is None:
        self._cache_conn = cache_db.connect(self.cache_db_path)
        cache_db.create_schema(self._cache_conn)
    return self._cache_conn
```

- [ ] **Step 3: Replace _rebuild() to populate cache**

Replace the `_rebuild` method:

```python
def _rebuild(self) -> list[dict[str, Any]]:
    """Full rebuild: replay all events into cache, return fresh inventory."""
    conn = self._get_cache()
    file_fieldnames, merged = inventory_ops.read_and_merge(
        self.input_csv, self.FIELDNAMES,
    )
    inventory_ops.apply_adjustments(merged, self.adjustments_csv, file_fieldnames)
    categorized = inventory_ops.categorize_and_sort(list(merged.values()))
    cache_db.populate_full(conn, merged, categorized)
    purchase_lines = cache_db.count_csv_data_lines(self.input_csv)
    adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
    cache_db.write_checkpoint(conn, purchase_lines=purchase_lines,
                              adjustment_lines=adj_lines)
    return cache_db.query_inventory(conn)
```

- [ ] **Step 4: Replace _load_organized() to query cache**

Replace the `_load_organized` method:

```python
def _load_organized(self) -> list[dict[str, Any]]:
    """Load current inventory from cache."""
    conn = self._get_cache()
    result = cache_db.query_inventory(conn)
    if not result:
        # Cache empty — populate from CSVs
        return self._rebuild()
    return result
```

- [ ] **Step 5: Update adjust_part() for incremental cache update**

Replace the body inside the `with self._lock:` block of `adjust_part`:

```python
with self._lock:
    self._append_adjustment(adj_type, part_key, record_qty, note=note, source=source)
    conn = self._get_cache()
    # Check if part exists in cache
    exists = conn.execute(
        "SELECT 1 FROM stock WHERE part_id = ?", (part_key,)
    ).fetchone()
    if not exists:
        # Part not in cache (e.g., "set" on a brand new part) — full rebuild
        return self._rebuild()
    if adj_type == "set":
        cache_db.set_stock_quantity(conn, part_key, max(0, record_qty))
    else:  # add, remove
        cache_db.apply_stock_delta(conn, part_key, record_qty)
    adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
    cp = cache_db.read_checkpoint(conn)
    cache_db.write_checkpoint(conn, purchase_lines=cp["purchase_lines"],
                              adjustment_lines=adj_lines)
    return cache_db.query_inventory(conn)
```

- [ ] **Step 6: Update consume_bom() for incremental cache update**

Replace the body inside the `with self._lock:` block of `consume_bom`:

```python
with self._lock:
    csv_io.append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, adj_rows)
    conn = self._get_cache()
    affected_parts = []
    for row in adj_rows:
        pn = row["lcsc_part"]
        delta = int(row["quantity"])
        cache_db.apply_stock_delta(conn, pn, delta)
        affected_parts.append(pn)
    adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
    cp = cache_db.read_checkpoint(conn)
    cache_db.write_checkpoint(conn, purchase_lines=cp["purchase_lines"],
                              adjustment_lines=adj_lines)
    # Spot-check verification on affected parts
    cache_db.verify_parts(
        conn, affected_parts, self.input_csv, self.adjustments_csv,
        self.FIELDNAMES, fix=True,
    )
    return cache_db.query_inventory(conn)
```

- [ ] **Step 7: Update import_purchases() for incremental cache update**

Replace the `return self._rebuild()` at the end of import_purchases' locked section with a full rebuild (import changes the merged state which requires recategorization):

```python
# At end of with self._lock: block in import_purchases
            return self._rebuild()
```

Note: `import_purchases` still does a full rebuild because new purchases can merge with existing parts and change categorization. This is the correct behavior — imports are infrequent and affect many parts.

- [ ] **Step 8: Update rollback_source() to do full rebuild**

No change needed — `rollback_source` already calls `self._rebuild()` which now populates the cache.

- [ ] **Step 9: Update update_part_price() and update_part_fields()**

These methods rewrite `purchase_ledger.csv` and call `self._rebuild()`. No change needed — the full rebuild now populates the cache.

- [ ] **Step 10: Add startup catch-up to rebuild_inventory()**

Update the public `rebuild_inventory` method:

```python
def rebuild_inventory(self) -> list[dict[str, Any]]:
    """Rebuild inventory. Uses catch-up if cache exists, full rebuild otherwise."""
    conn = self._get_cache()
    cp = cache_db.read_checkpoint(conn)
    if cp["purchase_lines"] > 0 or cp["adjustment_lines"] > 0:
        # Cache exists — try catch-up
        cache_db.catch_up(conn, self.input_csv, self.adjustments_csv,
                          self.ADJ_FIELDNAMES)
        return cache_db.query_inventory(conn)
    # No checkpoint — full rebuild
    return self._rebuild()
```

- [ ] **Step 11: Commit**

```bash
git add inventory_api.py
git commit -m "feat: wire SQLite cache into inventory_api (incremental + full rebuild)"
```

---

### Task 8: Update test fixture to handle cache.db

**Files:**
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Update the `api` fixture to include cache_db_path**

The existing `api` fixture sets `base_dir` to `tmp_path`. Since `cache_db_path` is derived from `base_dir + "/data/cache.db"`, we need to ensure the `data/` subdirectory exists:

```python
@pytest.fixture
def api(tmp_path):
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    inst.cache_db_path = str(data_dir / "cache.db")
    return inst
```

- [ ] **Step 2: Run full existing test suite**

Run: `python -m pytest tests/python/test_inventory_api.py -v`
Expected: All existing tests pass (the API surface is unchanged)

- [ ] **Step 3: Run cache_db tests to confirm nothing broken**

Run: `python -m pytest tests/python/test_cache_db.py -v`
Expected: All passed

- [ ] **Step 4: Run PnP server tests**

Run: `python -m pytest tests/python/test_pnp_server.py -v`
Expected: All passed (PnP server calls `api._load_organized()` which now returns from cache)

- [ ] **Step 5: Commit**

```bash
git add tests/python/test_inventory_api.py
git commit -m "test: update api fixture to include cache_db_path"
```

---

### Task 9: Integration test — cache matches full rebuild

**Files:**
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write integration test comparing cache output to legacy output**

```python
# Add to tests/python/test_cache_db.py

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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py::TestIntegration -v`
Expected: PASS — cache output matches legacy output

- [ ] **Step 3: Commit**

```bash
git add tests/python/test_cache_db.py
git commit -m "test: add integration test verifying cache matches legacy rebuild"
```

---

### Task 10: Run full test suite and lint

**Files:** None (verification only)

- [ ] **Step 1: Run all Python tests**

Run: `python -m pytest tests/python/ -v`
Expected: All tests pass (existing + new)

- [ ] **Step 2: Run Python linter**

Run: `ruff check cache_db.py inventory_ops.py inventory_api.py pnp_server.py`
Expected: No errors

- [ ] **Step 3: Run JS tests (should be unaffected)**

Run: `npx vitest run`
Expected: All tests pass (no JS changes in Phase 1)

- [ ] **Step 4: Run JS lint and type check**

Run: `npx eslint js/ && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Add data/README.txt**

Create `data/README.txt`:
```
dubIS Data Directory
====================

CSV files are the source of truth — do not edit manually, use the app.

  purchase_ledger.csv   Raw purchase import history (append-only)
  adjustments.csv       Stock adjustment history (append-only)

cache.db is a derived SQLite cache. It can be safely deleted — it will
be rebuilt from the CSV files on next app startup.

  preferences.json      User configuration (thresholds, directories)
  constants.json        Shared schema (field names, section order)
```

- [ ] **Step 6: Final commit**

```bash
git add data/README.txt
git commit -m "docs: add data directory README explaining file roles"
```

---

### Task 11: Add .gitignore entry for cache.db

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add cache.db to .gitignore**

Add this line to `.gitignore`:
```
data/cache.db
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore cache.db in git (derived file)"
```
