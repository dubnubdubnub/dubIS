"""Inventory API — all CSV read/write/rebuild logic exposed to JS via pywebview."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
import urllib.error
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
        self._dk_window = None
        self._dk_loaded = threading.Event()
        self._dk_lock = threading.Lock()
        self._dk_cdp_port: int | None = None
        self._dk_sync_result: dict[str, Any] = {}
        self._dk_poll_stop = threading.Event()
        self._dk_pending_cookies: list[dict] | None = None

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

    # Each rule: first match wins.  Within a rule, keywords in the same field
    # are OR'd; different fields (desc + mfr) are AND'd.  exclude_desc vetoes.
    CATEGORY_RULES: list[dict[str, Any]] = [
        # Connectors
        {"category": "Connectors", "desc": [
            "connector", "header", "receptacle", "banana", "xt60", "xt30",
            "ipex", "usb-c", "usb type-c", "crimp", "housing",
            "nv-2a", "nv-4a", "nv-2y", "nv-4y", "df40",
        ]},
        {"category": "Connectors", "mpn": [
            "xt60", "xt30", "sm04b", "sm05b", "sm06b",
            "svh-21t", "nv-", "df40", "bwipx", "xy-sh", "type-c",
        ]},
        # Switches (not "switching regulator")
        {"category": "Switches", "desc": ["switch", "tactile"],
         "exclude_desc": ["switching regulator"]},
        # LEDs
        {"category": "LEDs", "desc": ["led", "emitter", "emit"]},
        # Passives
        {"category": "Passives - Inductors", "desc": ["inductor"]},
        {"category": "Passives - Resistors", "desc": ["resistor"]},
        {"category": "Passives - Resistors", "desc": ["\u03c9", "\u03a9", "\u2126", "ohm"]},
        {"category": "Passives - Resistors", "mfr": ["uni-royal"]},
        {"category": "Passives - Resistors", "mfr": ["ta-i tech"], "desc": ["m\u03c9"]},
        {"category": "Passives - Capacitors", "desc": ["capacitor", "electrolytic", "cap cer"]},
        # Crystals
        {"category": "Crystals & Oscillators", "desc": ["crystal", "oscillator"]},
        # Diodes (not ESD)
        {"category": "Diodes", "desc": ["diode"], "exclude_desc": ["esd"]},
        {"category": "ICs - ESD Protection", "desc": ["esd"]},
        # Discrete
        {"category": "Discrete Semiconductors", "desc": ["transistor", "bjt", "mosfet"]},
        # Power
        {"category": "ICs - Power / Voltage Regulators", "desc": [
            "voltage regulator", "buck", "ldo", "linear voltage", "switching regulator",
        ]},
        # References
        {"category": "ICs - Voltage References", "desc": ["voltage reference"]},
        {"category": "ICs - Voltage References", "mpn": ["ref30"]},
        # Sensors
        {"category": "ICs - Sensors", "desc": ["current sensor"]},
        # Amplifiers
        {"category": "ICs - Amplifiers", "desc": ["amplifier", "csa"]},
        # Motor Drivers
        {"category": "ICs - Motor Drivers", "desc": ["motor", "mtr drvr", "half-bridge", "three-phase"]},
        {"category": "ICs - Motor Drivers", "mpn": ["drv8", "l6226"]},
        # Interface
        {"category": "ICs - Interface", "desc": ["transceiver", "driver"]},
        # Sensors (position / angle)
        {"category": "ICs - Sensors", "desc": ["position", "angle"]},
        {"category": "ICs - Sensors", "mpn": ["mt6835"]},
        # MCU
        {"category": "ICs - Microcontrollers", "desc": ["microcontroller", "mcu"]},
        # Mechanical
        {"category": "Mechanical & Hardware", "desc": ["spacer", "standoff", "battery holder"]},
    ]

    @staticmethod
    def categorize(row: dict[str, str]) -> str:
        desc = (row.get("Description") or "").lower()
        mpn = (row.get("Manufacture Part Number") or "").lower()
        mfr = (row.get("Manufacturer") or "").lower()

        for rule in InventoryApi.CATEGORY_RULES:
            if "exclude_desc" in rule and any(kw in desc for kw in rule["exclude_desc"]):
                continue
            matched = True
            has_condition = False
            for field, text in [("desc", desc), ("mpn", mpn), ("mfr", mfr)]:
                if field in rule:
                    has_condition = True
                    if not any(kw in text for kw in rule[field]):
                        matched = False
                        break
            if has_condition and matched:
                return rule["category"]
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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

    # ── Digikey browser session ──────────────────────────────────────────

    def _ensure_dk_window(self) -> None:
        """Ensure the hidden Digikey webview window exists, creating if needed.

        NOT thread-safe — caller must hold ``_dk_lock``.
        If pending cookies were stored by the login flow, they are injected
        after the window is ready.
        """
        if self._dk_window is not None:
            return
        import webview

        self._dk_loaded.clear()

        def on_loaded():
            self._dk_loaded.set()

        def on_closing():
            try:
                self._dk_window.hide()
            except (AttributeError, RuntimeError):
                pass
            return False  # Hide instead of destroy

        self._dk_window = webview.create_window(
            "Digikey",
            url="https://www.digikey.com",
            hidden=True,
            width=900,
            height=700,
        )
        self._dk_window.events.loaded += on_loaded
        self._dk_window.events.closing += on_closing
        self._dk_loaded.wait(timeout=15)

        # Inject cookies that were stored during login
        if self._dk_pending_cookies:
            try:
                self._inject_cookies_to_dk_window(self._dk_pending_cookies)
                print(f"[DK] injected {len(self._dk_pending_cookies)} pending cookies into dk window", flush=True)
            except Exception as exc:
                print(f"[DK] pending cookie injection failed: {exc}", flush=True)
            self._dk_pending_cookies = None

    @staticmethod
    def _find_default_browser_exe() -> str | None:
        """Find the default browser executable on Windows via registry."""
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
            ) as key:
                prog_id = winreg.QueryValueEx(key, "ProgId")[0]
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                rf"{prog_id}\shell\open\command",
            ) as key:
                cmd = winreg.QueryValueEx(key, "")[0]
            exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]
            return exe if os.path.exists(exe) else None
        except OSError:
            return None

    def check_digikey_session(self) -> dict[str, Any]:
        """Check if there's an existing Digikey session from the default browser.

        Launches the browser headless with CDP to read cookies without
        showing a window.  Called on app startup.
        """
        import random
        import subprocess
        import time

        exe = self._find_default_browser_exe()
        if not exe:
            return {"logged_in": False}

        port = random.randint(19200, 19299)
        proc = subprocess.Popen(
            [exe, f"--headless=new", f"--remote-debugging-port={port}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # Give headless browser a moment to start
            time.sleep(1.5)
            cookies = self._cdp_get_cookies(port)
            dk_cookies = [c for c in cookies if "digikey.com" in c.get("domain", "")]
            if dk_cookies and self._check_dk_cookies_logged_in(dk_cookies):
                self._dk_pending_cookies = dk_cookies
                self._dk_sync_result = {
                    "status": "ok",
                    "message": "Logged in",
                    "logged_in": True,
                    "cookies_injected": len(dk_cookies),
                    "browser": "cdp",
                }
                print(f"[DK] startup: found existing session ({len(dk_cookies)} cookies)", flush=True)
                return {"logged_in": True}
            print(f"[DK] startup: no existing session ({len(dk_cookies)} digikey cookies)", flush=True)
            return {"logged_in": False}
        except Exception as exc:
            print(f"[DK] startup: session check failed: {exc}", flush=True)
            return {"logged_in": False}
        finally:
            try:
                proc.terminate()
            except OSError:
                pass

    def start_digikey_login(self) -> dict[str, Any]:
        """Launch the default browser with CDP enabled and open the login page.

        Starts a background thread that polls CDP for cookies so that
        ``sync_digikey_cookies`` can return instantly with no I/O.
        """
        import random
        import subprocess

        self._dk_poll_stop.set()  # stop any previous poll thread

        url = "https://www.digikey.com/MyDigiKey/Login"
        exe = self._find_default_browser_exe()
        print(f"[DK] login: browser exe={exe}", flush=True)
        if not exe:
            import webbrowser
            webbrowser.open(url)
            self._dk_cdp_port = None
            self._dk_sync_result = {
                "status": "error",
                "message": "Could not find browser — cookie sync unavailable.",
                "logged_in": False,
                "cookies_injected": 0,
            }
            return {"status": "opened", "cdp": False}

        port = random.randint(19200, 19299)
        print(f"[DK] login: launching with CDP port {port}", flush=True)
        subprocess.Popen(
            [exe, f"--remote-debugging-port={port}", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._dk_cdp_port = port
        self._dk_sync_result = {
            "status": "waiting",
            "message": "Browser opened — waiting for login...",
            "logged_in": False,
            "cookies_injected": 0,
        }

        # Start background CDP poll thread
        self._dk_poll_stop = threading.Event()
        thread = threading.Thread(target=self._dk_poll_loop, args=(port,), daemon=True)
        thread.start()

        print("[DK] login: browser launched, poll thread started", flush=True)
        return {"status": "opened", "cdp": True, "port": port}

    @staticmethod
    def _check_dk_cookies_logged_in(cookies: list[dict]) -> bool:
        """Check whether cookies indicate a logged-in Digikey session.

        Looks for session cookies that are only present after login.
        """
        cookie_names = {c.get("name", "") for c in cookies}
        # dkuhint = "digikey user hint", only set after login
        return "dkuhint" in cookie_names

    def _dk_poll_loop(self, port: int) -> None:
        """Background thread: poll CDP for cookies, store when found.

        Does NOT touch the UI thread at all — no webview creation, no Invoke.
        Cookies are stored in ``_dk_pending_cookies`` and injected later when
        ``_ensure_dk_window`` creates the hidden scraping window.
        """
        for attempt in range(1, 41):  # max ~2 minutes at 3s intervals
            if self._dk_poll_stop.is_set():
                return

            debug_log = []
            try:
                all_cdp = self._cdp_get_cookies(port)
                cdp_cookies = [c for c in all_cdp if "digikey.com" in c.get("domain", "")]
                debug_log.append(
                    f"cdp(port={port}): {len(cdp_cookies)} digikey cookies "
                    f"(of {len(all_cdp)} total)"
                )
                print(f"[DK] poll #{attempt}: {len(cdp_cookies)} digikey cookies", flush=True)

                if cdp_cookies and self._check_dk_cookies_logged_in(cdp_cookies):
                    # Logged in — store cookies for later injection
                    self._dk_pending_cookies = cdp_cookies
                    cookie_names = [c["name"] for c in cdp_cookies[:20]]
                    self._dk_sync_result = {
                        "status": "ok",
                        "message": "Logged in",
                        "logged_in": True,
                        "cookies_injected": len(cdp_cookies),
                        "browser": "cdp",
                        "debug": debug_log + [f"names={cookie_names}"],
                    }
                    print(f"[DK] poll #{attempt}: logged in!", flush=True)
                    return  # done

            except ConnectionRefusedError:
                debug_log.append(f"cdp(port={port}): ConnectionRefusedError")
                self._dk_sync_result = {
                    "status": "browser_running",
                    "message": "Close your browser and click Login again.",
                    "logged_in": False,
                    "cookies_injected": 0,
                    "debug": debug_log,
                }
                print(f"[DK] poll #{attempt}: connection refused", flush=True)
                return  # stop polling — browser was already running

            except Exception as exc:
                debug_log.append(f"cdp(port={port}): {type(exc).__name__}: {exc}")
                self._dk_sync_result = {
                    "status": "waiting",
                    "message": "Waiting for login...",
                    "logged_in": False,
                    "cookies_injected": 0,
                    "debug": debug_log,
                }
                print(f"[DK] poll #{attempt}: {type(exc).__name__}: {exc}", flush=True)

            # Wait 3s before next attempt, but check stop flag
            if self._dk_poll_stop.wait(timeout=3):
                return

        # Timed out
        self._dk_sync_result = {
            "status": "error",
            "message": "Timed out waiting for login.",
            "logged_in": False,
            "cookies_injected": 0,
        }

    @staticmethod
    def _cdp_get_cookies(port: int) -> list[dict]:
        """Read digikey.com cookies via Chrome DevTools Protocol.

        Implements a minimal WebSocket client (no external deps) to send a
        single CDP command and read the response.
        """
        import base64
        import http.client
        import socket
        import struct

        # 1. Get a page target's WebSocket URL (cookies need page context)
        conn = http.client.HTTPConnection("localhost", port, timeout=2)
        conn.request("GET", "/json")
        targets = json.loads(conn.getresponse().read())
        conn.close()
        # Prefer a digikey tab; fall back to any page target
        page = None
        for t in targets:
            if t.get("type") == "page":
                if page is None:
                    page = t
                if "digikey" in t.get("url", "").lower():
                    page = t
                    break
        if not page or "webSocketDebuggerUrl" not in page:
            raise RuntimeError(f"No page target found ({len(targets)} targets)")
        ws_path = "/" + page["webSocketDebuggerUrl"].split("/", 3)[3]

        # 2. WebSocket handshake
        sock = socket.create_connection(("localhost", port), timeout=2)
        ws_key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            sock.close()
            raise RuntimeError("WebSocket upgrade failed")

        # 3. Send Network.getAllCookies on the page target
        cmd = json.dumps({
            "id": 1,
            "method": "Network.getAllCookies",
        }).encode()
        mask = os.urandom(4)
        hdr = bytes([0x81])  # FIN + text opcode
        if len(cmd) < 126:
            hdr += bytes([0x80 | len(cmd)])
        else:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", len(cmd))
        hdr += mask
        sock.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(cmd)))

        # 4. Read frames until we get our response (id=1)
        def _recv(n):
            d = b""
            while len(d) < n:
                c = sock.recv(n - len(d))
                if not c:
                    raise RuntimeError("CDP connection closed")
                d += c
            return d

        try:
            for _ in range(50):  # safety limit
                h = _recv(2)
                plen = h[1] & 0x7F
                if plen == 126:
                    plen = struct.unpack(">H", _recv(2))[0]
                elif plen == 127:
                    plen = struct.unpack(">Q", _recv(8))[0]
                payload = _recv(plen)
                try:
                    msg = json.loads(payload)
                    if msg.get("id") == 1:
                        return msg.get("result", {}).get("cookies", [])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        finally:
            sock.close()
        raise RuntimeError("No CDP response received")

    def _inject_cookies_to_dk_window(self, cookies: list[dict]) -> int:
        """Inject cookie dicts into the WebView2 session via CookieManager.

        All WebView2 access (CookieManager, CreateCookie, AddOrUpdateCookie)
        must happen on the UI thread, so the entire operation is marshaled
        via a single Invoke() call.
        """
        if self._dk_window is None:
            raise RuntimeError("Digikey window not created")

        import System
        from webview.platforms.winforms import BrowserView

        uid = self._dk_window.uid
        instance = BrowserView.instances.get(uid)
        if instance is None:
            raise RuntimeError("BrowserView instance not found")
        browser_form = instance.browser.form

        result = {"injected": 0, "error": None}

        def _inject_all():
            try:
                cookie_mgr = instance.browser.webview.CoreWebView2.CookieManager
                for c in cookies:
                    name = c.get("name", "")
                    if not name:
                        continue
                    value = c.get("value", "")
                    domain = c.get("domain", "")
                    path = c.get("path", "/")
                    try:
                        wv2_cookie = cookie_mgr.CreateCookie(name, value, domain, path)
                        wv2_cookie.IsHttpOnly = bool(c.get("httpOnly") or c.get("is_httponly"))
                        wv2_cookie.IsSecure = bool(c.get("secure") or c.get("is_secure"))
                        expires = c.get("expires")
                        if expires and float(expires) > 0:
                            epoch = System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc)
                            wv2_cookie.Expires = epoch.AddSeconds(float(expires))
                        cookie_mgr.AddOrUpdateCookie(wv2_cookie)
                        result["injected"] += 1
                    except Exception as exc:
                        logger.debug("Failed to inject cookie %s: %s", name, exc)
            except Exception as exc:
                result["error"] = str(exc)

        browser_form.Invoke(System.Action(_inject_all))

        if result["error"]:
            raise RuntimeError(result["error"])
        return result["injected"]

    def sync_digikey_cookies(self) -> dict[str, Any]:
        """Return the latest cookie sync status from the background poll thread.

        Does zero I/O — just reads cached state set by ``_dk_poll_loop``.
        """
        return dict(self._dk_sync_result) if self._dk_sync_result else {
            "status": "error",
            "message": "Login not started.",
            "logged_in": False,
            "cookies_injected": 0,
        }

    def get_digikey_login_status(self) -> dict[str, bool]:
        """Check whether user is logged into Digikey.

        Uses the fastest available check: pending cookies from CDP, cached
        sync result from the poll thread, or the hidden webview as last resort.
        """
        if self._dk_pending_cookies:
            return {"logged_in": self._check_dk_cookies_logged_in(self._dk_pending_cookies)}
        if self._dk_sync_result.get("logged_in"):
            return {"logged_in": True}
        return {"logged_in": False}

    def logout_digikey(self) -> dict[str, str]:
        """Log out of Digikey and clear the product cache."""
        self._dk_poll_stop.set()  # stop any running poll thread
        self._dk_sync_result = {}
        self._dk_pending_cookies = None
        if self._dk_window is not None:
            try:
                import System
                from webview.platforms.winforms import BrowserView

                uid = self._dk_window.uid
                instance = BrowserView.instances.get(uid)
                if instance is not None:
                    def _clear():
                        try:
                            cm = instance.browser.webview.CoreWebView2.CookieManager
                            cm.DeleteAllCookies()
                        except Exception as exc:
                            logger.debug("DeleteAllCookies failed: %s", exc)

                    instance.browser.form.Invoke(System.Action(_clear))
                self._dk_loaded.clear()
                self._dk_window.load_url(
                    "https://www.digikey.com/MyDigiKey/Logout"
                )
            except (RuntimeError, AttributeError, ImportError) as exc:
                logger.warning("Digikey logout failed: %s", exc)
        self._digikey_cache.clear()
        return {"status": "ok"}

    def fetch_digikey_product(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Digikey product details by navigating the hidden browser window.

        Navigates to the Digikey search page for *part_number*, waits for the
        page to load, then extracts structured product data (JSON-LD or
        Next.js SSR data) from the rendered DOM.

        Results (including ``None``) are cached for the session.
        """
        part_number = str(part_number).strip()
        if not part_number:
            raise ValueError("Part number must not be empty")

        if part_number in self._digikey_cache:
            return self._digikey_cache[part_number]

        with self._dk_lock:
            self._ensure_dk_window()

            search_url = (
                "https://www.digikey.com/en/products/detail/-/-/"
                + quote(part_number, safe="")
            )
            self._dk_loaded.clear()
            self._dk_window.load_url(search_url)
            if not self._dk_loaded.wait(timeout=15):
                logger.warning(
                    "Digikey page load timed out for %s", part_number
                )
                self._digikey_cache[part_number] = None
                return None

            try:
                result = self._dk_window.evaluate_js(
                    "(function() {"
                    # Strategy 1: JSON-LD structured data
                    "  var scripts = document.querySelectorAll("
                    "    'script[type=\"application/ld+json\"]');"
                    "  for (var i = 0; i < scripts.length; i++) {"
                    "    try {"
                    "      var ld = JSON.parse(scripts[i].textContent);"
                    "      var prod = null;"
                    "      if (ld['@type'] === 'Product') prod = ld;"
                    "      if (!prod && Array.isArray(ld)) {"
                    "        for (var j = 0; j < ld.length; j++) {"
                    "          if (ld[j]['@type'] === 'Product') { prod = ld[j]; break; }"
                    "        }"
                    "      }"
                    "      if (prod) {"
                    "        try {"
                    "          var bt = document.body.innerText || '';"
                    "          var sm = bt.match(/(\\d[\\d,]*)\\s+In\\s*Stock/i);"
                    "          if (sm) prod._stock = parseInt(sm[1].replace(/,/g,''),10);"
                    "        } catch(e2) {}"
                    "        return prod;"
                    "      }"
                    "    } catch(e) {}"
                    "  }"
                    # Strategy 2: __NEXT_DATA__ (Next.js SSR)
                    "  var ndEl = document.getElementById('__NEXT_DATA__');"
                    "  if (ndEl) {"
                    "    try {"
                    "      var nd = JSON.parse(ndEl.textContent);"
                    "      var pp = nd && nd.props && nd.props.pageProps;"
                    "      if (pp) return {_source: 'nextdata', _props: pp};"
                    "    } catch(e) {}"
                    "  }"
                    "  return null;"
                    "})()"
                )
            except (RuntimeError, AttributeError) as exc:
                logger.warning(
                    "Digikey evaluate_js failed for %s: %s",
                    part_number,
                    exc,
                )
                self._digikey_cache[part_number] = None
                return None

        if not result or not isinstance(result, dict):
            logger.info("Digikey product not found: %s", part_number)
            self._digikey_cache[part_number] = None
            return None

        product = self._normalize_digikey_result(result, part_number)
        product["_debug"] = result
        self._digikey_cache[part_number] = product
        return product

    @staticmethod
    def _normalize_digikey_result(
        raw: dict[str, Any], part_number: str
    ) -> dict[str, Any]:
        """Normalize scraped Digikey data to the same shape as LCSC product."""
        # JSON-LD Product schema
        if raw.get("@type") == "Product":
            offers = raw.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_val: float = 0
            try:
                price_val = float(
                    offers.get("price") or offers.get("lowPrice") or 0
                )
            except (ValueError, TypeError):
                pass

            brand = raw.get("brand") or {}
            image = raw.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""

            return {
                "productCode": raw.get("sku") or part_number,
                "title": raw.get("name", ""),
                "manufacturer": (
                    brand.get("name", "")
                    if isinstance(brand, dict)
                    else str(brand)
                ),
                "mpn": raw.get("mpn", "") or raw.get("sku", ""),
                "package": "",
                "description": raw.get("description", ""),
                "stock": raw.get("_stock") or (
                    1 if "InStock" in str(
                        offers.get("availability", "")
                    ) else 0
                ),
                "prices": (
                    [{"qty": 1, "price": price_val}] if price_val else []
                ),
                "imageUrl": image,
                "pdfUrl": "",
                "digikeyUrl": raw.get("url", ""),
                "attributes": [],
                "provider": "digikey",
            }

        # Next.js SSR data or other raw format — best-effort extraction
        props = raw.get("_props") or raw
        return {
            "productCode": (
                props.get("digiKeyPartNumber")
                or props.get("DigiKeyPartNumber")
                or part_number
            ),
            "title": (
                props.get("productDescription")
                or props.get("ProductDescription")
                or props.get("name")
                or ""
            ),
            "manufacturer": props.get("manufacturer") or "",
            "mpn": (
                props.get("manufacturerPartNumber")
                or props.get("ManufacturerPartNumber")
                or props.get("mpn")
                or ""
            ),
            "package": props.get("package") or "",
            "description": (
                props.get("detailedDescription")
                or props.get("DetailedDescription")
                or ""
            ),
            "stock": (
                props.get("quantityAvailable")
                or props.get("QuantityAvailable")
                or 0
            ),
            "prices": props.get("prices") or [],
            "imageUrl": (
                props.get("primaryPhoto")
                or props.get("PrimaryPhoto")
                or props.get("imageUrl")
                or ""
            ),
            "pdfUrl": (
                props.get("primaryDatasheet")
                or props.get("PrimaryDatasheet")
                or props.get("pdfUrl")
                or ""
            ),
            "digikeyUrl": (
                props.get("productUrl")
                or props.get("ProductUrl")
                or props.get("digikeyUrl")
                or ""
            ),
            "attributes": (
                props.get("attributes") or props.get("parameters") or []
            ),
            "provider": "digikey",
        }

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
