"""SQLite cache layer for inventory data.

The cache is a derived, deletable materialized view of the CSV event logs.
Delete cache.db at any time — it will be rebuilt from purchase_ledger.csv
and adjustments.csv on next startup.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sqlite3
from typing import Any

from domain.pricing import parse_price, parse_qty
from inventory_ops import apply_adjustments, compute_adjusted_qty, get_part_key, read_and_merge, sort_key_for_section

SCHEMA_VERSION = "7"

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
    # Idempotent column migrations: add columns to parts if they exist but lack them
    parts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='parts'"
    ).fetchone()
    if parts_exists:
        for col_ddl in (
            "ALTER TABLE parts ADD COLUMN primary_vendor_id TEXT DEFAULT ''",
            "ALTER TABLE parts ADD COLUMN po_history TEXT DEFAULT ''",
        ):
            try:
                conn.execute(col_ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
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
            primary_vendor_id TEXT DEFAULT '',
            po_history    TEXT DEFAULT ''
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
    *,
    ledger_path: str | None = None,
    po_csv_path: str | None = None,
    vendors_json_path: str | None = None,
) -> None:
    """Full population from merge + categorize results. Clears existing data."""
    # generic_part_members.part_id has a NOT NULL REFERENCES parts(part_id) FK.
    # DELETE FROM parts would fail mid-transaction with foreign_keys=ON; defer
    # the check to commit time so we can repopulate parts before validation.
    conn.execute("BEGIN")
    try:
        conn.execute("PRAGMA defer_foreign_keys = ON")
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
    except Exception:
        conn.rollback()
        raise

    # Build part_id → primary_vendor_id lookup and po_history per part
    primary_vendor: dict[str, str] = {}
    po_history_for_part: dict[str, list[str]] = {}

    if ledger_path and po_csv_path and os.path.isfile(po_csv_path):
        # part_id → most-recent po_id (latest order wins) + all po_ids in order
        po_id_for_part: dict[str, str] = {}
        if os.path.isfile(ledger_path):
            with open(ledger_path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    pk = get_part_key(row)
                    poid = (row.get("po_id") or "").strip()
                    if pk and poid:
                        po_id_for_part[pk] = poid  # last write wins (chronological)
                        # Track all po_ids for this part in chronological order
                        if pk not in po_history_for_part:
                            po_history_for_part[pk] = []
                        if poid not in po_history_for_part[pk]:
                            po_history_for_part[pk].append(poid)

        # po_id → vendor_id
        po_to_vendor: dict[str, str] = {}
        with open(po_csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                po_to_vendor[row["po_id"]] = row["vendor_id"]

        for pk, poid in po_id_for_part.items():
            v = po_to_vendor.get(poid)
            if v:
                primary_vendor[pk] = v

    # Fall back to inferred vendor by manufacturer name
    if vendors_json_path and os.path.isfile(vendors_json_path):
        import json
        with open(vendors_json_path, encoding="utf-8") as f:
            vendors_data = json.load(f)
        # name → id (case-insensitive)
        mfg_to_vendor = {v["name"].lower(): v["id"] for v in vendors_data
                         if v.get("type") in ("inferred", "real")}
        unknown_id = "v_unknown"
        for part in merged.values():
            pk = get_part_key(part)
            if pk and pk not in primary_vendor:
                mfg = (part.get("Manufacturer") or "").strip().lower()
                primary_vendor[pk] = mfg_to_vendor.get(mfg, unknown_id)

    # Update parts rows with primary_vendor_id
    for pk, vid in primary_vendor.items():
        conn.execute(
            "UPDATE parts SET primary_vendor_id=? WHERE part_id=?",
            (vid, pk),
        )

    # Update parts rows with po_history
    import json as _json
    for pk, po_ids in po_history_for_part.items():
        conn.execute(
            "UPDATE parts SET po_history=? WHERE part_id=?",
            (_json.dumps(po_ids), pk),
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


def _file_hash(path: str) -> str:
    """Return the sha256 hex digest of a file's bytes, or "" if missing."""
    if not os.path.exists(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_adjustment_rows(rows: list[dict[str, str]]) -> str:
    """Canonically hash a list of adjustment rows so the same rows always
    produce the same digest regardless of dict key ordering.

    Serializes each row as a list of values keyed by the row's sorted field
    names, then sha256-hashes the JSON of the whole list.  Used both when
    writing the checkpoint and when verifying the already-processed prefix in
    catch_up, so the two computations must stay identical.
    """
    canonical = [[row.get(fn, "") for fn in sorted(row)] for row in rows]
    payload = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_adjustment_rows(path: str) -> list[dict[str, str]]:
    """Parse all adjustment data rows via DictReader (handles quoted newlines)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_checkpoint(
    conn: sqlite3.Connection,
    *,
    purchase_path: str,
    adjustments_path: str,
) -> None:
    """Record content hashes of the source CSVs the cache has fully processed.

    Stores the purchase ledger's content hash, the number of adjustment data
    rows, and a canonical hash over ALL current adjustment rows (after a full
    write/append+apply every row is processed, so the full set is the
    processed prefix).
    """
    rows = _read_adjustment_rows(adjustments_path)
    meta = {
        "purchase_hash": _file_hash(purchase_path),
        "adjustment_count": str(len(rows)),
        "adjustment_prefix_hash": _hash_adjustment_rows(rows),
    }
    for key, value in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
    conn.commit()


def read_checkpoint(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read the checkpoint.

    Returns ``{"purchase_hash": str, "adjustment_count": int,
    "adjustment_prefix_hash": str}``.  Missing keys default to ""/0.  Old
    caches (which stored only purchase_lines/adjustment_lines) therefore come
    back with empty hashes, which makes catch_up trigger a one-time full
    rebuild that writes the new-format checkpoint.
    """
    result: dict[str, Any] = {
        "purchase_hash": "",
        "adjustment_count": 0,
        "adjustment_prefix_hash": "",
    }
    for key in ("purchase_hash", "adjustment_prefix_hash"):
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?", (key,)
        ).fetchone()
        if row:
            result[key] = row[0]
    count_row = conn.execute(
        "SELECT value FROM cache_meta WHERE key = ?", ("adjustment_count",)
    ).fetchone()
    if count_row:
        result["adjustment_count"] = int(count_row[0])
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

    # If the purchase ledger content changed at all (append OR in-place edit),
    # catch-up can't handle it — need a full rebuild.  A content hash catches
    # same-row-count edits that a line count would miss.
    if _file_hash(purchase_path) != cp["purchase_hash"]:
        logger.info("Purchase ledger content changed, full rebuild needed")
        return False

    # The already-processed region of adjustments.csv must be byte-for-byte the
    # same as when we checkpointed.  If it was edited, reordered, or had rows
    # removed (e.g. a test/source rollback), the ordinal replay would diverge —
    # force a full rebuild instead.
    rows = _read_adjustment_rows(adjustments_path)
    adj_seen = cp["adjustment_count"]
    if _hash_adjustment_rows(rows[:adj_seen]) != cp["adjustment_prefix_hash"]:
        logger.info(
            "Already-processed adjustments changed (edit/reorder/removal), "
            "full rebuild needed")
        return False

    # Replay only the new adjustment rows using the existing apply logic.
    new_rows = rows[adj_seen:]
    if new_rows:
        for row in new_rows:
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
        logger.info("Cache catch-up: replayed %d new adjustments", len(new_rows))

    # Update checkpoint
    write_checkpoint(conn, purchase_path=purchase_path,
                     adjustments_path=adjustments_path)
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
    mouser, manufacturer, package, description, qty, unit_price, ext_price,
    primary_vendor_id, po_history.
    """
    import json as _json
    rows = conn.execute("""
        SELECT p.section, p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser,
               p.manufacturer, p.package, p.description, p.primary_vendor_id,
               p.po_history,
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
            "primary_vendor_id": (row["primary_vendor_id"] or ""),
            "po_history": _json.loads(row["po_history"]) if row["po_history"] else [],
        }
        for row in rows
    ]
