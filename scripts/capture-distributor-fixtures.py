"""Capture live distributor API responses as test fixtures.

Fetches real HTTP responses from LCSC, Digikey, Mouser, and Pololu and writes them
to tests/fixtures/generated/distributor-scrapes.json so normalizer tests can run
offline.

Usage:
    python scripts/capture-distributor-fixtures.py             # fetch all
    python scripts/capture-distributor-fixtures.py --check     # per-distributor freshness report
    python scripts/capture-distributor-fixtures.py --lcsc-only # LCSC parts only
    python scripts/capture-distributor-fixtures.py --digikey-only  # Digikey parts only
    python scripts/capture-distributor-fixtures.py --mouser-only   # Mouser parts only
    python scripts/capture-distributor-fixtures.py --pololu-only   # Pololu parts only
    python scripts/capture-distributor-fixtures.py --refresh-if-stale  # re-capture only stale blocks
    python scripts/capture-distributor-fixtures.py --refresh-if-stale --public-only  # only lcsc/pololu
    python scripts/capture-distributor-fixtures.py --refresh-if-stale --max-age-days 7  # custom age

The --check flag reports each distributor's age and which are stale; it exits 0
if NONE of the four are stale, 1 if any is.

--refresh-if-stale re-captures only the distributors whose per-distributor
``captured_at`` is missing/unparseable/older than --max-age-days (default 30),
then MERGES the new blocks into the existing fixture so untouched distributors
are preserved. A stale distributor whose credentials are absent (Mouser API key
/ DigiKey cookies) is SKIPPED — its existing block is left untouched rather than
overwritten with an empty auth-error block (data-loss guard). --public-only
narrows the refresh scope to {lcsc, pololu}; it is only meaningful alongside
--refresh-if-stale and is ignored otherwise. --max-age-days N overrides the
default 30-day threshold (N must be a non-negative integer (0 = treat everything as stale)).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import datetime

# Top-level modules are importable (pythonpath=["."]); scripts/ is one level down,
# so ensure the project root is on sys.path before importing distributor_fixtures.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import distributor_fixtures  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "generated")
FIXTURE_PATH = os.path.join(GENERATED_DIR, "distributor-scrapes.json")
PURCHASE_LEDGER = os.path.join(PROJECT_ROOT, "data", "purchase_ledger.csv")
COOKIES_FILE = os.path.join(PROJECT_ROOT, "data", "digikey_cookies.json")
MOUSER_CREDENTIALS_FILE = os.path.join(PROJECT_ROOT, "data", "mouser_credentials.json")

MOUSER_API_SEARCH_URL = "https://api.mouser.com/api/v2/search/partnumber"
POLOLU_PRODUCT_URL = "https://www.pololu.com/product/{sku}"

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

# Mouser stocks the same real MPNs as Digikey; reuse a representative subset
# across categories (caps, resistors, inductors, LEDs, crystals, op-amps,
# references, ADCs, transceivers, EEPROM, sensors, diodes, ferrites, fuses,
# relays, optos, transistors, MOSFETs, supervisors).
MOUSER_HARDCODED = [
    "CL10A106MQ8NNNC",     # 10uF MLCC 0603
    "RC0805FR-07100KL",    # 100kΩ resistor 0805
    "SRN4018-4R7M",        # 4.7uH power inductor
    "ABM8-16.000MHZ-B2-T", # 16MHz crystal SMD
    "LM358DR",             # Dual op-amp SOIC-8
    "REF3030AIDBZR",       # 3.0V precision voltage reference SOT-23-3
    "MCP3008-I/SL",        # 8-channel 10-bit ADC SPI SOIC-16
    "SN74LVC1T45DBVR",     # Single-bit dual-supply bus transceiver SOT-23-5
    "AT24C256C-SSHL-T",    # 256Kb I2C EEPROM SOIC-8
    "TMP36GRTZ",           # Analog temperature sensor SOT-23
    "BAT54SLT1G",          # Schottky diode dual SOT-23
    "BLM18PG121SN1D",      # 120Ω ferrite bead 0603
    "MMBT3904LT1G",        # NPN transistor SOT-23
    "IRLZ44NPBF",          # N-channel power MOSFET TO-220
    "TPS3839G33DBZR",      # 3.3V supervisory circuit SOT-23-5
]

# Pololu SKUs are short numeric strings (the product/{sku} path segment).
POLOLU_HARDCODED = [
    "1992",   # Pololu carrier board
    "2590",   # Stepper motor driver carrier
    "3055",   # Voltage regulator
    "2117",   # Motor driver
    "1182",   # Distance sensor
    "2278",   # Voltage regulator
    "713",    # DC motor
    "1376",   # Ball caster
    "2447",   # Power module
    "1213",   # Wheel
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


def _load_digikey_cookies() -> str | None:
    """Load Digikey cookies from data/digikey_cookies.json and build a Cookie header string.

    Returns:
        "name=value; name=value; ..." on success
        None if the file doesn't exist, JSON is corrupt, or no cookies are present
    """
    if not os.path.exists(COOKIES_FILE):
        return None
    try:
        with open(COOKIES_FILE, encoding="utf-8") as f:
            cookies: list[dict] = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not cookies:
        return None
    pairs = [f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")]
    return "; ".join(pairs) if pairs else None


def _extract_nextdata(html: str) -> dict | None:
    """Extract and parse __NEXT_DATA__ JSON from a Digikey HTML page.

    Returns:
        {"_source": "nextdata", "_props": pageProps}  on success
        None if the tag is missing or JSON is invalid
    """
    match = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        nd = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    page_props = nd.get("props", {}).get("pageProps")
    if page_props is None:
        return None
    return {"_source": "nextdata", "_props": page_props}


def fetch_digikey_http(mpn: str, cookie_header: str) -> dict:
    """Fetch raw Digikey search page for an MPN via HTTP and extract __NEXT_DATA__.

    Args:
        mpn:           Manufacturer part number to search for
        cookie_header: Pre-built "name=value; ..." Cookie header string

    Returns:
        {"raw": extracted_nextdata, "raw_html": html, "source": "http"}  on success
        {"error": "message"}                                               on failure
    """
    from urllib.parse import quote

    url = f"https://www.digikey.com/en/products/result?keywords={quote(mpn, safe='')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_header,
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return {"error": f"network error: {exc}"}
    except TimeoutError:
        return {"error": "timeout"}

    extracted = _extract_nextdata(html)
    if extracted is None:
        return {"error": "no __NEXT_DATA__ found in response"}

    return {"raw": extracted, "raw_html": html, "source": "http"}


def capture_digikey(parts: list[str]) -> dict:
    """Fetch Digikey data for each part via HTTP, print progress, return collected results.

    Returns:
        {
            "capture_method": "http",
            "parts":  {mpn: {"raw": ..., "raw_html": ..., "source": "http"}, ...},
            "errors": {mpn: "error message", ...},
        }
    """
    cookie_header = _load_digikey_cookies()
    if cookie_header is None:
        msg = (
            f"no Digikey cookies found at {os.path.relpath(COOKIES_FILE, PROJECT_ROOT)} — "
            "log into Digikey via the app first"
        )
        return {"capture_method": "http", "parts": {}, "errors": {"_auth": msg}}

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for i, mpn in enumerate(parts, 1):
        print(f"  Digikey [{i}/{len(parts)}] {mpn} ... ", end="", flush=True)
        data = fetch_digikey_http(mpn, cookie_header)
        # Retry once on 403 (Cloudflare rate limit) with longer backoff
        if "error" in data and "403" in data["error"]:
            print("403, retrying in 10s... ", end="", flush=True)
            time.sleep(10)
            data = fetch_digikey_http(mpn, cookie_header)
        if "error" in data:
            print(f"ERROR: {data['error']}")
            errors[mpn] = data["error"]
        else:
            print("OK")
            results[mpn] = data
        if i < len(parts):
            time.sleep(5)  # 5s between requests to avoid Cloudflare rate limiting

    return {"capture_method": "http", "parts": results, "errors": errors}


def _load_mouser_api_key() -> str | None:
    """Load the Mouser API key from data/mouser_credentials.json.

    Returns the trimmed key, or None if the file is missing/corrupt/empty.
    """
    if not os.path.exists(MOUSER_CREDENTIALS_FILE):
        return None
    try:
        with open(MOUSER_CREDENTIALS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    key = (data.get("api_key") or "").strip()
    return key or None


def fetch_mouser_part(part_number: str, api_key: str) -> dict:
    """Fetch raw Mouser product detail via the partnumber Search API.

    POSTs to MOUSER_API_SEARCH_URL with the key as a query param (mirrors
    MouserClient._call_api), then pulls SearchResults.Parts.

    Returns:
        {"raw": parts[0], "raw_response": payload}  on success
        {"error": "message"}                         on failure / no result
    """
    full_url = f"{MOUSER_API_SEARCH_URL}?apiKey={urllib.parse.quote(api_key, safe='')}"
    body = {"SearchByPartRequest": {
        "mouserPartNumber": part_number,
        "partSearchOptions": "",
    }}
    req = urllib.request.Request(
        full_url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"error": f"network error: {exc}"}
    except TimeoutError:
        return {"error": "timeout"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"fetch error: {exc}"}

    errors = payload.get("Errors") if isinstance(payload, dict) else None
    if errors:
        messages = "; ".join(
            e.get("Message", "") for e in errors if isinstance(e, dict)
        )
        return {"error": f"API error: {messages or 'unknown'}"}

    results = payload.get("SearchResults") if isinstance(payload, dict) else None
    parts = (results or {}).get("Parts") or []
    if not parts:
        return {"error": "no parts in response"}

    return {"raw": parts[0], "raw_response": payload}


def capture_mouser(parts: list[str]) -> dict:
    """Fetch Mouser data for each part, print progress, return collected results.

    Returns:
        {
            "parts":  {mpn: {"raw": ..., "raw_response": ...}, ...},
            "errors": {mpn: "error message", ...},
        }
    """
    api_key = _load_mouser_api_key()
    if api_key is None:
        msg = (
            f"no Mouser API key found at "
            f"{os.path.relpath(MOUSER_CREDENTIALS_FILE, PROJECT_ROOT)} — "
            "set one in the app first"
        )
        return {"parts": {}, "errors": {"_auth": msg}}

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for i, mpn in enumerate(parts, 1):
        print(f"  Mouser [{i}/{len(parts)}] {mpn} ... ", end="", flush=True)
        data = fetch_mouser_part(mpn, api_key)
        if "error" in data:
            print(f"ERROR: {data['error']}")
            errors[mpn] = data["error"]
        else:
            print("OK")
            results[mpn] = data
        if i < len(parts):
            time.sleep(2)  # Mouser free tier ~30 req/min

    return {"parts": results, "errors": errors}


def fetch_pololu_part(sku: str) -> dict:
    """Fetch a raw Pololu product page for a given SKU.

    GETs https://www.pololu.com/product/{sku} with the same headers as
    PololuClient._fetch_raw so the captured HTML matches what production sees.

    Returns:
        {"raw_html": html}     on success
        {"error": "message"}   on failure
    """
    url = POLOLU_PRODUCT_URL.format(sku=sku)
    headers = {"User-Agent": "dubIS/1.0", "Accept": "text/html"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return {"error": f"network error: {exc}"}
    except TimeoutError:
        return {"error": "timeout"}
    except OSError as exc:
        return {"error": f"fetch error: {exc}"}

    return {"raw_html": html}


def capture_pololu(parts: list[str]) -> dict:
    """Fetch Pololu product pages for each SKU, print progress, return results.

    Returns:
        {
            "parts":  {sku: {"raw_html": ...}, ...},
            "errors": {sku: "error message", ...},
        }
    """
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for i, sku in enumerate(parts, 1):
        print(f"  Pololu [{i}/{len(parts)}] {sku} ... ", end="", flush=True)
        data = fetch_pololu_part(sku)
        if "error" in data:
            print(f"ERROR: {data['error']}")
            errors[sku] = data["error"]
        else:
            print("OK")
            results[sku] = data
        if i < len(parts):
            time.sleep(1)

    return {"parts": results, "errors": errors}


def write_json(path: str, data: object) -> None:
    """Write JSON with consistent formatting and a trailing newline."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _display_path(path: str) -> str:
    """Path relative to PROJECT_ROOT for display, falling back to the raw path.

    os.path.relpath raises ValueError across drive letters on Windows (e.g. a
    tmp fixture on C: vs the project on D:); fall back to the absolute path.
    """
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


