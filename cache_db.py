"""SQLite cache layer for inventory data.

The cache is a derived, deletable materialized view of the CSV event logs.
Delete cache.db at any time — it will be rebuilt from purchase_ledger.csv
and adjustments.csv on next startup.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
from typing import Any

from inventory_ops import apply_adjustments, compute_adjusted_qty, get_part_key, read_and_merge, sort_key_for_section
from price_ops import parse_price, parse_qty

SCHEMA_VERSION = "6"

logger = logging.getLogger(__name__)


def connect(db_path: str) -> sqlite3.Connection:
    """Open or create the cache database."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create cache tables if they don't exist. Migrates from older versions."""
    # Check if schema version is stale — drop derived tables so they get recreated
    row = conn.execute(
        "SELECT value FROM cache_meta WHERE key = 'schema_version'"
    ).fetchone() if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cache_meta'"
    ).fetchone() else None
    old_version = row[0] if row else None
    if old_version and old_version != SCHEMA_VERSION:
        conn.executescript("""
            DROP TABLE IF EXISTS generic_part_members;
            DROP TABLE IF EXISTS generic_parts;
            DROP TABLE IF EXISTS saved_searches;
            DROP TABLE IF EXISTS purchase_orders;
            DROP TABLE IF EXISTS vendors;
        """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS parts (
            part_id       TEXT PRIMARY KEY,
            lcsc          TEXT DEFAULT '',
            mpn           TEXT DEFAULT '',
            digikey       TEXT DEFAULT '',
            pololu        TEXT DEFAULT '',
            mouser        TEXT DEFAULT '',
            manufacturer  TEXT DEFAULT '',
            description   TEXT DEFAULT '',
            package       TEXT DEFAULT '',
            rohs          TEXT DEFAULT '',
            section       TEXT DEFAULT '',
            sort_key      REAL,
            date_code     TEXT DEFAULT '',
            primary_vendor_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stock (
            part_id     TEXT PRIMARY KEY REFERENCES parts(part_id),
            quantity    INTEGER NOT NULL DEFAULT 0,
            unit_price  REAL NOT NULL DEFAULT 0.0,
            ext_price   REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS prices (
            part_id            TEXT NOT NULL REFERENCES parts(part_id),
            distributor        TEXT NOT NULL,
            latest_unit_price  REAL,
            avg_unit_price     REAL,
            price_count        INTEGER NOT NULL DEFAULT 0,
            last_observed      TEXT,
            moq                INTEGER,
            source             TEXT,
            PRIMARY KEY (part_id, distributor)
        );
        CREATE TABLE IF NOT EXISTS generic_parts (
            generic_part_id  TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            part_type        TEXT NOT NULL,
            spec_json        TEXT NOT NULL DEFAULT '{}',
            strictness_json  TEXT NOT NULL DEFAULT '{}',
            source           TEXT NOT NULL DEFAULT 'manual'
        );
        CREATE TABLE IF NOT EXISTS generic_part_members (
            generic_part_id  TEXT NOT NULL REFERENCES generic_parts(generic_part_id),
            part_id          TEXT NOT NULL REFERENCES parts(part_id),
            source           TEXT NOT NULL DEFAULT 'auto',
            preferred        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (generic_part_id, part_id)
        );
        CREATE TABLE IF NOT EXISTS saved_searches (
            id               TEXT PRIMARY KEY,
            generic_part_id  TEXT,
            name             TEXT,
            tag_state        TEXT,
            search_text      TEXT,
            frozen_members   TEXT,
            created_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS vendors (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            url           TEXT DEFAULT '',
            favicon_path  TEXT DEFAULT '',
            type          TEXT NOT NULL,
            icon          TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS purchase_orders (
            po_id              TEXT PRIMARY KEY,
            vendor_id          TEXT NOT NULL REFERENCES vendors(id),
            source_file_hash   TEXT DEFAULT '',
            source_file_ext    TEXT DEFAULT '',
            purchase_date      TEXT DEFAULT '',
            notes              TEXT DEFAULT ''
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def populate_full(
    conn: sqlite3.Connection,
    merged: dict[str, dict[str, str]],
    categorized: dict[str, list[dict[str, str]]],
) -> None:
    """Full population from merge + categorize results. Clears existing data."""
    conn.execute("DELETE FROM prices")
    conn.execute("DELETE FROM stock")
    conn.execute("DELETE FROM parts")

    for section, parts_list in categorized.items():
        for part in parts_list:
            part_id = get_part_key(part)
            if not part_id:
                continue
            sk = sort_key_for_section(section, part.get("Description", ""))
            conn.execute(
                """INSERT OR REPLACE INTO parts
                   (part_id, lcsc, mpn, digikey, pololu, mouser,
                    manufacturer, description, package, rohs, section, sort_key, date_code,
                    primary_vendor_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    part_id,
                    (part.get("LCSC Part Number") or "").strip(),
                    (part.get("Manufacture Part Number") or "").strip(),
                    (part.get("Digikey Part Number") or "").strip(),
                    (part.get("Pololu Part Number") or "").strip(),
                    (part.get("Mouser Part Number") or "").strip(),
                    (part.get("Manufacturer") or "").strip(),
                    (part.get("Description") or "").strip(),
                    (part.get("Package") or "").strip(),
                    (part.get("RoHS") or "").strip(),
                    section,
                    sk,
                    (part.get("Date Code / Lot No.") or "").strip(),
                    "",
                ),
            )
            conn.execute(
                """INSERT OR REPLACE INTO stock (part_id, quantity, unit_price, ext_price)
                   VALUES (?,?,?,?)""",
                (
                    part_id,
                    parse_qty(part.get("Quantity")),
                    parse_price(part.get("Unit Price($)")),
                    parse_price(part.get("Ext.Price($)")),
                ),
            )
    conn.commit()


def apply_stock_delta(conn: sqlite3.Connection, part_id: str, delta: int) -> None:
    """Adjust stock quantity by delta. Floors at zero."""
    conn.execute(
        "UPDATE stock SET quantity = MAX(0, quantity + ?) WHERE part_id = ?",
        (delta, part_id),
    )
    conn.commit()


def set_stock_quantity(conn: sqlite3.Connection, part_id: str, quantity: int) -> None:
    """Set stock quantity to an absolute value."""
    conn.execute(
        "UPDATE stock SET quantity = MAX(0, ?) WHERE part_id = ?",
        (quantity, part_id),
    )
    conn.commit()


def upsert_part(
    conn: sqlite3.Connection,
    part_id: str,
    row: dict[str, str],
    section: str = "",
) -> None:
    """Insert or update a part and its stock from a CSV-column-named dict."""
    sk = sort_key_for_section(section, (row.get("Description") or "").strip())
    conn.execute(
        """INSERT INTO parts
           (part_id, lcsc, mpn, digikey, pololu, mouser,
            manufacturer, description, package, rohs, section, sort_key, date_code)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(part_id) DO UPDATE SET
            lcsc=excluded.lcsc, mpn=excluded.mpn, digikey=excluded.digikey,
            pololu=excluded.pololu, mouser=excluded.mouser,
            manufacturer=excluded.manufacturer, description=excluded.description,
            package=excluded.package, rohs=excluded.rohs, section=excluded.section,
            sort_key=excluded.sort_key, date_code=excluded.date_code""",
        (
            part_id,
            (row.get("LCSC Part Number") or "").strip(),
            (row.get("Manufacture Part Number") or "").strip(),
            (row.get("Digikey Part Number") or "").strip(),
            (row.get("Pololu Part Number") or "").strip(),
            (row.get("Mouser Part Number") or "").strip(),
            (row.get("Manufacturer") or "").strip(),
            (row.get("Description") or "").strip(),
            (row.get("Package") or "").strip(),
            (row.get("RoHS") or "").strip(),
            section,
            sk,
            (row.get("Date Code / Lot No.") or "").strip(),
        ),
    )
    conn.execute(
        """INSERT INTO stock (part_id, quantity, unit_price, ext_price)
           VALUES (?,?,?,?)
           ON CONFLICT(part_id) DO UPDATE SET
            quantity=excluded.quantity, unit_price=excluded.unit_price,
            ext_price=excluded.ext_price""",
        (
            part_id,
            parse_qty(row.get("Quantity")),
            parse_price(row.get("Unit Price($)")),
            parse_price(row.get("Ext.Price($)")),
        ),
    )
    conn.commit()


def update_stock_price(
    conn: sqlite3.Connection,
    part_id: str,
    unit_price: float,
    ext_price: float,
) -> None:
    """Update price fields for a part's stock entry."""
    conn.execute(
        "UPDATE stock SET unit_price = ?, ext_price = ? WHERE part_id = ?",
        (unit_price, ext_price, part_id),
    )
    conn.commit()


def write_checkpoint(
    conn: sqlite3.Connection,
    purchase_lines: int,
    adjustment_lines: int,
) -> None:
    """Record how many CSV data lines the cache has processed."""
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('purchase_lines', ?)",
        (str(purchase_lines),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('adjustment_lines', ?)",
        (str(adjustment_lines),),
    )
    conn.commit()


def read_checkpoint(conn: sqlite3.Connection) -> dict[str, int]:
    """Read the checkpoint. Returns zeros if no checkpoint exists."""
    result = {"purchase_lines": 0, "adjustment_lines": 0}
    for key in ("purchase_lines", "adjustment_lines"):
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?", (key,)
        ).fetchone()
        if row:
            result[key] = int(row[0])
    return result


def count_csv_data_lines(csv_path: str) -> int:
    """Count data lines in a CSV (excludes header). Returns 0 if file missing."""
    if not os.path.exists(csv_path):
        return 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def catch_up(
    conn: sqlite3.Connection,
    purchase_path: str,
    adjustments_path: str,
    adj_fieldnames: list[str],
) -> bool:
    """Replay only events added since the last checkpoint.

    Returns True if catch-up succeeded, False if a full rebuild is needed
    (e.g., purchase ledger changed — new parts require recategorization).
    """
    cp = read_checkpoint(conn)

    # If purchase ledger changed, catch-up can't handle it — need full rebuild
    purchase_total = count_csv_data_lines(purchase_path)
    if purchase_total != cp["purchase_lines"]:
        logger.info("Purchase ledger changed (%d -> %d lines), full rebuild needed",
                     cp["purchase_lines"], purchase_total)
        return False

    # Catch up on new adjustments
    adj_total = count_csv_data_lines(adjustments_path)
    adj_seen = cp["adjustment_lines"]
    if adj_total > adj_seen and os.path.exists(adjustments_path):
        with open(adjustments_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i < adj_seen:
                    continue  # skip already-processed rows
                adj_type = (row.get("type") or "").strip()
                pn = (row.get("lcsc_part") or "").strip()
                if not pn or not adj_type:
                    continue
                try:
                    qty = int(float(row.get("quantity", "0")))
                except ValueError:
                    continue
                new_qty = compute_adjusted_qty(0, adj_type, qty)
                if new_qty is None:
                    continue
                if adj_type == "set":
                    set_stock_quantity(conn, pn, new_qty)
                else:
                    apply_stock_delta(conn, pn, qty)
        logger.info("Cache catch-up: replayed %d new adjustments", adj_total - adj_seen)

    # Update checkpoint
    write_checkpoint(conn, purchase_lines=purchase_total, adjustment_lines=adj_total)
    return True


def verify_parts(
    conn: sqlite3.Connection,
    part_ids: list[str],
    purchase_path: str,
    adjustments_path: str,
    fieldnames: list[str],
    fix: bool = False,
) -> list[dict[str, Any]]:
    """Spot-check: replay events for specific parts and compare to cache.

    Returns list of mismatches: [{"part_id", "cache_qty", "expected_qty"}].
    If fix=True, corrects cache for any mismatched parts.
    """
    _, merged = read_and_merge(purchase_path, fieldnames)
    apply_adjustments(merged, adjustments_path, fieldnames)

    mismatches = []
    for pid in part_ids:
        cache_row = conn.execute(
            "SELECT quantity FROM stock WHERE part_id = ?", (pid,)
        ).fetchone()
        cache_qty = cache_row["quantity"] if cache_row else 0

        expected_qty = parse_qty(merged[pid]["Quantity"]) if pid in merged else 0

        if cache_qty != expected_qty:
            mismatches.append({
                "part_id": pid,
                "cache_qty": cache_qty,
                "expected_qty": expected_qty,
            })
            if fix:
                conn.execute(
                    "UPDATE stock SET quantity = ? WHERE part_id = ?",
                    (expected_qty, pid),
                )
                logger.warning(
                    "Cache mismatch fixed: %s was %d, expected %d",
                    pid, cache_qty, expected_qty,
                )

    if fix and mismatches:
        conn.commit()
    return mismatches


def query_inventory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Query cache in the same format as inventory_ops.load_organized().

    Returns list of dicts with keys: section, lcsc, mpn, digikey, pololu,
    mouser, manufacturer, package, description, qty, unit_price, ext_price.
    """
    rows = conn.execute("""
        SELECT p.section, p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser,
               p.manufacturer, p.package, p.description,
               s.quantity, s.unit_price, s.ext_price
        FROM parts p
        JOIN stock s USING (part_id)
        ORDER BY p.section, p.sort_key NULLS LAST
    """).fetchall()
    return [
        {
            "section": row["section"],
            "lcsc": row["lcsc"],
            "mpn": row["mpn"],
            "digikey": row["digikey"],
            "pololu": row["pololu"],
            "mouser": row["mouser"],
            "manufacturer": row["manufacturer"],
            "package": row["package"],
            "description": row["description"],
            "qty": row["quantity"],
            "unit_price": row["unit_price"],
            "ext_price": row["ext_price"],
        }
        for row in rows
    ]
