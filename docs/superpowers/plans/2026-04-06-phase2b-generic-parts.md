# Phase 2b: Generic Parts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic (meta) parts that group interchangeable real parts by spec, integrate as a third level in the category hierarchy, and resolve BOM rows without specific MPNs to the best available real part.

**Architecture:** New `generic_parts` and `generic_part_members` cache tables hold the grouping. A `spec_extractor.py` module extracts component specs from descriptions. `matching.js` gains a new step between value-match and "missing" that resolves through generic parts. The `data/events/part_events.csv` event log tracks generic part lifecycle changes. Backend API handles CRUD + auto-matching + BOM resolution. Frontend extends the BOM staging table and inventory view.

**Tech Stack:** Python 3.12, sqlite3, pytest (backend); vanilla JS ES modules, vitest (frontend).

**Scope note:** This plan covers backend data model + API + BOM matching integration. The inventory view UI (three-level hierarchy rendering, generic part management modal) is deferred to a follow-up plan to keep this focused and testable.

---

### File Structure

**New files:**
- `spec_extractor.py` — extract structured specs from part descriptions/metadata
- `generic_parts.py` — generic part CRUD, auto-matching, popularity scoring, BOM resolution
- `tests/python/test_spec_extractor.py` — unit tests
- `tests/python/test_generic_parts.py` — unit tests

**Modified files:**
- `cache_db.py` — schema v3 migration (add `generic_parts` + `generic_part_members` tables)
- `inventory_api.py` — add generic part API methods, hook auto-matching into `import_purchases()`
- `js/matching.js` — add generic part matching step to `matchBOM()`
- `tests/js/matching.test.js` — tests for generic part matching

---

### Task 1: Schema v3 — generic_parts + generic_part_members tables

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write failing test for schema v3**

```python
# Add to tests/python/test_cache_db.py

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
        assert row[0] == "3"

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
        assert version == "3"
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_cache_db.py::TestSchemaV3 -v`
Expected: FAIL

- [ ] **Step 3: Update create_schema() with v3 tables**

In `cache_db.py`, change `SCHEMA_VERSION = "2"` to `SCHEMA_VERSION = "3"`. Add to the `CREATE TABLE IF NOT EXISTS` block in `create_schema()`:

```sql
CREATE TABLE IF NOT EXISTS generic_parts (
    generic_part_id  TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    part_type        TEXT NOT NULL,
    spec_json        TEXT NOT NULL DEFAULT '{}',
    strictness_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS generic_part_members (
    generic_part_id  TEXT NOT NULL REFERENCES generic_parts(generic_part_id),
    part_id          TEXT NOT NULL REFERENCES parts(part_id),
    source           TEXT NOT NULL DEFAULT 'auto',
    preferred        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (generic_part_id, part_id)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_cache_db.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add generic_parts tables to cache schema (v2→v3)"
```

---

### Task 2: Spec extraction module

