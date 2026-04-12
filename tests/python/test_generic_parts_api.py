"""Tests for generic_parts API-level functions."""

import os

import pytest

import generic_parts


def _seed_parts(conn):
    """Insert test parts into cache with stock."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "Samsung", "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "YAGEO", "4.7k\u03a9 0402 Resistor", "0402"),
        ("C19702", "C19702", "GRM21BR61C106KE15L", "Murata", "10\u00b5F 16V 0805 Capacitor MLCC", "0805"),
        ("C9999", "C9999", "CL05B104KA5NNNC", "Samsung", "100nF 25V 0402 Capacitor MLCC", "0402"),
    ]
    for pid, lcsc, mpn, mfr, desc, pkg in parts:
        conn.execute(
            "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, description, package, section) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, lcsc, mpn, mfr, desc, pkg,
             "Passives - Capacitors" if "Capacitor" in desc else "Passives - Resistors"),
        )
        conn.execute(
            "INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,100,0.01)",
            (pid,),
        )
    conn.commit()


class TestCreateGenericPart:
    def test_creates_and_returns(self, db, events_dir):
        _seed_parts(db)
        result = generic_parts.create_generic_part_api(
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
        result = generic_parts.create_generic_part_api(
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
        generic_parts.create_generic_part_api(
            db, new_events,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert os.path.isdir(new_events)


class TestResolveBomSpec:
    def test_resolves_matching_spec(self, db, events_dir):
        _seed_parts(db)
        generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = generic_parts.resolve_bom_spec(db, "capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_returns_none_for_no_match(self, db, events_dir):
        _seed_parts(db)
        result = generic_parts.resolve_bom_spec(db, "capacitor", 4.7e-6, "1206")
        assert result is None


class TestListGenericParts:
    def test_lists_created_parts(self, db, events_dir):
        _seed_parts(db)
        generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = generic_parts.list_generic_parts_with_member_specs(db)
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"
        assert "members" in gps[0]
        assert gps[0]["part_type"] == "capacitor"
        assert gps[0]["spec"] == {"value": "100nF", "package": "0402"}

    def test_empty_when_no_parts(self, db):
        assert generic_parts.list_generic_parts_with_member_specs(db) == []


class TestAddRemoveMember:
    def test_add_member_returns_members(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        # Add a resistor manually
        members = generic_parts.add_member_api(db, events_dir, gp["generic_part_id"], "C2875244")
        part_ids = {m["part_id"] for m in members}
        assert "C2875244" in part_ids

    def test_remove_member(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = generic_parts.remove_member_api(db, events_dir, gp["generic_part_id"], "C1525")
        part_ids = {m["part_id"] for m in members}
        assert "C1525" not in part_ids


class TestSetPreferred:
    def test_set_preferred_member(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = generic_parts.set_preferred_api(db, events_dir, gp["generic_part_id"], "C1525")
        preferred = [m for m in members if m["preferred"] == 1]
        assert len(preferred) == 1
        assert preferred[0]["part_id"] == "C1525"


class TestUpdateGenericPart:
    def test_update_spec_and_rematch(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = generic_parts.update_generic_part_api(
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


class TestExtractSpec:
    def test_extracts_from_part(self, db, events_dir):
        _seed_parts(db)
        spec = generic_parts.extract_spec_for_part(db, "C1525")
        assert spec["type"] == "capacitor"
        assert "value" in spec

    def test_returns_empty_for_missing_part(self, db, events_dir):
        _seed_parts(db)
        spec = generic_parts.extract_spec_for_part(db, "NONEXISTENT")
        assert spec == {}


class TestFetchMembers:
    def test_fetch_members_returns_dicts(self, db, events_dir):
        _seed_parts(db)
        gp = generic_parts.create_generic_part_api(
            db, events_dir,
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = generic_parts.fetch_members(db, gp["generic_part_id"])
        assert isinstance(members, list)
        assert len(members) == 2
        assert all(isinstance(m, dict) for m in members)
        assert all("part_id" in m for m in members)
        assert all("quantity" in m for m in members)


class TestParseJson:
    def test_parses_string(self):
        result = generic_parts._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_passes_through_dict(self):
        d = {"key": "value"}
        assert generic_parts._parse_json(d) is d
