"""Tests for GenericPartsApi facade."""

import json
import os

import pytest

from generic_parts_api import GenericPartsApi


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


@pytest.fixture
def gp_api(db, events_dir):
    """GenericPartsApi wired to test db and events_dir."""
    return GenericPartsApi(get_cache=lambda: db, events_dir=events_dir)


class TestGenericPartsApiInit:
    def test_stores_get_cache(self, db, events_dir):
        def getter():
            return db
        api = GenericPartsApi(get_cache=getter, events_dir=events_dir)
        assert api._get_cache is getter

    def test_stores_events_dir(self, db, events_dir):
        api = GenericPartsApi(get_cache=lambda: db, events_dir=events_dir)
        assert api.events_dir == events_dir


class TestCreateGenericPart:
    def test_creates_and_returns(self, gp_api, db):
        _seed_parts(db)
        result = gp_api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["generic_part_id"].startswith("cap_")
        assert result["name"] == "100nF 0402 MLCC"
        assert len(result["members"]) == 2  # C1525 and C9999

    def test_accepts_dict_args(self, gp_api, db):
        _seed_parts(db)
        result = gp_api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json={"value": "100nF", "package": "0402"},
            strictness_json={"required": ["value", "package"]},
        )
        assert result["generic_part_id"].startswith("cap_")

    def test_ensures_events_dir(self, db, tmp_path):
        new_events = str(tmp_path / "new_events")
        api = GenericPartsApi(get_cache=lambda: db, events_dir=new_events)
        _seed_parts(db)
        api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert os.path.isdir(new_events)


