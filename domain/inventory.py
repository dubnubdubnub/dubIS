"""Inventory domain — pipeline helpers for load, rebuild, adjust, import, consume."""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain.schema import InventoryItem

import cache_db
import domain.attributes
import domain.generic_parts
import domain.pricing
import inventory_ops
from csv_io import append_csv_rows, atomic_write_rows
from domain import part_registry as _preg

logger = logging.getLogger(__name__)


# ── Constants helpers ──────────────────────────────────────────────────────────

def parse_section_order(raw: list) -> tuple[list[str], list[dict]]:
    """Parse mixed SECTION_ORDER (strings + objects with children) into:
    - flat_order: list of all section strings (compound + bare parents) for iteration
    - hierarchy: structured list for the frontend
    """
    flat_order: list[str] = []
    hierarchy: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            flat_order.append(entry)
            hierarchy.append({"name": entry, "children": None})
        else:
            name = entry["name"]
            children = entry["children"]
            flat_order.append(name)
            for child in children:
                flat_order.append(f"{name} > {child}")
            hierarchy.append({"name": name, "children": children})
    return flat_order, hierarchy


def _restore_derived_entities(conn: sqlite3.Connection, base_dir: str, events_dir: str) -> None:
    """Repopulate rebuild-only derived tables after a SCHEMA_VERSION bump.

    create_schema drops generic_parts/generic_part_members/saved_searches on
    version change while parts/stock/checkpoint survive, so the cache-hit
    startup paths never reach rebuild(). Detect the wipe (generic_parts
    empty while parts is not) and restore from durable stores.
    NOTE: vendors/purchase_orders are also dropped on version bump and only
    repopulated by populate_full — pre-existing gap, out of scope here.

    part_attributes survives a version bump (it is not in create_schema's drop
    list), but it is *created* empty on caches that predate it, so restore it
    whenever the table is empty and the durable CSV is not.
    """
    if not conn.execute("SELECT 1 FROM part_attributes LIMIT 1").fetchone():
        domain.attributes.load_into_db(conn, base_dir)
    if conn.execute("SELECT 1 FROM generic_parts LIMIT 1").fetchone():
        return
    if not conn.execute("SELECT 1 FROM parts LIMIT 1").fetchone():
        return
    from domain import generic_parts as _gp  # noqa: PLC0415
    os.makedirs(events_dir, exist_ok=True)
    _gp.auto_generate_passive_groups(conn, events_dir)
    _gp.load_into_db(conn, base_dir)
    import saved_searches  # noqa: PLC0415
    saved_searches.load_into_db(conn, base_dir)


# ── Pipeline helpers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RebuildContext:
    """Internal bundle for the 7 args threaded through every rebuild() call site.

    Constructed at the domain boundary from the same individual args the
    outward-facing functions in this module already accept — this is purely
    an internal collapsing of the repeated bundle, not a public API change.
    """
    base_dir: str
    input_csv: str
    adjustments_csv: str
    events_dir: str
    fieldnames: list[str]
    adj_fieldnames: list[str]
    conn: sqlite3.Connection


def rebuild(ctx: RebuildContext) -> "tuple[list[InventoryItem], dict[str, int]]":
    """Full rebuild: replay all events into cache, return (fresh_inventory, migration_summary).

    Mutates *ctx.conn* (SQLite cache).  Caller holds the lock.
    """
    base_dir = ctx.base_dir
    input_csv = ctx.input_csv
    adjustments_csv = ctx.adjustments_csv
    events_dir = ctx.events_dir
    fieldnames = ctx.fieldnames
    conn = ctx.conn

    vendors_json = os.path.join(base_dir, "vendors.json")
    migration_summary = inventory_ops.migrate_to_vendors(input_csv, vendors_json)

    registry = _preg.load(base_dir)
    file_fieldnames, merged = inventory_ops.read_and_merge(input_csv, fieldnames, registry=registry)
    inventory_ops.apply_adjustments(merged, adjustments_csv, file_fieldnames)
    categorized = inventory_ops.categorize_and_sort(list(merged.values()))
    cache_db.populate_full(
        conn, merged, categorized,
        ledger_path=input_csv,
        po_csv_path=os.path.join(base_dir, "purchase_orders.csv"),
        vendors_json_path=vendors_json,
        registry=registry,
    )
    if registry.dirty:
        _preg.save(base_dir, registry)
    cache_db.write_checkpoint(conn, purchase_path=input_csv,
                              adjustments_path=adjustments_csv)
    if os.path.exists(events_dir):
        domain.pricing.populate_prices_cache(conn, events_dir)
    from domain import generic_parts as _gp  # noqa: PLC0415
    os.makedirs(events_dir, exist_ok=True)
    _gp.auto_generate_passive_groups(conn, events_dir)
    _gp.load_into_db(conn, base_dir)
    import saved_searches  # noqa: PLC0415
    saved_searches.load_into_db(conn, base_dir)
    import carts  # noqa: PLC0415
    carts.load_into_db(conn, base_dir)
    domain.attributes.load_into_db(conn, base_dir)
    return cache_db.query_inventory(conn), migration_summary


