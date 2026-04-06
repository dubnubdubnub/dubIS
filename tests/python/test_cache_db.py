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
