"""Tests for generic_parts module."""

import csv
import os

import generic_parts


def _seed_parts(db):
    """Insert test parts into cache."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "Samsung", "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "YAGEO", "4.7k\u03a9 0402 Resistor", "0402"),
        ("C19702", "C19702", "GRM21BR61C106KE15L", "Murata", "10\u00b5F 16V 0805 Capacitor MLCC", "0805"),
        ("C9999", "C9999", "CL05B104KA5NNNC", "Samsung", "100nF 25V 0402 Capacitor MLCC", "0402"),
    ]
    for pid, lcsc, mpn, mfr, desc, pkg in parts:
        section = "Passives - Capacitors" if "Capacitor" in desc else "Passives - Resistors"
        db.execute(
            "INSERT INTO parts"
            " (part_id, lcsc, mpn, manufacturer, description, package, section)"
            " VALUES (?,?,?,?,?,?,?)",
            (pid, lcsc, mpn, mfr, desc, pkg, section),
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
        # C19702 is 10uF 0805 -- should NOT match
        assert "C19702" not in member_ids
        # C2875244 is a resistor -- should NOT match
        assert "C2875244" not in member_ids
        assert all(m["source"] == "auto" for m in members)

    def test_records_event(self, db, events_dir):
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


class TestAutoGeneratePassiveGroups:
    def test_generates_groups_for_capacitors(self, db, events_dir):
        _seed_parts(db)
        groups = generic_parts.auto_generate_passive_groups(db, events_dir)
        # Should create groups for 100nF 0402 (C1525 + C9999) and 10µF 0805 (C19702)
        assert len(groups) >= 2
        names = {g["name"] for g in groups}
        assert any("100nF" in n and "0402" in n for n in names)
        assert any("10µF" in n and "0805" in n for n in names)

    def test_auto_groups_have_source_auto(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        rows = db.execute("SELECT source FROM generic_parts").fetchall()
        assert all(r["source"] == "auto" for r in rows)

    def test_auto_groups_have_correct_members(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        # Find the 100nF 0402 group
        gp = db.execute(
            "SELECT generic_part_id FROM generic_parts WHERE name LIKE '%100nF%0402%'"
        ).fetchone()
        assert gp is not None
        members = db.execute(
            "SELECT part_id FROM generic_part_members WHERE generic_part_id=?",
            (gp["generic_part_id"],),
        ).fetchall()
        member_ids = {m["part_id"] for m in members}
        assert "C1525" in member_ids
        assert "C9999" in member_ids
        assert "C19702" not in member_ids

    def test_does_not_clobber_manual_groups(self, db, events_dir):
        _seed_parts(db)
        # Create a manual group first
        db.execute(
            "INSERT INTO generic_parts"
            " (generic_part_id, name, part_type, source)"
            " VALUES ('manual_1', 'My Group', 'other', 'manual')"
        )
        db.commit()
        generic_parts.auto_generate_passive_groups(db, events_dir)
        row = db.execute("SELECT source FROM generic_parts WHERE generic_part_id='manual_1'").fetchone()
        assert row["source"] == "manual"

    def test_idempotent(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        count1 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        generic_parts.auto_generate_passive_groups(db, events_dir)
        count2 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        assert count1 == count2


class TestListGenericPartsWithSpecs:
    def test_list_includes_source(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        gps = generic_parts.list_generic_parts_with_member_specs(db)
        assert len(gps) >= 1
        assert all("source" in gp for gp in gps)
        assert all(gp["source"] == "auto" for gp in gps)

    def test_list_includes_member_specs(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        gps = generic_parts.list_generic_parts_with_member_specs(db)
        gp = next(g for g in gps if "100nF" in g["name"])
        assert len(gp["members"]) >= 2
        # Each member should have extracted spec fields
        for m in gp["members"]:
            assert "spec" in m
            assert "type" in m["spec"]