class TestResolveBomSpec:
    def test_resolves_matching_spec(self, gp_api, db):
        _seed_parts(db)
        gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = gp_api.resolve_bom_spec("capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_returns_none_for_no_match(self, gp_api, db):
        _seed_parts(db)
        result = gp_api.resolve_bom_spec("capacitor", 4.7e-6, "1206")
        assert result is None


class TestListGenericParts:
    def test_lists_created_parts(self, gp_api, db):
        _seed_parts(db)
        gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = gp_api.list_generic_parts()
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"
        assert "members" in gps[0]
        assert gps[0]["part_type"] == "capacitor"
        assert gps[0]["spec"] == {"value": "100nF", "package": "0402"}

    def test_empty_when_no_parts(self, gp_api):
        assert gp_api.list_generic_parts() == []


class TestAddRemoveMember:
    def test_add_member_returns_members(self, gp_api, db):
        _seed_parts(db)
        gp = gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        # Add a resistor manually
        members = gp_api.add_generic_member(gp["generic_part_id"], "C2875244")
        part_ids = {m["part_id"] for m in members}
        assert "C2875244" in part_ids

    def test_remove_member(self, gp_api, db):
        _seed_parts(db)
        gp = gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = gp_api.remove_generic_member(gp["generic_part_id"], "C1525")
        part_ids = {m["part_id"] for m in members}
        assert "C1525" not in part_ids


class TestSetPreferred:
    def test_set_preferred_member(self, gp_api, db):
        _seed_parts(db)
        gp = gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = gp_api.set_preferred_member(gp["generic_part_id"], "C1525")
        preferred = [m for m in members if m["preferred"] == 1]
        assert len(preferred) == 1
        assert preferred[0]["part_id"] == "C1525"


class TestUpdateGenericPart:
    def test_update_spec_and_rematch(self, gp_api, db):
        _seed_parts(db)
        gp = gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = gp_api.update_generic_part(
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
    def test_extracts_from_part(self, gp_api, db):
        _seed_parts(db)
        spec = gp_api.extract_spec("C1525")
        assert spec["type"] == "capacitor"
        assert "value" in spec

    def test_returns_empty_for_missing_part(self, gp_api, db):
        _seed_parts(db)
        spec = gp_api.extract_spec("NONEXISTENT")
        assert spec == {}


class TestExtractSpecFromValue:
    def test_capacitor_value_string(self, gp_api):
        spec = gp_api.extract_spec_from_value("capacitor", "100nF", "0402")
        assert spec["type"] == "capacitor"
        assert "value" in spec
        assert abs(spec["value"] - 100e-9) < 1e-15
        assert spec["package"] == "0402"

    def test_resistor_value_string(self, gp_api):
        spec = gp_api.extract_spec_from_value("resistor", "4.7k", "0402")
        assert spec["type"] == "resistor"

    def test_type_override_sets_type(self, gp_api):
        # Even if value string contains no type hint, the part_type arg wins
        spec = gp_api.extract_spec_from_value("inductor", "10uH", "0805")
        assert spec["type"] == "inductor"

    def test_empty_value_returns_type(self, gp_api):
        spec = gp_api.extract_spec_from_value("capacitor", "", "")
        assert spec["type"] == "capacitor"


class TestFetchMembers:
    def test_fetch_members_returns_dicts(self, gp_api, db):
        _seed_parts(db)
        gp = gp_api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        members = gp_api._fetch_members(db, gp["generic_part_id"])
        assert isinstance(members, list)
        assert len(members) == 2
        assert all(isinstance(m, dict) for m in members)
        assert all("part_id" in m for m in members)
        assert all("quantity" in m for m in members)


class TestEnsureEventsDir:
    def test_creates_directory(self, db, tmp_path):
        new_dir = str(tmp_path / "deep" / "nested" / "events")
        api = GenericPartsApi(get_cache=lambda: db, events_dir=new_dir)
        api._ensure_events_dir()
        assert os.path.isdir(new_dir)

    def test_idempotent(self, gp_api):
        gp_api._ensure_events_dir()
        gp_api._ensure_events_dir()  # should not raise


class TestPreviewGenericMembers:
    def test_preview_returns_matches(self, gp_api, db):
        _seed_parts(db)
        results = gp_api.preview_generic_members(
            spec_json='{"value":"100nF","package":"0402"}',
            part_type="capacitor",
            strictness_json='{"required":["value","package"]}',
        )
        part_ids = {r["part_id"] for r in results}
        assert "C1525" in part_ids
        assert "C9999" in part_ids

    def test_preview_accepts_dict_args(self, gp_api, db):
        _seed_parts(db)
        results = gp_api.preview_generic_members(
            spec_json={"value": "100nF", "package": "0402"},
            part_type="capacitor",
            strictness_json={"required": ["value", "package"]},
        )
        assert len(results) >= 2

    def test_preview_does_not_create_group(self, gp_api, db):
        _seed_parts(db)
        gp_api.preview_generic_members(
            spec_json='{"value":"100nF","package":"0402"}',
            part_type="capacitor",
            strictness_json='{"required":["value","package"]}',
        )
        conn = gp_api._get_cache()
        count = conn.execute("SELECT COUNT(*) AS c FROM generic_parts").fetchone()["c"]
        assert count == 0


@pytest.fixture
def gp_api_with_data_dir(db, events_dir, tmp_path):
    """GenericPartsApi wired to test db, events_dir, and a data_dir."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    return GenericPartsApi(get_cache=lambda: db, events_dir=events_dir, data_dir=data_dir)


class TestListSavedSearches:
    def test_empty_when_none(self, gp_api_with_data_dir):
        result = gp_api_with_data_dir.list_saved_searches("cap_abc")
        assert result == []

    def test_returns_searches_for_group(self, gp_api_with_data_dir):
        gp_api_with_data_dir.create_saved_search(
            "cap_abc", "My Search", '{"mlcc": true}', "100nF", '["C1525"]'
        )
        result = gp_api_with_data_dir.list_saved_searches("cap_abc")
        assert len(result) == 1
        assert result[0]["name"] == "My Search"

    def test_filters_by_group(self, gp_api_with_data_dir):
        gp_api_with_data_dir.create_saved_search("cap_abc", "Cap Search", "{}", "", "[]")
        gp_api_with_data_dir.create_saved_search("res_xyz", "Res Search", "{}", "", "[]")
        result = gp_api_with_data_dir.list_saved_searches("cap_abc")
        assert len(result) == 1
        assert result[0]["name"] == "Cap Search"


class TestCreateSavedSearch:
    def test_creates_and_returns(self, gp_api_with_data_dir):
        result = gp_api_with_data_dir.create_saved_search(
            "cap_abc", "My Search", '{"mlcc": true}', "100nF", '["C1525"]'
        )
        assert result["name"] == "My Search"
        assert result["generic_part_id"] == "cap_abc"
        assert result["search_text"] == "100nF"
        assert result["tag_state"] == {"mlcc": True}
        assert result["frozen_members"] == ["C1525"]

    def test_accepts_dict_args(self, gp_api_with_data_dir):
        result = gp_api_with_data_dir.create_saved_search(
            "cap_abc", "My Search", {"mlcc": True}, "100nF", ["C1525"]
        )
        assert result["tag_state"] == {"mlcc": True}
        assert result["frozen_members"] == ["C1525"]

    def test_persists_to_json(self, gp_api_with_data_dir):
        gp_api_with_data_dir.create_saved_search(
            "cap_abc", "My Search", "{}", "100nF", "[]"
        )
        json_path = os.path.join(gp_api_with_data_dir._data_dir, "saved_searches.json")
        assert os.path.exists(json_path)
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "My Search"


class TestDeleteSavedSearch:
    def test_delete_removes_search(self, gp_api_with_data_dir, db):
        result = gp_api_with_data_dir.create_saved_search(
            "cap_abc", "My Search", "{}", "", "[]"
        )
        gp_api_with_data_dir.delete_saved_search(result["id"])
        remaining = gp_api_with_data_dir.list_saved_searches("cap_abc")
        assert remaining == []

    def test_delete_updates_json(self, gp_api_with_data_dir):
        a = gp_api_with_data_dir.create_saved_search("cap_abc", "Search A", "{}", "", "[]")
        gp_api_with_data_dir.create_saved_search("cap_abc", "Search B", "{}", "", "[]")
        gp_api_with_data_dir.delete_saved_search(a["id"])
        json_path = os.path.join(gp_api_with_data_dir._data_dir, "saved_searches.json")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "Search B"
