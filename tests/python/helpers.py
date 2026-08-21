"""Shared test helper functions."""

import csv
from pathlib import Path

from distributor_manager import DistributorManager
from inventory_api import InventoryApi


def make_api(tmp_path):
    """Build an InventoryApi wired to a temp directory (mirrors the conftest `api` fixture)."""
    tmp_path = Path(tmp_path)
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    inst.events_dir = str(tmp_path / "events")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    inst.cache_db_path = str(data_dir / "cache.db")
    # InventoryApi.__init__ constructs self._distributors = DistributorManager
    # bound to the DEFAULT base_dir (the real repo data/) before we ever get a
    # chance to repoint inst.base_dir above — DistributorManager captures a
    # plain string at construction time, not a live reference, so it doesn't
    # follow the reassignment. Without this, tests that touch distributor
    # credentials (set_mouser_api_key, digikey cookies, etc.) silently read
    # and write the REAL repo's data/ directory instead of tmp_path. Found via
    # test_distributors_routes.py::test_mouser_api_key_roundtrip polluting
    # data/mouser_credentials.json during Phase 1b Task 9's live E2E work.
    inst._distributors = DistributorManager(inst.base_dir, inst._get_cache)
    return inst


def write_ledger(api, rows):
    """Write rows to purchase_ledger.csv with standard fieldnames."""
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=InventoryApi.FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = {fn: "" for fn in InventoryApi.FIELDNAMES}
            row.update(r)
            writer.writerow(row)


def make_part(lcsc="", mpn="", qty=10, desc="Resistor 10kΩ", pkg="0402",
              unit_price="0.01", ext_price="0.10", digikey="",
              mouser="", pololu=""):
    """Build a purchase ledger row dict with sensible defaults."""
    return {
        "LCSC Part Number": lcsc,
        "Manufacture Part Number": mpn,
        "Digikey Part Number": digikey,
        "Mouser Part Number": mouser,
        "Pololu Part Number": pololu,
        "Quantity": str(qty),
        "Description": desc,
        "Package": pkg,
        "Unit Price($)": unit_price,
        "Ext.Price($)": ext_price,
    }


def lcsc_fixture_products():
    """Replay every captured LCSC response through the REAL ``LcscClient``.

    Returns ``{product_code: normalized_product}``. The captured
    ``raw_response`` payloads are fed back through a patched
    ``urllib.request.urlopen``, so ``lcsc_client.py``'s own parsing runs —
    including its ``paramVOList`` -> ``attributes`` extraction. Tests that
    re-implement that extraction inline (see the replay anti-pattern in
    ``tests/python/test_normalizers.py``) pass even when the client changes;
    this drives the real code path instead.
    """
    import json
    import urllib.request

    from lcsc_client import LcscClient

    path = (Path(__file__).resolve().parents[1] / "fixtures" / "generated"
            / "distributor-scrapes.json")
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    parts = fixtures.get("lcsc", {}).get("parts", {})
    assert parts, f"no captured LCSC parts in {path}"

    products = {}
    original = urllib.request.urlopen
    try:
        for code, entry in parts.items():
            payload = entry.get("raw_response")
            if payload is None:
                continue
            body = json.dumps(payload).encode("utf-8")

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self, _body=body):
                    return _body

            urllib.request.urlopen = lambda *a, **k: _Response()
            product = LcscClient().fetch_product(code)
            if product:
                products[code] = product
    finally:
        urllib.request.urlopen = original
    assert products, "no LCSC fixture replayed into a product"
    return products


def lcsc_fixture_param_values():
    """Every captured LCSC ``paramVOList`` (name, value) pair, one part per code.

    Walks the whole fixture for dicts carrying a ``productCode`` (the shape the
    capture script stores under both ``raw`` and ``raw_response.result``) and
    keeps the first occurrence per code, so each of the captured parts
    contributes its parametrics exactly once.
    """
    import json

    path = (Path(__file__).resolve().parents[1] / "fixtures" / "generated"
            / "distributor-scrapes.json")
    fixtures = json.loads(path.read_text(encoding="utf-8"))

    by_code: dict[str, list] = {}

    def walk(node):
        if isinstance(node, dict):
            code = node.get("productCode")
            if isinstance(code, str) and code and code not in by_code:
                params = node.get("paramVOList")
                if isinstance(params, list):
                    by_code[code] = params
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(fixtures)
    pairs = []
    for code, params in by_code.items():
        for param in params:
            if isinstance(param, dict):
                pairs.append((code, param.get("paramNameEn", ""),
                              param.get("paramValueEn", "")))
    assert pairs, "no LCSC paramVOList entries found in the fixture corpus"
    return pairs
