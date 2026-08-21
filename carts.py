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

# Every cart column, in the order _cart_dict reads them. Kept in one place
# because four call sites select them and a partial list silently produces a
# cart dict missing a field the frontend then renders as undefined.
_CART_COLUMNS = "SELECT id, name, created_at, board_count"

DEFAULT_BOARD_COUNT = 1
"""How many boards a cart builds when nobody has said. One, not zero -- a cart
for zero boards would zero every quantity derived from it."""


def _clean_optional_qty(value: Any) -> int | None:
    """A positive int, or None for "not recorded".

    0 collapses to None on purpose: a line that places zero parts per board is
    indistinguishable from one whose placement count was never captured, and
    treating it as a real 0 would multiply every board count down to nothing.
    """
    if value is None:
        return None
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def clean_board_count(value: Any) -> int:
    """Coerce a board count to a positive int, falling back to the default.

    Deliberately forgiving rather than raising: this runs on every cart read
    from `carts.json`, including files written before the column existed (where
    the value is absent) and hand-edited ones. A cart that fails to load is a
    worse outcome than a cart that quietly builds one board, and the API layer
    rejects bad input before it ever reaches here.
    """
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BOARD_COUNT
    return count if count > 0 else DEFAULT_BOARD_COUNT


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
        "SELECT ref, part_id, raw, qty, target_distributor, target_packaging, "
        "preset, per_board_qty FROM cart_items WHERE cart_id=? ORDER BY position, ref",
        (cart_id,),
    ).fetchall()
    return [
        {
            "ref": r["ref"],
            "part_id": r["part_id"],
            "raw": json.loads(r["raw"]) if r["raw"] else None,
            "qty": r["qty"],
            "target_distributor": r["target_distributor"],
            "target_packaging": r["target_packaging"],
            "preset": r["preset"],
            "per_board_qty": r["per_board_qty"],
        }
        for r in rows
    ]


def _cart_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "board_count": row["board_count"],
        "items": _item_rows(conn, row["id"]),
    }


def _persist(conn: sqlite3.Connection, data_dir: str) -> None:
    records = [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "board_count": r["board_count"],
            "items": _item_rows(conn, r["id"]),
        }
        for r in conn.execute(_CART_COLUMNS + " FROM carts ORDER BY created_at").fetchall()
    ]
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def create(conn: sqlite3.Connection, data_dir: str, name: str,
           board_count: int = DEFAULT_BOARD_COUNT) -> dict[str, Any]:
    cart_id = "cart_" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    boards = clean_board_count(board_count)
    conn.execute(
        "INSERT INTO carts (id, name, created_at, board_count) VALUES (?,?,?,?)",
        (cart_id, name, created_at, boards),
    )
    conn.commit()
    _persist(conn, data_dir)
    return {"id": cart_id, "name": name, "created_at": created_at,
            "board_count": boards, "items": []}


def list_carts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(_CART_COLUMNS + " FROM carts ORDER BY created_at").fetchall()
    return [_cart_dict(conn, r) for r in rows]


def get(conn: sqlite3.Connection, cart_id: str) -> dict[str, Any] | None:
    row = conn.execute(_CART_COLUMNS + " FROM carts WHERE id=?", (cart_id,)).fetchone()
    return _cart_dict(conn, row) if row else None


