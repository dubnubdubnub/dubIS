"""Tests for domain.generic_parts — CRUD, auto-matching, BOM resolution, API helpers."""

import csv
import json
import os

import pytest

import domain.generic_parts
from domain.generic_parts import (
    _auto_match,
    _parse_json,
    add_member,
    add_member_api,
    auto_generate_passive_groups,
    create_generic_part,
    create_generic_part_api,
    exclude_member,
    extract_spec_for_part,
    fetch_members,
    list_generic_parts_with_member_specs,
    preview_members,
    remove_member,
    remove_member_api,
    resolve_bom_spec,
    set_preferred,
    set_preferred_api,
    update_generic_part_api,
)


def _seed_parts(db):
    """Insert test parts into cache."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "Samsung", "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "YAGEO", "4.7kΩ 0402 Resistor", "0402"),
        ("C19702", "C19702", "GRM21BR61C106KE15L", "Murata", "10µF 16V 0805 Capacitor MLCC", "0805"),
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


# ── Core CRUD tests (from test_generic_parts.py) ────────────────────────────


class TestCreateGenericPart:
    def test_create_basic(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part(
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
        gp = create_generic_part(
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
        create_generic_part(
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
        gp = create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        # Add a resistor manually (override auto-matching)
        add_member(db, events_dir, gp["generic_part_id"], "C2875244", source="manual")
        members = db.execute(
            "SELECT part_id, source FROM generic_part_members WHERE generic_part_id=?",
            (gp["generic_part_id"],),
        ).fetchall()
        member_ids = {m["part_id"]: m["source"] for m in members}
        assert member_ids["C2875244"] == "manual"

    def test_set_preferred(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        set_preferred(db, events_dir, gp["generic_part_id"], "C1525")
        row = db.execute(
            "SELECT preferred FROM generic_part_members WHERE generic_part_id=? AND part_id='C1525'",
            (gp["generic_part_id"],),
        ).fetchone()
        assert row["preferred"] == 1

    def test_remove_member(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        remove_member(db, events_dir, gp["generic_part_id"], "C1525")
        row = db.execute(
            "SELECT 1 FROM generic_part_members WHERE generic_part_id=? AND part_id='C1525'",
            (gp["generic_part_id"],),
        ).fetchone()
        assert row is None


class TestExclusionRecords:
    """Excluded members survive auto-regeneration."""

    def test_exclude_member_persists_through_auto_match(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part(
            db, events_dir, "100nF 0402 Cap", "capacitor",
            {"value": "100nF", "package": "0402"},
            {"required": ["value", "package"]},
        )
        gp_id = gp["generic_part_id"]
        members_before = db.execute(
            "SELECT part_id FROM generic_part_members WHERE generic_part_id = ?",
            (gp_id,)
        ).fetchall()
        member_ids_before = [m["part_id"] for m in members_before]
        assert "C1525" in member_ids_before  # auto-matched 100nF 0402

        # Exclude a member (simulates drag-out)
        exclude_member(db, events_dir, gp_id, "C1525")

        # Re-run auto_match (simulates inventory rebuild)
        spec = {"value": "100nF", "package": "0402"}
        _auto_match(db, gp_id, "capacitor", spec, {"required": ["value", "package"]})

        # Excluded member should NOT reappear as auto — row stays with source='excluded'
        rows = db.execute(
            "SELECT part_id, source FROM generic_part_members WHERE generic_part_id = ?",
            (gp_id,)
        ).fetchall()
        member_map = {r["part_id"]: r["source"] for r in rows}
        assert member_map.get("C1525") == "excluded"

    def test_exclude_records_event(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part(
            db, events_dir, "100nF 0402 Cap", "capacitor",
            {"value": "100nF", "package": "0402"},
            {"required": ["value", "package"]},
        )
        exclude_member(db, events_dir, gp["generic_part_id"], "C1525")
        with open(os.path.join(events_dir, "part_events.csv")) as f:
            rows = list(csv.DictReader(f))
        exclude_events = [r for r in rows if r["event_type"] == "exclude_member"]
        assert len(exclude_events) == 1
        assert exclude_events[0]["part_id"] == "C1525"


class TestResolveBomRow:
    def test_resolve_to_best_part(self, db, events_dir):
        _seed_parts(db)
        # Give C1525 more stock to make it the "best"
        db.execute("UPDATE stock SET quantity=500 WHERE part_id='C1525'")
        db.commit()
        gp = create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        result = resolve_bom_spec(
            db, part_type="capacitor", value=1e-7, package="0402",
        )
        assert result is not None
        assert result["generic_part_id"] == gp["generic_part_id"]
        assert result["best_part_id"] == "C1525"  # more stock

    def test_preferred_wins(self, db, events_dir):
        _seed_parts(db)
        db.execute("UPDATE stock SET quantity=500 WHERE part_id='C1525'")
        db.commit()
        gp = create_generic_part(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        # Mark C9999 as preferred (even though it has less stock)
        set_preferred(db, events_dir, gp["generic_part_id"], "C9999")
        result = resolve_bom_spec(db, part_type="capacitor", value=1e-7, package="0402")
        assert result["best_part_id"] == "C9999"

    def test_no_match_returns_none(self, db, events_dir):
        _seed_parts(db)
        result = resolve_bom_spec(
            db, part_type="capacitor", value=4.7e-6, package="1206",
        )
        assert result is None


class TestAutoGeneratePassiveGroups:
    def test_generates_groups_for_capacitors(self, db, events_dir):
        _seed_parts(db)
        groups = auto_generate_passive_groups(db, events_dir)
        # Should create groups for 100nF 0402 (C1525 + C9999) and 10µF 0805 (C19702)
        assert len(groups) >= 2
        names = {g["name"] for g in groups}
        assert any("100nF" in n and "0402" in n for n in names)
        assert any("10µF" in n and "0805" in n for n in names)

    def test_auto_groups_have_source_auto(self, db, events_dir):
        _seed_parts(db)
        auto_generate_passive_groups(db, events_dir)
        rows = db.execute("SELECT source FROM generic_parts").fetchall()
        assert all(r["source"] == "auto" for r in rows)

    def test_auto_groups_have_correct_members(self, db, events_dir):
        _seed_parts(db)
        auto_generate_passive_groups(db, events_dir)
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
        auto_generate_passive_groups(db, events_dir)
        row = db.execute("SELECT source FROM generic_parts WHERE generic_part_id='manual_1'").fetchone()
        assert row["source"] == "manual"

    def test_idempotent(self, db, events_dir):
        _seed_parts(db)
        auto_generate_passive_groups(db, events_dir)
        count1 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        auto_generate_passive_groups(db, events_dir)
        count2 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        assert count1 == count2


class TestListGenericPartsWithSpecs:
    def test_list_includes_source(self, db, events_dir):
        _seed_parts(db)
        auto_generate_passive_groups(db, events_dir)
        gps = list_generic_parts_with_member_specs(db)
        assert len(gps) >= 1
        assert all("source" in gp for gp in gps)
        assert all(gp["source"] == "auto" for gp in gps)

    def test_list_includes_member_specs(self, db, events_dir):
        _seed_parts(db)
        auto_generate_passive_groups(db, events_dir)
        gps = list_generic_parts_with_member_specs(db)
        gp = next(g for g in gps if "100nF" in g["name"])
        assert len(gp["members"]) >= 2
        # Each member should have extracted spec fields
        for m in gp["members"]:
            assert "spec" in m
            assert "type" in m["spec"]


class TestPreviewMembers:
    def test_preview_returns_matching_parts(self, db):
        _seed_parts(db)
        results = preview_members(
            db,
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        part_ids = {r["part_id"] for r in results}
        assert "C1525" in part_ids
        assert "C9999" in part_ids

    def test_preview_does_not_create_group(self, db):
        _seed_parts(db)
        preview_members(
            db,
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        count = db.execute("SELECT COUNT(*) AS c FROM generic_parts").fetchone()["c"]
        assert count == 0

    def test_preview_includes_quantity(self, db):
        _seed_parts(db)
        results = preview_members(
            db,
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        assert len(results) > 0
        for r in results:
            assert "quantity" in r

    def test_preview_excludes_non_matching_type(self, db):
        _seed_parts(db)
        results = preview_members(
            db,
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        part_ids = {r["part_id"] for r in results}
        # Resistor should not appear
        assert "C2875244" not in part_ids

    def test_preview_includes_spec(self, db):
        _seed_parts(db)
        results = preview_members(
            db,
            part_type="capacitor",
            spec={"value": "100nF", "package": "0402"},
            strictness={"required": ["value", "package"]},
        )
        for r in results:
            assert "spec" in r


# ── API-level tests (from test_generic_parts_api.py) ────────────────────────


class TestCreateGenericPartApi:
    def test_creates_and_returns(self, db, events_dir):
        _seed_parts(db)
        result = create_generic_part_api(
            db, events_dir,
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["generic_part_id"].startswith("cap_")
        assert result["name"] == "100nF 0402 MLCC"
        assert len(result["members"]) == 2  # C1525 and C9999

    def test_accepts_dict_args(self, db, events_dir):
        _seed_parts(db)
        result = create_generic_part_api(
            db, events_dir,
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json={"value": "100nF", "package": "0402"},
            strictness_json={"required": ["value", "package"]},
        )
        assert result["generic_part_id"].startswith("cap_")

    def test_ensures_events_dir(self, db, tmp_path):
        new_events = str(tmp_path / "new_events")
        _seed_parts(db)
        create_generic_part_api(
            db, new_events,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert os.path.isdir(new_events)


class TestResolveBomSpecApi:
    def test_resolves_matching_spec(self, db, events_dir):
        _seed_parts(db)
        create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = resolve_bom_spec(db, "capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_returns_none_for_no_match(self, db, events_dir):
        _seed_parts(db)
        result = resolve_bom_spec(db, "capacitor", 4.7e-6, "1206")
        assert result is None


class TestListGenericPartsApi:
    def test_lists_created_parts(self, db, events_dir):
        _seed_parts(db)
        create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = list_generic_parts_with_member_specs(db)
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"
        assert "members" in gps[0]
        assert gps[0]["part_type"] == "capacitor"
        assert gps[0]["spec"] == {"value": "100nF", "package": "0402"}

    def test_empty_when_no_parts(self, db):
        assert list_generic_parts_with_member_specs(db) == []


class TestAddRemoveMemberApi:
    def test_add_member_returns_members(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        # Add a resistor manually
        members = add_member_api(db, events_dir, gp["generic_part_id"], "C2875244")
        part_ids = {m["part_id"] for m in members}
        assert "C2875244" in part_ids

    def test_remove_member(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = remove_member_api(db, events_dir, gp["generic_part_id"], "C1525")
        part_ids = {m["part_id"] for m in members}
        assert "C1525" not in part_ids


class TestSetPreferredApi:
    def test_set_preferred_member(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = set_preferred_api(db, events_dir, gp["generic_part_id"], "C1525")
        preferred = [m for m in members if m["preferred"] == 1]
        assert len(preferred) == 1
        assert preferred[0]["part_id"] == "C1525"


class TestUpdateGenericPartApi:
    def test_update_spec_and_rematch(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = update_generic_part_api(
            db, events_dir,
            gp["generic_part_id"],
            name="100nF 0805",
            spec_json='{"value":"100nF","package":"0805"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["name"] == "100nF 0805"
        assert result["spec"] == {"value": "100nF", "package": "0805"}
        # 0805 100nF parts: none in our test seed
        assert len(result["members"]) == 0


class TestExtractSpecApi:
    def test_extracts_from_part(self, db, events_dir):
        _seed_parts(db)
        spec = extract_spec_for_part(db, "C1525")
        assert spec["type"] == "capacitor"
        assert "value" in spec

    def test_returns_empty_for_missing_part(self, db, events_dir):
        _seed_parts(db)
        spec = extract_spec_for_part(db, "NONEXISTENT")
        assert spec == {}


class TestExtractSpecFromValue:
    """Test extract_spec_from_value logic (now on InventoryApi, tested via spec_extractor)."""

    def test_capacitor_value_string(self):
        import spec_extractor
        desc = "capacitor 100nF 0402"
        spec = spec_extractor.extract_spec(desc, "0402")
        spec["type"] = "capacitor"
        assert spec["type"] == "capacitor"
        assert "value" in spec
        assert spec["package"] == "0402"

    def test_resistor_value_string(self):
        import spec_extractor
        desc = "resistor 4.7k 0402"
        spec = spec_extractor.extract_spec(desc, "0402")
        spec["type"] = "resistor"
        assert spec["type"] == "resistor"

    def test_type_override_sets_type(self):
        import spec_extractor
        desc = "inductor 10uH 0805"
        spec = spec_extractor.extract_spec(desc, "0805")
        spec["type"] = "inductor"
        assert spec["type"] == "inductor"

    def test_empty_value_returns_type(self):
        import spec_extractor
        desc = "capacitor  "
        spec = spec_extractor.extract_spec(desc, "")
        spec["type"] = "capacitor"
        assert spec["type"] == "capacitor"


class TestFetchMembersApi:
    def test_fetch_members_returns_dicts(self, db, events_dir):
        _seed_parts(db)
        gp = create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = fetch_members(db, gp["generic_part_id"])
        assert isinstance(members, list)
        assert len(members) == 2
        assert all(isinstance(m, dict) for m in members)
        assert all("part_id" in m for m in members)
        assert all("quantity" in m for m in members)


class TestParseJson:
    def test_parses_string(self):
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_passes_through_dict(self):
        d = {"key": "value"}
        assert _parse_json(d) is d
