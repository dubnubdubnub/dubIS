"""Inventory API — thin facade delegating to domain.inventory, domain.pricing, file_dialogs."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any

import cache_db
import csv_io
import domain.generic_parts
import domain.inventory
import domain.pricing
import inventory_ops
from distributor_manager import DistributorManager
from domain.api_distributor import DistributorFacade
from domain.api_fileio import FileIOFacade
from domain.api_generic_parts import GenericPartsFacade
from domain.api_history import PartHistoryFacade
from domain.api_inventory import InventoryCRUDFacade
from domain.api_preferences import PreferencesFacade
from domain.api_pricing import PricingFacade
from domain.api_purchase_orders import PurchaseOrdersFacade
from domain.api_scan import ScanFacade
from domain.api_vendors import VendorsFacade

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
        self._files = FileIOFacade(self)
        self._dist = DistributorFacade(self)
        self._pricing = PricingFacade(self)
        self._generic = GenericPartsFacade(self)
        self._prefs = PreferencesFacade(self)
        self._inv = InventoryCRUDFacade(self)
        self._vendors = VendorsFacade(self)
        self._po = PurchaseOrdersFacade(self)
        self._scan = ScanFacade(self)
        self._history = PartHistoryFacade(self)
        from domain.api_mirror import MirrorFacade
        from mirror_push import MirrorController
        self._mirror = MirrorFacade(self)
        self._mirror_ctl = MirrorController(
            is_enabled=self._mirror_enabled, read_token=self._mirror_token)

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

    def shutdown(self) -> None:
        """Commit and close the cache connection. Idempotent and best-effort.

        Called from app.pyw during teardown so the SQLite WAL is flushed
        before process exit. Safe to call when no connection exists or after a
        prior shutdown. Any commit/close failure is logged, never raised — a
        cleanup error must not prevent the process from exiting.
        """
        with self._lock:
            if self._cache_conn is None:
                return
            try:
                self._cache_conn.commit()
                self._cache_conn.close()
            except Exception as exc:
                logger.warning("Error closing cache connection during shutdown: %s", exc)
            finally:
                self._cache_conn = None

    def bench_mark(self, label: str, detail: str = "") -> bool:
        """Record a startup-timing mark from the frontend.

        No-op unless DUBIS_BENCH_OUT is set (see bench.py). Timestamps are taken
        on the Python clock here, so JS marks share one timeline with the
        backend marks in app.pyw — no JS/Python clock skew to reconcile.
        ``detail`` carries optional JS-side data (e.g. navigation timing JSON).
        Returns True when bench mode is active so the frontend knows whether to
        emit further marks / trigger auto-close.
        """
        import bench
        bench.mark(label, detail)
        return bench.ENABLED

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
        return self._inv.rollback_source(source)

    def rebuild_inventory(self) -> list[dict[str, Any]]:
        return self._inv.rebuild_inventory()

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        return self._inv.adjust_part(adj_type, part_key, quantity, note, source)

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "", source: str = "") -> list[dict[str, Any]]:
        return self._inv.consume_bom(matches_json, board_qty, bom_name, note, source)

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
        return self._inv.remove_last_purchases(count)

    def remove_last_adjustments(self, count: int | str) -> list[dict[str, Any]]:
        return self._inv.remove_last_adjustments(count)

    def import_purchases(self, rows_json: str | list[dict[str, str]]) -> list[dict[str, Any]]:
        return self._inv.import_purchases(rows_json)

    def update_part_price(self, part_key: str, unit_price: float | None = None,
                          ext_price: float | None = None) -> list[dict[str, Any]]:
        return self._inv.update_part_price(part_key, unit_price, ext_price)

    def update_part_fields(self, part_key: str,
                           fields_json: str | dict[str, str]) -> list[dict[str, Any]]:
        return self._inv.update_part_fields(part_key, fields_json)

    def detect_columns(self, headers_json: str | list[str]) -> dict[str, str]:
        return self._files.detect_columns(headers_json)

    def load_preferences(self) -> dict[str, Any]:
        return self._prefs.load_preferences()

    def save_preferences(self, prefs_json: str | dict[str, Any]) -> None:
        return self._prefs.save_preferences(prefs_json)

    def save_file_dialog(self, content: str, default_name: str = "export.csv",
                         default_dir: str | None = None,
                         links_json: str | list | None = None) -> dict[str, str] | None:
        return self._files.save_file_dialog(content, default_name, default_dir, links_json)

    def convert_xls_to_csv(self, path: str) -> dict[str, Any] | None:
        return self._files.convert_xls_to_csv(path)

    def open_file_dialog(self, title: str = "Select CSV file",
                         default_dir: str | None = None) -> dict[str, Any] | None:
        return self._files.open_file_dialog(title, default_dir)

    def load_file(self, path: str) -> dict[str, Any] | None:
        return self._files.load_file(path)

    # ── Price history API ───────────────────────────────────────────────────

    def record_fetched_prices(self, part_key: str, distributor: str,
                               price_tiers: list[dict[str, Any]]) -> None:
        return self._pricing.record_fetched_prices(part_key, distributor, price_tiers)

    def get_price_summary(self, part_key: str) -> dict[str, dict[str, Any]]:
        return self._pricing.get_price_summary(part_key)

    def get_last_po_quantity(self, part_key: str) -> int | None:
        return self._pricing.get_last_po_quantity(part_key)

    # ── Part adjustment history ─────────────────────────────────────────────

    def get_part_history(self, part_key: str) -> list[dict[str, Any]]:
        return self._history.get_part_history(part_key)

    # ── Product preview (delegated to DistributorManager) ───────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        return self._dist.fetch_lcsc_product(product_code)

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        return self._dist.fetch_digikey_product(part_number)

    def fetch_pololu_product(self, sku: str) -> dict[str, Any] | None:
        return self._dist.fetch_pololu_product(sku)

    def fetch_mouser_product(self, part_number: str) -> dict[str, Any] | None:
        return self._dist.fetch_mouser_product(part_number)

    def check_digikey_session(self) -> dict[str, Any]:
        return self._dist.check_digikey_session()

    def start_digikey_login(self) -> dict[str, Any]:
        return self._dist.start_digikey_login()

    def sync_digikey_cookies(self) -> dict[str, Any]:
        return self._dist.sync_digikey_cookies()

    def get_digikey_login_status(self) -> dict[str, bool]:
        return self._dist.get_digikey_login_status()

    def validate_digikey_session(self) -> dict[str, Any]:
        return self._dist.validate_digikey_session()

    def logout_digikey(self) -> dict[str, str]:
        return self._dist.logout_digikey()

    def get_mouser_api_key_status(self) -> dict[str, bool]:
        return self._dist.get_mouser_api_key_status()

    def set_mouser_api_key(self, key: str) -> dict[str, bool]:
        return self._dist.set_mouser_api_key(key)

    def clear_mouser_api_key(self) -> dict[str, bool]:
        return self._dist.clear_mouser_api_key()

    # ── Poll API ───────────────────────────────────────────────────────────

    def get_poll_api_info(self) -> dict[str, Any]:
        return self._prefs.get_poll_api_info()

    def set_poll_api_port(self, port: int | str) -> dict[str, Any]:
        return self._prefs.set_poll_api_port(port)

    # ── Inventory mirror ──────────────────────────────────────────────────

    def enable_inventory_mirror(self) -> dict[str, Any]:
        return self._mirror.enable_inventory_mirror()

    def disable_inventory_mirror(self) -> dict[str, Any]:
        return self._mirror.disable_inventory_mirror()

    def get_inventory_mirror_info(self) -> dict[str, Any]:
        return self._mirror.get_inventory_mirror_info()

    def _mirror_enabled(self) -> bool:
        return bool(self.load_preferences().get("inventoryMirror", {}).get("enabled", False))

    def _mirror_token(self):
        path = os.path.join(self.base_dir, "mirror_token")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return None

    def _push_to_mirror(self, inventory) -> None:
        self._mirror_ctl.on_inventory_changed(inventory)

    # ── Generic parts ──────────────────────────────────────────────────────

    def create_generic_part(self, name: str, part_type: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return self._generic.create_generic_part(name, part_type, spec_json, strictness_json)

    def resolve_bom_spec(self, part_type: str, value: float,
                          package: str) -> dict[str, Any] | None:
        return self._generic.resolve_bom_spec(part_type, value, package)

    def list_generic_parts(self) -> list[dict[str, Any]]:
        return self._generic.list_generic_parts()

    def add_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._generic.add_generic_member(generic_part_id, part_id)

    def remove_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._generic.remove_generic_member(generic_part_id, part_id)

    def exclude_generic_member(self, generic_part_id: str, part_id: str) -> None:
        return self._generic.exclude_generic_member(generic_part_id, part_id)

    def set_preferred_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return self._generic.set_preferred_member(generic_part_id, part_id)

    def update_generic_part(self, generic_part_id: str, name: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return self._generic.update_generic_part(generic_part_id, name, spec_json, strictness_json)

    def extract_spec(self, part_key: str) -> dict[str, Any]:
        return self._generic.extract_spec(part_key)

    def extract_spec_from_value(self, part_type: str, value_str: str, package_str: str) -> dict[str, Any]:
        return self._generic.extract_spec_from_value(part_type, value_str, package_str)

    def list_saved_searches(self, generic_part_id: str) -> list[dict[str, Any]]:
        return self._generic.list_saved_searches(generic_part_id)

    def create_saved_search(self, generic_part_id: str, name: str,
                            tag_state_json: str, search_text: str,
                            frozen_members_json: str) -> dict[str, Any]:
        return self._generic.create_saved_search(
            generic_part_id, name, tag_state_json, search_text, frozen_members_json)

    def delete_saved_search(self, search_id: str) -> None:
        return self._generic.delete_saved_search(search_id)

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
        return self._vendors.list_vendors()

    def update_vendor(self, vendor_id: str = "", name: str = "",
                       url: str = "", favicon_path: str = "") -> dict[str, Any]:
        return self._vendors.update_vendor(vendor_id, name, url, favicon_path)

    def merge_vendors(self, src_id: str, dst_id: str) -> list[dict[str, Any]]:
        return self._vendors.merge_vendors(src_id, dst_id)

    def delete_vendor(self, vendor_id: str) -> list[dict[str, Any]]:
        return self._vendors.delete_vendor(vendor_id)

    def fetch_favicon(self, url: str) -> str:
        return self._vendors.fetch_favicon(url)

    def parse_source_file(self, path: str, template: str = "generic") -> list[dict[str, Any]]:
        return self._scan.parse_source_file(path, template)

    def parse_source_file_b64(
        self, file_b64: str, file_name: str, template: str = "generic",
    ) -> list[dict[str, Any]]:
        return self._scan.parse_source_file_b64(file_b64, file_name, template)

    def ocr_overlay_b64(
        self, file_b64: str, file_name: str, template: str = "generic",
    ) -> dict[str, Any]:
        return self._scan.ocr_overlay_b64(file_b64, file_name, template)

    def ocr_engine_available(self) -> bool:
        return self._scan.ocr_engine_available()

    def install_tesseract(self) -> dict[str, Any]:
        return self._scan.install_tesseract()

    def start_scan_session(self, template: str = "generic") -> dict[str, Any]:
        return self._scan.start_scan_session(template)

    def match_part(self, mpn: str, manufacturer: str = "") -> dict[str, Any]:
        return self._scan.match_part(mpn, manufacturer)

    def create_purchase_order_with_items(
        self,
        vendor_id: str,
        source_file_b64: str,
        source_file_name: str,
        purchase_date: str,
        notes: str,
        line_items_json: str,
    ) -> list[dict[str, Any]]:
        return self._po.create_purchase_order_with_items(
            vendor_id, source_file_b64, source_file_name, purchase_date, notes, line_items_json)

    def list_purchase_orders(self) -> list[dict[str, str]]:
        return self._po.list_purchase_orders()

    def update_purchase_order(self, po_id: str, vendor_id: str = "",
                               purchase_date: str = "",
                               notes: str = "") -> list[dict[str, Any]]:
        return self._po.update_purchase_order(po_id, vendor_id, purchase_date, notes)

    def get_po_with_items(self, po_id: str) -> dict[str, Any]:
        return self._po.get_po_with_items(po_id)

    def delete_purchase_order(self, po_id: str) -> list[dict[str, Any]]:
        return self._po.delete_purchase_order(po_id)

    def delete_last_purchase_order(self) -> list[dict[str, Any]]:
        return self._po.delete_last_purchase_order()

    def get_warnings(self) -> dict[str, Any]:
        return self._po.get_warnings()

    def get_po_source_preview(self, po_id: str) -> dict[str, Any]:
        return self._po.get_po_source_preview(po_id)

    def open_source_file(self, po_id: str) -> dict[str, str]:
        return self._po.open_source_file(po_id)

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
