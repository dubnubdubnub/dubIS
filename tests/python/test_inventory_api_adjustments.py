"""Tests for InventoryApi — adjustments, imports, consume BOM, and truncation."""

import csv
import json
import os

import pytest

from inventory_api import InventoryApi
from tests.python.helpers import make_part as _make_part
from tests.python.helpers import write_ledger as _write_ledger


class TestApplyAdjustments:
    def _write_adj(self, api, rows):
        with open(api.adjustments_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=InventoryApi.ADJ_FIELDNAMES)
            writer.writeheader()
            for r in rows:
                row = {fn: "" for fn in InventoryApi.ADJ_FIELDNAMES}
                row.update(r)
                writer.writerow(row)

    def test_set_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "set", "lcsc_part": "C100000", "quantity": "5"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "5"

    def test_add_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "add", "lcsc_part": "C100000", "quantity": "3"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "13"

    def test_consume_adjustment(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "consume", "lcsc_part": "C100000", "quantity": "-4"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "6"

    def test_malformed_qty_skipped(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        fieldnames, merged = api._read_raw_inventory()
        self._write_adj(api, [{"type": "set", "lcsc_part": "C100000", "quantity": "abc"}])
        api._apply_adjustments(merged, fieldnames)
        assert merged["C100000"]["Quantity"] == "10"  # unchanged

    def test_set_creates_new_part(self, api):
        fieldnames = list(InventoryApi.FIELDNAMES)
        merged = {}
        self._write_adj(api, [{"type": "set", "lcsc_part": "C999999", "quantity": "5"}])
        api._apply_adjustments(merged, fieldnames)
        assert "C999999" in merged
        assert merged["C999999"]["LCSC Part Number"] == "C999999"


class TestConsumeBom:
    def test_basic_consume(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=20)])
        matches = [{"part_key": "C100000", "bom_qty": 2}]
        result = api.consume_bom(matches, 3, "test.csv")
        # 20 - (2*3) = 14
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 14

    def test_writes_adjustments_csv(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=20)])
        api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], 2, "bom.csv")
        assert os.path.exists(api.adjustments_csv)

    def test_json_string_input(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        matches_json = json.dumps([{"part_key": "C100000", "bom_qty": 1}])
        result = api.consume_bom(matches_json, 1, "test.csv")
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 9

    def test_zero_board_qty_raises(self, api):
        with pytest.raises(ValueError, match="positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], 0, "test.csv")

    def test_empty_matches_raises(self, api):
        with pytest.raises(ValueError, match="empty"):
            api.consume_bom([], 1, "test.csv")

    def test_zero_bom_qty_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="bom_qty must be positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 0}], 1, "test.csv")

    def test_negative_board_qty_raises(self, api):
        with pytest.raises(ValueError, match="positive"):
            api.consume_bom([{"part_key": "C100000", "bom_qty": 1}], -1, "test.csv")


class TestImportPurchases:
    def test_creates_ledger(self, api):
        rows = [_make_part(lcsc="C100000", qty=5)]
        result = api.import_purchases(rows)
        assert any(r["lcsc"] == "C100000" for r in result)

    def test_appends_to_existing(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=5)])
        rows = [_make_part(lcsc="C200000", qty=3, desc="Capacitor 100nF 25V")]
        result = api.import_purchases(rows)
        lcscs = {r["lcsc"] for r in result}
        assert "C100000" in lcscs
        assert "C200000" in lcscs

    def test_empty_rows_error(self, api):
        with pytest.raises(ValueError, match="No rows to import"):
            api.import_purchases([])

    def test_json_string_input(self, api):
        rows_json = json.dumps([_make_part(lcsc="C100000", qty=5)])
        result = api.import_purchases(rows_json)
        assert any(r["lcsc"] == "C100000" for r in result)


class TestAdjustPart:
    def test_add(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("add", "C100000", 5)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15

    def test_remove(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("remove", "C100000", 3)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 7

    def test_set(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.adjust_part("set", "C100000", 42)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 42

    def test_unknown_type_error(self, api):
        with pytest.raises(ValueError, match="Unknown adjustment type"):
            api.adjust_part("delete", "C100000", 1)

    def test_negative_quantity_raises(self, api):
        with pytest.raises(ValueError, match="non-negative"):
            api.adjust_part("add", "C100000", -5)

    def test_empty_part_key_raises(self, api):
        with pytest.raises(ValueError, match="empty"):
            api.adjust_part("add", "", 5)

    def test_self_heals_divergence_on_existing_part(self, api):
        """adjust_part on an existing part reconciles a corrupted cache value
        against a full replay (mirrors consume_bom's verify_parts self-heal)."""
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        # Prime the cache so the part exists in the stock table.
        api.adjust_part("add", "C100000", 0)
        # Corrupt the cached delta to simulate divergence from a full replay.
        conn = api._get_cache()
        conn.execute("UPDATE stock SET quantity = 999 WHERE part_id = 'C100000'")
        conn.commit()
        # A further adjustment on the existing-part branch must self-heal:
        # expected after replay = 10 (ledger) + 5 (this add) = 15, not 999 + 5.
        result = api.adjust_part("add", "C100000", 5)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15

    def test_verify_parts_invoked_for_adjusted_part(self, api, monkeypatch):
        """The existing-part branch calls verify_parts(fix=True) for the
        adjusted key so divergence is reconciled, not silently trusted."""
        import cache_db

        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.adjust_part("add", "C100000", 0)  # prime cache (existing-part branch next)

        calls = []
        real_verify = cache_db.verify_parts

        def spy(conn, part_ids, *args, **kwargs):
            calls.append((list(part_ids), kwargs.get("fix")))
            return real_verify(conn, part_ids, *args, **kwargs)

        monkeypatch.setattr(cache_db, "verify_parts", spy)
        result = api.adjust_part("add", "C100000", 5)

        assert calls == [(["C100000"], True)]
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15


class TestTruncateCsv:
    def test_remove_last_purchases(self, api):
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
            _make_part(lcsc="C300000", qty=30, desc="LED Red"),
        ])
        result = api.remove_last_purchases(2)
        lcscs = {r["lcsc"] for r in result}
        assert "C100000" in lcscs
        assert "C200000" not in lcscs
        assert "C300000" not in lcscs

    def test_remove_last_adjustments(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.adjust_part("add", "C100000", 5)
        api.adjust_part("add", "C100000", 3)
        result = api.remove_last_adjustments(1)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["qty"] == 15  # 10 + 5, last adj removed

    def test_zero_count_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="positive"):
            api.remove_last_purchases(0)

    def test_count_exceeds_rows_raises(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        with pytest.raises(ValueError, match="Cannot remove"):
            api.remove_last_purchases(5)

    def test_missing_file_raises(self, api):
        with pytest.raises(ValueError, match="No purchase ledger file found"):
            api.remove_last_purchases(1)

    def test_missing_adjustments_file_raises(self, api):
        with pytest.raises(ValueError, match="No adjustments file found"):
            api.remove_last_adjustments(1)
