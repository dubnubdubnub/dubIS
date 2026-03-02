"""Inventory API — all CSV read/write/rebuild logic exposed to JS via pywebview."""

import csv
import json
import os
import re
from collections import OrderedDict
from datetime import datetime


class InventoryApi:
    FIELDNAMES = [
        "Digikey Part Number", "LCSC Part Number", "Manufacture Part Number",
        "Manufacturer", "Customer NO.", "Package", "Description", "RoHS",
        "Quantity", "Unit Price($)", "Ext.Price($)",
        "Estimated lead time (business days)", "Date Code / Lot No.",
    ]

    ADJ_FIELDNAMES = [
        "timestamp", "type", "lcsc_part", "quantity", "bom_file", "board_qty", "note",
    ]

    SECTION_ORDER = [
        "Connectors", "Switches", "Passives - Resistors", "Passives - Capacitors",
        "Passives - Inductors", "LEDs", "Crystals & Oscillators", "Diodes",
        "Discrete Semiconductors", "ICs - Microcontrollers",
        "ICs - Power / Voltage Regulators", "ICs - Voltage References",
        "ICs - Sensors", "ICs - Amplifiers", "ICs - Motor Drivers",
        "ICs - Interface", "ICs - ESD Protection", "Mechanical & Hardware", "Other",
    ]

    def __init__(self):
        self.base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.input_csv = os.path.join(self.base_dir, "purchase_ledger.csv")
        self.output_csv = os.path.join(self.base_dir, "inventory.csv")
        self.adjustments_csv = os.path.join(self.base_dir, "adjustments.csv")
        self.prefs_json = os.path.join(self.base_dir, "preferences.json")

    # ── Utility methods (ported from organize_inventory.py) ──────────────

    @staticmethod
    def fix_double_utf8(text):
        """Fix double-encoded UTF-8 text."""
        for enc in ("cp1252", "latin-1"):
            try:
                return text.encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return text

    @staticmethod
    def get_part_key(row):
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

    @staticmethod
    def parse_resistance(desc):
        m = re.search(r"(\d+\.?\d*)\s*(m|k|M)?\s*[\u03a9\u03c9\u2126]", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"m": 1e-3, "": 1, "k": 1e3, "M": 1e6}[prefix]

    @staticmethod
    def parse_capacitance(desc):
        m = re.search(r"(\d+\.?\d*)\s*(p|n|u|\u00b5|m)?\s*F\b", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]

    @staticmethod
    def parse_inductance(desc):
        m = re.search(r"(\d+\.?\d*)\s*(n|u|\u00b5|m)?\s*H\b", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]

    @staticmethod
    def categorize(row):
        desc = (row.get("Description") or "").lower()
        pkg = (row.get("Package") or "").lower()
        mpn = (row.get("Manufacture Part Number") or "").lower()

        connector_kw = [
            "connector", "header", "receptacle", "banana", "xt60", "xt30",
            "ipex", "usb-c", "usb type-c", "crimp", "housing",
            "nv-2a", "nv-4a", "nv-2y", "nv-4y", "df40",
        ]
        if any(kw in desc for kw in connector_kw):
            return "Connectors"
        if any(kw in mpn for kw in [
            "xt60", "xt30", "sm04b", "sm05b", "sm06b",
            "svh-21t", "nv-", "df40", "bwipx", "xy-sh", "type-c",
        ]):
            return "Connectors"

        if ("switch" in desc or "tactile" in desc) and "switching regulator" not in desc:
            return "Switches"
        if "led" in desc or "emitter" in desc or "emit" in desc:
            return "LEDs"
        if "inductor" in desc:
            return "Passives - Inductors"
        if "resistor" in desc:
            return "Passives - Resistors"
        if "\u03c9" in desc or "\u03a9" in desc or "\u2126" in desc or "ohm" in desc:
            return "Passives - Resistors"
        mfr = (row.get("Manufacturer") or "").lower()
        if "uni-royal" in mfr:
            return "Passives - Resistors"
        if "ta-i tech" in mfr and "m\u03c9" in desc:
            return "Passives - Resistors"
        if "capacitor" in desc or "electrolytic" in desc or "cap cer" in desc:
            return "Passives - Capacitors"
        if "crystal" in desc or "oscillator" in desc:
            return "Crystals & Oscillators"
        if "diode" in desc and "esd" not in desc:
            return "Diodes"
        if "esd" in desc:
            return "ICs - ESD Protection"
        if "transistor" in desc or "bjt" in desc or "mosfet" in desc:
            return "Discrete Semiconductors"
        if "voltage regulator" in desc or "buck" in desc or "ldo" in desc or "linear voltage" in desc:
            return "ICs - Power / Voltage Regulators"
        if "switching regulator" in desc:
            return "ICs - Power / Voltage Regulators"
        if "voltage reference" in desc or "ref30" in mpn:
            return "ICs - Voltage References"
        if "current sensor" in desc:
            return "ICs - Sensors"
        if "amplifier" in desc or "csa" in desc:
            return "ICs - Amplifiers"
        if any(kw in desc for kw in ["motor", "mtr drvr", "half-bridge", "three-phase"]):
            return "ICs - Motor Drivers"
        if any(kw in mpn for kw in ["drv8", "l6226"]):
            return "ICs - Motor Drivers"
        if "transceiver" in desc or "driver" in desc:
            return "ICs - Interface"
        if "position" in desc or "angle" in desc or "mt6835" in mpn:
            return "ICs - Sensors"
        if "microcontroller" in desc or "mcu" in desc:
            return "ICs - Microcontrollers"
        if "spacer" in desc or "standoff" in desc or "battery holder" in desc:
            return "Mechanical & Hardware"
        return "Other"

    # ── Core pipeline ────────────────────────────────────────────────────

    def _read_raw_inventory(self):
        """Read purchase_ledger.csv, fix encoding, merge duplicates.
        Returns (fieldnames, merged_OrderedDict).
        """
        if not os.path.exists(self.input_csv):
            return list(self.FIELDNAMES), OrderedDict()

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
        merged = OrderedDict()
        for r in rows:
            pn = self.get_part_key(r)
            if not pn:
                continue
            qty = int(r["Quantity"].replace(",", "")) if r.get("Quantity") else 0
            ext = float(r["Ext.Price($)"]) if r.get("Ext.Price($)") else 0.0
            if pn in merged:
                prev_qty = int(merged[pn]["Quantity"].replace(",", "") or "0")
                merged[pn]["Quantity"] = str(prev_qty + qty)
                new_ext = float(merged[pn]["Ext.Price($)"] or "0") + ext
                merged[pn]["Ext.Price($)"] = f"{new_ext:.2f}"
                old_up = float(merged[pn]["Unit Price($)"] or "0")
                new_up = float(r["Unit Price($)"]) if r.get("Unit Price($)") else 0.0
                if new_up > 0 and new_up < old_up:
                    merged[pn]["Unit Price($)"] = r["Unit Price($)"]
            else:
                r_copy = dict(r)
                r_copy["Quantity"] = str(qty)
                merged[pn] = r_copy

        return fieldnames, merged

    def _apply_adjustments(self, merged, fieldnames):
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

                current = int(merged[pn]["Quantity"].replace(",", "") or "0")
                if adj_type == "set":
                    new_qty = max(0, qty)
                elif adj_type in ("consume", "add", "remove"):
                    new_qty = max(0, current + qty)
                else:
                    continue
                merged[pn]["Quantity"] = str(new_qty)

    def _categorize_and_sort(self, parts):
        """Categorize parts and sort within sections. Returns OrderedDict."""
        categorized = {}
        for p in parts:
            cat = self.categorize(p)
            categorized.setdefault(cat, []).append(p)

        if "Passives - Resistors" in categorized:
            categorized["Passives - Resistors"].sort(
                key=lambda r: self.parse_resistance(r.get("Description", "")))
        if "Passives - Capacitors" in categorized:
            categorized["Passives - Capacitors"].sort(
                key=lambda r: self.parse_capacitance(r.get("Description", "")))
        if "Passives - Inductors" in categorized:
            categorized["Passives - Inductors"].sort(
                key=lambda r: self.parse_inductance(r.get("Description", "")))
        return categorized

    def _write_organized(self, categorized, fieldnames):
        """Write inventory.csv."""
        with open(self.output_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Section"] + list(fieldnames))
            for section in self.SECTION_ORDER:
                items = categorized.get(section)
                if not items:
                    continue
                writer.writerow([])
                writer.writerow([f"=== {section} ==="] + [""] * len(fieldnames))
                for item in items:
                    writer.writerow([section] + [item.get(fn, "") for fn in fieldnames])

    def _rebuild(self):
        """Full rebuild pipeline: merge -> adjust -> categorize -> sort -> write.
        Returns fresh inventory list.
        """
        fieldnames, merged = self._read_raw_inventory()
        self._apply_adjustments(merged, fieldnames)
        parts = list(merged.values())
        categorized = self._categorize_and_sort(parts)
        self._write_organized(categorized, fieldnames)
        return self._load_organized()

    def _load_organized(self):
        """Load organized inventory as list of dicts for JSON."""
        rows = []
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
                    "qty": int(float((row.get("Quantity") or "0").replace(",", ""))),
                    "unit_price": float((row.get("Unit Price($)") or "0").replace(",", "") or "0"),
                    "ext_price": float((row.get("Ext.Price($)") or "0").replace(",", "") or "0"),
                })
        return rows

    # ── Adjustment helpers ───────────────────────────────────────────────

    def _append_adjustment(self, adj_type, part_key, quantity, note="",
                           bom_file="", board_qty=""):
        """Append one row to adjustments.csv."""
        exists = os.path.exists(self.adjustments_csv)
        with open(self.adjustments_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.ADJ_FIELDNAMES)
            if not exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "type": adj_type,
                "lcsc_part": part_key,
                "quantity": quantity,
                "bom_file": bom_file,
                "board_qty": board_qty,
                "note": note,
            })

    # ── Public API methods (called from JS via pywebview) ────────────────

    def rebuild_inventory(self):
        """Force full rebuild of inventory.csv from purchase_ledger + adjustments."""
        return self._rebuild()

    def adjust_part(self, adj_type, part_key, quantity, note=""):
        """Set/add/remove adjustment. Returns fresh inventory."""
        quantity = int(quantity)
        if adj_type == "remove":
            record_qty = -abs(quantity)
        elif adj_type == "add":
            record_qty = abs(quantity)
        elif adj_type == "set":
            record_qty = quantity
        else:
            return {"error": f"Unknown adjustment type: {adj_type}"}
        self._append_adjustment(adj_type, part_key, record_qty, note=note)
        return self._rebuild()

    def consume_bom(self, matches_json, board_qty, bom_name, note=""):
        """Consume matched BOM parts. Returns fresh inventory."""
        matches = json.loads(matches_json) if isinstance(matches_json, str) else matches_json
        board_qty = int(board_qty)
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        exists = os.path.exists(self.adjustments_csv)
        with open(self.adjustments_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.ADJ_FIELDNAMES)
            if not exists:
                writer.writeheader()
            for m in matches:
                delta = -(m["bom_qty"] * board_qty)
                writer.writerow({
                    "timestamp": ts,
                    "type": "consume",
                    "lcsc_part": m["part_key"],
                    "quantity": delta,
                    "bom_file": bom_name,
                    "board_qty": board_qty,
                    "note": note or f"consumed {board_qty}x {bom_name}",
                })
        return self._rebuild()

    def import_purchases(self, rows_json):
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory."""
        rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
        if not rows:
            return {"error": "No rows to import"}

        # Read existing fieldnames or use defaults
        if os.path.exists(self.input_csv):
            with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
        else:
            fieldnames = list(self.FIELDNAMES)

        # Append new rows
        write_header = not os.path.exists(self.input_csv) or os.path.getsize(self.input_csv) == 0
        with open(self.input_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for row in rows:
                inv_row = {fn: row.get(fn, "") for fn in fieldnames}
                writer.writerow(inv_row)

        return self._rebuild()

    def update_part_price(self, part_key, unit_price=None, ext_price=None):
        """Update unit price and ext price for a part in purchase_ledger.csv.
        Auto-calculates the missing price field if only one is provided.
        Returns fresh inventory after rebuild.
        """
        if unit_price is not None:
            unit_price = float(unit_price)
        if ext_price is not None:
            ext_price = float(ext_price)

        if not os.path.exists(self.input_csv):
            return {"error": "No purchase ledger found"}

        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        found = False
        for row in rows:
            pk = self.get_part_key(row)
            if pk == part_key:
                qty = int(float((row.get("Quantity") or "0").replace(",", "")))
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

        with open(self.input_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return self._rebuild()

    def detect_columns(self, headers_json):
        """Auto-detect column mapping for purchase CSV import.
        Returns dict of {source_column_index: target_inventory_field}.
        """
        headers = json.loads(headers_json) if isinstance(headers_json, str) else headers_json
        lower_headers = [h.lower().strip() for h in headers]

        # Collect candidates for each target field
        candidates = {}
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
        mapping = {}
        used_indices = set()
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

    def load_preferences(self):
        """Read preferences.json and return its contents (empty dict if missing/corrupt)."""
        try:
            if os.path.exists(self.prefs_json):
                with open(self.prefs_json, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def save_preferences(self, prefs_json):
        """Write preferences JSON string to disk."""
        prefs = json.loads(prefs_json) if isinstance(prefs_json, str) else prefs_json
        with open(self.prefs_json, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)

    def save_file_dialog(self, content, default_name="export.csv", default_dir=None,
                         links_json=None):
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
                links = json.loads(links_json) if isinstance(links_json, str) else links_json
                if links:
                    links_path = os.path.splitext(path)[0] + ".links.json"
                    with open(links_path, "w", encoding="utf-8") as f:
                        json.dump(links, f, indent=2)
            return {"path": path}
        return None

    def open_file_dialog(self, title="Select CSV file", default_dir=None):
        """Open native file dialog, return {name, content, directory} or None."""
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
            }
            # Check for sidecar .links.json
            links_path = os.path.splitext(path)[0] + ".links.json"
            if os.path.exists(links_path):
                try:
                    with open(links_path, encoding="utf-8") as lf:
                        resp["links"] = json.load(lf)
                except (json.JSONDecodeError, OSError):
                    pass
            return resp
        return None

    @staticmethod
    def _read_text(path):
        """Read a text file, auto-detecting UTF-16 vs UTF-8 encoding."""
        with open(path, "rb") as f:
            bom = f.read(2)
        encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        with open(path, encoding=encoding) as f:
            return f.read()