**Files:**
- Create: `spec_extractor.py`
- Create: `tests/python/test_spec_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/python/test_spec_extractor.py
"""Tests for spec_extractor — extract structured specs from part descriptions."""

import pytest

import spec_extractor


class TestExtractSpec:
    def test_capacitor_basic(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec["type"] == "capacitor"
        assert spec["value"] == pytest.approx(1e-7)  # 100nF
        assert spec["value_display"] == "100nF"
        assert spec["package"] == "0402"

    def test_resistor_basic(self):
        spec = spec_extractor.extract_spec(
            description="4.7kΩ 0402 Resistor",
            package="0402",
        )
        assert spec["type"] == "resistor"
        assert spec["value"] == pytest.approx(4700)
        assert spec["package"] == "0402"

    def test_inductor_basic(self):
        spec = spec_extractor.extract_spec(
            description="10µH Inductor 0805",
            package="0805",
        )
        assert spec["type"] == "inductor"
        assert spec["value"] == pytest.approx(1e-5)
        assert spec["package"] == "0805"

    def test_voltage_extraction(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec.get("voltage") == pytest.approx(16.0)

    def test_tolerance_extraction(self):
        spec = spec_extractor.extract_spec(
            description="4.7kΩ ±1% 0402 Resistor",
            package="0402",
        )
        assert spec.get("tolerance") == "1%"

    def test_dielectric_extraction(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V C0G 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec.get("dielectric") == "C0G"

    def test_unknown_type(self):
        spec = spec_extractor.extract_spec(
            description="STM32G491 Microcontroller",
            package="LQFP-48",
        )
        assert spec["type"] == "other"
        assert spec.get("value") is None

    def test_empty_description(self):
        spec = spec_extractor.extract_spec(description="", package="")
        assert spec["type"] == "other"


class TestSpecMatchesGeneric:
    def test_exact_match(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402"}
        strictness = {"required": ["value", "package"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_value_mismatch(self):
        spec = {"type": "capacitor", "value": 1e-6, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402"}
        strictness = {"required": ["value", "package"]}
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_optional_field_ignored(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402", "dielectric": "C0G"}
        strictness = {"required": ["value", "package"], "optional": ["dielectric"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_required_field_missing_fails(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        # spec doesn't have voltage, but it's required
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_voltage_min_check(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402", "voltage": 25}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_voltage_too_low(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402", "voltage": 6.3}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)


class TestGenerateGenericId:
    def test_capacitor_id(self):
        gid = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        assert gid == "cap_100nf_0402"

    def test_resistor_id(self):
        gid = spec_extractor.generate_generic_id("resistor", {"value": "4.7kΩ", "package": "0402"})
        assert gid == "res_4.7kohm_0402"

    def test_deduplicates(self):
        gid1 = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        gid2 = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        assert gid1 == gid2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_spec_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement spec_extractor.py**

```python
# spec_extractor.py
"""Extract structured specs from part descriptions and match against generic part specs."""

from __future__ import annotations

import re

from categorize import parse_capacitance, parse_inductance, parse_resistance


def extract_spec(description: str = "", package: str = "") -> dict:
    """Extract structured spec from a part's description and package.

    Returns dict with: type, value (float), value_display (str), package (str),
    and optional: voltage, tolerance, dielectric.
    """
    desc = description.lower()
    spec: dict = {"type": "other", "package": (package or "").strip()}

    # Determine component type
    if any(kw in desc for kw in ("capacitor", "cap ", "mlcc", "electrolytic", "tantalum")):
        spec["type"] = "capacitor"
    elif any(kw in desc for kw in ("resistor", "ω", "Ω", "Ω", "ohm")):
        spec["type"] = "resistor"
    elif "inductor" in desc:
        spec["type"] = "inductor"

    # Extract value
    if spec["type"] == "capacitor":
        val = parse_capacitance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "F")
    elif spec["type"] == "resistor":
        val = parse_resistance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "Ω")
    elif spec["type"] == "inductor":
        val = parse_inductance(description)
        if val != float("inf"):
            spec["value"] = val
            spec["value_display"] = _format_value(val, "H")

    # Extract voltage (e.g., "16V", "25V")
    m = re.search(r"(\d+\.?\d*)\s*V\b", description)
    if m:
        spec["voltage"] = float(m.group(1))

    # Extract tolerance (e.g., "±1%", "5%", "10%")
    m = re.search(r"[±]?(\d+\.?\d*)%", description)
    if m:
        spec["tolerance"] = m.group(1) + "%"

    # Extract dielectric (C0G/NP0, X5R, X7R, Y5V)
    m = re.search(r"\b(C0G|NP0|X[457][RSPTUVW]|Y5V)\b", description, re.IGNORECASE)
    if m:
        spec["dielectric"] = m.group(1).upper()

    return spec


