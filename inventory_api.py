"""Inventory API — all CSV read/write/rebuild logic exposed to JS via pywebview."""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

import kicad_openpnp
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
        "timestamp", "type", "lcsc_part", "quantity", "bom_file", "board_qty", "note",
    ]

    SECTION_ORDER = _CONSTANTS["SECTION_ORDER"]
    FLAT_SECTION_ORDER = _FLAT_SECTION_ORDER
    SECTION_HIERARCHY = _SECTION_HIERARCHY

    def __init__(self, *, debug: bool = False) -> None:
        self.base_dir: str = os.environ.get("DUBIS_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data",
        )
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
        """Append rows to a CSV file, writing header if the file is new."""
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)

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
                           board_qty: int | str = "") -> None:
        """Append one row to adjustments.csv."""
        self._append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, [{
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "type": adj_type,
            "lcsc_part": part_key,
            "quantity": quantity,
            "bom_file": bom_file,
            "board_qty": board_qty,
            "note": note,
        }])

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
                    note: str = "") -> list[dict[str, Any]]:
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
            self._append_adjustment(adj_type, part_key, record_qty, note=note)
            return self._rebuild()

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "") -> list[dict[str, Any]]:
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
        """Terminate the process immediately — user confirmed close."""
        if self._closing:
            return
        self._closing = True
        self._force_close = True
        # Hide window for instant visual feedback, then kill the process.
        # Bypasses destroy() which can deadlock or trigger slow WebView2 cleanup.
        import sys  # noqa: I001

        import webview
        for w in webview.windows:
            try:
                w.hide()
            except Exception:
                pass
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.TerminateProcess(
                ctypes.windll.kernel32.GetCurrentProcess(), 0)
        else:
            os._exit(0)

    # ── KiCad + OpenPnP methods (delegated to kicad_openpnp.py) ────────────

    def open_kicad_project_dialog(self) -> dict | None:
        """Open native folder dialog to select a KiCad project directory."""
        import webview
        result = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        if result and len(result) > 0:
            path = result[0]
            return self.scan_kicad_project(path)
        return None

    def scan_kicad_project(self, path: str) -> dict:
        """Scan a KiCad project and register it. Returns project data with parts."""
        result = kicad_openpnp.scan_kicad_project(path)
        projects = kicad_openpnp.load_kicad_projects()
        projects["projects"][path] = {
            "name": result["name"],
            "kicad_pro": result["kicad_pro"],
            "last_scan": result["last_scan"],
        }
        kicad_openpnp.save_kicad_projects(projects)
        return result

    def get_kicad_projects(self) -> dict:
        """Return registered KiCad projects."""
        return kicad_openpnp.load_kicad_projects()

    def remove_kicad_project(self, path: str) -> dict:
        """Remove a KiCad project from the registry."""
        projects = kicad_openpnp.load_kicad_projects()
        projects["projects"].pop(path, None)
        kicad_openpnp.save_kicad_projects(projects)
        return projects

    def get_part_links(self) -> dict:
        """Return part links data."""
        return kicad_openpnp.load_part_links()

    def save_part_link(self, source_id: str, part_key: str) -> dict:
        """Save a link from a KiCad identifier to an inventory part key."""
        if not source_id or not part_key:
            raise ValueError("source_id and part_key must not be empty")
        data = kicad_openpnp.load_part_links()
        data["links"][source_id] = part_key
        kicad_openpnp.save_part_links(data)
        return data

    def remove_part_link(self, source_id: str) -> dict:
        """Remove a part link."""
        data = kicad_openpnp.load_part_links()
        data["links"].pop(source_id, None)
        kicad_openpnp.save_part_links(data)
        return data

    def auto_link_kicad_parts(self, path: str) -> dict:
        """Auto-link KiCad parts to inventory by matching LCSC numbers and MPNs.

        Returns ``{"linked": count, "links": updated_links_data}``.
        """
        result = kicad_openpnp.scan_kicad_project(path)
        inventory = self._load_organized()
        link_data = kicad_openpnp.load_part_links()

        inv_by_lcsc: dict[str, str] = {}
        inv_by_mpn: dict[str, str] = {}
        for item in inventory:
            lcsc = item.get("lcsc", "").strip()
            mpn = item.get("mpn", "").strip()
            pk = lcsc or mpn
            if not pk:
                continue
            if lcsc:
                inv_by_lcsc[lcsc.upper()] = pk
            if mpn:
                inv_by_mpn[mpn.upper()] = pk

        linked = 0
        for part in result["parts"]:
            lcsc = part.get("lcsc", "").strip()
            mpn = part.get("mpn", "").strip()

            # Already linked?
            if lcsc and lcsc in link_data["links"]:
                continue
            if mpn and mpn in link_data["links"]:
                continue

            # Try LCSC match
            if lcsc and lcsc.upper() in inv_by_lcsc:
                link_data["links"][lcsc] = inv_by_lcsc[lcsc.upper()]
                linked += 1
                continue

            # Try MPN match
            if mpn and mpn.upper() in inv_by_mpn:
                link_data["links"][mpn] = inv_by_mpn[mpn.upper()]
                linked += 1

        kicad_openpnp.save_part_links(link_data)
        return {"linked": linked, "links": link_data}

    def get_openpnp_parts(self) -> dict:
        """Return OpenPnP parts data."""
        return kicad_openpnp.load_openpnp_parts()

    def fetch_footprint(self, lcsc_id: str) -> dict:
        """Fetch footprint from EasyEDA for an LCSC part. Returns footprint dict."""
        raw = kicad_openpnp.fetch_easyeda_footprint(lcsc_id)
        if not raw:
            raise ValueError(f"Could not fetch footprint for {lcsc_id}")
        package_id = lcsc_id  # use LCSC id as fallback package id
        return kicad_openpnp.parse_easyeda_footprint(raw, package_id)

    def fetch_kicad_footprint(self, fp_ref: str) -> dict:
        """Parse a KiCad .kicad_mod footprint from installed libraries."""
        result = kicad_openpnp.parse_kicad_footprint(fp_ref)
        if not result:
            raise ValueError(f"Could not find KiCad footprint: {fp_ref}")
        return result

    def save_openpnp_part(self, data_json: str | dict) -> dict:
        """Save OpenPnP metadata for a part.

        Expects ``{part_key, openpnp_id, package_id, height, speed, nozzle_tips, footprint}``.
        """
        data = self._ensure_parsed(data_json)
        part_key = data.get("part_key")
        if not part_key:
            raise ValueError("part_key is required")

        openpnp_data = kicad_openpnp.load_openpnp_parts()
        openpnp_data["parts"][part_key] = {
            "openpnp_id": data.get("openpnp_id", part_key),
            "package_id": data.get("package_id", ""),
            "height": float(data.get("height", 0)),
            "speed": float(data.get("speed", 1.0)),
            "nozzle_tips": data.get("nozzle_tips", []),
            "footprint": data.get("footprint"),
            "footprint_source": data.get("footprint_source", "manual"),
            "footprint_fetched": data.get("footprint_fetched", ""),
        }
        kicad_openpnp.save_openpnp_parts(openpnp_data)
        kicad_openpnp.regenerate_pnp_part_map(openpnp_data)
        return openpnp_data

    def remove_openpnp_part(self, part_key: str) -> dict:
        """Remove OpenPnP metadata for a part."""
        openpnp_data = kicad_openpnp.load_openpnp_parts()
        openpnp_data["parts"].pop(part_key, None)
        kicad_openpnp.save_openpnp_parts(openpnp_data)
        kicad_openpnp.regenerate_pnp_part_map(openpnp_data)
        return openpnp_data

    def sync_openpnp(self) -> dict:
        """Write packages.xml + parts.xml to OpenPnP config dir, rebuild pnp_part_map."""
        openpnp_data = kicad_openpnp.load_openpnp_parts()
        config_path = openpnp_data.get("openpnp_config_path", "")
        if not config_path or not os.path.isdir(config_path):
            raise ValueError(
                f"OpenPnP config path not set or invalid: {config_path!r}. "
                "Set it via Preferences or set_openpnp_config_path."
            )
        pkg_path = kicad_openpnp.generate_openpnp_packages_xml(openpnp_data, config_path)
        parts_path = kicad_openpnp.generate_openpnp_parts_xml(openpnp_data, config_path)
        kicad_openpnp.regenerate_pnp_part_map(openpnp_data)
        return {"packages_xml": pkg_path, "parts_xml": parts_path}

    def set_openpnp_config_path(self, path: str) -> dict:
        """Set the OpenPnP config directory path."""
        if not os.path.isdir(path):
            raise ValueError(f"Not a directory: {path}")
        openpnp_data = kicad_openpnp.load_openpnp_parts()
        openpnp_data["openpnp_config_path"] = path
        kicad_openpnp.save_openpnp_parts(openpnp_data)
        return openpnp_data

    def detect_kicad_lib_path(self) -> dict:
        """Auto-detect KiCad footprint library paths on this system."""
        paths = kicad_openpnp.find_kicad_lib_paths()
        return {"paths": paths}

    @staticmethod
    def _read_text(path: str) -> str:
        """Read a text file, auto-detecting UTF-16 vs UTF-8 encoding."""
        with open(path, "rb") as f:
            bom = f.read(2)
        encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        with open(path, encoding=encoding) as f:
            return f.read()