def load_or_rebuild(
    *,
    base_dir: str,
    input_csv: str,
    adjustments_csv: str,
    events_dir: str,
    fieldnames: list[str],
    adj_fieldnames: list[str],
    conn: sqlite3.Connection,
) -> "tuple[list[InventoryItem], dict[str, int]]":
    """Load from cache if populated; full rebuild otherwise.

    Returns (inventory, migration_summary).  migration_summary is {} on cache hit.
    """
    result = cache_db.query_inventory(conn)
    if result:
        _restore_derived_entities(conn, base_dir, events_dir)
        return result, {}
    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    return rebuild(ctx)


def rebuild_or_catchup(
    *,
    base_dir: str,
    input_csv: str,
    adjustments_csv: str,
    events_dir: str,
    fieldnames: list[str],
    adj_fieldnames: list[str],
    conn: sqlite3.Connection,
) -> "tuple[list[InventoryItem], dict[str, int]]":
    """Rebuild inventory using catch-up if possible, full rebuild otherwise.

    Returns (inventory, migration_summary).  migration_summary is {} on catch-up.
    """
    cp = cache_db.read_checkpoint(conn)
    has_cache = conn.execute("SELECT 1 FROM parts LIMIT 1").fetchone() is not None
    if has_cache and cp["purchase_hash"]:
        if cache_db.catch_up(conn, input_csv, adjustments_csv, adj_fieldnames):
            _restore_derived_entities(conn, base_dir, events_dir)
            return cache_db.query_inventory(conn), {}
    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    return rebuild(ctx)


def append_adjustment(
    adjustments_csv: str,
    adj_fieldnames: list[str],
    adj_type: str,
    part_key: str,
    quantity: int,
    *,
    note: str = "",
    bom_file: str = "",
    board_qty: int | str = "",
    source: str = "",
) -> None:
    """Append one row to adjustments.csv."""
    inventory_ops.append_adjustment(
        adjustments_csv, adj_fieldnames, adj_type, part_key, quantity,
        note=note, bom_file=bom_file, board_qty=board_qty, source=source,
    )


def record_import_prices(
    rows: list[dict[str, str]],
    events_dir: str,
    distributors: Any,
    base_dir: str,
) -> None:
    """Extract and record price observations from imported purchase rows.

    Resolves each row's part key registry-aware so that an enrichment import
    (a row that adds a higher-precedence PN to an already-registered part)
    records its price observation under the part's stable canonical key
    rather than the newly-added alias.
    """
    os.makedirs(events_dir, exist_ok=True)
    registry = _preg.load(base_dir)
    observations = []
    for row in rows:
        part_key = inventory_ops.get_part_key(row, registry)
        if not part_key:
            continue
        up = domain.pricing.parse_price(row.get("Unit Price($)"))
        if up <= 0:
            continue
        distributor = distributors.infer_distributor(row)
        observations.append({
            "part_id": part_key,
            "distributor": distributor,
            "unit_price": up,
            "source": "import",
        })
    if observations:
        domain.pricing.record_observations(events_dir, observations)


# ── adjust_part logic ─────────────────────────────────────────────────────────

