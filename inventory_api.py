"""Inventory API — all CSV read/write/rebuild logic exposed to JS via pywebview."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


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

    def __init__(self) -> None:
        self.base_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.input_csv: str = os.path.join(self.base_dir, "purchase_ledger.csv")
        self.output_csv: str = os.path.join(self.base_dir, "inventory.csv")
        self.adjustments_csv: str = os.path.join(self.base_dir, "adjustments.csv")
        self.prefs_json: str = os.path.join(self.base_dir, "preferences.json")
        self._force_close: bool = False
        self._closing: bool = False
        self._bom_dirty: bool = False
        self._lcsc_cache: dict[str, dict[str, Any] | None] = {}
        self._digikey_cache: dict[str, dict[str, Any] | None] = {}
        self._digikey_token: str | None = None
        self._digikey_token_expiry: float = 0

    # ── Utility methods (ported from organize_inventory.py) ──────────────

    @staticmethod
    def _parse_qty(value: Any, default: int = 0) -> int:
        """Parse a quantity string to int, tolerating commas and floats."""
        try:
            return int(float(str(value).replace(",", "")))
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

    @staticmethod
    def parse_resistance(desc: str) -> float:
        m = re.search(r"(\d+\.?\d*)\s*(m|k|M)?\s*[\u03a9\u03c9\u2126]", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"m": 1e-3, "": 1, "k": 1e3, "M": 1e6}[prefix]

    @staticmethod
    def parse_capacitance(desc: str) -> float:
        m = re.search(r"(\d+\.?\d*)\s*(p|n|u|\u00b5|m)?\s*F\b", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]

    @staticmethod
    def parse_inductance(desc: str) -> float:
        m = re.search(r"(\d+\.?\d*)\s*(n|u|\u00b5|m)?\s*H\b", desc)
        if not m:
            return float("inf")
        value = float(m.group(1))
        prefix = m.group(2) or ""
        return value * {"n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "m": 1e-3, "": 1}[prefix]

    @staticmethod
    def categorize(row: dict[str, str]) -> str:
        desc = (row.get("Description") or "").lower()
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
            ext = float(r["Ext.Price($)"]) if r.get("Ext.Price($)") else 0.0
            if pn in merged:
                prev_qty = self._parse_qty(merged[pn]["Quantity"])
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

    def _write_organized(self, categorized: dict[str, list[dict[str, str]]],
                         fieldnames: list[str]) -> None:
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
                    "unit_price": float((row.get("Unit Price($)") or "0").replace(",", "") or "0"),
                    "ext_price": float((row.get("Ext.Price($)") or "0").replace(",", "") or "0"),
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

    # ── LCSC product preview ────────────────────────────────────────────

    def fetch_lcsc_product(self, product_code: str) -> dict[str, Any] | None:
        """Fetch LCSC product details by product code (e.g. C2040).

        Returns a normalized dict of product info, or None if not found/failed.
        Results (including None) are cached for the session.
        """
        product_code = str(product_code).strip().upper()
        if not re.match(r"^C\d{4,}$", product_code):
            raise ValueError(f"Invalid LCSC product code: {product_code!r}")

        if product_code in self._lcsc_cache:
            return self._lcsc_cache[product_code]

        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={product_code}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("LCSC fetch failed for %s: %s", product_code, exc)
            self._lcsc_cache[product_code] = None
            return None

        result_data = data.get("result") if isinstance(data, dict) else None
        if not result_data or not isinstance(result_data, dict):
            logger.warning("LCSC returned no result for %s", product_code)
            self._lcsc_cache[product_code] = None
            return None

        # Extract price tiers
        prices = []
        for tier in (result_data.get("productPriceList") or []):
            if isinstance(tier, dict):
                prices.append({
                    "qty": tier.get("ladder", 0),
                    "price": tier.get("productPrice", 0),
                })

        # Build normalized response
        cat_name = ""
        subcat_name = ""
        for cat in (result_data.get("parentCatalogList") or []):
            if isinstance(cat, dict):
                if not cat_name:
                    cat_name = cat.get("catalogName", "")
                else:
                    subcat_name = cat.get("catalogName", "")

        # Extract key attributes from paramVOList
        attributes = []
        for param in (result_data.get("paramVOList") or []):
            if isinstance(param, dict):
                name = param.get("paramNameEn", "")
                value = param.get("paramValueEn", "")
                if name and value and value != "-":
                    attributes.append({"name": name, "value": value})

        # Image: API returns productImages array, fall back to productImageUrl
        images = result_data.get("productImages") or []
        image_url = images[0] if images else result_data.get("productImageUrl", "")

        product = {
            "productCode": result_data.get("productCode", product_code),
            "title": result_data.get("title", "") or result_data.get("productIntroEn", ""),
            "manufacturer": result_data.get("brandNameEn", ""),
            "mpn": result_data.get("productModel", ""),
            "package": result_data.get("encapStandard", ""),
            "description": result_data.get("productIntroEn", ""),
            "stock": result_data.get("stockNumber", 0),
            "prices": prices,
            "imageUrl": image_url,
            "pdfUrl": result_data.get("pdfUrl", ""),
            "lcscUrl": f"https://www.lcsc.com/product-detail/{product_code}.html",
            "category": cat_name,
            "subcategory": subcat_name,
            "attributes": attributes,
            "provider": "lcsc",
        }

        self._lcsc_cache[product_code] = product
        return product

    # ── Digikey credential management ─────────────────────────────────────

    @staticmethod
    def _get_digikey_credentials() -> tuple[str, str] | None:
        """Retrieve Digikey API credentials from OS keyring."""
        import keyring
        client_id = keyring.get_password("dubIS", "digikey_client_id")
        client_secret = keyring.get_password("dubIS", "digikey_client_secret")
        if client_id and client_secret:
            return (client_id, client_secret)
        return None

    def save_digikey_credentials(self, client_id: str, client_secret: str) -> dict[str, str]:
        """Store Digikey API credentials in OS keyring."""
        import keyring
        if not client_id or not client_secret:
            raise ValueError("Client ID and Client Secret must not be empty")
        keyring.set_password("dubIS", "digikey_client_id", client_id)
        keyring.set_password("dubIS", "digikey_client_secret", client_secret)
        self._digikey_token = None
        self._digikey_token_expiry = 0
        return {"status": "ok"}

    def clear_digikey_credentials(self) -> dict[str, str]:
        """Remove Digikey API credentials from OS keyring."""
        import keyring
        try:
            keyring.delete_password("dubIS", "digikey_client_id")
        except keyring.errors.PasswordDeleteError:
            pass
        try:
            keyring.delete_password("dubIS", "digikey_client_secret")
        except keyring.errors.PasswordDeleteError:
            pass
        self._digikey_token = None
        self._digikey_token_expiry = 0
        return {"status": "ok"}

    def get_digikey_credentials_status(self) -> dict[str, bool]:
        """Check whether Digikey credentials are configured (never exposes secrets)."""
        return {"configured": self._get_digikey_credentials() is not None}

    def _ensure_digikey_token(self) -> str:
        """Return a valid Digikey OAuth2 bearer token, refreshing if needed."""
        if self._digikey_token and time.time() < self._digikey_token_expiry:
            return self._digikey_token

        creds = self._get_digikey_credentials()
        if not creds:
            raise ValueError("Digikey API credentials not configured")

        client_id, client_secret = creds
        body = (
            "grant_type=client_credentials"
            f"&client_id={quote(client_id)}"
            f"&client_secret={quote(client_secret)}"
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.digikey.com/v1/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Digikey OAuth token request failed: {exc}") from exc

        token = data.get("access_token")
        if not token:
            raise RuntimeError("Digikey OAuth response missing access_token")

        expires_in = int(data.get("expires_in", 600))
        self._digikey_token = token
        self._digikey_token_expiry = time.time() + expires_in - 30
        return token

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Digikey product details by part number.

        Returns a normalized dict (same shape as LCSC), or None if not found.
        Results (including None) are cached for the session.
        """
        part_number = str(part_number).strip()
        if not part_number:
            raise ValueError("Part number must not be empty")

        if part_number in self._digikey_cache:
            return self._digikey_cache[part_number]

        try:
            token = self._ensure_digikey_token()
        except (ValueError, RuntimeError) as exc:
            logger.warning("Digikey auth failed for %s: %s", part_number, exc)
            self._digikey_cache[part_number] = None
            return None

        creds = self._get_digikey_credentials()
        client_id = creds[0] if creds else ""

        encoded_pn = quote(part_number, safe="")
        url = f"https://api.digikey.com/products/v4/search/{encoded_pn}/productdetails"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "X-IBMCLIENTID": client_id,
            "Accept": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                logger.info("Digikey part not found: %s", part_number)
            else:
                logger.warning("Digikey fetch failed for %s: HTTP %s", part_number, exc.code)
            self._digikey_cache[part_number] = None
            return None
        except Exception as exc:
            logger.warning("Digikey fetch failed for %s: %s", part_number, exc)
            self._digikey_cache[part_number] = None
            return None

        # Normalize to same shape as LCSC product
        prices = []
        for tier in (data.get("StandardPricing") or []):
            if isinstance(tier, dict):
                prices.append({
                    "qty": tier.get("BreakQuantity", 0),
                    "price": tier.get("UnitPrice", 0),
                })

        attributes = []
        package = ""
        for param in (data.get("Parameters") or []):
            if isinstance(param, dict):
                name = param.get("ParameterText", "")
                value = param.get("ValueText", "")
                if name and value and value != "-":
                    attributes.append({"name": name, "value": value})
                    if "package" in name.lower() and not package:
                        package = value

        mfr = data.get("Manufacturer") or {}

        product = {
            "productCode": data.get("DigiKeyPartNumber", part_number),
            "title": data.get("ProductDescription", ""),
            "manufacturer": mfr.get("Name", "") if isinstance(mfr, dict) else "",
            "mpn": data.get("ManufacturerPartNumber", ""),
            "package": package,
            "description": data.get("DetailedDescription", ""),
            "stock": data.get("QuantityAvailable", 0),
            "prices": prices,
            "imageUrl": data.get("PrimaryPhoto", ""),
            "pdfUrl": data.get("PrimaryDatasheet", ""),
            "digikeyUrl": data.get("ProductUrl", ""),
            "attributes": attributes,
            "provider": "digikey",
        }

        self._digikey_cache[part_number] = product
        return product

    # ── Public API methods (called from JS via pywebview) ────────────────

    def rebuild_inventory(self) -> list[dict[str, Any]]:
        """Force full rebuild of inventory.csv from purchase_ledger + adjustments."""
        return self._rebuild()

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "") -> list[dict[str, Any]] | dict[str, str]:
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
            return {"error": f"Unknown adjustment type: {adj_type}"}
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
        self._append_csv_rows(self.adjustments_csv, self.ADJ_FIELDNAMES, adj_rows)
        return self._rebuild()

    def import_purchases(self, rows_json: str | list[dict[str, str]]) -> list[dict[str, Any]] | dict[str, str]:
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory."""
        rows = self._ensure_parsed(rows_json)
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

    def update_part_price(self, part_key: str, unit_price: float | None = None,
                          ext_price: float | None = None) -> list[dict[str, Any]] | dict[str, str]:
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
        except Exception:
            logger.debug("Window already destroyed or unavailable", exc_info=True)

    @staticmethod
    def _read_text(path: str) -> str:
        """Read a text file, auto-detecting UTF-16 vs UTF-8 encoding."""
        with open(path, "rb") as f:
            bom = f.read(2)
        encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        with open(path, encoding=encoding) as f:
            return f.read()
