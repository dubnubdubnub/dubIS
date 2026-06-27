"""Tests for get_part_history — per-part adjustment history."""

from __future__ import annotations

import csv
import os

import pytest

from domain.api_history import read_part_history, _HISTORY_CAP
from inventory_api import InventoryApi


ADJ_FIELDNAMES = InventoryApi.ADJ_FIELDNAMES


def _write_adj(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ADJ_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = {fn: "" for fn in ADJ_FIELDNAMES}
            row.update(r)
            writer.writerow(row)


class TestReadPartHistory:
    def test_returns_empty_when_file_missing(self, tmp_path):
        result = read_part_history(str(tmp_path / "adjustments.csv"), "C100000")
        assert result == []

    def test_filters_to_correct_part_key(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        _write_adj(path, [
            {"timestamp": "2024-01-01T00:00:00", "type": "add", "lcsc_part": "C100000", "quantity": "5", "source": "manual"},
            {"timestamp": "2024-01-02T00:00:00", "type": "add", "lcsc_part": "C999999", "quantity": "3", "source": "manual"},
        ])
        result = read_part_history(path, "C100000")
        assert len(result) == 1
        assert result[0]["qty_delta"] == 5

    def test_chronological_order(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        # Deliberately write rows out of chronological order to verify sorting.
        _write_adj(path, [
            {"timestamp": "2024-01-03T00:00:00", "type": "add", "lcsc_part": "C100000", "quantity": "3"},
            {"timestamp": "2024-01-01T00:00:00", "type": "add", "lcsc_part": "C100000", "quantity": "1"},
            {"timestamp": "2024-01-02T00:00:00", "type": "add", "lcsc_part": "C100000", "quantity": "2"},
        ])
        result = read_part_history(path, "C100000")
        # Function must sort by timestamp ascending, regardless of file order.
        timestamps = [r["timestamp"] for r in result]
        assert timestamps == ["2024-01-01T00:00:00", "2024-01-02T00:00:00", "2024-01-03T00:00:00"]

    def test_capped_to_history_cap_most_recent(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        # Use ISO timestamps with sequential seconds so string sort is chronological.
        # Write deliberately out of file order to also verify sort correctness.
        total = _HISTORY_CAP + 10
        rows = [
            {"timestamp": f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}", "type": "add",
             "lcsc_part": "C100000", "quantity": str(i)}
            for i in range(total)
        ]
        # Shuffle file order; function must sort and then keep the most recent 100.
        import random as _random
        shuffled = rows[:]
        _random.Random(42).shuffle(shuffled)
        _write_adj(path, shuffled)
        result = read_part_history(path, "C100000")
        assert len(result) == _HISTORY_CAP
        # After ascending sort, the oldest 10 are dropped; the remaining 100 are
        # i=10..109 in ascending order.
        assert result[0]["qty_delta"] == 10
        assert result[-1]["qty_delta"] == total - 1

    def test_source_preserved(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        _write_adj(path, [
            {"timestamp": "2024-01-01T00:00:00", "type": "consume", "lcsc_part": "C1",
             "quantity": "-4", "source": "openpnp", "note": "run 5"},
        ])
        result = read_part_history(path, "C1")
        assert result[0]["source"] == "openpnp"
        assert result[0]["note"] == "run 5"
        assert result[0]["kind"] == "consume"
        assert result[0]["qty_delta"] == -4

    def test_malformed_qty_defaults_to_zero(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        _write_adj(path, [
            {"timestamp": "2024-01-01T00:00:00", "type": "add", "lcsc_part": "C1",
             "quantity": "abc"},
        ])
        result = read_part_history(path, "C1")
        assert result[0]["qty_delta"] == 0

    def test_set_kind_delta_value(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        _write_adj(path, [
            {"timestamp": "2024-01-01T00:00:00", "type": "set", "lcsc_part": "C1",
             "quantity": "50"},
        ])
        result = read_part_history(path, "C1")
        assert result[0]["kind"] == "set"
        assert result[0]["qty_delta"] == 50

    def test_empty_adjustments_file_returns_empty(self, tmp_path):
        path = str(tmp_path / "adjustments.csv")
        _write_adj(path, [])
        result = read_part_history(path, "C100000")
        assert result == []


class TestGetPartHistoryApi:
    """Integration: test get_part_history through InventoryApi."""

    def test_get_part_history_via_api(self, api):
        path = api.adjustments_csv
        _write_adj(path, [
            {"timestamp": "2024-06-01T10:00:00", "type": "add", "lcsc_part": "C12345",
             "quantity": "10", "source": "import", "note": ""},
            {"timestamp": "2024-06-02T12:00:00", "type": "consume", "lcsc_part": "C12345",
             "quantity": "-3", "source": "openpnp", "note": "board run"},
        ])
        result = api.get_part_history("C12345")
        assert len(result) == 2
        assert result[0]["kind"] == "add"
        assert result[0]["qty_delta"] == 10
        assert result[1]["source"] == "openpnp"
        assert result[1]["note"] == "board run"

    def test_get_part_history_missing_file_returns_empty(self, api):
        # No adjustments.csv written
        result = api.get_part_history("C99999")
        assert result == []

    def test_get_part_history_wrong_part_returns_empty(self, api):
        path = api.adjustments_csv
        _write_adj(path, [
            {"timestamp": "2024-01-01T00:00:00", "type": "add", "lcsc_part": "C11111",
             "quantity": "5"},
        ])
        result = api.get_part_history("C22222")
        assert result == []
