"""SQLite cache layer for inventory data.

The cache is a derived, deletable materialized view of the CSV event logs.
Delete cache.db at any time — it will be rebuilt from purchase_ledger.csv
and adjustments.csv on next startup.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from inventory_ops import get_part_key, sort_key_for_section
from price_ops import parse_price, parse_qty

SCHEMA_VERSION = "1"


def connect(db_path: str) -> sqlite3.Connection:
    """Open or create the cache database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create cache tables if they don't exist."""
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
            date_code     TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stock (
            part_id     TEXT PRIMARY KEY REFERENCES parts(part_id),
            quantity    INTEGER NOT NULL DEFAULT 0,
            unit_price  REAL NOT NULL DEFAULT 0.0,
            ext_price   REAL NOT NULL DEFAULT 0.0
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
                    manufacturer, description, package, rohs, section, sort_key, date_code)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
