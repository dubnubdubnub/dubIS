"""Capture live distributor API responses as test fixtures.

Fetches real HTTP responses from LCSC (and later Digikey) and writes them to
tests/fixtures/generated/distributor-scrapes.json so normalizer tests can run offline.

Usage:
    python scripts/capture-distributor-fixtures.py             # fetch all
    python scripts/capture-distributor-fixtures.py --check     # verify fixtures exist
    python scripts/capture-distributor-fixtures.py --lcsc-only # LCSC parts only
    python scripts/capture-distributor-fixtures.py --digikey-only  # Digikey parts only

The --check flag exits 0 if all fixture files are present, 1 if any are missing.
"""

from __future__ import annotations

import csv
import json
import os
import re  # noqa: F401  # used in Task 2 Digikey capture
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "generated")
FIXTURE_PATH = os.path.join(GENERATED_DIR, "distributor-scrapes.json")
PURCHASE_LEDGER = os.path.join(PROJECT_ROOT, "data", "purchase_ledger.csv")
COOKIES_FILE = os.path.join(PROJECT_ROOT, "data", "digikey_cookies.json")

# fmt: off
LCSC_HARDCODED = [
    "C1525",   # Ceramic Capacitors
    "C3338",   # Aluminum Electrolytic
    "C25741",  # Resistors
    "C1046",   # Inductors
    "C72043",  # LEDs
    "C13738",  # Crystals
    "C7950",   # Op-Amps
    "C3113",   # Voltage References
    "C5947",   # Logic ICs
    "C7562",   # Memory - EEPROM
    "C34565",  # Sensors - Temperature
    "C8598",   # Diodes - Schottky
    "C8062",   # Diodes - Zener
    "C15879",  # TVS Diodes
    "C1015",   # Ferrite Beads
    "C89657",  # Fuses - PTC
    "C6649",   # Optocouplers
    "C2146",   # Transistors - BJT
    "C5446",   # Voltage Regulators
    "C37593",  # ADC
    "C393939", # Connectors
    "C35449",  # Relays
    "C49651",  # Voltage Supervisors
]

DIGIKEY_HARDCODED = [
    "CL10A106MQ8NNNC",     # 10uF MLCC 0603
    "EEE-FK1V100R",        # 100uF electrolytic SMD
    "RC0805FR-07100KL",    # 100kΩ resistor 0805
    "SRN4018-4R7M",        # 4.7uH power inductor
    "150080VS75000",       # Green LED 0603
    "ABM8-16.000MHZ-B2-T", # 16MHz crystal SMD
    "10118192-0001LF",     # Micro USB Type-B receptacle
    "PRPC040SAAN-RC",      # 40-pin 0.1" single-row header
    "1729018",             # 2-pos 3.5mm screw terminal
    "LM358DR",             # Dual op-amp SOIC-8
    "REF3030AIDBZR",       # 3.0V precision voltage reference SOT-23-3
    "MCP3008-I/SL",        # 8-channel 10-bit ADC SPI SOIC-16
    "SN74LVC1T45DBVR",     # Single-bit dual-supply bus transceiver SOT-23-5
    "AT24C256C-SSHL-T",    # 256Kb I2C EEPROM SOIC-8
    "TMP36GRTZ",           # Analog temperature sensor SOT-23
    "ESP-12F",             # ESP8266 WiFi module
    "BAT54SLT1G",          # Schottky diode dual SOT-23
    "SMBJ5.0A",            # 5V TVS diode SMB
    "BLM18PG121SN1D",      # 120Ω ferrite bead 0603
    "0467001.NR",          # 1A fast-blow fuse
    "G5V-1-DC5",           # 5V SPDT signal relay
    "PC817X2NIP0F",        # Optocoupler DIP-4
    "MMBT3904LT1G",        # NPN transistor SOT-23
    "IRLZ44NPBF",          # N-channel power MOSFET TO-220
    "TPS3839G33DBZR",      # 3.3V supervisory circuit SOT-23-5
]
# fmt: on


