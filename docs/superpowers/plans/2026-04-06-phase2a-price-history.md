# Phase 2a: Price History — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add append-only price observation tracking so every price seen (from PO imports, manual edits, and distributor fetches) is recorded with distributor and timestamp, and aggregated per-distributor pricing is queryable from the cache.

**Architecture:** New `data/events/price_observations.csv` event log captures every price observation. New `prices` cache table aggregates per-distributor latest/average pricing. Recording hooks added to `import_purchases()`, `update_part_price()`, and a new `record_fetched_prices()` API. Schema migration from v1→v2 adds the prices table.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), csv (stdlib), pytest.

---

### File Structure

**New files:**
- `price_history.py` — price observation recording, reading, and cache population
- `tests/python/test_price_history.py` — unit tests for price history module
- `data/events/` — directory for new event log CSVs

**Modified files:**
- `cache_db.py` — schema v2 migration (add `prices` table), query function for prices
- `inventory_api.py` — hook price recording into `import_purchases()`, `update_part_price()`, add `record_fetched_prices()` and `get_price_summary()` API methods
- `.gitignore` — add `data/events/*.csv`

---

### Task 1: Schema migration — add `prices` table

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing test for schema v2**

```python
# Add to tests/python/test_cache_db.py

class TestSchemaMigration:
    def test_fresh_schema_has_prices_table(self, db):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "prices" in tables

    def test_schema_version_is_2(self, db):
        row = db.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "2"

    def test_prices_table_columns(self, db):
        # Insert a valid part first (foreign key)
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

    def test_v1_to_v2_migration(self, tmp_path):
        """Opening a v1 database should auto-migrate to v2."""
        db_path = str(tmp_path / "old.db")
        conn = cache_db.connect(db_path)
        # Create v1 schema manually (no prices table)
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
        # Now call create_schema which should migrate
        cache_db.create_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "prices" in tables
        version = conn.execute(
            "SELECT value FROM cache_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "2"
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_cache_db.py::TestSchemaMigration -v`
Expected: FAIL — `prices` table doesn't exist in v1 schema

- [ ] **Step 3: Update create_schema() with v2 migration**

In `cache_db.py`, change `SCHEMA_VERSION = "1"` to `SCHEMA_VERSION = "2"` and update `create_schema()`:

```python
SCHEMA_VERSION = "2"


def create_schema(conn: sqlite3.Connection) -> None:
    """Create cache tables if they don't exist. Migrates from older versions."""
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
        CREATE TABLE IF NOT EXISTS prices (
            part_id            TEXT NOT NULL REFERENCES parts(part_id),
            distributor        TEXT NOT NULL,
            latest_unit_price  REAL,
            avg_unit_price     REAL,
            price_count        INTEGER NOT NULL DEFAULT 0,
            last_observed      TEXT,
            moq                INTEGER,
            source             TEXT,
            PRIMARY KEY (part_id, distributor)
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py::TestSchemaMigration -v`
Expected: 4 passed

- [ ] **Step 5: Run all existing cache_db tests to confirm no regression**

Run: `python -m pytest tests/python/test_cache_db.py -v`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add prices table to cache schema (v1→v2 migration)"
```

---

### Task 2: Price observation event log module

**Files:**
- Create: `price_history.py`
- Create: `tests/python/test_price_history.py`

- [ ] **Step 1: Write failing tests for price observation recording and reading**

```python
# tests/python/test_price_history.py
"""Tests for price_history module."""

import csv
import os

import pytest

import price_history


@pytest.fixture
def events_dir(tmp_path):
    d = tmp_path / "events"
    d.mkdir()
    return str(d)


class TestRecordObservation:
    def test_creates_csv_with_header(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0074,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        assert os.path.exists(csv_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert "timestamp" in reader.fieldnames
            assert "part_id" in reader.fieldnames
            assert "distributor" in reader.fieldnames
            assert "unit_price" in reader.fieldnames

    def test_appends_rows(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0074,
             "source": "import"},
        ])
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0080,
             "source": "manual"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert float(rows[0]["unit_price"]) == pytest.approx(0.0074)
        assert float(rows[1]["unit_price"]) == pytest.approx(0.0080)

    def test_timestamp_auto_filled(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["timestamp"]  # not empty
        assert "T" in row["timestamp"]  # ISO format

    def test_optional_fields_default_empty(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["currency"] == ""
        assert row["moq"] == ""
        assert row["note"] == ""


class TestReadObservations:
    def test_read_all(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.010,
             "source": "import"},
        ])
        obs = price_history.read_observations(events_dir)
        assert len(obs) == 2

    def test_read_filtered_by_part(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C9999", "distributor": "lcsc", "unit_price": 0.050,
             "source": "import"},
        ])
        obs = price_history.read_observations(events_dir, part_id="C1525")
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"

    def test_read_empty_returns_empty(self, events_dir):
        obs = price_history.read_observations(events_dir)
        assert obs == []