def _load_fixture() -> dict:
    """Load the existing fixture JSON, returning {} if missing or corrupt."""
    if not os.path.exists(FIXTURE_PATH):
        return {}
    try:
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def check_freshness(max_age_days: int = 30) -> bool:
    """Report per-distributor fixture freshness.

    Prints the age of each of the four distributors (using the per-block
    timestamp, falling back to the legacy top-level ``captured_at``) and which
    are stale relative to *max_age_days*.

    Returns True if NONE of the four distributors are stale, False if any is.
    Backward-compatible with the single top-level ``captured_at`` format.
    """
    if not os.path.exists(FIXTURE_PATH):
        print(f"MISSING: {_display_path(FIXTURE_PATH)}")
        print("  Run: python scripts/capture-distributor-fixtures.py")
        return False

    fixture = _load_fixture()
    if not fixture:
        print(f"CORRUPT or EMPTY: {_display_path(FIXTURE_PATH)}")
        print("  Run: python scripts/capture-distributor-fixtures.py")
        return False

    now = datetime.now()
    stale = distributor_fixtures.stale_distributors(
        fixture, distributor_fixtures.DISTRIBUTORS, now, max_age_days
    )

    for dist in distributor_fixtures.DISTRIBUTORS:
        ts = distributor_fixtures.block_captured_at(fixture, dist)
        block = fixture.get(dist) if isinstance(fixture.get(dist), dict) else {}
        count = len(block.get("parts", {}))
        if ts is None:
            print(f"  {dist:<8} STALE: no timestamp")
            continue
        try:
            age_days = (now - datetime.fromisoformat(ts)).days
        except (ValueError, TypeError):
            print(f"  {dist:<8} STALE: invalid timestamp {ts!r}")
            continue
        label = "STALE" if dist in stale else "OK"
        print(f"  {dist:<8} {label}: {count} parts, {age_days} days old (captured {ts})")

    if stale:
        print(f"STALE distributors: {', '.join(sorted(stale))}")
        print("  Run: python scripts/capture-distributor-fixtures.py --refresh-if-stale")
        return False
    print("OK: all distributors fresh")
    return True


