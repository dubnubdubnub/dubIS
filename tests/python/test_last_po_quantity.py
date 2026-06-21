"""Tests for inventory_ops.last_po_quantity."""

import csv as _csv

import inventory_ops

HEADERS = [
    "LCSC Part Number",
    "Manufacture Part Number",
    "Digikey Part Number",
    "Pololu Part Number",
    "Mouser Part Number",
    "Quantity",
    "po_id",
]


def _write_ledger(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            full = {h: r.get(h, "") for h in HEADERS}
            w.writerow(full)


class TestLastPoQuantity:
    def test_single_row_returns_quantity(self, tmp_path):
        """Single PO row for a part → returns that Quantity."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C12345", "Quantity": "50", "po_id": "PO001"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") == 50

    def test_multiple_rows_returns_last(self, tmp_path):
        """Multiple PO rows for same part → returns last row's Quantity (most recent PO)."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C12345", "Quantity": "100", "po_id": "PO001"},
            {"LCSC Part Number": "C12345", "Quantity": "200", "po_id": "PO002"},
            {"LCSC Part Number": "C12345", "Quantity": "75",  "po_id": "PO003"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") == 75

    def test_no_rows_for_part_returns_none(self, tmp_path):
        """Part with no matching rows → returns None."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C99999", "Quantity": "10", "po_id": "PO001"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") is None

    def test_missing_file_returns_none(self, tmp_path):
        """Missing file → returns None."""
        p = str(tmp_path / "nonexistent.csv")
        assert inventory_ops.last_po_quantity(p, "C12345") is None

    def test_garbage_quantity_does_not_clobber_good_earlier_value(self, tmp_path):
        """A later row with bad Quantity is skipped; good earlier value is kept."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C12345", "Quantity": "30",  "po_id": "PO001"},
            {"LCSC Part Number": "C12345", "Quantity": "abc", "po_id": "PO002"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") == 30

    def test_only_row_has_blank_quantity_returns_none(self, tmp_path):
        """Part whose only row has blank Quantity → returns None."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C12345", "Quantity": "", "po_id": "PO001"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") is None

    def test_mixed_parts_returns_correct_part(self, tmp_path):
        """Other parts in the ledger do not affect the target part's result."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C11111", "Quantity": "999", "po_id": "PO001"},
            {"LCSC Part Number": "C12345", "Quantity": "42",  "po_id": "PO001"},
            {"LCSC Part Number": "C22222", "Quantity": "7",   "po_id": "PO001"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") == 42

    def test_fractional_quantity_truncated_to_int(self, tmp_path):
        """Fractional quantity strings like '10.0' are parsed via int(float(...))."""
        p = str(tmp_path / "ledger.csv")
        _write_ledger(p, [
            {"LCSC Part Number": "C12345", "Quantity": "10.0", "po_id": "PO001"},
        ])
        assert inventory_ops.last_po_quantity(p, "C12345") == 10