def _format_value(val: float, unit: str) -> str:
    """Format a numeric value with SI prefix for display."""
    if val == 0:
        return f"0{unit}"
    prefixes = [
        (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
        (1, ""), (1e3, "k"), (1e6, "M"),
    ]
    for scale, prefix in reversed(prefixes):
        if abs(val) >= scale:
            display = val / scale
            if display == int(display):
                return f"{int(display)}{prefix}{unit}"
            return f"{display:g}{prefix}{unit}"
    return f"{val:g}{unit}"


def spec_matches(
    part_spec: dict,
    generic_spec: dict,
    strictness: dict,
) -> bool:
    """Check if a real part's spec matches a generic part's spec + strictness.

    Args:
        part_spec: extracted spec from a real part (from extract_spec)
        generic_spec: the generic part's spec_json (parsed)
        strictness: the generic part's strictness_json (parsed)
    """
    required = strictness.get("required", [])
    for field in required:
        if field == "value":
            # Compare parsed values with tolerance
            generic_val = _parse_spec_value(generic_spec.get("value", ""))
            part_val = part_spec.get("value")
            if generic_val is None or part_val is None:
                return False
            if generic_val == 0 and part_val == 0:
                continue
            if generic_val == 0 or part_val == 0:
                return False
            if abs(generic_val - part_val) / max(abs(generic_val), abs(part_val)) > 0.001:
                return False
        elif field == "package":
            gp = (generic_spec.get("package") or "").upper()
            pp = (part_spec.get("package") or "").upper()
            if gp and pp and gp != pp:
                return False
        elif field == "voltage_min":
            min_v = generic_spec.get("voltage_min", 0)
            part_v = part_spec.get("voltage")
            if part_v is None or part_v < min_v:
                return False
        elif field == "tolerance":
            gt = generic_spec.get("tolerance", "")
            pt = part_spec.get("tolerance", "")
            if gt and pt and gt != pt:
                return False
        elif field == "dielectric":
            gd = (generic_spec.get("dielectric") or "").upper()
            pd = (part_spec.get("dielectric") or "").upper()
            if gd and pd and gd != pd:
                return False
    return True


def _parse_spec_value(value_str: str) -> float | None:
    """Parse a value string like '100nF', '4.7kΩ', '10µH' to float."""
    if not value_str:
        return None
    # Try as capacitance
    val = parse_capacitance(value_str + " F" if "F" not in value_str else value_str)
    if val != float("inf"):
        return val
    # Try as resistance
    val = parse_resistance(value_str + " Ω" if "Ω" not in value_str and "ohm" not in value_str.lower() else value_str)
    if val != float("inf"):
        return val
    # Try as inductance
    val = parse_inductance(value_str + " H" if "H" not in value_str else value_str)
    if val != float("inf"):
        return val
    # Try as plain float
    try:
        return float(value_str)
    except ValueError:
        return None


def generate_generic_id(part_type: str, spec: dict) -> str:
    """Generate a stable, human-readable generic part ID from type + spec."""
    prefix = {"capacitor": "cap", "resistor": "res", "inductor": "ind"}.get(part_type, part_type[:3])
    value = (spec.get("value") or "").lower()
    # Normalize unicode
    value = value.replace("µ", "u").replace("Ω", "ohm").replace("ω", "ohm").replace("Ω", "ohm")
    value = re.sub(r"[^a-z0-9._]", "", value)
    package = (spec.get("package") or "").lower()
    package = re.sub(r"[^a-z0-9]", "", package)
    parts = [prefix]
    if value:
        parts.append(value)
    if package:
        parts.append(package)
    return "_".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_spec_extractor.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add spec_extractor.py tests/python/test_spec_extractor.py
git commit -m "feat: add spec_extractor module (extract specs, match generics)"
```

---

### Task 3: Generic parts module — CRUD + auto-matching + event log

**Files:**
- Create: `generic_parts.py`
- Create: `tests/python/test_generic_parts.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/python/test_generic_parts.py
"""Tests for generic_parts module."""

import json
import os

import pytest

import cache_db
import generic_parts


@pytest.fixture
def db(tmp_path):
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def events_dir(tmp_path):
    d = tmp_path / "events"
    d.mkdir()
    return str(d)


def _seed_parts(db):
    """Insert test parts into cache."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "Samsung", "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "YAGEO", "4.7kΩ 0402 Resistor", "0402"),
        ("C19702", "C19702", "GRM21BR61C106KE15L", "Murata", "10µF 16V 0805 Capacitor MLCC", "0805"),
        ("C9999", "C9999", "CL05B104KA5NNNC", "Samsung", "100nF 25V 0402 Capacitor MLCC", "0402"),
    ]
    for pid, lcsc, mpn, mfr, desc, pkg in parts:
        db.execute(
            "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, description, package, section) VALUES (?,?,?,?,?,?,?)",
            (pid, lcsc, mpn, mfr, desc, pkg, "Passives - Capacitors" if "Capacitor" in desc else "Passives - Resistors"),
        )
        db.execute("INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,100,0.01)", (pid,))
    db.commit()


class TestCreateGenericPart:
    def test_create_basic(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        assert gp["generic_part_id"].startswith("cap_")
        assert gp["name"] == "100nF 0402 MLCC"
        row = db.execute("SELECT * FROM generic_parts").fetchone()
        assert row is not None

    def test_auto_matches_members(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        members = db.execute(
            "SELECT part_id, source FROM generic_part_members WHERE generic_part_id=?",
            (gp["generic_part_id"],),
        ).fetchall()
        member_ids = {m["part_id"] for m in members}
        # C1525 and C9999 are both 100nF 0402 caps
        assert "C1525" in member_ids
        assert "C9999" in member_ids
        # C19702 is 10µF 0805 — should NOT match
        assert "C19702" not in member_ids
        # C2875244 is a resistor — should NOT match
        assert "C2875244" not in member_ids
        assert all(m["source"] == "auto" for m in members)

    def test_records_event(self, db, events_dir):
        import csv
        _seed_parts(db)
        generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        csv_path = os.path.join(events_dir, "part_events.csv")
        assert os.path.exists(csv_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 1
        assert rows[0]["event_type"] == "create_generic"


class TestManualMembership:
    def test_add_manual_member(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        # Add a resistor manually (override auto-matching)
        generic_parts.add_member(db, events_dir, gp["generic_part_id"], "C2875244", source="manual")
        members = db.execute(
            "SELECT part_id, source FROM generic_part_members WHERE generic_part_id=?",
            (gp["generic_part_id"],),
        ).fetchall()
        member_ids = {m["part_id"]: m["source"] for m in members}
        assert member_ids["C2875244"] == "manual"

    def test_set_preferred(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        generic_parts.set_preferred(db, events_dir, gp["generic_part_id"], "C1525")
        row = db.execute(
            "SELECT preferred FROM generic_part_members WHERE generic_part_id=? AND part_id='C1525'",
            (gp["generic_part_id"],),
        ).fetchone()
        assert row["preferred"] == 1

    def test_remove_member(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        generic_parts.remove_member(db, events_dir, gp["generic_part_id"], "C1525")
        row = db.execute(
            "SELECT 1 FROM generic_part_members WHERE generic_part_id=? AND part_id='C1525'",
            (gp["generic_part_id"],),
        ).fetchone()
        assert row is None


class TestResolveBomRow:
    def test_resolve_to_best_part(self, db, events_dir):
        _seed_parts(db)
        # Give C1525 more stock to make it the "best"
        db.execute("UPDATE stock SET quantity=500 WHERE part_id='C1525'")
        db.commit()
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        result = generic_parts.resolve_bom_spec(
            db, part_type="capacitor", value=1e-7, package="0402",
        )
        assert result is not None
        assert result["generic_part_id"] == gp["generic_part_id"]
        assert result["best_part_id"] == "C1525"  # more stock

    def test_preferred_wins(self, db, events_dir):
        _seed_parts(db)
        db.execute("UPDATE stock SET quantity=500 WHERE part_id='C1525'")
        db.commit()
        gp = generic_parts.create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        # Mark C9999 as preferred (even though it has less stock)
        generic_parts.set_preferred(db, events_dir, gp["generic_part_id"], "C9999")
        result = generic_parts.resolve_bom_spec(db, part_type="capacitor", value=1e-7, package="0402")
        assert result["best_part_id"] == "C9999"

    def test_no_match_returns_none(self, db, events_dir):
        _seed_parts(db)
        result = generic_parts.resolve_bom_spec(
            db, part_type="capacitor", value=4.7e-6, package="1206",
        )
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_generic_parts.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement generic_parts.py**

```python
# generic_parts.py
"""Generic parts — CRUD, auto-matching, popularity scoring, BOM resolution."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any

import spec_extractor
from price_ops import parse_qty

PART_EVENTS_FILE = "part_events.csv"
PART_EVENTS_FIELDS = ["timestamp", "event_type", "part_id", "generic_part_id", "data_json"]


def _record_event(events_dir: str, event_type: str,
                   part_id: str = "", generic_part_id: str = "",
                   data: dict | None = None) -> None:
    """Append an event to part_events.csv."""
    csv_path = os.path.join(events_dir, PART_EVENTS_FILE)
    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PART_EVENTS_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "event_type": event_type,
            "part_id": part_id,
            "generic_part_id": generic_part_id,
            "data_json": json.dumps(data) if data else "",
        })