def _lcsc_part_list() -> list[str]:
    """Hardcoded + dynamic LCSC parts, matching the full-capture order in main()."""
    dynamic = get_dynamic_lcsc_parts()
    return LCSC_HARDCODED + [p for p in dynamic if p not in LCSC_HARDCODED]


def refresh_if_stale(scope: Iterable[str], max_age_days: int = 30) -> bool:
    """Re-capture only the stale distributors in *scope*, merge, and write.

    For each stale distributor:
      - lcsc / pololu: always re-captured (public, no creds needed).
      - mouser: re-captured ONLY if ``_load_mouser_api_key()`` is truthy.
      - digikey: re-captured ONLY if ``_load_digikey_cookies()`` is truthy.
      - A stale distributor with absent creds is SKIPPED entirely — its capture
        function is NOT called and it is NOT added to the merge, so its existing
        block is preserved untouched (data-loss guard).

    Returns True if anything was re-captured and written, False otherwise.
    """
    existing = _load_fixture()
    now = datetime.now()
    stale = distributor_fixtures.stale_distributors(existing, scope, now, max_age_days)

    if not stale:
        print("Nothing stale — all distributors in scope are fresh.")
        return False

    new_blocks: dict = {}
    skipped: list[str] = []

    if "lcsc" in stale:
        parts = _lcsc_part_list()
        print(f"Refreshing LCSC ({len(parts)} parts)...")
        new_blocks["lcsc"] = capture_lcsc(parts)

    if "pololu" in stale:
        print(f"Refreshing Pololu ({len(POLOLU_HARDCODED)} parts)...")
        new_blocks["pololu"] = capture_pololu(POLOLU_HARDCODED)

    if "mouser" in stale:
        if _load_mouser_api_key():
            print(f"Refreshing Mouser ({len(MOUSER_HARDCODED)} parts)...")
            new_blocks["mouser"] = capture_mouser(MOUSER_HARDCODED)
        else:
            skipped.append("mouser")
            print("Skipping stale Mouser: no API key (existing block preserved).")

    if "digikey" in stale:
        if _load_digikey_cookies():
            dk_parts = DIGIKEY_HARDCODED + get_dynamic_digikey_parts()
            print(f"Refreshing Digikey ({len(dk_parts)} parts)...")
            new_blocks["digikey"] = capture_digikey(dk_parts)
        else:
            skipped.append("digikey")
            print("Skipping stale Digikey: no cookies (existing block preserved).")

    if not new_blocks:
        if skipped:
            print(f"Nothing refreshed — stale but missing creds: {', '.join(sorted(skipped))}.")
        return False

    merged = distributor_fixtures.merge_capture(existing, new_blocks, now)
    write_json(FIXTURE_PATH, merged)
    print(f"Refreshed: {', '.join(sorted(new_blocks))}")
    if skipped:
        print(f"Preserved (stale, no creds): {', '.join(sorted(skipped))}")
    print(f"Fixtures written to {_display_path(FIXTURE_PATH)}")
    return True


