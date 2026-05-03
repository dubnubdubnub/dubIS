"""Tests for inventory_ops module."""

import csv as _csv

import inventory_ops


class TestGetPartKey:
    def test_lcsc_prefix_wins(self):
        row = {
            "LCSC Part Number": "C1234",
            "Manufacture Part Number": "ABC-123",
        }
        assert inventory_ops.get_part_key(row) == "C1234"

    def test_non_c_lcsc_falls_through_to_mpn(self):
        row = {
            "LCSC Part Number": "X999",
            "Manufacture Part Number": "ABC-123",
        }
        assert inventory_ops.get_part_key(row) == "ABC-123"

    def test_mpn_when_no_lcsc(self):
        row = {
            "LCSC Part Number": "",
            "Manufacture Part Number": "TMR2615",
        }
        assert inventory_ops.get_part_key(row) == "TMR2615"

    def test_empty_row_returns_empty(self):
        row = {}
        assert inventory_ops.get_part_key(row) == ""


class TestReadAndMerge:
    def _write_ledger(self, path, rows):
        fields = [
            "LCSC Part Number", "Manufacture Part Number", "Manufacturer",
            "Quantity", "Unit Price($)", "Ext.Price($)",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return fields

    def test_reads_single_row(self, tmp_path):
        p = str(tmp_path / "ledger.csv")
        self._write_ledger(p, [
            {"LCSC Part Number": "C1", "Manufacture Part Number": "M1",
             "Manufacturer": "Acme", "Quantity": "10",
             "Unit Price($)": "1.00", "Ext.Price($)": "10.00"},
        ])
        _, merged = inventory_ops.read_and_merge(p, [])
        assert "C1" in merged

    def test_merges_duplicates(self, tmp_path):
        p = str(tmp_path / "ledger.csv")
        self._write_ledger(p, [
            {"LCSC Part Number": "C1", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "10",
             "Unit Price($)": "1.00", "Ext.Price($)": "10.00"},
            {"LCSC Part Number": "C1", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "5",
             "Unit Price($)": "0.90", "Ext.Price($)": "4.50"},
        ])
        _, merged = inventory_ops.read_and_merge(p, [])
        assert int(merged["C1"]["Quantity"]) == 15

    def test_missing_file_returns_empty(self, tmp_path):
        _, merged = inventory_ops.read_and_merge(
            str(tmp_path / "nonexistent.csv"), []
        )
        assert merged == {}


class TestApplyAdjustments:
    def test_remove_reduces_qty(self, tmp_path):
        merged = {"C1": {"LCSC Part Number": "C1", "Quantity": "100"}}
        adj = str(tmp_path / "adj.csv")
        with open(adj, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["timestamp", "type", "lcsc_part", "quantity",
                                                "bom_file", "board_qty", "note", "source"])
            w.writeheader()
            w.writerow({"timestamp": "", "type": "remove", "lcsc_part": "C1",
                        "quantity": "-20", "bom_file": "", "board_qty": "", "note": "", "source": ""})
        inventory_ops.apply_adjustments(merged, adj, list(merged["C1"].keys()))
        assert merged["C1"]["Quantity"] == "80"

    def test_missing_adj_file_is_noop(self, tmp_path):
        merged = {"C1": {"LCSC Part Number": "C1", "Quantity": "100"}}
        inventory_ops.apply_adjustments(merged, str(tmp_path / "nope.csv"), [])
        assert merged["C1"]["Quantity"] == "100"


def test_migrate_to_vendors_seeds_inferred(tmp_path):
    """Distinct manufacturer values become inferred vendor entries."""
    import json
    import inventory_ops as iops

    base = tmp_path / "data"
    base.mkdir()
    ledger = base / "purchase_ledger.csv"
    fields = ["LCSC Part Number", "Manufacture Part Number", "Manufacturer", "Quantity", "po_id"]
    with open(ledger, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "C1", "Manufacture Part Number": "A1",
                    "Manufacturer": "MDT", "Quantity": "10", "po_id": ""})
        w.writerow({"LCSC Part Number": "C2", "Manufacture Part Number": "A2",
                    "Manufacturer": "HRS", "Quantity": "5", "po_id": ""})
        w.writerow({"LCSC Part Number": "C3", "Manufacture Part Number": "A3",
                    "Manufacturer": "MDT", "Quantity": "3", "po_id": ""})
        w.writerow({"LCSC Part Number": "C4", "Manufacture Part Number": "A4",
                    "Manufacturer": "", "Quantity": "1", "po_id": ""})

    vjson = str(base / "vendors.json")
    summary = iops.migrate_to_vendors(str(ledger), vjson)
    with open(vjson, encoding="utf-8") as f:
        data = json.load(f)
    names = {v["name"] for v in data if v["type"] == "inferred"}
    assert names == {"MDT", "HRS"}
    assert summary["unknown_count"] == 1  # the row with empty manufacturer
    assert summary["inferred_count"] == 2


def test_migrate_to_vendors_idempotent(tmp_path):
    import json
    import inventory_ops as iops

    base = tmp_path / "data"
    base.mkdir()
    ledger = base / "purchase_ledger.csv"
    fields = ["LCSC Part Number", "Manufacture Part Number", "Manufacturer", "Quantity", "po_id"]
    with open(ledger, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "C1", "Manufacture Part Number": "A1",
                    "Manufacturer": "MDT", "Quantity": "10", "po_id": ""})

    vjson = str(base / "vendors.json")
    iops.migrate_to_vendors(str(ledger), vjson)
    iops.migrate_to_vendors(str(ledger), vjson)
    with open(vjson, encoding="utf-8") as f:
        data = json.load(f)
    inferred = [v for v in data if v["type"] == "inferred" and v["name"] == "MDT"]
    assert len(inferred) == 1
