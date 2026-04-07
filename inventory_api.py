"""Inventory API — thin facade delegating to csv_io, inventory_ops, price_ops, file_dialogs."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

import cache_db
import csv_io
import file_dialogs
import inventory_ops
import price_ops
from distributor_api import DistributorApi
from distributor_manager import DistributorManager
from generic_parts_api import GenericPartsApi
from price_api import PriceApi

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
        self.output_csv: str = os.path.join(self.base_dir, "inventory.csv")  # legacy, no longer written
        self.adjustments_csv: str = os.path.join(self.base_dir, "adjustments.csv")
        self.prefs_json: str = os.path.join(self.base_dir, "preferences.json")
        self.cache_db_path = os.path.join(self.base_dir, "cache.db")
        self.events_dir: str = os.path.join(self.base_dir, "events")
        self._cache_conn: sqlite3.Connection | None = None
        self._force_close: bool = False
        self._closing: bool = False
        self._bom_dirty: bool = False
        self._debug: bool = debug
        self._lock: threading.Lock = threading.Lock()
        self._dist_api = DistributorApi(
            base_dir=self.base_dir, get_cache=self._get_cache, debug=self._debug,
        )
        self._distributors = self._dist_api._distributors
        self._gp_api = GenericPartsApi(
            get_cache=self._get_cache, events_dir=self.events_dir,
        )
        self._price_api = PriceApi(
            get_cache=self._get_cache, events_dir=self.events_dir,
        )

    def _get_cache(self) -> sqlite3.Connection:
        """Get or create the cache database connection."""
        if self._cache_conn is None:
            self._cache_conn = cache_db.connect(self.cache_db_path)
            cache_db.create_schema(self._cache_conn)
        return self._cache_conn

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

    @staticmethod
    def _infer_distributor(row: dict[str, str]) -> str:
        """Infer distributor from which part number fields are populated."""
        return DistributorManager.infer_distributor(row)

    def _infer_distributor_for_key(self, part_key: str) -> str:
        """Infer distributor from a part key string."""
        return self._distributors.infer_distributor_for_key(part_key)

    # ── Compatibility shims (tests + legacy callers) ──────────────────────

    @property
    def _lcsc(self):
        return self._distributors._lcsc

    @property
    def _digikey(self):
        return self._distributors._digikey

    @property
    def _pololu(self):
        return self._distributors._pololu

    @property
    def _mouser(self):
        return self._distributors._mouser

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

    def _rebuild(self) -> list[dict[str, Any]]:
        """Full rebuild: replay all events into cache, return fresh inventory."""
        conn = self._get_cache()
        file_fieldnames, merged = inventory_ops.read_and_merge(
            self.input_csv, self.FIELDNAMES,
        )
        inventory_ops.apply_adjustments(merged, self.adjustments_csv, file_fieldnames)
        categorized = inventory_ops.categorize_and_sort(list(merged.values()))
        cache_db.populate_full(conn, merged, categorized)
        purchase_lines = cache_db.count_csv_data_lines(self.input_csv)
        adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
        cache_db.write_checkpoint(conn, purchase_lines=purchase_lines,
                                  adjustment_lines=adj_lines)
        import price_history
        if os.path.exists(self.events_dir):
            price_history.populate_prices_cache(conn, self.events_dir)
        import generic_parts
        os.makedirs(self.events_dir, exist_ok=True)
        generic_parts.auto_generate_passive_groups(conn, self.events_dir)
        return cache_db.query_inventory(conn)

    def _load_organized(self) -> list[dict[str, Any]]:
        """Load current inventory from cache."""
        conn = self._get_cache()
        result = cache_db.query_inventory(conn)
        if not result:
            # Cache empty — populate from CSVs
            return self._rebuild()
        return result

    def _append_adjustment(self, adj_type: str, part_key: str, quantity: int,
                           note: str = "", bom_file: str = "",
                           board_qty: int | str = "", source: str = "") -> None:
        inventory_ops.append_adjustment(
            self.adjustments_csv, self.ADJ_FIELDNAMES,
            adj_type, part_key, quantity,
            note=note, bom_file=bom_file, board_qty=board_qty, source=source,
        )

    def _record_import_prices(self, rows: list[dict[str, str]]) -> None:
        """Extract and record price observations from imported purchase rows."""
        import price_history
        os.makedirs(self.events_dir, exist_ok=True)
        observations = []
        for row in rows:
            part_key = inventory_ops.get_part_key(row)
            if not part_key:
                continue
            up = price_ops.parse_price(row.get("Unit Price($)"))
            if up <= 0:
                continue
            distributor = self._infer_distributor(row)
            observations.append({
                "part_id": part_key,
                "distributor": distributor,
                "unit_price": up,
                "source": "import",
            })
        if observations:
            price_history.record_observations(self.events_dir, observations)

    # ── Public API methods (called from JS via pywebview) ────────────────

    def rollback_source(self, source: str) -> list[dict]:
        """Remove all adjustments with the given source tag and rebuild."""
        with self._lock:
            removed = inventory_ops.rollback_source(self.adjustments_csv, source)
            if removed:
                self._rebuild()
        return removed

    def rebuild_inventory(self) -> list[dict[str, Any]]:
        """Rebuild inventory. Uses catch-up if cache exists, full rebuild otherwise."""
        conn = self._get_cache()
        cp = cache_db.read_checkpoint(conn)
        has_cache = conn.execute("SELECT 1 FROM parts LIMIT 1").fetchone() is not None
        if has_cache and (cp["purchase_lines"] > 0 or cp["adjustment_lines"] > 0):
            # Cache exists — try catch-up (returns False if purchase ledger changed)
            if cache_db.catch_up(conn, self.input_csv, self.adjustments_csv,
                                 self.ADJ_FIELDNAMES):
                return cache_db.query_inventory(conn)
        # No cache, no checkpoint, or catch-up declined — full rebuild
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
            conn = self._get_cache()
            # Check if part exists in cache
            exists = conn.execute(
                "SELECT 1 FROM stock WHERE part_id = ?", (part_key,)
            ).fetchone()
            if not exists:
                # Part not in cache (e.g., "set" on a brand new part) — full rebuild
                return self._rebuild()
            if adj_type == "set":
                cache_db.set_stock_quantity(conn, part_key, max(0, record_qty))
            else:  # add, remove
                cache_db.apply_stock_delta(conn, part_key, record_qty)
            adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
            cp = cache_db.read_checkpoint(conn)
            cache_db.write_checkpoint(conn, purchase_lines=cp["purchase_lines"],
                                      adjustment_lines=adj_lines)
            return cache_db.query_inventory(conn)

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
            conn = self._get_cache()
            affected_parts = [row["lcsc_part"] for row in adj_rows]
            # Check if all affected parts are already in the cache
            all_cached = all(
                conn.execute(
                    "SELECT 1 FROM stock WHERE part_id = ?", (pn,)
                ).fetchone()
                for pn in affected_parts
            )
            if not all_cached:
                # Cache empty or stale — full rebuild includes the new adjustments
                return self._rebuild()
            for row in adj_rows:
                pn = row["lcsc_part"]
                delta = int(row["quantity"])
                cache_db.apply_stock_delta(conn, pn, delta)
            adj_lines = cache_db.count_csv_data_lines(self.adjustments_csv)
            cp = cache_db.read_checkpoint(conn)
            cache_db.write_checkpoint(conn, purchase_lines=cp["purchase_lines"],
                                      adjustment_lines=adj_lines)
            # Spot-check verification on affected parts
            cache_db.verify_parts(
                conn, affected_parts, self.input_csv, self.adjustments_csv,
                self.FIELDNAMES, fix=True,
            )
            return cache_db.query_inventory(conn)

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

            self._record_import_prices(rows)
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
                unit_price, ext_price = price_ops.derive_missing_price(unit_price, ext_price, qty)
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

            # Record price observation
            import price_history
            os.makedirs(self.events_dir, exist_ok=True)
            if unit_price is not None and unit_price > 0:
                price_history.record_observations(self.events_dir, [{
                    "part_id": part_key,
                    "distributor": self._infer_distributor_for_key(part_key),
                    "unit_price": unit_price,
                    "source": "manual",
                }])

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

    # ── Price history API (delegated to PriceApi) ──────────────────────────

    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        """Record prices fetched from a distributor API/scraper."""
        return self._price_api.record_fetched_prices(part_key, distributor, price_tiers)

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        """Get aggregated pricing per distributor for a part."""
        return self._price_api.get_price_summary(part_key)

    # ── Product preview (delegated to DistributorApi) ──────────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        return self._dist_api.fetch_lcsc_product(product_code)

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        return self._dist_api.fetch_digikey_product(part_number)

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        return self._dist_api.fetch_pololu_product(sku)

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        return self._dist_api.fetch_mouser_product(part_number)

    def check_digikey_session(self) -> dict[str, Any]:
        return self._dist_api.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        return self._dist_api.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        return self._dist_api.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        return self._dist_api.get_digikey_login_status()

    def logout_digikey(self) -> dict[str, str]:
        return self._dist_api.logout_digikey()

    # ── Generic parts (delegated to GenericPartsApi) ───────────────────────

    def create_generic_part(self, name: str, part_type: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return self._gp_api.create_generic_part(name, part_type, spec_json, strictness_json)

    def resolve_bom_spec(self, part_type: str, value: float,
                          package: str) -> dict[str, Any] | None:
        return self._gp_api.resolve_bom_spec(part_type, value, package)

    def list_generic_parts(self) -> list[dict[str, Any]]:
        return self._gp_api.list_generic_parts()

    def add_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._gp_api.add_generic_member(generic_part_id, part_id)

    def remove_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._gp_api.remove_generic_member(generic_part_id, part_id)

    def set_preferred_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._gp_api.set_preferred_member(generic_part_id, part_id)

    def update_generic_part(self, generic_part_id: str, name: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return self._gp_api.update_generic_part(generic_part_id, name, spec_json, strictness_json)

    def extract_spec(self, part_key: str) -> dict[str, Any]:
        return self._gp_api.extract_spec(part_key)

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
