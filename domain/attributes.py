"""Part attribute store — durable per-part parametrics fetched from distributors.

Every distributor client already returns parametric attributes on
`NormalizedProduct.attributes`; before this module they were fetched, shown in
the product preview, and dropped. Mechanically judging an alternate part is
mostly numeric predicate work ("min VCCA <= 0.9 V", "resolution >= 13 bit"),
which is impossible without the parametrics stored per part — that is what
this module persists.

Storage follows the entity-store convention (docs/entity-store.md):

* **Durable file** `data/part_attributes.csv` — one row per
  (part_id, canonical_name, distributor), rewritten atomically through
  `csv_io.atomic_write_rows`. Re-fetching a part *updates* its rows instead of
  appending, so the file stays one-row-per-fact rather than a growing log.
  Unlike `events/price_observations.csv` (a genuine time series — prices move)
  a parametric is a property of the part: the useful record is the current
  value plus when it was last seen, not every sighting of it.
* **SQLite table** `part_attributes` in `cache_db.create_schema` — a derived,
  deletable mirror, repopulated from the CSV by `load_into_db` (called from
  `domain.inventory.rebuild`).

Each row keeps the value three ways: `raw_value` exactly as the distributor
published it, the parsed `value_min`/`value_max`/`unit` when
`domain.attribute_parse` could read a magnitude safely, and `kind` saying
which of those applies. `distributor` records who published the number, so a
reader can prefer DigiKey's parseable parametrics over LCSC's free text, and
`observed_at` makes staleness visible.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

import csv_io
from domain.attribute_parse import KIND_EMPTY, canonical_name, parse_value
from domain.pricing import resolve_part_key

logger = logging.getLogger(__name__)

CSV_FILE = "part_attributes.csv"

FIELDNAMES = [
    "part_id",         # inventory part_id (or the raw fetch key if unresolvable)
    "name",            # attribute name exactly as the distributor published it
    "canonical_name",  # cross-distributor key (raw normalized name if unmapped)
    "distributor",     # who published this value: lcsc / digikey / mouser / pololu
    "raw_value",       # the published value string, never rewritten
    "kind",            # scalar | range | tolerance | unparsed
    "value_min",       # parsed magnitude in `unit` ("" when unparsed)
    "value_max",       # == value_min for scalars; both endpoints for ranges
    "unit",            # SI base unit for prefixed inputs ("100nF" -> "F")
    "qualifier",       # measurement condition after "@" ("600mV@1A" -> "1A")
    "observed_at",     # local ISO timestamp of the fetch that wrote this row
    "source",          # how it was obtained (matches price observations' `source`)
]

DEFAULT_SOURCE = "live_fetch"

# (part_id, canonical_name, distributor) — the upsert key.
_KEY_FIELDS = ("part_id", "canonical_name", "distributor")


def csv_path(data_dir: str) -> str:
    return os.path.join(data_dir, CSV_FILE)


def _format_number(value: float | None) -> str:
    """Render a parsed magnitude compactly ("1e-07", not "1.0000000000000001e-07")."""
    return "" if value is None else f"{value:.10g}"


def _parse_number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return tuple((row.get(f) or "") for f in _KEY_FIELDS)  # type: ignore[return-value]


def build_rows(
    part_id: str,
    distributor: str,
    attributes: list[dict[str, Any]] | None,
    *,
    source: str = DEFAULT_SOURCE,
    observed_at: str | None = None,
) -> list[dict[str, str]]:
    """Turn a product's `attributes` list into storable rows.

    Values the distributor published as absent ("-", "") are dropped — a row
    saying "unknown" is worse than no row. Duplicate names within one fetch
    collapse onto the last occurrence (same upsert key).
    """
    stamp = observed_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for attribute in attributes or []:
        if not isinstance(attribute, dict):
            continue
        name = str(attribute.get("name") or "").strip()
        raw_value = attribute.get("value")
        if not name:
            continue
        parsed = parse_value(raw_value if isinstance(raw_value, str) else str(raw_value or ""))
        if parsed.kind == KIND_EMPTY:
            continue
        row = {
            "part_id": part_id,
            "name": name,
            "canonical_name": canonical_name(name),
            "distributor": distributor,
            "raw_value": parsed.raw.strip(),
            "kind": parsed.kind,
            "value_min": _format_number(parsed.value_min),
            "value_max": _format_number(parsed.value_max),
            "unit": parsed.unit,
            "qualifier": parsed.qualifier,
            "observed_at": stamp,
            "source": source,
        }
        rows[_row_key(row)] = row
    return list(rows.values())


def read_rows(data_dir: str, part_id: str | None = None) -> list[dict[str, str]]:
    """Read the durable CSV, optionally filtered by part_id. Missing file -> []."""
    path = csv_path(data_dir)
    if not os.path.exists(path):
        return []
    csv_io.migrate_csv_header(path, FIELDNAMES)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{k: (row.get(k) or "") for k in FIELDNAMES} for row in csv.DictReader(f)]
    if part_id is not None:
        rows = [r for r in rows if r["part_id"] == part_id]
    return rows


def _write_rows(data_dir: str, rows: list[dict[str, str]]) -> None:
    os.makedirs(data_dir, exist_ok=True)
    normalized = [{k: row.get(k, "") for k in FIELDNAMES} for row in rows]
    csv_io.atomic_write_rows(csv_path(data_dir), FIELDNAMES, normalized, encoding="utf-8")


def record_attributes(
    data_dir: str,
    part_id: str,
    distributor: str,
    attributes: list[dict[str, Any]] | None,
    *,
    source: str = DEFAULT_SOURCE,
    observed_at: str | None = None,
) -> int:
    """Upsert one part's attributes into the durable CSV. Returns rows written.

    Idempotent per (part_id, canonical_name, distributor): re-fetching a part
    refreshes its rows in place (keeping file order) instead of duplicating
    them. The same attribute seen from two distributors is two rows, on
    purpose — that is how a reader tells whose number it is looking at.
    """
    new_rows = build_rows(
        part_id, distributor, attributes, source=source, observed_at=observed_at,
    )
    if not new_rows:
        return 0
    pending = {_row_key(r): r for r in new_rows}
    merged: list[dict[str, str]] = []
    for existing in read_rows(data_dir):
        key = _row_key(existing)
        merged.append(pending.pop(key) if key in pending else existing)
    merged.extend(pending.values())
    _write_rows(data_dir, merged)
    return len(new_rows)


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    """Rebuild the derived `part_attributes` table from the durable CSV.

    Rows whose part_id is not in `parts` are skipped: the table has a foreign
    key onto it, and a candidate part we fetched but never purchased has no
    inventory row. The CSV keeps those rows regardless — the durable store is
    the source of truth, the cache is what the FK constrains.
    """
    conn.execute("DELETE FROM part_attributes")
    rows = read_rows(data_dir)
    if not rows:
        conn.commit()
        return
    known = {r[0] for r in conn.execute("SELECT part_id FROM parts")}
    skipped = 0
    for row in rows:
        if row["part_id"] not in known:
            skipped += 1
            continue
        conn.execute(
            """INSERT OR REPLACE INTO part_attributes
               (part_id, canonical_name, distributor, name, raw_value, kind,
                value_min, value_max, unit, qualifier, observed_at, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["part_id"], row["canonical_name"], row["distributor"], row["name"],
             row["raw_value"], row["kind"], _parse_number(row["value_min"]),
             _parse_number(row["value_max"]), row["unit"], row["qualifier"],
             row["observed_at"], row["source"]),
        )
    if skipped:
        logger.warning(
            "load_into_db: skipped %d attribute row(s) for parts not in inventory", skipped)
    conn.commit()


