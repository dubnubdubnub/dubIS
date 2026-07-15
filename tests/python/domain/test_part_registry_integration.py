"""End-to-end: enriching a part with a higher-precedence PN keeps its identity."""

import csv
import os
import sqlite3

import cache_db
import inventory_ops
from domain import inventory as domain_inventory
from domain import part_registry

LEDGER_FIELDS = [
    "LCSC Part Number", "Manufacture Part Number", "Digikey Part Number",
    "Pololu Part Number", "Mouser Part Number", "Manufacturer", "Description",
    "Package", "RoHS", "Quantity", "Unit Price($)", "Ext.Price($)",
    "Date Code / Lot No.", "po_id",
]


def _write_ledger(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_FIELDS})


def test_merge_key_stable_across_enrichment(tmp_path):
    ledger = os.path.join(str(tmp_path), "purchase_ledger.csv")
    _write_ledger(ledger, [{
        "Manufacture Part Number": "STM32F405", "Description": "MCU",
        "Quantity": "10", "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])

    # First merge: part registers under its MPN.
    reg = part_registry.load(str(tmp_path))
    _, merged1 = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS, registry=reg)
    assert "STM32F405" in merged1
    part_registry.save(str(tmp_path), reg)

    # Enrichment: the same row gains an LCSC number (higher precedence).
    _write_ledger(ledger, [{
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])
    reg2 = part_registry.load(str(tmp_path))
    _, merged2 = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS, registry=reg2)

    # Identity did NOT flip to C99.
    assert "STM32F405" in merged2
    assert "C99" not in merged2


def test_merge_without_registry_matches_today(tmp_path):
    ledger = os.path.join(str(tmp_path), "purchase_ledger.csv")
    _write_ledger(ledger, [{
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])
    _, merged = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS)
    assert "C99" in merged  # derived-precedence behavior unchanged


def test_populate_full_uses_registry_keys(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cache_db.create_schema(conn)

    reg = part_registry.PartRegistry({"STM32F405": ["STM32F405", "C99"]})
    part = {
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Package": "LQFP64", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }
    cache_db.populate_full(conn, {"STM32F405": part}, {"ICs": [part]}, registry=reg)
    row = conn.execute("SELECT part_id FROM parts").fetchone()
    assert row["part_id"] == "STM32F405"


class _StubDistributors:
    def infer_distributor(self, row):
        return "lcsc"


def test_record_import_prices_uses_registry_canonical_key(tmp_path):
    """A row bearing an alias PN must record its price observation under the
    part's canonical key, not the newly-added alias — otherwise price history
    forks from the part's identity on enrichment imports."""
    base_dir = str(tmp_path)
    events_dir = os.path.join(base_dir, "events")

    # C99 is already a registered alias of canonical STM32F405.
    reg = part_registry.PartRegistry({"STM32F405": ["STM32F405", "C99"]})
    part_registry.save(base_dir, reg)

    row = {
        "LCSC Part Number": "C99",
        "Manufacture Part Number": "STM32F405",
        "Unit Price($)": "5.00",
    }
    domain_inventory.record_import_prices([row], events_dir, _StubDistributors(), base_dir)

    obs_path = os.path.join(events_dir, "price_observations.csv")
    with open(obs_path, newline="", encoding="utf-8") as f:
        obs_rows = list(csv.DictReader(f))
    assert len(obs_rows) == 1
    assert obs_rows[0]["part_id"] == "STM32F405"
