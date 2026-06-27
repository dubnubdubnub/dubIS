import csv
import io

from mirror_serialize import INVENTORY_CSV_FIELDS, inventory_stats, inventory_to_csv


def test_csv_has_header_and_rows():
    inv = [{"section": "R", "lcsc": "C1", "qty": 5, "extra": "ignored"}]
    out = inventory_to_csv(inv)
    lines = out.strip().splitlines()
    assert lines[0] == ",".join(INVENTORY_CSV_FIELDS)
    assert "C1" in lines[1]
    assert "ignored" not in out  # extrasaction="ignore"


def test_csv_respects_custom_fields():
    inv = [{"a": 1, "b": 2}]
    out = inventory_to_csv(inv, fields=["a", "b"])
    assert out.splitlines()[0] == "a,b"


def test_csv_empty_writes_header_only():
    text = inventory_to_csv([])
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0][0] == "section"
    assert "lcsc" in rows[0]
    assert "qty" in rows[0]


def test_csv_single_row_round_trips():
    text = inventory_to_csv([{
        "section": "Resistors",
        "lcsc": "C100000",
        "mpn": "RC0402",
        "digikey": "",
        "pololu": "",
        "mouser": "",
        "manufacturer": "Yageo",
        "package": "0402",
        "description": "Resistor 10kΩ",
        "qty": 100,
        "unit_price": 0.01,
        "ext_price": 1.0,
        "primary_vendor_id": "lcsc",
    }])
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["lcsc"] == "C100000"
    assert rows[0]["qty"] == "100"
    assert rows[0]["manufacturer"] == "Yageo"


def test_csv_extra_keys_are_ignored():
    text = inventory_to_csv([{
        "section": "X",
        "lcsc": "C1",
        "qty": 5,
        "po_history": [{"date": "2026-01-01"}],
    }])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["lcsc"] == "C1"
    assert "po_history" not in rows[0]


def test_stats_counts_sections_and_qty():
    inv = [{"section": "R", "qty": 2}, {"section": "R", "qty": 3}, {"section": "C", "qty": 1}]
    stats = inventory_stats(inv)
    assert stats["part_count"] == 3
    assert stats["total_qty"] == 6
    assert stats["section_counts"] == {"R": 2, "C": 1}


def test_stats_tolerates_missing_qty():
    assert inventory_stats([{"section": "R"}])["total_qty"] == 0


def test_stats_empty():
    stats = inventory_stats([])
    assert stats == {"part_count": 0, "total_qty": 0, "section_counts": {}}


def test_stats_missing_qty_treated_as_zero():
    stats = inventory_stats([{"section": "X", "qty": None}, {"section": "X"}])
    assert stats["total_qty"] == 0
    assert stats["part_count"] == 2