def create_generic_part(
    conn: Any,
    events_dir: str,
    name: str,
    part_type: str,
    spec: dict,
    strictness: dict,
) -> dict[str, Any]:
    """Create a generic part and auto-match existing real parts."""
    generic_part_id = spec_extractor.generate_generic_id(part_type, spec)

    conn.execute(
        """INSERT OR REPLACE INTO generic_parts
           (generic_part_id, name, part_type, spec_json, strictness_json)
           VALUES (?,?,?,?,?)""",
        (generic_part_id, name, part_type, json.dumps(spec), json.dumps(strictness)),
    )

    # Auto-match: find all real parts whose spec matches
    _auto_match(conn, generic_part_id, part_type, spec, strictness)

    conn.commit()

    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "create_generic", generic_part_id=generic_part_id,
                   data={"name": name, "part_type": part_type, "spec": spec,
                          "strictness": strictness})

    return {"generic_part_id": generic_part_id, "name": name,
            "part_type": part_type, "spec": spec, "strictness": strictness}


def _auto_match(conn: Any, generic_part_id: str, part_type: str,
                 spec: dict, strictness: dict) -> None:
    """Find and insert auto-matched members for a generic part."""
    # Remove old auto-matches (keep manual)
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND source='auto'",
        (generic_part_id,),
    )
    # Scan all parts and check spec match
    parts = conn.execute(
        "SELECT part_id, description, package, section FROM parts"
    ).fetchall()
    for part in parts:
        part_spec = spec_extractor.extract_spec(
            description=part["description"], package=part["package"],
        )
        if part_spec["type"] != part_type:
            continue
        if spec_extractor.spec_matches(part_spec, spec, strictness):
            conn.execute(
                """INSERT OR IGNORE INTO generic_part_members
                   (generic_part_id, part_id, source, preferred)
                   VALUES (?, ?, 'auto', 0)""",
                (generic_part_id, part["part_id"]),
            )


