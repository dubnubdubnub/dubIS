"""Shared test helper functions."""

import csv

from inventory_api import InventoryApi


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
