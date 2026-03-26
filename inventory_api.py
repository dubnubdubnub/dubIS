"""Inventory API — thin facade delegating to csv_io, inventory_ops, price_ops, file_dialogs."""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

import csv_io
import file_dialogs
import inventory_ops
import price_ops
from digikey_client import DigikeyClient
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient

logger = logging.getLogger(__name__)


def _load_constants() -> dict:
    """Load shared constants from data/constants.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "constants.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_CONSTANTS = _load_constants()


def _parse_section_order(raw: list) -> tuple[list[str], list[dict]]:
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
            # Parent section (for parts that don't match any subcategory)
            flat_order.append(name)
            # Compound sections for each child
            for child in children:
                flat_order.append(f"{name} > {child}")
            hierarchy.append({"name": name, "children": children})
    return flat_order, hierarchy


_FLAT_SECTION_ORDER, _SECTION_HIERARCHY = _parse_section_order(_CONSTANTS["SECTION_ORDER"])


class InventoryApi:
    FIELDNAMES = _CONSTANTS["FIELDNAMES"]

    ADJ_FIELDNAMES = [
        "timestamp", "type", "lcsc_part", "quantity", "bom_file", "board_qty", "note", "source",
    ]

    SECTION_ORDER = _CONSTANTS["SECTION_ORDER"]
    FLAT_SECTION_ORDER = _FLAT_SECTION_ORDER
    SECTION_HIERARCHY = _SECTION_HIERARCHY

    def __init__(self, *, debug: bool = False) -> None:
        self.base_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.input_csv: str = os.path.join(self.base_dir, "purchase_ledger.csv")
        self.output_csv: str = os.path.join(self.base_dir, "inventory.csv")
        self.adjustments_csv: str = os.path.join(self.base_dir, "adjustments.csv")
        self.prefs_json: str = os.path.join(self.base_dir, "preferences.json")
        self._force_close: bool = False
        self._closing: bool = False
        self._bom_dirty: bool = False
        self._debug: bool = debug
        self._lock: threading.Lock = threading.Lock()
        self._lcsc = LcscClient()
        self._digikey = DigikeyClient(
            cookies_file=os.path.join(self.base_dir, "digikey_cookies.json"),
        )
        self._pololu = PololuClient()
        self._mouser = MouserClient()

    # ── Static utility delegates ─────────────────────────────────────────

    @staticmethod
    def _parse_qty(value: Any, default: int = 0) -> int:
        return price_ops.parse_qty(value, default)

    @staticmethod
    def _parse_price(value: Any, default: float = 0.0) -> float:
        return price_ops.parse_price(value, default)

    @staticmethod
    def _ensure_parsed(value: str | Any) -> Any:
        return price_ops.ensure_parsed(value)

    @staticmethod
    def fix_double_utf8(text: str) -> str:
        return csv_io.fix_double_utf8(text)

    @staticmethod
    def get_part_key(row: dict[str, str]) -> str:
        return inventory_ops.get_part_key(row)

    @staticmethod
    def _read_text(path: str) -> str:
        return csv_io.read_text(path)

    @staticmethod
    def _migrate_csv_header(path: str, expected_fieldnames: list[str]) -> None:
        return csv_io.migrate_csv_header(path, expected_fieldnames)

    # Map JS field names to CSV column names (delegate to inventory_ops)
    _FIELD_TO_COL = inventory_ops._FIELD_TO_COL

    # ── Core pipeline delegates ──────────────────────────────────────────

    def _append_csv_rows(self, path: str, fieldnames: list[str],
                         rows: list[dict[str, Any]]) -> None:
        csv_io.append_csv_rows(path, fieldnames, rows)

    def _read_raw_inventory(self) -> tuple[list[str], dict[str, dict[str, str]]]:
        return inventory_ops.read_and_merge(self.input_csv, self.FIELDNAMES)

    def _apply_adjustments(self, merged: dict[str, dict[str, str]],
                           fieldnames: list[str]) -> None:
        inventory_ops.apply_adjustments(merged, self.adjustments_csv, fieldnames)

    def _categorize_and_sort(self, parts: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
        return inventory_ops.categorize_and_sort(parts)

    def _write_organized(self, categorized: dict[str, list[dict[str, str]]],
                         fieldnames: list[str]) -> None:
        inventory_ops.write_organized(categorized, self.output_csv, fieldnames, self.FLAT_SECTION_ORDER)

    def _rebuild(self) -> list[dict[str, Any]]:
        return inventory_ops.rebuild(
            self.input_csv, self.adjustments_csv, self.output_csv,
            self.FIELDNAMES, self.FLAT_SECTION_ORDER,
        )

    def _load_organized(self) -> list[dict[str, Any]]:
        return inventory_ops.load_organized(self.output_csv)

    def _append_adjustment(self, adj_type: str, part_key: str, quantity: int,
                           note: str = "", bom_file: str = "",
                           board_qty: int | str = "", source: str = "") -> None:
        inventory_ops.append_adjustment(
            self.adjustments_csv, self.ADJ_FIELDNAMES,
            adj_type, part_key, quantity,
            note=note, bom_file=bom_file, board_qty=board_qty, source=source,
        )

    # ── Public API methods (called from JS via pywebview) ────────────────

    def rollback_source(self, source: str) -> list[dict]:
        """Remove all adjustments with the given source tag and rebuild."""
        with self._lock:
            removed = inventory_ops.rollback_source(self.adjustments_csv, source)
            if removed:
                self._rebuild()
        return removed

    def rebuild_inventory(self) -> list[dict[str, Any]]:
        """Force full rebuild of inventory.csv from purchase_ledger + adjustments."""
        return self._rebuild()

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        """Set/add/remove adjustment. Returns fresh inventory."""
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
        with self._lock:
            self._append_adjustment(adj_type, part_key, record_qty, note=note, source=source)
            return self._rebuild()

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        """Consume matched BOM parts. Returns fresh inventory."""
        matches = self._ensure_parsed(matches_json)
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
        with self._lock:
            csv_io.append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, adj_rows)
            return self._rebuild()

    def _truncate_csv(self, csv_path: str, count: int, label: str) -> list[dict[str, Any]]:
        """Remove the last *count* rows from a CSV and rebuild inventory."""
        fieldnames, rows = inventory_ops.truncate_csv(csv_path, count, label)

        with self._lock:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            return self._rebuild()

    def remove_last_purchases(self, count: int | str) -> list[dict[str, Any]]:
        """Remove the last `count` rows from purchase_ledger.csv and rebuild inventory."""
        return self._truncate_csv(self.input_csv, int(count), "purchase ledger")

    def remove_last_adjustments(self, count: int | str) -> list[dict[str, Any]]:
        """Remove the last `count` rows from adjustments.csv and rebuild inventory."""
        return self._truncate_csv(self.adjustments_csv, int(count), "adjustments")

    def import_purchases(self, rows_json: str | list[dict[str, str]]) -> list[dict[str, Any]]:
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory."""
        rows = self._ensure_parsed(rows_json)
        if not rows:
            raise ValueError("No rows to import")

        # Read existing fieldnames or use defaults
        if os.path.exists(self.input_csv):
            with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
        else:
            fieldnames = list(self.FIELDNAMES)

        # Append new rows
        with self._lock:
            write_header = not os.path.exists(self.input_csv) or os.path.getsize(self.input_csv) == 0
            with open(self.input_csv, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                for row in rows:
                    inv_row = {fn: row.get(fn, "") for fn in fieldnames}
                    writer.writerow(inv_row)

            return self._rebuild()

    def update_part_price(self, part_key: str, unit_price: float | None = None,
                          ext_price: float | None = None) -> list[dict[str, Any]]:
        """Update unit price and ext price for a part in purchase_ledger.csv.
        Auto-calculates the missing price field if only one is provided.
        Returns fresh inventory after rebuild.
        """
        if unit_price is not None:
            unit_price = float(unit_price)
        if ext_price is not None:
            ext_price = float(ext_price)

        if not os.path.exists(self.input_csv):
            raise ValueError("No purchase ledger found")

        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        found = False
        for row in rows:
            pk = inventory_ops.get_part_key(row)
            if pk == part_key:
                qty = price_ops.parse_qty(row.get("Quantity"))
                if unit_price is not None and ext_price is None and qty > 0:
                    ext_price = unit_price * qty
                elif ext_price is not None and unit_price is None and qty > 0:
                    unit_price = ext_price / qty
                if unit_price is not None:
                    row["Unit Price($)"] = f"{unit_price:.4f}"
                if ext_price is not None:
                    row["Ext.Price($)"] = f"{ext_price:.2f}"
                found = True

        if not found:
            # Part only exists via adjustments -- add a new ledger row with price info
            new_row = {fn: "" for fn in fieldnames}
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

        with self._lock:
            with open(self.input_csv, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            return self._rebuild()

    def update_part_fields(self, part_key: str,
                           fields_json: str | dict[str, str]) -> list[dict[str, Any]]:
        """Update metadata fields for a part in purchase_ledger.csv."""
        fields = self._ensure_parsed(fields_json)
        if not fields:
            raise ValueError("No fields to update")

        if not os.path.exists(self.input_csv):
            raise ValueError("No purchase ledger found")

        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        found = False
        for row in rows:
            pk = inventory_ops.get_part_key(row)
            if pk == part_key:
                for js_name, value in fields.items():
                    col = self._FIELD_TO_COL.get(js_name)
                    if col and col in fieldnames:
                        row[col] = value
                found = True

        if not found:
            raise ValueError(f"Part {part_key!r} not found in purchase ledger")

        with self._lock:
            with open(self.input_csv, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            return self._rebuild()

    def detect_columns(self, headers_json: str | list[str]) -> dict[str, str]:
        """Auto-detect column mapping for purchase CSV import."""
        return file_dialogs.detect_columns(headers_json)

    def load_preferences(self) -> dict[str, Any]:
        """Read preferences.json and return its contents (empty dict if missing/corrupt)."""
        try:
            if os.path.exists(self.prefs_json):
                with open(self.prefs_json, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load preferences: %s", exc)
        return {}

    def save_preferences(self, prefs_json: str | dict[str, Any]) -> None:
        """Write preferences JSON string to disk."""
        prefs = self._ensure_parsed(prefs_json)
        with open(self.prefs_json, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)

    def save_file_dialog(self, content: str, default_name: str = "export.csv",
                         default_dir: str | None = None,
                         links_json: str | list | None = None) -> dict[str, str] | None:
        """Open native Save As dialog and write content to the chosen path."""
        return file_dialogs.save_file_dialog(content, default_name, default_dir, links_json)

    def convert_xls_to_csv(self, path: str) -> dict[str, Any] | None:
        """Convert a binary XLS file to CSV text for the import panel."""
        return csv_io.convert_xls_to_csv(path)

    def open_file_dialog(self, title: str = "Select CSV file",
                         default_dir: str | None = None) -> dict[str, Any] | None:
        """Open native file dialog, return {name, content, directory, path} or None."""
        return file_dialogs.open_file_dialog(title, default_dir)

    def load_file(self, path: str) -> dict[str, Any] | None:
        """Load a file by path, return {name, content, directory, path, links?} or None."""
        return file_dialogs.load_file(path)

    # ── Product preview (delegated to client modules) ─────────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        """Delegate to LcscClient."""
        result = self._lcsc.fetch_product(product_code)
        if result and not self._debug:
            result.pop("_debug", None)
        return result

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        """Delegate to DigikeyClient."""
        result = self._digikey.fetch_product(part_number)
        if result and not self._debug:
            result.pop("_debug", None)
        return result

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        """Delegate to PololuClient."""
        result = self._pololu.fetch_product(sku)
        if result and not self._debug:
            result.pop("_debug", None)
        return result

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        """Delegate to MouserClient."""
        result = self._mouser.fetch_product(part_number)
        if result and not self._debug:
            result.pop("_debug", None)
        return result

    def check_digikey_session(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.check_session()

    def start_digikey_login(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.start_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        """Delegate to DigikeyClient."""
        return self._digikey.sync_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        """Delegate to DigikeyClient."""
        return self._digikey.get_login_status()

    def logout_digikey(self) -> dict[str, str]:
        """Delegate to DigikeyClient."""
        return self._digikey.logout()

    # ── Window lifecycle ─────────────────────────────────────────────────

    def set_bom_dirty(self, dirty) -> None:
        """Track BOM dirty state so on_closing can check without evaluate_js."""
        self._bom_dirty = bool(dirty)

    def confirm_close(self) -> None:
        """Set force-close flag and destroy the window."""
        if self._closing:
            return
        self._closing = True
        import webview
        self._force_close = True
        try:
            webview.windows[0].destroy()
        except (IndexError, RuntimeError, AttributeError):
            logger.debug("Window already destroyed or unavailable", exc_info=True)
