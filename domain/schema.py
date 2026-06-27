"""Inventory record schema — single source of truth for the InventoryItem record.

This module has ZERO imports from cache_db, inventory_ops, or any other
backend module so it can be imported by both the runtime and the code-generation
script (scripts/gen-inventory-types.py) without cycles.

Phase-1 role: defines INVENTORY_FIELDS (the to_js surface), PARTS_INTERNAL_COLS
(stored-but-not-projected columns), and the InventoryItem TypedDict for Python
type annotations.  cache_db.query_inventory, inventory_ops.load_organized, and
the SQLite DDL are NOT yet derived from this module — that is Phase 3.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict


@dataclass(frozen=True)
class FieldDef:
    """Descriptor for one field in the inventory record that crosses the Python→JS bridge."""

    py_key: str
    """JS/dict key, e.g. 'qty' (differs from sql_col for the quantity field)."""

    sql_col: str
    """SQLite column name, e.g. 'quantity' (note rename for qty)."""

    csv_col: str | None
    """purchase_ledger.csv column header, or None if derived/computed."""

    table: str
    """'parts' | 'stock' — which SQLite table holds this column."""

    sql_ddl: str | None
    """Column DDL fragment for CREATE TABLE, or None for stock-managed columns."""

    ts_type: str
    """TypeScript type string: 'string' | 'number' | 'string[]'."""

    default: Any
    """Python default value."""

    decode: Callable[[Any], Any] = field(default=lambda v: v, compare=False)
    """How to turn the raw SQLite Row value into the JS-facing Python value.
    Present for Phase-3 reuse; unused in Phase 1."""

    to_js: bool = True
    """Whether query_inventory projects this field to JS.
    rohs / date_code / sort_key are stored but NOT sent to the frontend."""


# ── Authoritative ordered field list ──────────────────────────────────────────
#
# This is the ONLY place the to_js inventory record is enumerated.
# Matches cache_db.query_inventory's output exactly (cache_db.py:595-611).
#
INVENTORY_FIELDS: list[FieldDef] = [
    FieldDef(
        py_key="section",
        sql_col="section",
        csv_col=None,
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="lcsc",
        sql_col="lcsc",
        csv_col="LCSC Part Number",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="mpn",
        sql_col="mpn",
        csv_col="Manufacture Part Number",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="digikey",
        sql_col="digikey",
        csv_col="Digikey Part Number",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="pololu",
        sql_col="pololu",
        csv_col="Pololu Part Number",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="mouser",
        sql_col="mouser",
        csv_col="Mouser Part Number",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="manufacturer",
        sql_col="manufacturer",
        csv_col="Manufacturer",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="package",
        sql_col="package",
        csv_col="Package",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="description",
        sql_col="description",
        csv_col="Description",
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
    ),
    FieldDef(
        py_key="qty",
        sql_col="quantity",
        csv_col="Quantity",
        table="stock",
        sql_ddl=None,  # stock table column, not parts DDL
        ts_type="number",
        default=0,
    ),
    FieldDef(
        py_key="unit_price",
        sql_col="unit_price",
        csv_col="Unit Price($)",
        table="stock",
        sql_ddl=None,
        ts_type="number",
        default=0.0,
    ),
    FieldDef(
        py_key="ext_price",
        sql_col="ext_price",
        csv_col="Ext.Price($)",
        table="stock",
        sql_ddl=None,
        ts_type="number",
        default=0.0,
    ),
    FieldDef(
        py_key="primary_vendor_id",
        sql_col="primary_vendor_id",
        csv_col=None,
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string",
        default="",
        decode=lambda v: v or "",
    ),
    FieldDef(
        py_key="po_history",
        sql_col="po_history",
        csv_col=None,
        table="parts",
        sql_ddl="TEXT DEFAULT ''",
        ts_type="string[]",
        default=[],
        decode=lambda v: _json.loads(v) if v else [],
    ),
]

# ── Stored-but-not-projected columns ──────────────────────────────────────────
#
# These exist in the SQLite `parts` table but are intentionally excluded from
# query_inventory's SELECT and therefore absent from the JS record.
# Kept here so the full DDL surface is documented in one place.
#
PARTS_INTERNAL_COLS: list[tuple[str, str]] = [
    ("rohs",      "TEXT DEFAULT ''"),
    ("date_code", "TEXT DEFAULT ''"),
    ("sort_key",  "REAL"),
]


# ── Python type annotation ────────────────────────────────────────────────────

class InventoryItem(TypedDict):
    """The inventory record as it crosses the Python → JS bridge.

    Matches cache_db.query_inventory's output (cache_db.py:595-611) exactly.
    Used for Python return-type annotations; erased at runtime.
    """

    section: str
    lcsc: str
    mpn: str
    digikey: str
    pololu: str
    mouser: str
    manufacturer: str
    package: str
    description: str
    qty: int
    unit_price: float
    ext_price: float
    primary_vendor_id: str
    po_history: list[str]


class PartHistoryEntry(TypedDict):
    """One adjustment entry as it crosses the Python → JS bridge.

    Returned by get_part_history(); erased at runtime.
    """

    timestamp: str
    kind: str
    qty_delta: int
    source: str
    note: str
