"""Generate pre-computed test fixtures from Python backend.

Runs the real InventoryApi against test data to produce JSON fixtures
consumed by JS tests, eliminating logic duplication between Python and JS.

Usage:
    python scripts/generate-test-fixtures.py          # Generate fixtures
    python scripts/generate-test-fixtures.py --check   # Verify fixtures are up to date
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

# Add project root to path so we can import backend modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from inventory_api import InventoryApi  # noqa: E402
from inventory_ops import load_organized  # noqa: E402

FIXTURES_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures")
GENERATED_DIR = os.path.join(FIXTURES_DIR, "generated")
E2E_FIXTURES = os.path.join(PROJECT_ROOT, "tests", "js", "e2e", "fixtures")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def generate_inventory() -> list[dict]:
    """Generate inventory.json from tests/fixtures/inventory.csv.

    Uses inventory_ops.load_organized() directly to parse the fixture CSV
    into the same JSON shape the frontend receives from load_inventory().
    """
    return load_organized(os.path.join(FIXTURES_DIR, "inventory.csv"))


def generate_column_detections() -> dict[str, dict[str, str]]:
    """Generate column detection mappings for all PO fixture CSVs.

    Runs InventoryApi.detect_columns() on headers from each CSV.
    Keys by the comma-joined header string so the JS mock can look up
    directly by the headers array it receives from the frontend.
    """
    api = InventoryApi()
    results: dict[str, dict[str, str]] = {}

    # Collect CSVs from both fixture locations
    csv_dirs = [E2E_FIXTURES, FIXTURES_DIR]
    seen: set[str] = set()

    for d in csv_dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv") or name in seen:
                continue
            seen.add(name)
            path = os.path.join(d, name)
            try:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    headers = next(reader, None)
                if headers:
                    mapping = api.detect_columns(headers)
                    if mapping:
                        # Key by comma-joined headers for direct lookup in JS
                        header_key = ",".join(headers)
                        results[header_key] = mapping
            except (csv.Error, UnicodeDecodeError):
                continue

    return results


def generate_xls_conversions() -> dict[str, dict]:
    """Generate XLS-to-CSV conversions for any .xls files in data/.

    Returns {filename: {csv_text, headers, row_count}}.
    """
    api = InventoryApi()
    results: dict[str, dict] = {}

    for d in [DATA_DIR, E2E_FIXTURES, FIXTURES_DIR]:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".xls") or name in results:
                continue
            path = os.path.join(d, name)
            try:
                conversion = api.convert_xls_to_csv(path)
                if conversion:
                    results[name] = conversion
            except Exception:
                continue

    return results


def write_json(path: str, data: object) -> None:
    """Write JSON with consistent formatting."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def generate_all(output_dir: str) -> None:
    """Generate all fixtures to the given directory."""
    print("Generating inventory.json...")
    inventory = generate_inventory()
    write_json(os.path.join(output_dir, "inventory.json"), inventory)
    print(f"  {len(inventory)} items")

    print("Generating column-detections.json...")
    detections = generate_column_detections()
    write_json(os.path.join(output_dir, "column-detections.json"), detections)
    print(f"  {len(detections)} CSVs detected")

    print("Generating xls-conversions.json...")
    conversions = generate_xls_conversions()
    write_json(os.path.join(output_dir, "xls-conversions.json"), conversions)
    print(f"  {len(conversions)} XLS files converted")


def check_freshness() -> bool:
    """Compare generated fixtures against committed versions.

    Returns True if all fixtures are up to date, False otherwise.
    """
    with tempfile.TemporaryDirectory() as tmp:
        generate_all(tmp)

        all_fresh = True
        for name in ["inventory.json", "column-detections.json", "xls-conversions.json"]:
            generated_path = os.path.join(tmp, name)
            committed_path = os.path.join(GENERATED_DIR, name)

            if not os.path.exists(committed_path):
                print(f"MISSING: {name} — run: python scripts/generate-test-fixtures.py")
                all_fresh = False
                continue

            with open(generated_path, encoding="utf-8") as f:
                generated = f.read()
            with open(committed_path, encoding="utf-8") as f:
                committed = f.read()

            if generated != committed:
                print(f"STALE: {name} — run: python scripts/generate-test-fixtures.py")
                all_fresh = False
            else:
                print(f"OK: {name}")

        return all_fresh


def main() -> None:
    if "--check" in sys.argv:
        if check_freshness():
            print("\nAll generated fixtures are up to date.")
            sys.exit(0)
        else:
            print("\nGenerated fixtures are stale. Regenerate with:")
            print("  python scripts/generate-test-fixtures.py")
            sys.exit(1)
    else:
        generate_all(GENERATED_DIR)
        print(f"\nFixtures written to {GENERATED_DIR}")


if __name__ == "__main__":
    main()
