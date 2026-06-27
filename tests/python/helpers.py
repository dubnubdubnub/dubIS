"""Shared test helper functions."""

import csv
from pathlib import Path

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