def _parse_max_age_days(args: list[str]) -> int:
    """Parse the integer following --max-age-days; default 30. Errors clearly if invalid."""
    if "--max-age-days" not in args:
        return 30
    idx = args.index("--max-age-days")
    if idx + 1 >= len(args):
        print("Error: --max-age-days requires a positive integer argument.", file=sys.stderr)
        sys.exit(1)
    raw = args[idx + 1]
    try:
        value = int(raw)
    except ValueError:
        print(f"Error: --max-age-days must be an integer, got {raw!r}.", file=sys.stderr)
        sys.exit(1)
    if value < 0:
        print(f"Error: --max-age-days must be non-negative, got {value}.", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    args = sys.argv[1:]

    max_age_days = _parse_max_age_days(args)

    if "--check" in args:
        sys.exit(0 if check_freshness(max_age_days) else 1)

    if "--refresh-if-stale" in args:
        scope = (
            ("lcsc", "pololu")
            if "--public-only" in args
            else distributor_fixtures.DISTRIBUTORS
        )
        refresh_if_stale(scope, max_age_days)
        return

    if "--public-only" in args:
        print(
            "Error: --public-only is only meaningful with --refresh-if-stale.",
            file=sys.stderr,
        )
        sys.exit(1)

    digikey_only = "--digikey-only" in args
    lcsc_only = "--lcsc-only" in args
    mouser_only = "--mouser-only" in args
    pololu_only = "--pololu-only" in args

    only_flags = [f for f in ("--lcsc-only", "--digikey-only", "--mouser-only", "--pololu-only") if f in args]
    if len(only_flags) > 1:
        print(f"Error: pass at most one of {', '.join(only_flags)} — they are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    # A distributor runs only when no OTHER --*-only flag excludes it.
    do_lcsc = not (digikey_only or mouser_only or pololu_only)
    do_digikey = not (lcsc_only or mouser_only or pololu_only)
    do_mouser = not (lcsc_only or digikey_only or pololu_only)
    do_pololu = not (lcsc_only or digikey_only or mouser_only)

    new_blocks: dict = {}

    if do_lcsc:
        lcsc_parts = _lcsc_part_list()
        print(f"Capturing {len(lcsc_parts)} LCSC parts...")
        new_blocks["lcsc"] = capture_lcsc(lcsc_parts)
        ok = len(new_blocks["lcsc"]["parts"])
        err = len(new_blocks["lcsc"]["errors"])
        print(f"  Done: {ok} OK, {err} errors")

    if do_digikey:
        dk_parts = DIGIKEY_HARDCODED + get_dynamic_digikey_parts()
        print(f"Capturing {len(dk_parts)} Digikey parts...")
        new_blocks["digikey"] = capture_digikey(dk_parts)
        dk_ok = len(new_blocks["digikey"]["parts"])
        dk_err = len(new_blocks["digikey"]["errors"])
        print(f"  Done: {dk_ok} OK, {dk_err} errors")

    if do_mouser:
        print(f"Capturing {len(MOUSER_HARDCODED)} Mouser parts...")
        new_blocks["mouser"] = capture_mouser(MOUSER_HARDCODED)
        m_ok = len(new_blocks["mouser"]["parts"])
        m_err = len(new_blocks["mouser"]["errors"])
        print(f"  Done: {m_ok} OK, {m_err} errors")

    if do_pololu:
        print(f"Capturing {len(POLOLU_HARDCODED)} Pololu parts...")
        new_blocks["pololu"] = capture_pololu(POLOLU_HARDCODED)
        p_ok = len(new_blocks["pololu"]["parts"])
        p_err = len(new_blocks["pololu"]["errors"])
        print(f"  Done: {p_ok} OK, {p_err} errors")

    # Merge into the existing fixture so per-distributor timestamps are stamped
    # and any distributor NOT captured in this run (e.g. a --lcsc-only run) keeps
    # its prior block + timestamp instead of being dropped.
    existing = _load_fixture()
    merged = distributor_fixtures.merge_capture(existing, new_blocks, datetime.now())
    write_json(FIXTURE_PATH, merged)
    print(f"\nFixtures written to {_display_path(FIXTURE_PATH)}")


if __name__ == "__main__":
    main()
