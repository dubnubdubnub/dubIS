"""Carts — CRUD for persistent purchase carts.

data_dir/carts.json is the source of truth; SQLite (carts + cart_items) is a
derived materialized view rebuilt on cache load. Mirrors saved_searches.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any

import csv_io

logger = logging.getLogger(__name__)

_JSON_FILE = "carts.json"


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def item_ref(part_id: str | None, raw: dict | None) -> str:
    """Stable identity for a cart line: part_id, else a hash of raw fields."""
    if part_id:
        return part_id
    payload = json.dumps(raw or {}, sort_keys=True)
    return "raw:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _item_rows(conn: sqlite3.Connection, cart_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ref, part_id, raw, qty, target_distributor FROM cart_items "
        "WHERE cart_id=? ORDER BY position, ref",
        (cart_id,),
    ).fetchall()
    return [
        {
            "ref": r["ref"],
            "part_id": r["part_id"],
            "raw": json.loads(r["raw"]) if r["raw"] else None,
            "qty": r["qty"],
            "target_distributor": r["target_distributor"],
        }
        for r in rows
    ]


def _cart_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "items": _item_rows(conn, row["id"]),
    }


def _persist(conn: sqlite3.Connection, data_dir: str) -> None:
    records = [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "items": _item_rows(conn, r["id"]),
        }
        for r in conn.execute("SELECT id, name, created_at FROM carts ORDER BY created_at").fetchall()
    ]
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def create(conn: sqlite3.Connection, data_dir: str, name: str) -> dict[str, Any]:
    cart_id = "cart_" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO carts (id, name, created_at) VALUES (?,?,?)",
        (cart_id, name, created_at),
    )
    conn.commit()
    _persist(conn, data_dir)
    return {"id": cart_id, "name": name, "created_at": created_at, "items": []}


def list_carts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, created_at FROM carts ORDER BY created_at").fetchall()
    return [_cart_dict(conn, r) for r in rows]


def get(conn: sqlite3.Connection, cart_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, name, created_at FROM carts WHERE id=?", (cart_id,)).fetchone()
    return _cart_dict(conn, row) if row else None


def rename(conn: sqlite3.Connection, data_dir: str, cart_id: str, name: str) -> dict[str, Any]:
    conn.execute("UPDATE carts SET name=? WHERE id=?", (name, cart_id))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def delete(conn: sqlite3.Connection, data_dir: str, cart_id: str) -> None:
    conn.execute("DELETE FROM cart_items WHERE cart_id=?", (cart_id,))
    conn.execute("DELETE FROM carts WHERE id=?", (cart_id,))
    conn.commit()
    _persist(conn, data_dir)


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for rec in records:
        conn.execute(
            "INSERT OR REPLACE INTO carts (id, name, created_at) VALUES (?,?,?)",
            (rec["id"], rec.get("name", ""), rec.get("created_at", "")),
        )
        for pos, item in enumerate(rec.get("items", [])):
            ref = item.get("ref") or item_ref(item.get("part_id"), item.get("raw"))
            conn.execute(
                "INSERT OR REPLACE INTO cart_items "
                "(cart_id, ref, part_id, raw, qty, target_distributor, position) VALUES (?,?,?,?,?,?,?)",
                (
                    rec["id"], ref, item.get("part_id"),
                    json.dumps(item["raw"]) if item.get("raw") else None,
                    int(item.get("qty", 1)), item.get("target_distributor"), pos,
                ),
            )
    conn.commit()
    logger.info("Loaded %d carts from %s", len(records), path)