def adjust_part(
    *,
    adj_type: str,
    part_key: str,
    quantity: int,
    note: str,
    source: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    input_csv: str,
    events_dir: str,
    fieldnames: list[str],
    conn: sqlite3.Connection,
) -> "list[InventoryItem]":
    """Validate, record, and apply a stock adjustment.  Caller holds the lock.

    Returns fresh inventory.
    """
    if not part_key or not str(part_key).strip():
        raise ValueError("part_key must not be empty")
    quantity = int(quantity)
    if quantity < 0:
        raise ValueError(f"quantity must be non-negative, got {quantity}")
    if adj_type == "remove":
        record_qty = -abs(quantity)
    elif adj_type == "add":
        record_qty = abs(quantity)
    elif adj_type == "set":
        record_qty = quantity
    else:
        raise ValueError(f"Unknown adjustment type: {adj_type}")

    append_adjustment(adjustments_csv, adj_fieldnames, adj_type, part_key, record_qty,
                      note=note, source=source)

    exists = conn.execute(
        "SELECT 1 FROM stock WHERE part_id = ?", (part_key,)
    ).fetchone()
    if not exists:
        ctx = RebuildContext(
            base_dir=base_dir,
            input_csv=input_csv,
            adjustments_csv=adjustments_csv,
            events_dir=events_dir,
            fieldnames=fieldnames,
            adj_fieldnames=adj_fieldnames,
            conn=conn,
        )
        result, _ = rebuild(ctx)
        return result

    if adj_type == "set":
        cache_db.set_stock_quantity(conn, part_key, max(0, record_qty))
    else:
        cache_db.apply_stock_delta(conn, part_key, record_qty)

    cache_db.write_checkpoint(conn, purchase_path=input_csv,
                              adjustments_path=adjustments_csv)
    cache_db.verify_parts(
        conn, [part_key], input_csv, adjustments_csv, fieldnames, fix=True,
    )
    return cache_db.query_inventory(conn)


# ── consume_bom logic ─────────────────────────────────────────────────────────

def consume_bom(
    *,
    matches: list[dict[str, Any]],
    board_qty: int,
    bom_name: str,
    note: str,
    source: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    input_csv: str,
    events_dir: str,
    fieldnames: list[str],
    conn: sqlite3.Connection,
) -> "list[InventoryItem]":
    """Consume matched BOM parts and return fresh inventory.  Caller holds the lock."""
    board_qty = int(board_qty)
    if board_qty <= 0:
        raise ValueError(f"board_qty must be positive, got {board_qty}")
    if not matches:
        raise ValueError("matches must not be empty")

    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    adj_rows = []
    for m in matches:
        bom_qty = int(m["bom_qty"])
        if bom_qty <= 0:
            raise ValueError(f"bom_qty must be positive, got {bom_qty}")
        delta = -(bom_qty * board_qty)
        adj_rows.append({
            "timestamp": ts,
            "type": "consume",
            "lcsc_part": m["part_key"],
            "quantity": delta,
            "bom_file": bom_name,
            "board_qty": board_qty,
            "note": note or f"consumed {board_qty}x {bom_name}",
            "source": source,
        })

    append_csv_rows(adjustments_csv, adj_fieldnames, adj_rows)

    affected_parts = [row["lcsc_part"] for row in adj_rows]
    all_cached = all(
        conn.execute("SELECT 1 FROM stock WHERE part_id = ?", (pn,)).fetchone()
        for pn in affected_parts
    )
    if not all_cached:
        ctx = RebuildContext(
            base_dir=base_dir,
            input_csv=input_csv,
            adjustments_csv=adjustments_csv,
            events_dir=events_dir,
            fieldnames=fieldnames,
            adj_fieldnames=adj_fieldnames,
            conn=conn,
        )
        result, _ = rebuild(ctx)
        return result

    for row in adj_rows:
        pn = row["lcsc_part"]
        delta = int(row["quantity"])
        cache_db.apply_stock_delta(conn, pn, delta)

    cache_db.write_checkpoint(conn, purchase_path=input_csv,
                              adjustments_path=adjustments_csv)
    cache_db.verify_parts(
        conn, affected_parts, input_csv, adjustments_csv, fieldnames, fix=True,
    )
    return cache_db.query_inventory(conn)


# ── import_purchases logic ────────────────────────────────────────────────────