def add_member(conn: Any, events_dir: str, generic_part_id: str,
                part_id: str, source: str = "manual") -> None:
    """Add a part to a generic group (manual override)."""
    conn.execute(
        """INSERT OR REPLACE INTO generic_part_members
           (generic_part_id, part_id, source, preferred)
           VALUES (?, ?, ?, 0)""",
        (generic_part_id, part_id, source),
    )
    conn.commit()
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "add_member", part_id=part_id,
                   generic_part_id=generic_part_id, data={"source": source})


def remove_member(conn: Any, events_dir: str, generic_part_id: str,
                   part_id: str) -> None:
    """Remove a part from a generic group."""
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND part_id=?",
        (generic_part_id, part_id),
    )
    conn.commit()
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "remove_member", part_id=part_id,
                   generic_part_id=generic_part_id)


def set_preferred(conn: Any, events_dir: str, generic_part_id: str,
                   part_id: str) -> None:
    """Mark a part as preferred within its generic group."""
    # Clear existing preferred in this group
    conn.execute(
        "UPDATE generic_part_members SET preferred=0 WHERE generic_part_id=?",
        (generic_part_id,),
    )
    conn.execute(
        "UPDATE generic_part_members SET preferred=1 WHERE generic_part_id=? AND part_id=?",
        (generic_part_id, part_id),
    )
    conn.commit()
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "set_preferred", part_id=part_id,
                   generic_part_id=generic_part_id)


