"""Inventory API — thin facade delegating to domain.inventory, domain.pricing, file_dialogs."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import sys
import threading
from typing import Any

import cache_db
import csv_io
import domain.inventory
import domain.pricing
import file_dialogs
import generic_parts
import inventory_ops
from distributor_manager import DistributorManager

logger = logging.getLogger(__name__)


def _load_constants() -> dict:
    """Load shared constants from data/constants.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "constants.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_CONSTANTS = _load_constants()

_FLAT_SECTION_ORDER, _SECTION_HIERARCHY = domain.inventory.parse_section_order(
    _CONSTANTS["SECTION_ORDER"]
)


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
        # Reentrant: lock-holding methods (adjust_part, consume_bom, _rebuild,
        # …) call _get_cache(), whose lazy init re-acquires this same lock.
        # A plain Lock would deadlock on the first cache access from those paths.
        self._lock: threading.RLock = threading.RLock()
        self._last_migration_summary: dict[str, int] = {}
        self._distributors = DistributorManager(self.base_dir, self._get_cache)

    def _get_cache(self) -> sqlite3.Connection:
        """Get or create the cache database connection.

        Thread-safe lazy init (double-checked locking against the reentrant
        self._lock): the PnP HTTP server thread and the pywebview UI thread can
        both race into the first cache access. Without this guard, two
        connections would be created (one leaked) and create_schema would run
        twice. self._lock is an RLock, so this is safe even when a lock-holding
        method triggers the very first init.
        """
        if self._cache_conn is None:
            with self._lock:
                if self._cache_conn is None:
                    conn = cache_db.connect(self.cache_db_path)
                    cache_db.create_schema(conn)
                    self._cache_conn = conn  # publish only after fully initialized
        return self._cache_conn

    # ── Utility delegates ──────────────────────────────────────────────────

    @staticmethod
    def _parse_qty(value: Any, default: int = 0) -> int:
        return domain.pricing.parse_qty(value, default)

    @staticmethod
    def _ensure_parsed(value: str | Any) -> Any:
        return domain.pricing.ensure_parsed(value)

    @staticmethod
    def fix_double_utf8(text: str) -> str:
        return csv_io.fix_double_utf8(text)

    @staticmethod
    def get_part_key(row: dict[str, str]) -> str:
        return inventory_ops.get_part_key(row)

    def _infer_distributor_for_key(self, part_key: str) -> str:
        """Infer distributor from a part key string."""
        return self._distributors.infer_distributor_for_key(part_key)

    # Map JS field names to CSV column names (delegate to inventory_ops)
    _FIELD_TO_COL = inventory_ops._FIELD_TO_COL

    # ── Pipeline helpers ───────────────────────────────────────────────────

    def _read_raw_inventory(self) -> tuple[list[str], dict[str, dict[str, str]]]:
        return inventory_ops.read_and_merge(self.input_csv, self.FIELDNAMES)

    def _apply_adjustments(self, merged: dict[str, dict[str, str]],
                           fieldnames: list[str]) -> None:
        inventory_ops.apply_adjustments(merged, self.adjustments_csv, fieldnames)

    def _rebuild(self) -> list[dict[str, Any]]:
        """Full rebuild: replay all events into cache, return fresh inventory."""
        result, migration_summary = domain.inventory.rebuild(
            base_dir=self.base_dir,
            input_csv=self.input_csv,
            adjustments_csv=self.adjustments_csv,
            events_dir=self.events_dir,
            fieldnames=self.FIELDNAMES,
            adj_fieldnames=self.ADJ_FIELDNAMES,
            conn=self._get_cache(),
        )
        self._last_migration_summary = migration_summary
        return result

    def _load_organized(self) -> list[dict[str, Any]]:
        """Load current inventory from cache."""
        result, _ = domain.inventory.load_or_rebuild(
            base_dir=self.base_dir,
            input_csv=self.input_csv,
            adjustments_csv=self.adjustments_csv,
            events_dir=self.events_dir,
            fieldnames=self.FIELDNAMES,
            adj_fieldnames=self.ADJ_FIELDNAMES,
            conn=self._get_cache(),
        )
        return result

    def _record_import_prices(self, rows: list[dict[str, str]]) -> None:
        """Extract and record price observations from imported purchase rows."""
        domain.inventory.record_import_prices(rows, self.events_dir, self._distributors)

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
        result, migration_summary = domain.inventory.rebuild_or_catchup(
            base_dir=self.base_dir,
            input_csv=self.input_csv,
            adjustments_csv=self.adjustments_csv,
            events_dir=self.events_dir,
            fieldnames=self.FIELDNAMES,
            adj_fieldnames=self.ADJ_FIELDNAMES,
            conn=self._get_cache(),
        )
        if migration_summary:
            self._last_migration_summary = migration_summary
        return result

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        """Set/add/remove adjustment. Returns fresh inventory."""
        with self._lock:
            return domain.inventory.adjust_part(
                adj_type=adj_type,
                part_key=part_key,
                quantity=int(quantity),
                note=note,
                source=source,
                adjustments_csv=self.adjustments_csv,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                base_dir=self.base_dir,
                input_csv=self.input_csv,
                events_dir=self.events_dir,
                fieldnames=self.FIELDNAMES,
                conn=self._get_cache(),
            )

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        """Consume matched BOM parts. Returns fresh inventory."""
        matches = self._ensure_parsed(matches_json)
        with self._lock:
            return domain.inventory.consume_bom(
                matches=matches,
                board_qty=int(board_qty),
                bom_name=bom_name,
                note=note,
                source=source,
                adjustments_csv=self.adjustments_csv,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                base_dir=self.base_dir,
                input_csv=self.input_csv,
                events_dir=self.events_dir,
                fieldnames=self.FIELDNAMES,
                conn=self._get_cache(),
            )

    def _truncate_csv(self, csv_path: str, count: int, label: str) -> list[dict[str, Any]]:
        """Remove the last *count* rows from a CSV and rebuild inventory."""
        with self._lock:
            return domain.inventory.truncate_and_rebuild(
                csv_path=csv_path,
                count=count,
                label=label,
                base_dir=self.base_dir,
                input_csv=self.input_csv,
                adjustments_csv=self.adjustments_csv,
                events_dir=self.events_dir,
                fieldnames=self.FIELDNAMES,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                conn=self._get_cache(),
            )

    def remove_last_purchases(self, count: int | str) -> list[dict[str, Any]]:
        """Remove the last `count` rows from purchase_ledger.csv and rebuild inventory."""
        return self._truncate_csv(self.input_csv, int(count), "purchase ledger")

    def remove_last_adjustments(self, count: int | str) -> list[dict[str, Any]]:
        """Remove the last `count` rows from adjustments.csv and rebuild inventory."""
        return self._truncate_csv(self.adjustments_csv, int(count), "adjustments")

    def import_purchases(self, rows_json: str | list[dict[str, str]]) -> list[dict[str, Any]]:
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory."""
        rows = self._ensure_parsed(rows_json)
        with self._lock:
            return domain.inventory.import_purchases(
                rows=rows,
                fieldnames=self.FIELDNAMES,
                input_csv=self.input_csv,
                events_dir=self.events_dir,
                adjustments_csv=self.adjustments_csv,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                base_dir=self.base_dir,
                conn=self._get_cache(),
                distributors=self._distributors,
            )

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
        with self._lock:
            return domain.inventory.update_part_price(
                part_key=part_key,
                unit_price=unit_price,
                ext_price=ext_price,
                input_csv=self.input_csv,
                events_dir=self.events_dir,
                adjustments_csv=self.adjustments_csv,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                base_dir=self.base_dir,
                fieldnames=self.FIELDNAMES,
                conn=self._get_cache(),
                infer_distributor_for_key=self._infer_distributor_for_key,
            )

    def update_part_fields(self, part_key: str,
                           fields_json: str | dict[str, str]) -> list[dict[str, Any]]:
        """Update metadata fields for a part in purchase_ledger.csv."""
        fields = self._ensure_parsed(fields_json)
        with self._lock:
            return domain.inventory.update_part_fields(
                part_key=part_key,
                fields=fields,
                field_to_col=self._FIELD_TO_COL,
                input_csv=self.input_csv,
                adjustments_csv=self.adjustments_csv,
                adj_fieldnames=self.ADJ_FIELDNAMES,
                base_dir=self.base_dir,
                fieldnames=self.FIELDNAMES,
                events_dir=self.events_dir,
                conn=self._get_cache(),
            )

    def detect_columns(self, headers_json: str | list[str]) -> dict[str, str]:
        """Auto-detect column mapping for purchase CSV import."""
        return file_dialogs.detect_columns(headers_json)

    def load_preferences(self) -> dict[str, Any]:
        """Read preferences.json and return its contents (empty dict if missing/corrupt)."""
        try:
            if os.path.exists(self.prefs_json):
                with open(self.prefs_json, encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate saved distributor_filter sets: "other" → "direct"
                if isinstance(data, dict) and isinstance(data.get("distributor_filter"), list):
                    data["distributor_filter"] = [
                        "direct" if d == "other" else d for d in data["distributor_filter"]
                    ]
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load preferences: %s", exc)
        return {}

    def save_preferences(self, prefs_json: str | dict[str, Any]) -> None:
        """Write preferences JSON string to disk."""
        prefs = self._ensure_parsed(prefs_json)
        csv_io.atomic_write_text(
            self.prefs_json, json.dumps(prefs, indent=2), encoding="utf-8",
        )

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

    # ── Price history API ───────────────────────────────────────────────────

    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        """Record prices fetched from a distributor API/scraper."""
        return domain.pricing.record_fetched_prices(
            self._get_cache(), self.events_dir, part_key, distributor, price_tiers,
        )

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        """Get aggregated pricing per distributor for a part."""
        return domain.pricing.get_price_summary(
            self._get_cache(), self.events_dir, part_key,
        )

    # ── Product preview (delegated to DistributorManager) ───────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        return self._distributors.fetch_lcsc_product(product_code, debug=self._debug)

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        return self._distributors.fetch_digikey_product(part_number, debug=self._debug)

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        return self._distributors.fetch_pololu_product(sku, debug=self._debug)

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        return self._distributors.fetch_mouser_product(part_number, debug=self._debug)

    def check_digikey_session(self) -> dict[str, Any]:
        return self._distributors.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        return self._distributors.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        return self._distributors.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        return self._distributors.get_digikey_login_status()

    def validate_digikey_session(self) -> dict[str, Any]:
        return self._distributors.validate_digikey_session()

    def logout_digikey(self) -> dict[str, str]:
        return self._distributors.logout_digikey()

    def get_mouser_api_key_status(self) -> dict[str, bool]:
        return self._distributors.get_mouser_api_key_status()

    def set_mouser_api_key(self, key: str) -> dict[str, bool]:
        return self._distributors.set_mouser_api_key(key)

    def clear_mouser_api_key(self) -> dict[str, bool]:
        return self._distributors.clear_mouser_api_key()

    # ── Poll API ───────────────────────────────────────────────────────────

    def get_poll_api_info(self) -> dict[str, Any]:
        """Return the local poll API URL and active port."""
        import poll_api
        server = getattr(self, "_poll_server", None)
        prefs = self.load_preferences()
        info: dict[str, Any] = {
            "default_port": poll_api.POLL_PORT,
            "configured_port": prefs.get("pollApiPort"),
            "running": server is not None,
        }
        if server is not None:
            host, port = server.server_address
            info["host"] = host
            info["port"] = port
            info["url"] = f"http://{host}:{port}"
        else:
            info["host"] = ""
            info["port"] = None
            info["url"] = ""
        return info

    def set_poll_api_port(self, port: int | str) -> dict[str, Any]:
        """Restart the poll API server on a new port and persist to preferences."""
        import poll_api
        try:
            port_int = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"port must be an integer, got {port!r}") from exc
        if port_int < 1024 or port_int > 65535:
            raise ValueError(f"port out of range (1024-65535): {port_int}")
        poll_api.restart_poll_server(self, port_int)
        prefs = self.load_preferences()
        prefs["pollApiPort"] = port_int
        self.save_preferences(prefs)
        return self.get_poll_api_info()

    # ── Generic parts ──────────────────────────────────────────────────────

    def create_generic_part(self, name: str, part_type: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return generic_parts.create_generic_part_api(
            self._get_cache(), self.events_dir, name, part_type, spec_json, strictness_json,
        )

    def resolve_bom_spec(self, part_type: str, value: float,
                          package: str) -> dict[str, Any] | None:
        return generic_parts.resolve_bom_spec(self._get_cache(), part_type, float(value), package)

    def list_generic_parts(self) -> list[dict[str, Any]]:
        return generic_parts.list_generic_parts_with_member_specs(self._get_cache())

    def add_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return generic_parts.add_member_api(
            self._get_cache(), self.events_dir, generic_part_id, part_id,
        )

    def remove_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return generic_parts.remove_member_api(
            self._get_cache(), self.events_dir, generic_part_id, part_id,
        )

    def exclude_generic_member(self, generic_part_id: str, part_id: str) -> None:
        return generic_parts.exclude_member(
            self._get_cache(), self.events_dir, generic_part_id, part_id,
        )

    def set_preferred_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return generic_parts.set_preferred_api(
            self._get_cache(), self.events_dir, generic_part_id, part_id,
        )

    def update_generic_part(self, generic_part_id: str, name: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return generic_parts.update_generic_part_api(
            self._get_cache(), self.events_dir, generic_part_id, name,
            spec_json, strictness_json,
        )

    def extract_spec(self, part_key: str) -> dict[str, Any]:
        return generic_parts.extract_spec_for_part(self._get_cache(), part_key)

    def extract_spec_from_value(self, part_type: str, value_str: str, package_str: str) -> dict[str, Any]:
        import spec_extractor
        desc = part_type + " " + value_str + " " + package_str
        spec = spec_extractor.extract_spec(desc, package_str)
        spec["type"] = part_type
        return spec

    def list_saved_searches(self, generic_part_id: str) -> list[dict[str, Any]]:
        import saved_searches
        return saved_searches.list_for_group(self._get_cache(), generic_part_id)

    def create_saved_search(self, generic_part_id: str, name: str,
                            tag_state_json: str, search_text: str,
                            frozen_members_json: str) -> dict[str, Any]:
        import json

        import saved_searches
        tag_state = json.loads(tag_state_json) if isinstance(tag_state_json, str) else tag_state_json
        frozen = json.loads(frozen_members_json) if isinstance(frozen_members_json, str) else frozen_members_json
        return saved_searches.create(
            self._get_cache(), self.base_dir, generic_part_id, name,
            tag_state, search_text, frozen)

    def delete_saved_search(self, search_id: str) -> None:
        import saved_searches
        saved_searches.delete(self._get_cache(), self.base_dir, search_id)

    # ── Mfg-direct vendors / POs ─────────────────────────────────────────

    @property
    def _vendors_json(self) -> str:
        return os.path.join(self.base_dir, "vendors.json")

    @property
    def _po_csv(self) -> str:
        return os.path.join(self.base_dir, "purchase_orders.csv")

    @property
    def _sources_dir(self) -> str:
        d = os.path.join(self.base_dir, "sources")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def _favicons_dir(self) -> str:
        d = os.path.join(self._sources_dir, "favicons")
        os.makedirs(d, exist_ok=True)
        return d

    def list_vendors(self) -> list[dict[str, Any]]:
        """Return all vendors. Seeds built-ins on first call."""
        import vendors
        vendors.seed_builtins(self._vendors_json)
        return vendors.list_vendors(self._vendors_json)

    def update_vendor(self, vendor_id: str = "", name: str = "",
                       url: str = "", favicon_path: str = "") -> dict[str, Any]:
        """Create (vendor_id="") or update a vendor. Optionally fetch favicon if URL set."""
        import vendors
        vendors.seed_builtins(self._vendors_json)
        if not vendor_id:
            if not name.strip() and url.strip():
                name = vendors.name_from_url(url)
            v = vendors.create_vendor(self._vendors_json, name=name, url=url)
        else:
            v = vendors.update_vendor(self._vendors_json, vendor_id,
                                      name=name or None, url=url or None,
                                      favicon_path=favicon_path or None)
        if v.get("url") and not v.get("favicon_path"):
            import requests
            try:
                fp = vendors.fetch_favicon(v["url"], self._favicons_dir)
                v = vendors.update_vendor(self._vendors_json, v["id"],
                                           favicon_path=os.path.relpath(fp, self.base_dir))
            except (requests.exceptions.RequestException, OSError) as exc:
                logger.warning("favicon fetch failed for %s: %s", v["url"], exc)
        return v

    def merge_vendors(self, src_id: str, dst_id: str) -> list[dict[str, Any]]:
        """Reassign all POs from src to dst, then remove src. Returns fresh inventory."""
        import vendors
        # Reassign POs first
        with self._lock:
            import csv as _csv
            if os.path.isfile(self._po_csv):
                with open(self._po_csv, newline="", encoding="utf-8-sig") as f:
                    rows = list(_csv.DictReader(f))
                for r in rows:
                    if r["vendor_id"] == src_id:
                        r["vendor_id"] = dst_id
                csv_io.atomic_write_rows(self._po_csv, [
                    "po_id", "vendor_id", "source_file_hash", "source_file_ext",
                    "purchase_date", "notes",
                ], rows, encoding="utf-8")
            vendors.merge_vendors(self._vendors_json, src_id, dst_id)
            return self._rebuild()

    def delete_vendor(self, vendor_id: str) -> list[dict[str, Any]]:
        """Delete a vendor (cannot be a pseudo-vendor or have POs)."""
        import vendors
        # Refuse if any PO references it
        if os.path.isfile(self._po_csv):
            with open(self._po_csv, newline="", encoding="utf-8-sig") as f:
                if any(r["vendor_id"] == vendor_id for r in csv.DictReader(f)):
                    raise ValueError("vendor has POs; merge first")
        vendors.delete_vendor(self._vendors_json, vendor_id)
        return self._rebuild()

    def fetch_favicon(self, url: str) -> str:
        """Fetch favicon for a URL; return absolute path to cached file."""
        import vendors
        return vendors.fetch_favicon(url, self._favicons_dir)

    def parse_source_file(self, path: str) -> list[dict[str, Any]]:
        """Parse a CSV/PDF/image source file into candidate line items."""
        import mfg_direct_import
        return mfg_direct_import.parse_source_file(path)

    def parse_source_file_b64(self, file_b64: str, file_name: str) -> list[dict[str, Any]]:
        """Decode base64, write to temp file, parse, and return rows."""
        import base64
        import tempfile

        import mfg_direct_import
        ext = os.path.splitext(file_name)[1].lower()
        data = base64.b64decode(file_b64)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tf:
            tf.write(data)
            tmp_path = tf.name
        try:
            return mfg_direct_import.parse_source_file(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def match_part(self, mpn: str, manufacturer: str = "") -> dict[str, Any]:
        """Match an MPN against existing parts. See mfg_direct_import.match_part."""
        import mfg_direct_import
        return mfg_direct_import.match_part(self._get_cache(), mpn, manufacturer)

    def create_purchase_order_with_items(
        self,
        vendor_id: str,
        source_file_b64: str,
        source_file_name: str,
        purchase_date: str,
        notes: str,
        line_items_json: str,
    ) -> list[dict[str, Any]]:
        """Create a PO + ledger rows. Returns fresh inventory."""
        import base64

        import mfg_direct_import
        line_items = self._ensure_parsed(line_items_json)
        if not line_items:
            raise ValueError("line_items must not be empty")

        source_bytes = None
        source_ext = None
        if source_file_b64 and source_file_name:
            source_bytes = base64.b64decode(source_file_b64)
            source_ext = os.path.splitext(source_file_name)[1].lower()

        with self._lock:
            mfg_direct_import.import_po(
                ledger_csv=self.input_csv,
                po_csv=self._po_csv,
                sources_dir=self._sources_dir,
                vendor_id=vendor_id,
                source_file_bytes=source_bytes,
                source_file_ext=source_ext,
                purchase_date=purchase_date,
                notes=notes,
                line_items=line_items,
            )
            self._record_import_prices([
                {"Manufacture Part Number": li.get("mpn", ""),
                 "Manufacturer": li.get("manufacturer", ""),
                 "Unit Price($)": str(li.get("unit_price", "")),
                 "Quantity": str(li.get("quantity", ""))}
                for li in line_items
            ])
            return self._rebuild()

    def list_purchase_orders(self) -> list[dict[str, str]]:
        import purchase_orders
        return purchase_orders.list_purchase_orders(self._po_csv)

    def update_purchase_order(self, po_id: str, vendor_id: str = "",
                               purchase_date: str = "",
                               notes: str = "") -> list[dict[str, Any]]:
        import purchase_orders
        with self._lock:
            kwargs = {}
            if vendor_id:
                kwargs["vendor_id"] = vendor_id
            if purchase_date:
                kwargs["purchase_date"] = purchase_date
            if notes is not None and notes != "":
                kwargs["notes"] = notes
            purchase_orders.update_purchase_order(self._po_csv, po_id, **kwargs)
            return self._rebuild()

    def get_po_with_items(self, po_id: str) -> dict[str, Any]:
        """Return PO metadata + the ledger rows tagged with this po_id."""
        import purchase_orders
        po = purchase_orders.get_purchase_order(self._po_csv, po_id)
        if not po:
            raise KeyError(po_id)
        items = []
        if os.path.isfile(self.input_csv):
            with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("po_id") == po_id:
                        items.append({
                            "mpn": row.get("Manufacture Part Number", ""),
                            "manufacturer": row.get("Manufacturer", ""),
                            "package": row.get("Package", ""),
                            "quantity": int(row.get("Quantity") or 0),
                            "unit_price": float(row.get("Unit Price($)") or 0),
                        })
        return {"po": po, "line_items": items}

    def delete_purchase_order(self, po_id: str) -> list[dict[str, Any]]:
        import purchase_orders
        with self._lock:
            # Remove ledger rows first
            if os.path.isfile(self.input_csv):
                with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
                    fn = csv.DictReader(f).fieldnames
                    f.seek(0)
                    rows = [r for r in csv.DictReader(f) if r.get("po_id") != po_id]
                csv_io.atomic_write_rows(
                    self.input_csv, list(fn or []), rows, encoding="utf-8",
                )
            purchase_orders.delete_purchase_order(self._po_csv, self._sources_dir, po_id)
            return self._rebuild()

    def get_warnings(self) -> dict[str, Any]:
        """Return a dict of console warnings for the frontend to display."""
        import vendors
        out: dict[str, Any] = {
            "migration": self._last_migration_summary,
            "duplicates": [],
            "inferred_only": 0,
        }
        all_vendors = vendors.list_vendors(self._vendors_json)
        out["inferred_only"] = sum(1 for v in all_vendors if v.get("type") == "inferred")
        for a, b in vendors.find_possible_duplicates(self._vendors_json):
            out["duplicates"].append({
                "src": {"id": a["id"], "name": a["name"]},
                "dst": {"id": b["id"], "name": b["name"]},
            })
        return out

    def open_source_file(self, po_id: str) -> dict[str, str]:
        """Open the archived source file for a PO in the OS default app."""
        import purchase_orders
        path = purchase_orders.resolve_source_path(self._sources_dir, po_id, self._po_csv)
        if not path:
            return {"opened": False, "reason": "no source file"}
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path])
        return {"opened": True, "path": path}

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