def rename(conn: sqlite3.Connection, data_dir: str, cart_id: str, name: str) -> dict[str, Any]:
    _require(conn, cart_id)
    conn.execute("UPDATE carts SET name=? WHERE id=?", (name, cart_id))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def set_board_count(conn: sqlite3.Connection, data_dir: str, cart_id: str,
                    board_count: int) -> dict[str, Any]:
    """Set how many boards this cart builds.

    The count is stored rather than folded into the item quantities, so a
    5,000-piece line stays explainable as "25 boards x 8 placements, less 112
    on hand" weeks later. Re-deriving it from the BOM instead would require the
    BOM to be byte-identical, which at procurement stage it never is.
    """
    _require(conn, cart_id)
    conn.execute("UPDATE carts SET board_count=? WHERE id=?",
                 (clean_board_count(board_count), cart_id))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def delete(conn: sqlite3.Connection, data_dir: str, cart_id: str) -> None:
    _require(conn, cart_id)
    conn.execute("DELETE FROM cart_items WHERE cart_id=?", (cart_id,))
    conn.execute("DELETE FROM carts WHERE id=?", (cart_id,))
    conn.commit()
    _persist(conn, data_dir)
    _prune_active(data_dir, cart_id)


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for rec in records:
        conn.execute(
            "INSERT OR REPLACE INTO carts (id, name, created_at, board_count) VALUES (?,?,?,?)",
            (rec["id"], rec.get("name", ""), rec.get("created_at", ""),
             clean_board_count(rec.get("board_count"))),
        )
        for pos, item in enumerate(rec.get("items", [])):
            ref = item.get("ref") or item_ref(item.get("part_id"), item.get("raw"))
            conn.execute(
                "INSERT OR REPLACE INTO cart_items "
                "(cart_id, ref, part_id, raw, qty, target_distributor, target_packaging, "
                "preset, per_board_qty, position) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rec["id"], ref, item.get("part_id"),
                    json.dumps(item["raw"]) if item.get("raw") else None,
                    int(item.get("qty", 1)), item.get("target_distributor"),
                    item.get("target_packaging"), item.get("preset"),
                    _clean_optional_qty(item.get("per_board_qty")), pos,
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
    target_packaging: str | None = None,
    preset: str | None = None,
    per_board_qty: int | None = None,
) -> dict[str, Any]:
    """Add an item to the cart. Re-adding an existing ref SETS qty (not additive)
    and updates target_distributor.

    `per_board_qty` is what makes the cart's board count mean anything: it is
    the placement count for one board, so a quantity stays derivable as
    `per_board_qty x board_count - on_hand` instead of being a number nobody
    can account for later. None for one-off lines that are not per-board.

    `preset` records whether `qty` is a *rule* ("whatever the cheapest option
    is at this volume") or a *pinned number* ("buy exactly 5,000"). Without it a
    board-count change cannot tell which rows to re-derive and which to leave
    alone.
    """
    _require(conn, cart_id)
    ref = item_ref(part_id, raw)
    existing = conn.execute(
        "SELECT ref FROM cart_items WHERE cart_id=? AND ref=?", (cart_id, ref)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE cart_items SET qty=?, target_distributor=?, target_packaging=?, "
            "preset=?, per_board_qty=? WHERE cart_id=? AND ref=?",
            (int(qty), target_distributor, target_packaging, preset,
             _clean_optional_qty(per_board_qty), cart_id, ref),
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (cart_id, ref, part_id, raw, qty, target_distributor, "
            "target_packaging, preset, per_board_qty, position) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                cart_id, ref, part_id, json.dumps(raw) if raw else None, int(qty),
                target_distributor, target_packaging, preset,
                _clean_optional_qty(per_board_qty), _next_position(conn, cart_id),
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
    target_packaging: str | None = None,
    preset: str | None = None,
    per_board_qty: int | None = None,
) -> dict[str, Any]:
    """Patch one cart line. Only the fields passed are touched.

    Every field is None-means-leave-alone, so a caller changing a preset does
    not have to restate the quantity -- and clearing a field is therefore a
    deliberate act, not something a partial update does by accident. The one
    exception is the empty string, which clears `preset`/`target_packaging`
    back to "follow the cart default".
    """
    _require(conn, cart_id)
    if qty is not None:
        conn.execute("UPDATE cart_items SET qty=? WHERE cart_id=? AND ref=?", (int(qty), cart_id, ref))
    if target_distributor is not None:
        conn.execute(
            "UPDATE cart_items SET target_distributor=? WHERE cart_id=? AND ref=?",
            (target_distributor, cart_id, ref),
        )
    if target_packaging is not None:
        conn.execute(
            "UPDATE cart_items SET target_packaging=? WHERE cart_id=? AND ref=?",
            (target_packaging or None, cart_id, ref),
        )
    if preset is not None:
        conn.execute(
            "UPDATE cart_items SET preset=? WHERE cart_id=? AND ref=?",
            (preset or None, cart_id, ref),
        )
    if per_board_qty is not None:
        conn.execute(
            "UPDATE cart_items SET per_board_qty=? WHERE cart_id=? AND ref=?",
            (_clean_optional_qty(per_board_qty), cart_id, ref),
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


def set_active(conn: sqlite3.Connection, data_dir: str, identity: str, cart_id: str) -> None:
    _require(conn, cart_id)
    m = _read_active(data_dir)
    m[identity] = cart_id
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(_active_path(data_dir), json.dumps(m, indent=2), encoding="utf-8")


def get_active(data_dir: str, identity: str) -> str | None:
    return _read_active(data_dir).get(identity)


def _prune_active(data_dir: str, cart_id: str) -> None:
    """Remove any identity->cart_id pointer entries that reference a deleted
    cart, so no dangling active-cart pointer survives the cart's deletion."""
    m = _read_active(data_dir)
    pruned = {identity: cid for identity, cid in m.items() if cid != cart_id}
    if pruned != m:
        os.makedirs(data_dir, exist_ok=True)
        csv_io.atomic_write_text(_active_path(data_dir), json.dumps(pruned, indent=2), encoding="utf-8")


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