def resolve_bom_spec(
    conn: Any,
    part_type: str,
    value: float,
    package: str,
) -> dict[str, Any] | None:
    """Find a generic part matching a BOM spec, return its best real part.

    Returns: {"generic_part_id", "generic_name", "best_part_id", "members"} or None.
    """
    generics = conn.execute("SELECT * FROM generic_parts").fetchall()
    for gp in generics:
        if gp["part_type"] != part_type:
            continue
        spec = json.loads(gp["spec_json"])
        strictness = json.loads(gp["strictness_json"])
        # Build a pseudo part_spec from the BOM values
        part_spec = {"type": part_type, "value": value, "package": package}
        if spec_extractor.spec_matches(part_spec, spec, strictness):
            # Found matching generic — resolve to best member
            members = conn.execute(
                """SELECT gm.part_id, gm.preferred, s.quantity
                   FROM generic_part_members gm
                   JOIN stock s USING (part_id)
                   WHERE gm.generic_part_id = ?
                   ORDER BY gm.preferred DESC, s.quantity DESC""",
                (gp["generic_part_id"],),
            ).fetchall()
            if not members:
                continue
            return {
                "generic_part_id": gp["generic_part_id"],
                "generic_name": gp["name"],
                "best_part_id": members[0]["part_id"],
                "members": [{"part_id": m["part_id"], "preferred": m["preferred"],
                             "quantity": m["quantity"]} for m in members],
            }
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_generic_parts.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add generic_parts.py tests/python/test_generic_parts.py
git commit -m "feat: add generic_parts module (CRUD, auto-matching, BOM resolution)"
```

---

### Task 4: Wire generic parts API into inventory_api.py

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/python/test_inventory_api.py

class TestGenericPartsAPI:
    def _import_parts(self, api):
        api.import_purchases([
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "CL05B104KO5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "200",
             "Unit Price($)": "0.0074", "Ext.Price($)": "1.48",
             "Description": "100nF 16V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
            {"LCSC Part Number": "C9999", "Manufacture Part Number": "CL05B104KA5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "50",
             "Unit Price($)": "0.006", "Ext.Price($)": "0.30",
             "Description": "100nF 25V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ])

    def test_create_generic_part(self, api):
        self._import_parts(api)
        result = api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["generic_part_id"].startswith("cap_")
        assert len(result["members"]) == 2  # C1525 and C9999

    def test_resolve_bom_spec(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = api.resolve_bom_spec("capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_list_generic_parts(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = api.list_generic_parts()
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/python/test_inventory_api.py::TestGenericPartsAPI -v`
Expected: FAIL

- [ ] **Step 3: Add API methods to InventoryApi**

Add these methods to `inventory_api.py`:

```python
def create_generic_part(self, name: str, part_type: str,
                         spec_json: str, strictness_json: str) -> dict[str, Any]:
    """Create a generic part with auto-matching."""
    import generic_parts
    spec = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
    strictness = json.loads(strictness_json) if isinstance(strictness_json, str) else strictness_json
    conn = self._get_cache()
    os.makedirs(self.events_dir, exist_ok=True)
    gp = generic_parts.create_generic_part(conn, self.events_dir, name, part_type, spec, strictness)
    # Fetch members
    members = conn.execute(
        """SELECT gm.part_id, gm.source, gm.preferred, s.quantity
           FROM generic_part_members gm
           JOIN stock s USING (part_id)
           WHERE gm.generic_part_id = ?""",
        (gp["generic_part_id"],),
    ).fetchall()
    gp["members"] = [dict(m) for m in members]
    return gp


def resolve_bom_spec(self, part_type: str, value: float,
                      package: str) -> dict[str, Any] | None:
    """Resolve a BOM spec to a generic part and its best real part."""
    import generic_parts
    conn = self._get_cache()
    return generic_parts.resolve_bom_spec(conn, part_type, float(value), package)


def list_generic_parts(self) -> list[dict[str, Any]]:
    """List all generic parts with their members."""
    conn = self._get_cache()
    gps = conn.execute("SELECT * FROM generic_parts").fetchall()
    result = []
    for gp in gps:
        members = conn.execute(
            """SELECT gm.part_id, gm.source, gm.preferred, s.quantity
               FROM generic_part_members gm
               JOIN stock s USING (part_id)
               WHERE gm.generic_part_id = ?""",
            (gp["generic_part_id"],),
        ).fetchall()
        result.append({
            "generic_part_id": gp["generic_part_id"],
            "name": gp["name"],
            "part_type": gp["part_type"],
            "spec": json.loads(gp["spec_json"]),
            "strictness": json.loads(gp["strictness_json"]),
            "members": [dict(m) for m in members],
        })
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_inventory_api.py::TestGenericPartsAPI -v`
Expected: 3 passed