def import_purchases(
    *,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    input_csv: str,
    events_dir: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    conn: sqlite3.Connection,
    distributors: Any,
) -> "list[InventoryItem]":
    """Append purchase rows to purchase_ledger.csv and return fresh inventory.

    Caller holds the lock.
    """
    if not rows:
        raise ValueError("No rows to import")

    normalized = [{fn: row.get(fn, "") for fn in fieldnames} for row in rows]
    append_csv_rows(input_csv, list(fieldnames), normalized)
    record_import_prices(rows, events_dir, distributors, base_dir)
    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return result


# ── update_part_price logic ───────────────────────────────────────────────────

def update_part_price(
    *,
    part_key: str,
    unit_price: float | None,
    ext_price: float | None,
    input_csv: str,
    events_dir: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    fieldnames: list[str],
    conn: sqlite3.Connection,
    infer_distributor_for_key: Any,
) -> "list[InventoryItem]":
    """Update unit/ext price for a part in purchase_ledger.csv.

    Caller holds the lock.
    """
    if not os.path.exists(input_csv):
        raise ValueError("No purchase ledger found")

    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        file_fieldnames = reader.fieldnames
        rows = list(reader)

    registry = _preg.load(base_dir)
    found = False
    for row in rows:
        pk = inventory_ops.get_part_key(row, registry)
        if pk == part_key:
            qty = domain.pricing.parse_qty(row.get("Quantity"))
            unit_price, ext_price = domain.pricing.derive_missing_price(unit_price, ext_price, qty)
            if unit_price is not None:
                row["Unit Price($)"] = f"{unit_price:.4f}"
            if ext_price is not None:
                row["Ext.Price($)"] = f"{ext_price:.2f}"
            found = True

    if not found:
        new_row = {fn: "" for fn in file_fieldnames}
        if part_key.upper().startswith("C") and part_key[1:].isdigit():
            new_row["LCSC Part Number"] = part_key
        else:
            new_row["Manufacture Part Number"] = part_key
        new_row["Quantity"] = "0"
        if unit_price is not None:
            new_row["Unit Price($)"] = f"{unit_price:.4f}"
        if ext_price is not None:
            new_row["Ext.Price($)"] = f"{ext_price:.2f}"
        rows.append(new_row)

    atomic_write_rows(input_csv, file_fieldnames, rows, encoding="utf-8-sig")

    os.makedirs(events_dir, exist_ok=True)
    if unit_price is not None and unit_price > 0:
        domain.pricing.record_observations(events_dir, [{
            "part_id": part_key,
            "distributor": infer_distributor_for_key(part_key),
            "unit_price": unit_price,
            "source": "manual",
        }])

    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return result


# ── update_part_fields logic ──────────────────────────────────────────────────

def update_part_fields(
    *,
    part_key: str,
    fields: dict[str, str],
    field_to_col: dict[str, str],
    input_csv: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    fieldnames: list[str],
    events_dir: str,
    conn: sqlite3.Connection,
) -> "list[InventoryItem]":
    """Update metadata fields for a part in purchase_ledger.csv.

    Caller holds the lock.
    """
    if not fields:
        raise ValueError("No fields to update")

    if not os.path.exists(input_csv):
        raise ValueError("No purchase ledger found")

    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        file_fieldnames = reader.fieldnames
        rows = list(reader)

    registry = _preg.load(base_dir)
    found = False
    for row in rows:
        pk = inventory_ops.get_part_key(row, registry)
        if pk == part_key:
            for js_name, value in fields.items():
                col = field_to_col.get(js_name)
                if col and col in file_fieldnames:
                    row[col] = value
            found = True

    if not found:
        raise ValueError(f"Part {part_key!r} not found in purchase ledger")

    atomic_write_rows(input_csv, file_fieldnames, rows, encoding="utf-8-sig")

    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return result


