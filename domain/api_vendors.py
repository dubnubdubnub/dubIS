"""Vendors facade — mfg-direct vendor CRUD and favicon management."""

from __future__ import annotations

import logging
import os
from typing import Any

import csv_io

logger = logging.getLogger(__name__)


class VendorsFacade:
    def __init__(self, api) -> None:
        self._api = api

    def list_vendors(self) -> list[dict[str, Any]]:
        """Return all vendors, each enriched with a favicon ``data:`` URI when one
        is cached. Seeds built-ins on first call."""
        import vendors
        vendors.seed_builtins(self._api._vendors_json)
        result = vendors.list_vendors(self._api._vendors_json)
        for v in result:
            fp = v.get("favicon_path")
            if fp:
                abs_fp = fp if os.path.isabs(fp) else os.path.join(self._api.base_dir, fp)
                v["favicon_data_uri"] = vendors.favicon_data_uri(abs_fp)
        return result

    def update_vendor(self, vendor_id: str = "", name: str = "",
                      url: str = "", favicon_path: str = "") -> dict[str, Any]:
        """Create (vendor_id="") or update a vendor. Optionally fetch favicon if URL set."""
        import vendors
        vendors.seed_builtins(self._api._vendors_json)
        if not vendor_id:
            if not name.strip() and url.strip():
                name = vendors.name_from_url(url)
            v = vendors.create_vendor(self._api._vendors_json, name=name, url=url)
        else:
            v = vendors.update_vendor(self._api._vendors_json, vendor_id,
                                      name=name or None, url=url or None,
                                      favicon_path=favicon_path or None)
        if v.get("url") and not v.get("favicon_path"):
            import requests
            try:
                fp = vendors.fetch_favicon(v["url"], self._api._favicons_dir)
                v = vendors.update_vendor(self._api._vendors_json, v["id"],
                                          favicon_path=os.path.relpath(fp, self._api.base_dir))
            except (requests.exceptions.RequestException, OSError) as exc:
                logger.warning("favicon fetch failed for %s: %s", v["url"], exc)
        return v

    def merge_vendors(self, src_id: str, dst_id: str) -> list[dict[str, Any]]:
        """Reassign all POs from src to dst, then remove src. Returns fresh inventory."""
        import vendors
        # Reassign POs first
        with self._api._lock:
            import csv as _csv
            if os.path.isfile(self._api._po_csv):
                with open(self._api._po_csv, newline="", encoding="utf-8-sig") as f:
                    rows = list(_csv.DictReader(f))
                for r in rows:
                    if r["vendor_id"] == src_id:
                        r["vendor_id"] = dst_id
                csv_io.atomic_write_rows(self._api._po_csv, [
                    "po_id", "vendor_id", "source_file_hash", "source_file_ext",
                    "purchase_date", "notes",
                ], rows, encoding="utf-8")
            vendors.merge_vendors(self._api._vendors_json, src_id, dst_id)
            return self._api._rebuild()

    def delete_vendor(self, vendor_id: str) -> list[dict[str, Any]]:
        """Delete a vendor (cannot be a pseudo-vendor or have POs)."""
        import csv

        import vendors
        # Refuse if any PO references it
        if os.path.isfile(self._api._po_csv):
            with open(self._api._po_csv, newline="", encoding="utf-8-sig") as f:
                if any(r["vendor_id"] == vendor_id for r in csv.DictReader(f)):
                    raise ValueError("vendor has POs; merge first")
        vendors.delete_vendor(self._api._vendors_json, vendor_id)
        return self._api._rebuild()

    def fetch_favicon(self, url: str) -> str:
        """Fetch favicon for a URL; return absolute path to cached file."""
        import vendors
        return vendors.fetch_favicon(url, self._api._favicons_dir)