- [ ] **Step 5: Run all Python tests**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add inventory_api.py tests/python/test_inventory_api.py
git commit -m "feat: add generic parts API (create, resolve, list)"
```

---

### Task 5: BOM matching — add generic part resolution to matching.js

**Files:**
- Modify: `js/matching.js`
- Modify: `tests/js/matching.test.js`

- [ ] **Step 1: Write failing test**

```javascript
// Add to tests/js/matching.test.js

describe("matchBOM with generic parts", () => {
  // genericParts: array of {generic_part_id, part_type, spec, strictness, members: [{part_id, preferred, quantity}]}
  const inventory = [
    { lcsc: "C1525", mpn: "CL05B104KO5NNNC", section: "Passives - Capacitors > MLCC",
      description: "100nF 16V 0402 Capacitor MLCC", package: "0402", qty: 200, unit_price: 0.007 },
    { lcsc: "C9999", mpn: "CL05B104KA5NNNC", section: "Passives - Capacitors > MLCC",
      description: "100nF 25V 0402 Capacitor MLCC", package: "0402", qty: 50, unit_price: 0.006 },
  ];
  const genericParts = [{
    generic_part_id: "cap_100nf_0402",
    part_type: "capacitor",
    spec: { value: "100nF", package: "0402" },
    strictness: { required: ["value", "package"] },
    members: [
      { part_id: "C1525", preferred: 0, quantity: 200 },
      { part_id: "C9999", preferred: 0, quantity: 50 },
    ],
  }];

  it("resolves BOM row without MPN to generic part", () => {
    const aggregated = new Map();
    aggregated.set("100nF:0402", {
      lcsc: "", mpn: "", qty: 10, refs: "C1 C2",
      desc: "Cap 100nF", value: "100nF", footprint: "0402",
    });
    const results = matchBOM(aggregated, inventory, [], [], genericParts);
    const r = results[0];
    expect(r.matchType).toBe("generic");
    expect(r.inv.lcsc).toBe("C1525"); // best by stock
    expect(r.genericPartId).toBe("cap_100nf_0402");
  });

  it("preferred member wins in generic resolution", () => {
    const gps = [{
      ...genericParts[0],
      members: [
        { part_id: "C1525", preferred: 0, quantity: 200 },
        { part_id: "C9999", preferred: 1, quantity: 50 },
      ],
    }];
    const aggregated = new Map();
    aggregated.set("100nF:0402", {
      lcsc: "", mpn: "", qty: 10, refs: "C1",
      desc: "Cap 100nF", value: "100nF", footprint: "0402",
    });
    const results = matchBOM(aggregated, inventory, [], [], gps);
    expect(results[0].inv.lcsc).toBe("C9999"); // preferred
  });

  it("falls through to value match when no generic matches", () => {
    const aggregated = new Map();
    aggregated.set("4.7uF:0805", {
      lcsc: "", mpn: "", qty: 10, refs: "C1",
      desc: "Cap 4.7µF", value: "4.7µF", footprint: "0805",
    });
    const results = matchBOM(aggregated, inventory, [], [], genericParts);
    // No generic for 4.7µF 0805, falls through to value match or missing
    expect(results[0].matchType).not.toBe("generic");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/matching.test.js`
Expected: FAIL — `matchBOM` doesn't accept genericParts parameter yet

- [ ] **Step 3: Add generic part matching to matchBOM()**

In `js/matching.js`, update the `matchBOM` signature to accept an optional `genericParts` parameter:

```javascript
export function matchBOM(aggregated, inventory, manualLinks, confirmedMatches, genericParts) {
```

Add a new step 5.5 between the existing value match (step 5) and the status assignment. Insert this after the `// 5. Value match` block and before `let status;`:

```javascript
    // 5.5. Generic part resolution (when no specific match found)
    if (!inv && genericParts && genericParts.length > 0) {
      const bomVal = extractBomValue(bom);
      const bomType = componentTypeFromRefs(bom.refs);
      const bomPkg = (bom.footprint || "").toUpperCase();

      for (const gp of genericParts) {
        // Check type compatibility
        const gpType = gp.part_type === "capacitor" ? "C"
                     : gp.part_type === "resistor" ? "R"
                     : gp.part_type === "inductor" ? "L" : null;
        if (bomType && gpType && bomType !== gpType) continue;

        // Check value from spec
        const specVal = parseEEValue(gp.spec.value) ?? extractValueFromDesc(gp.spec.value);
        if (specVal == null || bomVal == null) continue;
        if (specVal !== 0 && bomVal !== 0) {
          if (Math.abs(specVal - bomVal) / Math.max(Math.abs(specVal), Math.abs(bomVal)) > VALUE_TOLERANCE) continue;
        } else if (specVal !== bomVal) continue;

        // Check package from spec
        const gpPkg = (gp.spec.package || "").toUpperCase();
        if (bomPkg && gpPkg && !bomPkg.includes(gpPkg) && !gpPkg.includes(bomPkg)) continue;

        // Match found — resolve to best member
        if (gp.members && gp.members.length > 0) {
          // Sort: preferred first, then by quantity descending
          const sorted = [...gp.members].sort((a, b) => {
            if (a.preferred !== b.preferred) return b.preferred - a.preferred;
            return b.quantity - a.quantity;
          });
          const bestId = sorted[0].part_id;
          const found = invByLCSC[bestId.toUpperCase()] || invByMPN[bestId.toUpperCase()];
          if (found) {
            inv = found;
            matchType = "generic";
            // Attach generic part info for the UI
            bom._genericPartId = gp.generic_part_id;
            bom._genericMembers = gp.members;
            break;
          }
        }
      }
    }
```

Also update the result object to include `genericPartId`:

```javascript
    results.push({ bom, inv, status, matchType, alts,
      genericPartId: bom._genericPartId || null });
```

Clean up the temp properties:
```javascript
    delete bom._genericPartId;
    delete bom._genericMembers;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/js/matching.test.js`
Expected: All passed (existing + new)

- [ ] **Step 5: Commit**

```bash
git add js/matching.js tests/js/matching.test.js
git commit -m "feat: add generic part resolution to BOM matching (step 5.5)"
```

---

### Task 6: Full test suite + lint

**Files:** None (verification only)

- [ ] **Step 1: Run all Python tests**

Run: `python -m pytest tests/python/ --tb=short -q`
Expected: All passed

- [ ] **Step 2: Run Python lint**

Run: `ruff check spec_extractor.py generic_parts.py inventory_api.py cache_db.py`
Expected: Clean

- [ ] **Step 3: Run JS tests**

Run: `npx vitest run`
Expected: All passed

- [ ] **Step 4: Run JS lint**

Run: `npx eslint js/`
Expected: Clean

- [ ] **Step 5: Update .gitignore for part_events.csv**

Already covered by `data/events/*.csv` in `.gitignore`. Verify:
```bash
grep "data/events" .gitignore
```
Expected: `data/events/*.csv`

- [ ] **Step 6: Commit any remaining changes**

```bash
git add -A && git status
# If clean, no commit needed
```
