"""PurchaseOrders facade — PO CRUD, warnings, and source-file preview/open."""

from __future__ import annotations

import csv
import logging
import os
import sys
from typing import Any

import csv_io

logger = logging.getLogger(__name__)


class PurchaseOrdersFacade:
    def __init__(self, api) -> None:
        self._api = api

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
        line_items = self._api._ensure_parsed(line_items_json)
        if not line_items:
            raise ValueError("line_items must not be empty")

        source_bytes = None
        source_ext = None
        if source_file_b64 and source_file_name:
            source_bytes = base64.b64decode(source_file_b64)
            source_ext = os.path.splitext(source_file_name)[1].lower()

        with self._api._lock:
            mfg_direct_import.import_po(
                ledger_csv=self._api.input_csv,
                po_csv=self._api._po_csv,
                sources_dir=self._api._sources_dir,
                vendor_id=vendor_id,
                source_file_bytes=source_bytes,
                source_file_ext=source_ext,
                purchase_date=purchase_date,
                notes=notes,
                line_items=line_items,
            )
            self._api._record_import_prices([
                {"Manufacture Part Number": li.get("mpn", ""),
                 "Manufacturer": li.get("manufacturer", ""),
                 "Unit Price($)": str(li.get("unit_price", "")),
                 "Quantity": str(li.get("quantity", ""))}
                for li in line_items
            ])
            return self._api._rebuild()

    def list_purchase_orders(self) -> list[dict[str, str]]:
        import purchase_orders
        return purchase_orders.list_purchase_orders(self._api._po_csv)

    def update_purchase_order(self, po_id: str, vendor_id: str = "",
                              purchase_date: str = "",
                              notes: str = "") -> list[dict[str, Any]]:
        import purchase_orders
        with self._api._lock:
            kwargs = {}
            if vendor_id:
                kwargs["vendor_id"] = vendor_id
            if purchase_date:
                kwargs["purchase_date"] = purchase_date
            if notes is not None and notes != "":
                kwargs["notes"] = notes
            purchase_orders.update_purchase_order(self._api._po_csv, po_id, **kwargs)
            return self._api._rebuild()

    def get_po_with_items(self, po_id: str) -> dict[str, Any]:
        """Return PO metadata + the ledger rows tagged with this po_id."""
        import purchase_orders
        po = purchase_orders.get_purchase_order(self._api._po_csv, po_id)
        if not po:
            raise KeyError(po_id)
        items = []
        if os.path.isfile(self._api.input_csv):
            with open(self._api.input_csv, newline="", encoding="utf-8-sig") as f:
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
        with self._api._lock:
            # Remove ledger rows first
            if os.path.isfile(self._api.input_csv):
                with open(self._api.input_csv, newline="", encoding="utf-8-sig") as f:
                    fn = csv.DictReader(f).fieldnames
                    f.seek(0)
                    rows = [r for r in csv.DictReader(f) if r.get("po_id") != po_id]
                csv_io.atomic_write_rows(
                    self._api.input_csv, list(fn or []), rows, encoding="utf-8",
                )
            purchase_orders.delete_purchase_order(self._api._po_csv, self._api._sources_dir, po_id)
            return self._api._rebuild()

    def delete_last_purchase_order(self) -> list[dict[str, Any]]:
        """Delete the most-recently-created PO (and its ledger rows). Returns
        fresh inventory. Raises if there is no PO to remove."""
        import purchase_orders
        pos = purchase_orders.list_purchase_orders(self._api._po_csv)
        if not pos:
            raise ValueError("no purchase order to remove")
        return self.delete_purchase_order(pos[-1]["po_id"])

    def get_warnings(self) -> dict[str, Any]:
        """Return a dict of console warnings for the frontend to display."""
        import vendors
        out: dict[str, Any] = {
            "migration": self._api._last_migration_summary,
            "duplicates": [],
            "inferred_only": 0,
        }
        all_vendors = vendors.list_vendors(self._api._vendors_json)
        out["inferred_only"] = sum(1 for v in all_vendors if v.get("type") == "inferred")
        for a, b in vendors.find_possible_duplicates(self._api._vendors_json):
            out["duplicates"].append({
                "src": {"id": a["id"], "name": a["name"]},
                "dst": {"id": b["id"], "name": b["name"]},
            })
        return out

    def get_po_source_preview(self, po_id: str) -> dict[str, Any]:
        """Return a renderable image preview of a PO's archived source file.

        {"kind": "image", "data_uri", "mime", "width", "height", "page_count"}
        for image/PDF sources (PDFs rasterized to PNG); {"kind": "none"} for
        spreadsheet/CSV/missing sources. The frontend uses this to show an
        inline thumbnail (and click-to-zoom lightbox) in the PO picker.
        """
        import purchase_orders
        return purchase_orders.source_preview(self._api._sources_dir, po_id, self._api._po_csv)

    def open_source_file(self, po_id: str) -> dict[str, str]:
        """Open the archived source file for a PO in the OS default app."""
        import purchase_orders
        path = purchase_orders.resolve_source_path(self._api._sources_dir, po_id, self._api._po_csv)
        if not path:
            return {"opened": False, "reason": "no source file"}
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path])
        return {"opened": True, "path": path}
