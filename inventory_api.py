"""Inventory API — all CSV read/write/rebuild logic exposed to JS via pywebview."""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from categorize import categorize, parse_capacitance, parse_inductance, parse_resistance
from digikey_client import DigikeyClient
from lcsc_client import LcscClient

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

    # ── Utility methods (ported from organize_inventory.py) ──────────────

    @staticmethod
    def _parse_qty(value: Any, default: int = 0) -> int:
        """Parse a quantity string to int, tolerating commas and floats."""
        try:
            return int(float(str(value).replace(",", "")))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_price(value: Any, default: float = 0.0) -> float:
        """Parse a price string to float, tolerating commas and dollar signs."""
        try:
            return float(str(value).replace(",", "").replace("$", "") or "0")
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _ensure_parsed(value: str | Any) -> Any:
        """Parse JSON string if needed, otherwise return as-is."""
        return json.loads(value) if isinstance(value, str) else value

    def _append_csv_rows(self, path: str, fieldnames: list[str],
                         rows: list[dict[str, Any]]) -> None:
        """Append rows to a CSV file, writing header if the file is new.

        If the file exists with an older header (fewer columns), migrates it
        to the new schema before appending.
        """
        if os.path.exists(path):
            self._migrate_csv_header(path, fieldnames)
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                for row in rows:
                    writer.writerow(row)
        else:
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)

    @staticmethod
    def _migrate_csv_header(path: str, expected_fieldnames: list[str]) -> None:
        """If a CSV file has an older header, rewrite it with the new schema."""
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_fields = reader.fieldnames or []
            if set(expected_fieldnames) == set(existing_fields):
                return
            existing_rows = list(reader)

        # Rewrite with new header, filling missing fields with ""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=expected_fieldnames)
            writer.writeheader()
            for row in existing_rows:
                migrated = {fn: row.get(fn, "") for fn in expected_fieldnames}
                writer.writerow(migrated)

    @staticmethod
    def fix_double_utf8(text: str) -> str:
        """Fix double-encoded UTF-8 text."""
        for enc in ("cp1252", "latin-1"):
            try:
                return text.encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return text

    @staticmethod
    def get_part_key(row: dict[str, str]) -> str:
        """Return best unique identifier: LCSC (C-prefixed) > MPN > Digikey PN."""
        lcsc = (row.get("LCSC Part Number") or "").strip()
        if lcsc and lcsc.upper().startswith("C"):
            return lcsc
        mpn = (row.get("Manufacture Part Number") or "").strip()
        if mpn:
            return mpn
        dk = (row.get("Digikey Part Number") or "").strip()
        if dk:
            return dk
        return ""

    # ── Core pipeline ────────────────────────────────────────────────────

    def _read_raw_inventory(self) -> tuple[list[str], dict[str, dict[str, str]]]:
        """Read purchase_ledger.csv, fix encoding, merge duplicates.
        Returns (fieldnames, merged_dict).
        """
        if not os.path.exists(self.input_csv):
            return list(self.FIELDNAMES), {}

        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        # Fix double-encoded descriptions
        for r in rows:
            for field in ("Description", "Package"):
                if r.get(field):
                    r[field] = self.fix_double_utf8(r[field])

        # Merge duplicates by part key
        merged: dict[str, dict[str, str]] = {}
        for r in rows:
            pn = self.get_part_key(r)
            if not pn:
                continue
            qty = self._parse_qty(r.get("Quantity"))
            ext = self._parse_price(r.get("Ext.Price($)"))
            if pn in merged:
                prev_qty = self._parse_qty(merged[pn]["Quantity"])
                merged[pn]["Quantity"] = str(prev_qty + qty)
                new_ext = self._parse_price(merged[pn]["Ext.Price($)"]) + ext
                merged[pn]["Ext.Price($)"] = f"{new_ext:.2f}"
                old_up = self._parse_price(merged[pn]["Unit Price($)"])
                new_up = self._parse_price(r.get("Unit Price($)"))
                if new_up > 0 and new_up < old_up:
                    merged[pn]["Unit Price($)"] = r["Unit Price($)"]
            else:
                r_copy = dict(r)
                r_copy["Quantity"] = str(qty)
                merged[pn] = r_copy

        # Derive missing price from the other price field + qty
        for part in merged.values():
            up = self._parse_price(part.get("Unit Price($)"))
            ext = self._parse_price(part.get("Ext.Price($)"))
            qty = self._parse_qty(part.get("Quantity"))
            if up == 0.0 and ext > 0 and qty > 0:
                part["Unit Price($)"] = f"{ext / qty:.4f}"
            elif ext == 0.0 and up > 0 and qty > 0:
                part["Ext.Price($)"] = f"{up * qty:.2f}"

        return fieldnames, merged

    def _apply_adjustments(self, merged: dict[str, dict[str, str]],
                           fieldnames: list[str]) -> None:
        """Apply adjustments.csv entries to merged dict."""
        if not os.path.exists(self.adjustments_csv):
            return
        with open(self.adjustments_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                adj_type = (row.get("type") or "").strip()
                pn = (row.get("lcsc_part") or "").strip()
                if not pn or not adj_type:
                    continue
                try:
                    qty = int(float(row.get("quantity", "0")))
                except ValueError:
                    logger.warning("Skipping adjustment: malformed quantity %r for part %s", row.get("quantity"), pn)
                    continue

                if pn not in merged:
                    if adj_type == "set" and qty > 0:
                        merged[pn] = {fn: "" for fn in fieldnames}
                        if pn.upper().startswith("C") and pn[1:].isdigit():
                            merged[pn]["LCSC Part Number"] = pn
                        else:
                            merged[pn]["Manufacture Part Number"] = pn
                        merged[pn]["Quantity"] = "0"
                    else:
                        continue

                current = self._parse_qty(merged[pn]["Quantity"])
                if adj_type == "set":
                    new_qty = max(0, qty)
                elif adj_type in ("consume", "add", "remove"):
                    new_qty = max(0, current + qty)
                else:
                    continue
                merged[pn]["Quantity"] = str(new_qty)

    def _categorize_and_sort(self, parts: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
        """Categorize parts and sort within sections."""
        categorized: dict[str, list[dict[str, str]]] = {}
        for p in parts:
            cat = categorize(p)
            categorized.setdefault(cat, []).append(p)

        # Sort passives by value — applies to both bare and compound section names
        for section, items in categorized.items():
            if "Resistor" in section:
                items.sort(key=lambda r: parse_resistance(r.get("Description", "")))
            elif "Capacitor" in section:
                items.sort(key=lambda r: parse_capacitance(r.get("Description", "")))
            elif "Inductor" in section:
                items.sort(key=lambda r: parse_inductance(r.get("Description", "")))
        return categorized

    def _write_organized(self, categorized: dict[str, list[dict[str, str]]],
                         fieldnames: list[str]) -> None:
        """Write inventory.csv."""
        with open(self.output_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Section"] + list(fieldnames))
            for section in self.FLAT_SECTION_ORDER:
                items = categorized.get(section)
                if not items:
                    continue
                writer.writerow([])
                writer.writerow([f"=== {section} ==="] + [""] * len(fieldnames))
                for item in items:
                    writer.writerow([section] + [item.get(fn, "") for fn in fieldnames])

    def _rebuild(self) -> list[dict[str, Any]]:
        """Full rebuild pipeline: merge -> adjust -> categorize -> sort -> write.
        Returns fresh inventory list.
        """
        fieldnames, merged = self._read_raw_inventory()
        self._apply_adjustments(merged, fieldnames)
        parts = list(merged.values())
        categorized = self._categorize_and_sort(parts)
        self._write_organized(categorized, fieldnames)
        return self._load_organized()

    def _load_organized(self) -> list[dict[str, Any]]:
        """Load organized inventory as list of dicts for JSON."""
        rows: list[dict[str, Any]] = []
        if not os.path.exists(self.output_csv):
            return rows
        with open(self.output_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                section = (row.get("Section") or "").strip()
                lcsc = (row.get("LCSC Part Number") or "").strip()
                mpn = (row.get("Manufacture Part Number") or "").strip()
                if section.startswith("=") or lcsc.startswith("="):
                    continue
                if not lcsc and not mpn:
                    continue
                rows.append({
                    "section": section,
                    "lcsc": lcsc,
                    "mpn": mpn,
                    "digikey": (row.get("Digikey Part Number") or "").strip(),
                    "manufacturer": (row.get("Manufacturer") or "").strip(),
                    "package": (row.get("Package") or "").strip(),
                    "description": (row.get("Description") or "").strip(),
                    "qty": self._parse_qty(row.get("Quantity")),
                    "unit_price": self._parse_price(row.get("Unit Price($)")),
                    "ext_price": self._parse_price(row.get("Ext.Price($)")),
                })
        return rows

    # ── Adjustment helpers ───────────────────────────────────────────────

    def _append_adjustment(self, adj_type: str, part_key: str, quantity: int,
                           note: str = "", bom_file: str = "",
                           board_qty: int | str = "", source: str = "") -> None:
        """Append one row to adjustments.csv."""
        self._append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, [{
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "type": adj_type,
            "lcsc_part": part_key,
            "quantity": quantity,
            "bom_file": bom_file,
            "board_qty": board_qty,
            "note": note,
            "source": source,
        }])

    def rollback_source(self, source: str) -> list[dict]:
        """Remove all adjustments with the given source tag and rebuild.

        Returns the list of removed rows (for logging/verification).
        """
        if not source:
            raise ValueError("source must not be empty")
        if not os.path.exists(self.adjustments_csv):
            return []

        with open(self.adjustments_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        kept = []
        removed = []
        for row in rows:
            if (row.get("source") or "") == source:
                removed.append(row)
            else:
                kept.append(row)

        if not removed:
            return []

        with self._lock:
            with open(self.adjustments_csv, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(kept)
            self._rebuild()

        logger.info("Rolled back %d adjustment(s) with source=%r", len(removed), source)
        return removed

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

    # ── Public API methods (called from JS via pywebview) ────────────────

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
            self._append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, adj_rows)
            return self._rebuild()

    def _truncate_csv(self, csv_path: str, count: int, label: str) -> list[dict[str, Any]]:
        """Remove the last *count* rows from a CSV and rebuild inventory."""
        if count <= 0:
            raise ValueError(f"count must be positive, got {count}")

        if not os.path.exists(csv_path):
            raise ValueError(f"No {label} file found")

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        if count > len(rows):
            raise ValueError(
                f"Cannot remove {count} rows: {label} only has {len(rows)} rows"
            )

        rows = rows[:-count]

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
            pk = self.get_part_key(row)
            if pk == part_key:
                qty = self._parse_qty(row.get("Quantity"))
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
            # Part only exists via adjustments — add a new ledger row with price info
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

    # Map JS field names to CSV column names
    _FIELD_TO_COL = {
        "lcsc": "LCSC Part Number",
        "digikey": "Digikey Part Number",
        "mpn": "Manufacture Part Number",
        "manufacturer": "Manufacturer",
        "package": "Package",
        "description": "Description",
    }

    def update_part_fields(self, part_key: str,
                           fields_json: str | dict[str, str]) -> list[dict[str, Any]]:
        """Update metadata fields for a part in purchase_ledger.csv.

        ``fields_json`` maps JS field names (lcsc, digikey, mpn, manufacturer,
        package, description) to new string values.  Only supplied fields are
        written; omitted fields are left untouched.  Returns fresh inventory.
        """
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
            pk = self.get_part_key(row)
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
        """Auto-detect column mapping for purchase CSV import.
        Returns dict of {source_column_index: target_inventory_field}.
        """
        headers = self._ensure_parsed(headers_json)
        lower_headers = [h.lower().strip() for h in headers]

        # Collect candidates for each target field
        candidates: dict[str, list[int]] = {}
        for i, h in enumerate(lower_headers):
            if "lcsc" in h:
                candidates.setdefault("LCSC Part Number", []).append(i)
            if "digikey" in h or "digi-key" in h:
                candidates.setdefault("Digikey Part Number", []).append(i)
            if h == "mpn" or ("manufactur" in h and "part" in h) or ("mfr" in h and "part" in h):
                candidates.setdefault("Manufacture Part Number", []).append(i)
            if ("manufacturer" in h or h.startswith("mfr")) and "part" not in h:
                candidates.setdefault("Manufacturer", []).append(i)
            # Prefer "shipped" quantity over "ordered" over generic
            if "shipped" in h:
                candidates.setdefault("Quantity", []).insert(0, i)
            elif "quantity" in h or h.startswith("qty"):
                candidates.setdefault("Quantity", []).append(i)
            if "description" in h:
                candidates.setdefault("Description", []).append(i)
            if "package" in h:
                candidates.setdefault("Package", []).append(i)
            if "unit price" in h:
                candidates.setdefault("Unit Price($)", []).append(i)
            if ("ext" in h and "price" in h) or "extended price" in h:
                candidates.setdefault("Ext.Price($)", []).append(i)
            if "rohs" in h:
                candidates.setdefault("RoHS", []).append(i)
            if "customer" in h:
                candidates.setdefault("Customer NO.", []).append(i)

        # Assign one source column per target (no duplicates)
        mapping: dict[str, str] = {}
        used_indices: set[int] = set()
        target_order = [
            "LCSC Part Number", "Digikey Part Number", "Manufacture Part Number",
            "Manufacturer", "Quantity", "Description", "Package",
            "Unit Price($)", "Ext.Price($)", "RoHS", "Customer NO.",
        ]
        for target in target_order:
            for idx in candidates.get(target, []):
                if idx not in used_indices:
                    mapping[str(idx)] = target
                    used_indices.add(idx)
                    break

        return mapping

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
        """Open native Save As dialog and write content to the chosen path.
        If links_json is provided, writes a .links.json sidecar file next to the CSV.
        Returns {"path": chosen_path} on success, None if cancelled.
        """
        import webview
        kwargs = {"file_types": ("CSV Files (*.csv)",)}
        if default_dir and os.path.isdir(default_dir):
            kwargs["directory"] = default_dir
        if default_name:
            kwargs["save_filename"] = default_name
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE,
            **kwargs,
        )
        if result:
            path = result if isinstance(result, str) else result[0]
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(content)
            # Write sidecar links file
            if links_json:
                links = self._ensure_parsed(links_json)
                if links:
                    links_path = os.path.splitext(path)[0] + ".links.json"
                    with open(links_path, "w", encoding="utf-8") as f:
                        json.dump(links, f, indent=2)
            return {"path": path}
        return None

    def open_file_dialog(self, title: str = "Select CSV file",
                         default_dir: str | None = None) -> dict[str, Any] | None:
        """Open native file dialog, return {name, content, directory, path} or None."""
        import webview
        kwargs = {"file_types": ("CSV Files (*.csv)",)}
        if default_dir and os.path.isdir(default_dir):
            kwargs["directory"] = default_dir
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            **kwargs,
        )
        if result and len(result) > 0:
            path = result[0]
            resp = {
                "name": os.path.basename(path),
                "content": self._read_text(path),
                "directory": os.path.dirname(path),
                "path": path,
            }
            # Check for sidecar .links.json
            links_path = os.path.splitext(path)[0] + ".links.json"
            if os.path.exists(links_path):
                try:
                    with open(links_path, encoding="utf-8") as lf:
                        resp["links"] = json.load(lf)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Failed to read sidecar links: %s", exc)
            return resp
        return None

    def load_file(self, path: str) -> dict[str, Any] | None:
        """Load a file by path, return {name, content, directory, path, links?} or None."""
        if not path or not os.path.isfile(path):
            return None
        resp = {
            "name": os.path.basename(path),
            "content": self._read_text(path),
            "directory": os.path.dirname(path),
            "path": path,
        }
        # Check for sidecar .links.json
        links_path = os.path.splitext(path)[0] + ".links.json"
        if os.path.exists(links_path):
            try:
                with open(links_path, encoding="utf-8") as lf:
                    resp["links"] = json.load(lf)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read sidecar links: %s", exc)
        return resp

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

    @staticmethod
    def _read_text(path: str) -> str:
        """Read a text file, auto-detecting UTF-16 vs UTF-8 encoding."""
        with open(path, "rb") as f:
            bom = f.read(2)
        encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        with open(path, encoding=encoding) as f:
            return f.read()
