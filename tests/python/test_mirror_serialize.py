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


def test_stats_counts_sections_and_qty():
    inv = [{"section": "R", "qty": 2}, {"section": "R", "qty": 3}, {"section": "C", "qty": 1}]
    stats = inventory_stats(inv)
    assert stats["part_count"] == 3
    assert stats["total_qty"] == 6
    assert stats["section_counts"] == {"R": 2, "C": 1}


def test_stats_tolerates_missing_qty():
    assert inventory_stats([{"section": "R"}])["total_qty"] == 0
