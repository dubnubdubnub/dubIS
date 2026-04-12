"""Tests for saved_searches CRUD module."""

import json
import os

import pytest

import cache_db
import saved_searches


@pytest.fixture
def data_dir(tmp_path):
    """Temporary data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return str(d)


@pytest.fixture
def conn(tmp_path):
    """SQLite cache database with schema including saved_searches table."""
    c = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(c)
    yield c
    c.close()


class TestCreate:
    def test_create_returns_dict(self, conn, data_dir):
        result = saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={"mlcc": True},
            search_text="100nF",
            frozen_members=["C1525", "C9999"],
        )
        assert isinstance(result, dict)
        assert result["name"] == "My Search"
        assert result["generic_part_id"] == "cap_abc"
        assert result["search_text"] == "100nF"

    def test_create_assigns_id(self, conn, data_dir):
        result = saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={},
            search_text="",
            frozen_members=[],
        )
        assert result["id"]
        assert len(result["id"]) > 4

    def test_create_has_created_at(self, conn, data_dir):
        result = saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={},
            search_text="",
            frozen_members=[],
        )
        assert result["created_at"]

    def test_create_stores_in_db(self, conn, data_dir):
        saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={"mlcc": True},
            search_text="100nF",
            frozen_members=["C1525"],
        )
        row = conn.execute("SELECT * FROM saved_searches").fetchone()
        assert row is not None
        assert row["name"] == "My Search"
        assert row["search_text"] == "100nF"

    def test_create_stores_tag_state_as_json(self, conn, data_dir):
        saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={"mlcc": True, "ceramic": False},
            search_text="",
            frozen_members=[],
        )
        row = conn.execute("SELECT tag_state FROM saved_searches").fetchone()
        parsed = json.loads(row["tag_state"])
        assert parsed == {"mlcc": True, "ceramic": False}

    def test_create_stores_frozen_members_as_json(self, conn, data_dir):
        saved_searches.create(
            conn, data_dir,
            generic_part_id="cap_abc",
            name="My Search",
            tag_state={},
            search_text="",
            frozen_members=["C1525", "C9999"],
        )
        row = conn.execute("SELECT frozen_members FROM saved_searches").fetchone()
        parsed = json.loads(row["frozen_members"])
        assert parsed == ["C1525", "C9999"]


class TestListForGroup:
    def test_list_returns_empty_when_none(self, conn, data_dir):
        result = saved_searches.list_for_group(conn, "cap_abc")
        assert result == []

    def test_list_returns_searches_for_group(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "Search A", {}, "100nF", [])
        saved_searches.create(conn, data_dir, "cap_abc", "Search B", {}, "10nF", [])
        result = saved_searches.list_for_group(conn, "cap_abc")
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"Search A", "Search B"}

    def test_list_filters_by_group(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "Cap Search", {}, "", [])
        saved_searches.create(conn, data_dir, "res_xyz", "Res Search", {}, "", [])
        result = saved_searches.list_for_group(conn, "cap_abc")
        assert len(result) == 1
        assert result[0]["name"] == "Cap Search"

    def test_list_returns_dicts(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "Search A", {}, "", [])
        result = saved_searches.list_for_group(conn, "cap_abc")
        assert isinstance(result[0], dict)
        assert "id" in result[0]
        assert "name" in result[0]
        assert "tag_state" in result[0]
        assert "search_text" in result[0]
        assert "frozen_members" in result[0]
        assert "created_at" in result[0]


class TestListAll:
    def test_list_all_empty(self, conn, data_dir):
        result = saved_searches.list_all(conn)
        assert result == []

    def test_list_all_returns_all_groups(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "Cap Search", {}, "", [])
        saved_searches.create(conn, data_dir, "res_xyz", "Res Search", {}, "", [])
        result = saved_searches.list_all(conn)
        assert len(result) == 2


class TestDelete:
    def test_delete_removes_from_db(self, conn, data_dir):
        s = saved_searches.create(conn, data_dir, "cap_abc", "My Search", {}, "", [])
        saved_searches.delete(conn, data_dir, s["id"])
        row = conn.execute(
            "SELECT * FROM saved_searches WHERE id=?", (s["id"],)
        ).fetchone()
        assert row is None

    def test_delete_nonexistent_does_not_raise(self, conn, data_dir):
        # Should not raise
        saved_searches.delete(conn, data_dir, "nonexistent-id")

    def test_delete_leaves_others_intact(self, conn, data_dir):
        a = saved_searches.create(conn, data_dir, "cap_abc", "Search A", {}, "", [])
        b = saved_searches.create(conn, data_dir, "cap_abc", "Search B", {}, "", [])
        saved_searches.delete(conn, data_dir, a["id"])
        result = saved_searches.list_all(conn)
        assert len(result) == 1
        assert result[0]["id"] == b["id"]


class TestPersistsToJson:
    def test_create_writes_json_file(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "My Search", {}, "100nF", [])
        json_path = os.path.join(data_dir, "saved_searches.json")
        assert os.path.exists(json_path)

    def test_create_json_contains_search(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "My Search", {"mlcc": True}, "100nF", ["C1525"])
        json_path = os.path.join(data_dir, "saved_searches.json")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "My Search"
        assert data[0]["search_text"] == "100nF"
        assert data[0]["tag_state"] == {"mlcc": True}
        assert data[0]["frozen_members"] == ["C1525"]

    def test_create_multiple_all_in_json(self, conn, data_dir):
        saved_searches.create(conn, data_dir, "cap_abc", "Search A", {}, "", [])
        saved_searches.create(conn, data_dir, "cap_abc", "Search B", {}, "", [])
        json_path = os.path.join(data_dir, "saved_searches.json")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 2

    def test_delete_updates_json(self, conn, data_dir):
        a = saved_searches.create(conn, data_dir, "cap_abc", "Search A", {}, "", [])
        saved_searches.create(conn, data_dir, "cap_abc", "Search B", {}, "", [])
        saved_searches.delete(conn, data_dir, a["id"])
        json_path = os.path.join(data_dir, "saved_searches.json")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "Search B"


class TestLoadFromJson:
    def test_load_into_db_from_json(self, conn, data_dir):
        # Write JSON manually
        records = [
            {
                "id": "abc123",
                "generic_part_id": "cap_abc",
                "name": "Loaded Search",
                "tag_state": {"mlcc": True},
                "search_text": "100nF",
                "frozen_members": ["C1525"],
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        json_path = os.path.join(data_dir, "saved_searches.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(records, f)

        saved_searches.load_into_db(conn, data_dir)
        result = saved_searches.list_all(conn)
        assert len(result) == 1
        assert result[0]["id"] == "abc123"
        assert result[0]["name"] == "Loaded Search"

    def test_load_into_db_no_file_is_noop(self, conn, data_dir):
        # No JSON file exists — should not raise
        saved_searches.load_into_db(conn, data_dir)
        assert saved_searches.list_all(conn) == []

    def test_load_into_db_idempotent(self, conn, data_dir):
        """Loading twice should not duplicate entries."""
        records = [
            {
                "id": "abc123",
                "generic_part_id": "cap_abc",
                "name": "Loaded Search",
                "tag_state": {},
                "search_text": "",
                "frozen_members": [],
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        json_path = os.path.join(data_dir, "saved_searches.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(records, f)

        saved_searches.load_into_db(conn, data_dir)
        saved_searches.load_into_db(conn, data_dir)
        result = saved_searches.list_all(conn)
        assert len(result) == 1