def fetch_missing_descriptions(
    *,
    fetchers: "dict[str, Any]",
    input_csv: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    fieldnames: list[str],
    events_dir: str,
    conn: sqlite3.Connection,
) -> dict:
    """Fetch descriptions for ledger rows that have a distributor PN but no
    description. Writes all results in one pass, rebuilds once. Caller holds lock.
    """
    _PN_COLS = {
        "lcsc": "LCSC Part Number",
        "digikey": "Digikey Part Number",
        "mouser": "Mouser Part Number",
        "pololu": "Pololu Part Number",
    }
    _ORDER = ("lcsc", "digikey", "mouser", "pololu")

    def _blank(v: str) -> bool:
        return (v or "").strip().lower() in ("", "nan", "none")

    if not os.path.exists(input_csv):
        raise ValueError("No purchase ledger found")

    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        file_fieldnames = reader.fieldnames
        rows = list(reader)

    updated = failed = skipped = 0
    for row in rows:
        if not _blank(row.get("Description", "")):
            skipped += 1
            continue
        pns = [(d, (row.get(_PN_COLS[d]) or "").strip()) for d in _ORDER]
        pns = [(d, pn) for d, pn in pns if pn]
        if not pns:
            continue  # no PN → not fetchable, not counted
        desc = ""
        for dist, pn in pns:
            fetch = fetchers.get(dist)
            if not fetch:
                continue
            try:
                product = fetch(pn)
            except Exception as e:  # noqa: BLE001 - one bad lookup must not abort batch
                logger.warning("fetch_missing_descriptions: %s %s failed: %s", dist, pn, e)
                continue
            cand = (product or {}).get("description") if product else None
            if cand and str(cand).strip():
                desc = str(cand).strip()
                break
        if desc and "Description" in file_fieldnames:
            row["Description"] = desc
            updated += 1
        else:
            failed += 1

    if updated:
        atomic_write_rows(input_csv, file_fieldnames, rows, encoding="utf-8-sig")

    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return {"inventory": result, "summary": {"updated": updated, "failed": failed, "skipped": skipped}}


# ── truncate_csv logic ────────────────────────────────────────────────────────

def truncate_and_rebuild(
    *,
    csv_path: str,
    count: int,
    label: str,
    base_dir: str,
    input_csv: str,
    adjustments_csv: str,
    events_dir: str,
    fieldnames: list[str],
    adj_fieldnames: list[str],
    conn: sqlite3.Connection,
) -> "list[InventoryItem]":
    """Remove the last *count* rows from a CSV and rebuild.  Caller holds the lock."""
    file_fieldnames, rows = inventory_ops.truncate_csv(csv_path, count, label)

    atomic_write_rows(csv_path, file_fieldnames, rows, encoding="utf-8-sig")

    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return result


def has_purchase_history(input_csv: str, part_key: str) -> bool:
    """Whether any purchase_ledger.csv row belongs to this part.

    Deliberately derived-key (registry-unaware), like `last_po_quantity` and
    `get_sourced_distributors` — this call site doesn't have a registry handle
    to thread through. Revisit once the Phase-1 service layer provides a
    shared registry handle to callers like this one.
    """
    if not os.path.exists(input_csv):
        return False
    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if inventory_ops.get_part_key(row) == part_key:
                return True
    return False


def delete_part(
    *,
    part_key: str,
    input_csv: str,
    adjustments_csv: str,
    adj_fieldnames: list[str],
    base_dir: str,
    fieldnames: list[str],
    events_dir: str,
    conn: sqlite3.Connection,
) -> "list[InventoryItem]":
    """Permanently delete a part that has no purchase_ledger.csv history.

    Caller holds the lock.
    """
    if has_purchase_history(input_csv, part_key):
        raise ValueError(f"Part {part_key!r} has purchase history; cannot delete")

    for generic_part_id, _name in domain.generic_parts.group_memberships_for_part(conn, part_key):
        domain.generic_parts.remove_member(conn, events_dir, base_dir, generic_part_id, part_key)

    if os.path.exists(adjustments_csv):
        with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            file_fieldnames = reader.fieldnames
            rows = [r for r in reader if (r.get("lcsc_part") or "").strip() != part_key]
        if file_fieldnames:
            atomic_write_rows(adjustments_csv, file_fieldnames, rows, encoding="utf-8-sig")

    ctx = RebuildContext(
        base_dir=base_dir,
        input_csv=input_csv,
        adjustments_csv=adjustments_csv,
        events_dir=events_dir,
        fieldnames=fieldnames,
        adj_fieldnames=adj_fieldnames,
        conn=conn,
    )
    result, _ = rebuild(ctx)
    return result