def get_dynamic_lcsc_parts(max_extra: int = 30) -> list[str]:
    """Read LCSC part numbers from purchase_ledger.csv not in LCSC_HARDCODED.

    Returns up to max_extra additional part numbers sorted for determinism.
    """
    hardcoded_set = set(LCSC_HARDCODED)
    extras: list[str] = []

    if not os.path.exists(PURCHASE_LEDGER):
        return extras

    try:
        with open(PURCHASE_LEDGER, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pn = (row.get("LCSC Part Number") or "").strip()
                if pn and pn not in hardcoded_set and pn not in extras:
                    extras.append(pn)
                    if len(extras) >= max_extra:
                        break
    except (OSError, csv.Error) as exc:
        print(f"Warning: could not read purchase ledger: {exc}", file=sys.stderr)

    return sorted(extras)


def get_dynamic_digikey_parts() -> list[str]:
    """Read Digikey part numbers from purchase_ledger.csv not in DIGIKEY_HARDCODED.

    Returns all discovered part numbers sorted for determinism.
    """
    hardcoded_set = set(DIGIKEY_HARDCODED)
    extras: list[str] = []

    if not os.path.exists(PURCHASE_LEDGER):
        return extras

    try:
        with open(PURCHASE_LEDGER, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pn = (row.get("Digikey Part Number") or "").strip()
                if pn and pn not in hardcoded_set and pn not in extras:
                    extras.append(pn)
    except (OSError, csv.Error) as exc:
        print(f"Warning: could not read purchase ledger: {exc}", file=sys.stderr)

    return sorted(extras)


def fetch_lcsc_part(product_code: str) -> dict:
    """Fetch raw LCSC product detail for a given product code.

    GETs https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}.

    Returns:
        {"raw": result_data, "raw_response": full_response}  on success
        {"error": "message"}                                  on failure
    """
    url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={product_code}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_bytes = resp.read()
        full_response = json.loads(raw_bytes.decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"error": f"network error: {exc}"}
    except TimeoutError:
        return {"error": "timeout"}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc}"}

    result_data = full_response.get("result") if isinstance(full_response, dict) else None
    if not result_data or not isinstance(result_data, dict):
        code = full_response.get("code") if isinstance(full_response, dict) else "?"
        return {"error": f"no result in response (code={code})"}

    return {"raw": result_data, "raw_response": full_response}


def capture_lcsc(parts: list[str]) -> dict:
    """Fetch LCSC data for each part, print progress, return collected results.

    Returns:
        {
            "parts":  {product_code: {"raw": ..., "raw_response": ...}, ...},
            "errors": {product_code: "error message", ...},
        }
    """
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for i, code in enumerate(parts, 1):
        print(f"  [{i}/{len(parts)}] {code} ... ", end="", flush=True)
        data = fetch_lcsc_part(code)
        if "error" in data:
            print(f"ERROR: {data['error']}")
            errors[code] = data["error"]
        else:
            print("ok")
            results[code] = data
        if i < len(parts):
            time.sleep(1)

    return {"parts": results, "errors": errors}


def write_json(path: str, data: object) -> None:
    """Write JSON with consistent formatting and a trailing newline."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def check_freshness() -> bool:
    """Check if fixtures exist and are less than 30 days old."""
    if not os.path.exists(FIXTURE_PATH):
        print(f"MISSING: {os.path.relpath(FIXTURE_PATH, PROJECT_ROOT)}")
        print("  Run: python scripts/capture-distributor-fixtures.py")
        return False
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    captured_at = data.get("captured_at", "")
    if not captured_at:
        print("STALE: no captured_at timestamp")
        return False
    try:
        dt = datetime.fromisoformat(captured_at)
        age_days = (datetime.now() - dt).days
    except ValueError:
        print(f"STALE: invalid timestamp {captured_at!r}")
        return False
    if age_days > 30:
        print(f"STALE: fixtures are {age_days} days old (captured {captured_at})")
        print("  Run: python scripts/capture-distributor-fixtures.py")
        return False
    dk_count = len(data.get("digikey", {}).get("parts", {}))
    lcsc_count = len(data.get("lcsc", {}).get("parts", {}))
    print(f"OK: {dk_count} Digikey + {lcsc_count} LCSC parts (captured {captured_at}, {age_days} days ago)")
    return True


def main() -> None:
    args = sys.argv[1:]

    if "--check" in args:
        sys.exit(0 if check_freshness() else 1)

    digikey_only = "--digikey-only" in args
    lcsc_only = "--lcsc-only" in args

    output: dict = {"captured_at": datetime.now().isoformat(timespec="seconds")}

    if not digikey_only:
        dynamic = get_dynamic_lcsc_parts()
        lcsc_parts = LCSC_HARDCODED + [p for p in dynamic if p not in LCSC_HARDCODED]
        print(f"Capturing {len(lcsc_parts)} LCSC parts...")
        output["lcsc"] = capture_lcsc(lcsc_parts)
        ok = len(output["lcsc"]["parts"])
        err = len(output["lcsc"]["errors"])
        print(f"  Done: {ok} OK, {err} errors")

    if not lcsc_only:
        print("Digikey capture not yet implemented — skipping.")

    write_json(FIXTURE_PATH, output)
    print(f"\nFixtures written to {os.path.relpath(FIXTURE_PATH, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