class TestPopulatePricesCache:
    def test_populate_aggregates_by_distributor(self, events_dir, tmp_path):
        import cache_db
        conn = cache_db.connect(str(tmp_path / "cache.db"))
        cache_db.create_schema(conn)
        conn.execute("INSERT INTO parts (part_id) VALUES ('C1525')")
        conn.execute("INSERT INTO stock (part_id, quantity) VALUES ('C1525', 100)")
        conn.commit()

        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.009,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.012,
             "source": "import"},
        ])

        price_history.populate_prices_cache(conn, events_dir)

        lcsc = conn.execute(
            "SELECT * FROM prices WHERE part_id='C1525' AND distributor='lcsc'"
        ).fetchone()
        assert lcsc["price_count"] == 2
        assert lcsc["latest_unit_price"] == pytest.approx(0.009)
        assert lcsc["avg_unit_price"] == pytest.approx(0.008)  # (0.007+0.009)/2

        dk = conn.execute(
            "SELECT * FROM prices WHERE part_id='C1525' AND distributor='digikey'"
        ).fetchone()
        assert dk["price_count"] == 1
        assert dk["latest_unit_price"] == pytest.approx(0.012)

        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_price_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'price_history'`

- [ ] **Step 3: Implement price_history.py**

```python
# price_history.py
"""Price observation event log — append-only recording and cache population."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

OBSERVATIONS_FILE = "price_observations.csv"
FIELDNAMES = ["timestamp", "part_id", "distributor", "unit_price", "currency",
              "source", "moq", "note"]


def record_observations(
    events_dir: str,
    observations: list[dict[str, Any]],
) -> None:
    """Append price observations to the event log CSV."""
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for obs in observations:
            writer.writerow({
                "timestamp": obs.get("timestamp", ts),
                "part_id": obs["part_id"],
                "distributor": obs.get("distributor", ""),
                "unit_price": obs.get("unit_price", ""),
                "currency": obs.get("currency", ""),
                "source": obs.get("source", ""),
                "moq": obs.get("moq", ""),
                "note": obs.get("note", ""),
            })


def read_observations(
    events_dir: str,
    part_id: str | None = None,
) -> list[dict[str, str]]:
    """Read price observations, optionally filtered by part_id."""
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if part_id:
        rows = [r for r in rows if r.get("part_id") == part_id]
    return rows


def populate_prices_cache(conn: Any, events_dir: str) -> None:
    """Rebuild the prices cache table from all price observations."""
    conn.execute("DELETE FROM prices")
    observations = read_observations(events_dir)

    # Aggregate by (part_id, distributor)
    agg: dict[tuple[str, str], dict] = {}
    for obs in observations:
        pid = obs.get("part_id", "").strip()
        dist = obs.get("distributor", "").strip()
        if not pid or not dist:
            continue
        try:
            price = float(obs["unit_price"])
        except (ValueError, TypeError):
            continue
        key = (pid, dist)
        if key not in agg:
            agg[key] = {"prices": [], "last_observed": "", "source": "",
                         "moq": None}
        agg[key]["prices"].append(price)
        agg[key]["last_observed"] = obs.get("timestamp", "")
        agg[key]["source"] = obs.get("source", "")
        moq = obs.get("moq", "")
        if moq:
            try:
                agg[key]["moq"] = int(moq)
            except (ValueError, TypeError):
                pass

    for (pid, dist), data in agg.items():
        prices = data["prices"]
        latest = prices[-1]
        avg = sum(prices) / len(prices)
        conn.execute(
            """INSERT OR REPLACE INTO prices
               (part_id, distributor, latest_unit_price, avg_unit_price,
                price_count, last_observed, moq, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, dist, latest, avg, len(prices),
             data["last_observed"], data["moq"], data["source"]),
        )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_price_history.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add price_history.py tests/python/test_price_history.py