def record_fetched_attributes(
    conn: sqlite3.Connection,
    data_dir: str,
    part_key: str,
    distributor: str,
    attributes: list[dict[str, Any]] | None,
    *,
    source: str = DEFAULT_SOURCE,
) -> int:
    """Persist attributes from a distributor fetch. Returns rows written.

    `part_key` is whatever the fetch was keyed on (an LCSC code, a DigiKey PN,
    an MPN); it is resolved to the inventory part_id when possible so all
    distributors' rows for one part share a key. Unlike
    `domain.pricing.record_fetched_prices`, an unresolvable key is *kept*
    (stored under the raw key) rather than dropped: an alternate-part candidate
    we have never purchased is exactly the case these parametrics exist for.
    """
    if not attributes:
        return 0
    resolved_key = resolve_part_key(conn, part_key) or part_key
    written = record_attributes(
        data_dir, resolved_key, distributor, attributes, source=source)
    if written:
        load_into_db(conn, data_dir)
    return written


def get_attributes(
    conn: sqlite3.Connection,
    data_dir: str,
    part_key: str,
) -> list[dict[str, Any]]:
    """Stored attributes for a part, by canonical name then distributor, numbers typed."""
    resolved_key = resolve_part_key(conn, part_key) or part_key
    out: list[dict[str, Any]] = []
    for row in read_rows(data_dir, resolved_key):
        out.append({
            "part_id": row["part_id"],
            "name": row["name"],
            "canonical_name": row["canonical_name"],
            "distributor": row["distributor"],
            "raw_value": row["raw_value"],
            "kind": row["kind"],
            "value_min": _parse_number(row["value_min"]),
            "value_max": _parse_number(row["value_max"]),
            "unit": row["unit"],
            "qualifier": row["qualifier"],
            "observed_at": row["observed_at"],
            "source": row["source"],
        })
    out.sort(key=lambda r: (r["canonical_name"], r["distributor"]))
    return out
