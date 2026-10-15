"""Tests for inventory_ops module."""

import csv as _csv

import pytest

import csv_io
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

    def test_only_parts_filters_rows(self, tmp_path):
        """only_parts restricts the merge to the given keys — the basis for
        targeted verify_parts, which must not replay the whole ledger."""
        p = str(tmp_path / "ledger.csv")
        self._write_ledger(p, [
            {"LCSC Part Number": "C1", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "10",
             "Unit Price($)": "1.00", "Ext.Price($)": "10.00"},
            {"LCSC Part Number": "C1", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "5",
             "Unit Price($)": "0.90", "Ext.Price($)": "4.50"},
            {"LCSC Part Number": "C2", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "7",
             "Unit Price($)": "2.00", "Ext.Price($)": "14.00"},
        ])
        _, merged = inventory_ops.read_and_merge(p, [], only_parts={"C1"})
        assert set(merged) == {"C1"}
        # Same merged quantity as an unfiltered merge would produce for C1
        assert int(merged["C1"]["Quantity"]) == 15

    def test_only_parts_none_keeps_everything(self, tmp_path):
        p = str(tmp_path / "ledger.csv")
        self._write_ledger(p, [
            {"LCSC Part Number": "C1", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "10",
             "Unit Price($)": "1.00", "Ext.Price($)": "10.00"},
            {"LCSC Part Number": "C2", "Manufacture Part Number": "",
             "Manufacturer": "Acme", "Quantity": "7",
             "Unit Price($)": "2.00", "Ext.Price($)": "14.00"},
        ])
        _, merged = inventory_ops.read_and_merge(p, [], only_parts=None)
        assert set(merged) == {"C1", "C2"}


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

    def _write_adj(self, tmp_path, rows):
        adj = str(tmp_path / "adj.csv")
        fields = ["timestamp", "type", "lcsc_part", "quantity",
                  "bom_file", "board_qty", "note", "source"]
        with open(adj, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                row = dict.fromkeys(fields, "")
                row.update(r)
                w.writerow(row)
        return adj

    def test_only_parts_filters_adjustment_rows(self, tmp_path):
        merged = {"C1": {"LCSC Part Number": "C1", "Quantity": "100"}}
        adj = self._write_adj(tmp_path, [
            {"type": "remove", "lcsc_part": "C1", "quantity": "-20"},
            # A "set" for a part outside the filter must NOT create an entry
            {"type": "set", "lcsc_part": "C9", "quantity": "50"},
        ])
        inventory_ops.apply_adjustments(
            merged, adj, ["LCSC Part Number", "Quantity"], only_parts={"C1"})
        assert merged["C1"]["Quantity"] == "80"
        assert "C9" not in merged

    def test_only_parts_none_applies_everything(self, tmp_path):
        merged = {"C1": {"LCSC Part Number": "C1", "Quantity": "100"}}
        adj = self._write_adj(tmp_path, [
            {"type": "remove", "lcsc_part": "C1", "quantity": "-20"},
            {"type": "set", "lcsc_part": "C9", "quantity": "50"},
        ])
        inventory_ops.apply_adjustments(
            merged, adj, ["LCSC Part Number", "Quantity"], only_parts=None)
        assert merged["C1"]["Quantity"] == "80"
        assert merged["C9"]["Quantity"] == "50"


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


# ── mutation-tag matching (the undo path) ────────────────────────────────────
#
# `stamp_source` stores `{tag}@{identity}` for a non-local caller, so rollback
# has to bridge the gap between the tag an operator names and the tag on disk —
# without turning that bridge into a wildcard that eats neighbouring sessions.

TAG_CASES = [
    # (stored cell, tag named, matches?)
    ("xfer-2026", "xfer-2026", True),                    # local caller, exact
    ("xfer-2026@root", "xfer-2026", True),               # aspen: uid identity
    ("xfer-2026@isaac", "xfer-2026", True),              # reality-labs: peer uid
    ("xfer-2026@isaacchiu2006@gmail.com", "xfer-2026", True),  # fremont: tailnet
    ("xfer-2026@bob", "xfer-2026@bob", True),            # full stored tag
    ("xfer-2026-12-31@bob", "xfer-2026", False),         # longer tag, shared prefix
    ("xfer-20261@bob", "xfer-2026", False),              # no separator boundary
    ("xfer-2026", "xfer-2026@bob", False),               # never the other way
    ("xfer-2026@bob", "xfer-2026@", False),              # a trailing @ is not a wildcard
    ("", "xfer-2026", False),                            # untagged row
    ("xfer-2026", "", False),                            # empty tag matches nothing
    ("", "", False),
]


@pytest.mark.parametrize("stored,tag,expected", TAG_CASES)
def test_tag_matches(stored, tag, expected):
    assert inventory_ops.tag_matches(stored, tag) is expected


def _write(path, fieldnames, rows):
    csv_io.atomic_write_rows(str(path), fieldnames, rows, encoding="utf-8")


def _rows(path):
    with open(str(path), newline="", encoding="utf-8-sig") as f:
        return list(_csv.DictReader(f))


def _tagged(path):
    """Rows that survive a rollback, as (source, part-key) pairs."""
    return [(row["source"], row.get("lcsc_part", row.get("LCSC Part Number")))
            for row in _rows(path)]


ADJ_FIELDS = ["timestamp", "type", "lcsc_part", "quantity", "bom_file",
              "board_qty", "note", "source"]
LEDGER_FIELDS = ["LCSC Part Number", "Quantity", "source"]


def _adj(source, part="C1", qty="5"):
    return {"timestamp": "2026-10-14T00:00:00", "type": "add", "lcsc_part": part,
            "quantity": qty, "bom_file": "", "board_qty": "", "note": "",
            "source": source}


def _purchase(source, part="C1", qty="10"):
    return {"LCSC Part Number": part, "Quantity": qty, "source": source}


def test_rollback_source_removes_suffixed_and_exact_rows(tmp_path):
    path = str(tmp_path / "adjustments.csv")
    _write(path, ADJ_FIELDS, [_adj("xfer@alice"), _adj("xfer@bob"),
                             _adj("other@alice"), _adj("")])

    removed = inventory_ops.rollback_source(path, "xfer")

    assert len(removed) == 2
    assert _tagged(path) == [("other@alice", "C1"), ("", "C1")]


def test_rollback_purchases_removes_suffixed_and_exact_rows(tmp_path):
    path = str(tmp_path / "purchase_ledger.csv")
    _write(path, LEDGER_FIELDS, [_purchase("xfer@alice", part="C1"),
                                _purchase("xfer@bob", part="C2"),
                                _purchase("xfer-2026-12-31@bob", part="C3"),
                                _purchase("", part="C4")])

    removed = inventory_ops.rollback_purchases(path, "xfer")

    assert [row["LCSC Part Number"] for row in removed] == ["C1", "C2"]
    assert [row["LCSC Part Number"] for row in _rows(path)] == ["C3", "C4"]


def test_rollback_keeps_the_header_and_the_untagged_rows(tmp_path):
    """A ledger written before the `source` column existed has no tag to match:
    those rows must survive a rollback untouched, header included."""
    path = str(tmp_path / "purchase_ledger.csv")
    _write(path, ["LCSC Part Number", "Quantity"],
           [{"LCSC Part Number": "C1", "Quantity": "10"}])

    assert inventory_ops.rollback_purchases(path, "xfer") == []
    assert [row["LCSC Part Number"] for row in _rows(path)] == ["C1"]


def test_rollback_rejects_an_empty_tag(tmp_path):
    """An empty tag would match every untagged row of the file."""
    path = str(tmp_path / "adjustments.csv")
    _write(path, ADJ_FIELDS, [_adj("")])
    with pytest.raises(ValueError):
        inventory_ops.rollback_source(path, "")
    with pytest.raises(ValueError):
        inventory_ops.rollback_purchases(path, "")


def test_distinct_sources_lists_what_the_server_actually_holds(tmp_path):
    """The 404 has to name the stored tags, or the operator is left guessing
    which suffix the server appended."""
    path = str(tmp_path / "adjustments.csv")
    _write(path, ADJ_FIELDS, [_adj("xfer@alice"), _adj("xfer@bob"),
                             _adj("xfer@alice"), _adj("")])

    assert inventory_ops.distinct_sources(path) == ["xfer@alice", "xfer@bob"]


def test_distinct_sources_on_a_missing_file(tmp_path):
    assert inventory_ops.distinct_sources(str(tmp_path / "nope.csv")) == []