git commit -m "feat: add price_history module (event log + cache population)"
```

---

### Task 3: Hook price recording into import_purchases()

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/python/test_inventory_api.py

class TestPriceHistoryOnImport:
    def test_import_records_price_observations(self, api, tmp_path):
        import csv as csv_mod
        rows = [
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "",
             "Digikey Part Number": "", "Pololu Part Number": "",
             "Mouser Part Number": "",
             "Manufacturer": "", "Quantity": "100",
             "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
             "Description": "", "Package": "", "RoHS": "",
             "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ]
        api.import_purchases(rows)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        assert os.path.exists(obs_path)
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"
        assert obs[0]["distributor"] == "lcsc"
        assert float(obs[0]["unit_price"]) == pytest.approx(0.0074)
        assert obs[0]["source"] == "import"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPriceHistoryOnImport -v`
Expected: FAIL — no price observations file created

- [ ] **Step 3: Add events_dir to InventoryApi.__init__() and hook into import_purchases()**

In `inventory_api.py`, add to `__init__()` after `self.cache_db_path`:

```python
self.events_dir: str = os.path.join(self.base_dir, "events")
```

Then in `import_purchases()`, after the `csv_io.append_csv_rows` or the write loop and before `return self._rebuild()`, add:

```python
            # Record price observations for imported rows
            self._record_import_prices(rows, fieldnames)

            return self._rebuild()
```

Add this helper method to the class:

```python
def _record_import_prices(self, rows: list[dict[str, str]],
                           fieldnames: list[str]) -> None:
    """Extract and record price observations from imported purchase rows."""
    import price_history
    os.makedirs(self.events_dir, exist_ok=True)
    observations = []
    for row in rows:
        part_key = inventory_ops.get_part_key(row)
        if not part_key:
            continue
        up = price_ops.parse_price(row.get("Unit Price($)"))
        if up <= 0:
            continue
        # Infer distributor from which part number field is populated
        distributor = self._infer_distributor(row)
        observations.append({
            "part_id": part_key,
            "distributor": distributor,
            "unit_price": up,
            "source": "import",
        })
    if observations:
        price_history.record_observations(self.events_dir, observations)
```

Add the distributor inference helper:

```python
@staticmethod
def _infer_distributor(row: dict[str, str]) -> str:
    """Infer distributor from which part number fields are populated."""
    if (row.get("LCSC Part Number") or "").strip():
        return "lcsc"
    if (row.get("Digikey Part Number") or "").strip():
        return "digikey"
    if (row.get("Mouser Part Number") or "").strip():
        return "mouser"
    if (row.get("Pololu Part Number") or "").strip():
        return "pololu"
    return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPriceHistoryOnImport -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to confirm no regression**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add inventory_api.py tests/python/test_inventory_api.py
git commit -m "feat: record price observations during PO import"
```

---

### Task 4: Hook price recording into update_part_price()

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/python/test_inventory_api.py

