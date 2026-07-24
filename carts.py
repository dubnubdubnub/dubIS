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
from dubis_errors import NotFoundError

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


def _require(conn: sqlite3.Connection, cart_id: str) -> None:
    if conn.execute("SELECT 1 FROM carts WHERE id=?", (cart_id,)).fetchone() is None:
        raise NotFoundError(f"cart {cart_id} not found")


def _next_position(conn: sqlite3.Connection, cart_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM cart_items WHERE cart_id=?", (cart_id,)
    ).fetchone()
    return int(row["m"]) + 1


def add_item(
    conn: sqlite3.Connection,
    data_dir: str,
    cart_id: str,
    *,
    part_id: str | None = None,
    raw: dict | None = None,
    qty: int = 1,
    target_distributor: str | None = None,
) -> dict[str, Any]:
    """Add an item to the cart. Re-adding an existing ref SETS qty (not additive)
    and updates target_distributor."""
    _require(conn, cart_id)
    ref = item_ref(part_id, raw)
    existing = conn.execute(
        "SELECT ref FROM cart_items WHERE cart_id=? AND ref=?", (cart_id, ref)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE cart_items SET qty=?, target_distributor=? WHERE cart_id=? AND ref=?",
            (int(qty), target_distributor, cart_id, ref),
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (cart_id, ref, part_id, raw, qty, target_distributor, position) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                cart_id, ref, part_id, json.dumps(raw) if raw else None, int(qty),
                target_distributor, _next_position(conn, cart_id),
            ),
        )
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def update_item(
    conn: sqlite3.Connection,
    data_dir: str,
    cart_id: str,
    ref: str,
    *,
    qty: int | None = None,
    target_distributor: str | None = None,
) -> dict[str, Any]:
    _require(conn, cart_id)
    if qty is not None:
        conn.execute("UPDATE cart_items SET qty=? WHERE cart_id=? AND ref=?", (int(qty), cart_id, ref))
    if target_distributor is not None:
        conn.execute(
            "UPDATE cart_items SET target_distributor=? WHERE cart_id=? AND ref=?",
            (target_distributor, cart_id, ref),
        )
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def remove_item(conn: sqlite3.Connection, data_dir: str, cart_id: str, ref: str) -> dict[str, Any]:
    _require(conn, cart_id)
    conn.execute("DELETE FROM cart_items WHERE cart_id=? AND ref=?", (cart_id, ref))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def clear(conn: sqlite3.Connection, data_dir: str, cart_id: str) -> dict[str, Any]:
    _require(conn, cart_id)
    conn.execute("DELETE FROM cart_items WHERE cart_id=?", (cart_id,))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


_ACTIVE_FILE = "cart_active.json"


def _active_path(data_dir: str) -> str:
    return os.path.join(data_dir, _ACTIVE_FILE)


def _read_active(data_dir: str) -> dict[str, str]:
    path = _active_path(data_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_active(data_dir: str, identity: str, cart_id: str) -> None:
    m = _read_active(data_dir)
    m[identity] = cart_id
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(_active_path(data_dir), json.dumps(m, indent=2), encoding="utf-8")


def get_active(data_dir: str, identity: str) -> str | None:
    return _read_active(data_dir).get(identity)


def split_by_distributor(
    conn: sqlite3.Connection,
    data_dir: str,
    cart_id: str,
    distributor: str,
    new_name: str,
    remove_from_source: bool,
    part_distributors,
) -> dict[str, Any]:
    """Create a new cart containing every line targeting (or sourceable from)
    ``distributor``. If ``remove_from_source``, those lines are removed from
    the source cart."""
    _require(conn, cart_id)
    src = get(conn, cart_id)
    moved = [
        it for it in src["items"]
        if it["target_distributor"] == distributor
        or (it["target_distributor"] is None and it.get("part_id")
            and distributor in part_distributors(it["part_id"]))
    ]
    new_cart = create(conn, data_dir, new_name)
    for it in moved:
        add_item(conn, data_dir, new_cart["id"], part_id=it.get("part_id"), raw=it.get("raw"),
                 qty=it["qty"], target_distributor=distributor)
    if remove_from_source:
        for it in moved:
            remove_item(conn, data_dir, cart_id, it["ref"])
    return {"source": get(conn, cart_id), "new": get(conn, new_cart["id"])}


def consolidate(
    conn: sqlite3.Connection,
    data_dir: str,
    cart_id: str,
    distributor: str,
    part_distributors,
) -> dict[str, Any]:
    """Set target_distributor=distributor on every line sourceable from it;
    lines that can't source from ``distributor`` are left untouched and
    listed in ``unresolved``."""
    _require(conn, cart_id)
    cart = get(conn, cart_id)
    unresolved = []
    for it in cart["items"]:
        pid = it.get("part_id")
        if pid and distributor in part_distributors(pid):
            update_item(conn, data_dir, cart_id, it["ref"], target_distributor=distributor)
        else:
            unresolved.append(it["ref"])
    return {"cart": get(conn, cart_id), "unresolved": unresolved}
