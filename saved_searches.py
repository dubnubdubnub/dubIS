"""Saved searches — CRUD for per-group saved search configurations.

Saved searches store tag state, search text, and frozen member lists for a
generic part flyout. The JSON file at data_dir/saved_searches.json is the
source of truth; SQLite is a derived materialized view rebuilt on cache load.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_JSON_FILE = "saved_searches.json"


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def _persist(conn: sqlite3.Connection, data_dir: str) -> None:
    """Write all saved searches from SQLite to the JSON file."""
    rows = conn.execute(
        "SELECT id, generic_part_id, name, tag_state, search_text, frozen_members, created_at "
        "FROM saved_searches ORDER BY created_at"
    ).fetchall()
    records = []
    for row in rows:
        records.append({
            "id": row["id"],
            "generic_part_id": row["generic_part_id"],
            "name": row["name"],
            "tag_state": json.loads(row["tag_state"]),
            "search_text": row["search_text"],
            "frozen_members": json.loads(row["frozen_members"]),
            "created_at": row["created_at"],
        })
    os.makedirs(data_dir, exist_ok=True)
    with open(_json_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a plain dict with parsed JSON fields."""
    return {
        "id": row["id"],
        "generic_part_id": row["generic_part_id"],
        "name": row["name"],
        "tag_state": json.loads(row["tag_state"]),
        "search_text": row["search_text"],
        "frozen_members": json.loads(row["frozen_members"]),
        "created_at": row["created_at"],
    }


def create(
    conn: sqlite3.Connection,
    data_dir: str,
    generic_part_id: str,
    name: str,
    tag_state: dict,
    search_text: str,
    frozen_members: list,
) -> dict[str, Any]:
    """INSERT a new saved search, persist to JSON, return the created record."""
    search_id = str(uuid.uuid4())
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """INSERT INTO saved_searches
           (id, generic_part_id, name, tag_state, search_text, frozen_members, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            search_id,
            generic_part_id,
            name,
            json.dumps(tag_state),
            search_text,
            json.dumps(frozen_members),
            created_at,
        ),
    )
    conn.commit()
    _persist(conn, data_dir)
    return {
        "id": search_id,
        "generic_part_id": generic_part_id,
        "name": name,
        "tag_state": tag_state,
        "search_text": search_text,
        "frozen_members": frozen_members,
        "created_at": created_at,
    }


def list_for_group(conn: sqlite3.Connection, generic_part_id: str) -> list[dict[str, Any]]:
    """SELECT all saved searches for a given generic part."""
    rows = conn.execute(
        "SELECT id, generic_part_id, name, tag_state, search_text, frozen_members, created_at "
        "FROM saved_searches WHERE generic_part_id=? ORDER BY created_at",
        (generic_part_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """SELECT all saved searches across all generic parts."""
    rows = conn.execute(
        "SELECT id, generic_part_id, name, tag_state, search_text, frozen_members, created_at "
        "FROM saved_searches ORDER BY created_at"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete(conn: sqlite3.Connection, data_dir: str, search_id: str) -> None:
    """DELETE a saved search by id and update the JSON file."""
    conn.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
    conn.commit()
    _persist(conn, data_dir)


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    """Load saved searches from data_dir/saved_searches.json into SQLite.

    Uses INSERT OR REPLACE so calling this multiple times is idempotent.
    No-op if the JSON file does not exist.
    """
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for rec in records:
        conn.execute(
            """INSERT OR REPLACE INTO saved_searches
               (id, generic_part_id, name, tag_state, search_text, frozen_members, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                rec["id"],
                rec.get("generic_part_id", ""),
                rec.get("name", ""),
                json.dumps(rec.get("tag_state", {})),
                rec.get("search_text", ""),
                json.dumps(rec.get("frozen_members", [])),
                rec.get("created_at", ""),
            ),
        )
    conn.commit()
    logger.info("Loaded %d saved searches from %s", len(records), path)