class TestPriceHistoryOnManualEdit:
    def _setup_part(self, api):
        """Create a part via import so it exists for price update."""
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_price_update_records_observation(self, api, tmp_path):
        import csv as csv_mod
        self._setup_part(api)
        api.update_part_price("C1525", unit_price=0.01)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        # Should have 2: one from import, one from manual edit
        manual = [o for o in obs if o["source"] == "manual"]
        assert len(manual) == 1
        assert manual[0]["part_id"] == "C1525"
        assert float(manual[0]["unit_price"]) == pytest.approx(0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPriceHistoryOnManualEdit -v`
Expected: FAIL — no manual price observation recorded

- [ ] **Step 3: Add price recording to update_part_price()**

In `update_part_price()`, after the price is calculated but before the file write, add recording. Add this just before `return self._rebuild()` inside the lock:

```python
            # Record price observation
            import price_history
            os.makedirs(self.events_dir, exist_ok=True)
            if unit_price is not None and unit_price > 0:
                price_history.record_observations(self.events_dir, [{
                    "part_id": part_key,
                    "distributor": self._infer_distributor_for_key(part_key),
                    "unit_price": unit_price,
                    "source": "manual",
                }])
```

Add a helper to infer distributor from a part key (since we don't have the full row context in update_part_price):

```python
def _infer_distributor_for_key(self, part_key: str) -> str:
    """Infer distributor from a part key string."""
    if part_key.upper().startswith("C") and part_key[1:].isdigit():
        return "lcsc"
    conn = self._get_cache()
    row = conn.execute(
        """SELECT digikey, pololu, mouser FROM parts WHERE part_id = ?""",
        (part_key,),
    ).fetchone()
    if row:
        if row["digikey"]:
            return "digikey"
        if row["pololu"]:
            return "pololu"
        if row["mouser"]:
            return "mouser"
    return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPriceHistoryOnManualEdit -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add inventory_api.py tests/python/test_inventory_api.py
git commit -m "feat: record price observations on manual price edit"
```

---

### Task 5: API to record fetched prices and query price summary

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/python/test_inventory_api.py

class TestRecordFetchedPrices:
    def _setup_part(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_record_fetched_prices(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
            {"qty": 10, "price": 0.0070},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0070)
        assert summary["lcsc"]["price_count"] >= 2  # import + fetch

    def test_get_price_summary_empty(self, api):
        summary = api.get_price_summary("NONEXISTENT")
        assert summary == {}

    def test_get_price_summary_multiple_distributors(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "digikey", [
            {"qty": 1, "price": 0.012},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary  # from import
        assert "digikey" in summary  # from fetch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_inventory_api.py::TestRecordFetchedPrices -v`
Expected: FAIL — `AttributeError: 'InventoryApi' object has no attribute 'record_fetched_prices'`

- [ ] **Step 3: Implement record_fetched_prices() and get_price_summary()**

Add to `inventory_api.py`:

```python
def record_fetched_prices(self, part_key: str, distributor: str,
                           price_tiers: list[dict[str, Any]]) -> None:
    """Record prices fetched from a distributor API/scraper."""
    import price_history
    os.makedirs(self.events_dir, exist_ok=True)
    observations = []
    for tier in price_tiers:
        price = float(tier.get("price", 0))
        if price <= 0:
            continue
        observations.append({
            "part_id": part_key,
            "distributor": distributor,
            "unit_price": price,
            "source": "live_fetch",
            "moq": tier.get("qty", ""),
        })
    if observations:
        price_history.record_observations(self.events_dir, observations)
        # Rebuild prices cache
        conn = self._get_cache()
        price_history.populate_prices_cache(conn, self.events_dir)


def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
    """Get aggregated pricing per distributor for a part.

    Returns: {"lcsc": {"latest_unit_price": 0.007, "avg_unit_price": 0.008,
              "price_count": 3, "last_observed": "..."}, ...}
    """
    import price_history
    conn = self._get_cache()
    # Ensure prices cache is populated
    if not conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone():
        price_history.populate_prices_cache(conn, self.events_dir)
    rows = conn.execute(
        "SELECT * FROM prices WHERE part_id = ?", (part_key,)
    ).fetchall()
    result = {}
    for row in rows:
        result[row["distributor"]] = {
            "latest_unit_price": row["latest_unit_price"],
            "avg_unit_price": row["avg_unit_price"],
            "price_count": row["price_count"],
            "last_observed": row["last_observed"],
            "moq": row["moq"],
            "source": row["source"],
        }
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_inventory_api.py::TestRecordFetchedPrices -v`
Expected: 3 passed

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add inventory_api.py tests/python/test_inventory_api.py
git commit -m "feat: add record_fetched_prices and get_price_summary API"
```

---

### Task 6: Populate prices cache during full rebuild

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/python/test_inventory_api.py

class TestPricesCacheOnRebuild:
    def test_rebuild_populates_prices_cache(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])
        # Prices should be populated in cache after rebuild
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0074)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPricesCacheOnRebuild -v`
Expected: FAIL — prices cache empty after rebuild (only populated on-demand in get_price_summary)

- [ ] **Step 3: Add prices cache population to _rebuild()**

In `_rebuild()`, add after the `write_checkpoint` call:

```python
    # Populate prices cache from event log
    import price_history
    if os.path.exists(self.events_dir):
        price_history.populate_prices_cache(conn, self.events_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_inventory_api.py::TestPricesCacheOnRebuild -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add inventory_api.py tests/python/test_inventory_api.py
git commit -m "feat: populate prices cache during full rebuild"
```

---

### Task 7: .gitignore and lint

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add events directory to .gitignore**

Add to `.gitignore`:
```
data/events/*.csv
```

- [ ] **Step 2: Run lint on all modified files**

Run: `ruff check price_history.py inventory_api.py cache_db.py`
Expected: Clean

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore price observation event logs in git"
```
