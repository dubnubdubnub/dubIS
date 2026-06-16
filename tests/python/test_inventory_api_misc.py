"""Tests for InventoryApi — preferences, file I/O, close behavior, columns, POs, vendors."""

import base64
import json
import os
import types

import pytest


class TestLoadPreferences:
    def test_malformed_json_returns_empty(self, api):
        with open(api.prefs_json, "w") as f:
            f.write("{bad json!!")
        assert api.load_preferences() == {}

    def test_missing_file_returns_empty(self, api):
        assert api.load_preferences() == {}

    def test_valid_json_loaded(self, api):
        with open(api.prefs_json, "w") as f:
            json.dump({"theme": "dark"}, f)
        assert api.load_preferences() == {"theme": "dark"}


class TestGetCache:
    def test_returns_same_connection(self, api):
        conn1 = api._get_cache()
        conn2 = api._get_cache()
        assert conn1 is conn2

    def test_schema_created(self, api):
        conn = api._get_cache()
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "parts" in tables
        assert "stock" in tables

    def test_connect_called_once_under_concurrent_access(self, api, monkeypatch):
        """Lazy init must not create two connections if threads race in."""
        import threading

        import cache_db

        calls = []
        real_connect = cache_db.connect

        def counting_connect(path):
            calls.append(path)
            return real_connect(path)

        monkeypatch.setattr(cache_db, "connect", counting_connect)

        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()  # maximize the race window
            results.append(api._get_cache())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(calls) == 1
        assert all(c is results[0] for c in results)

    def test_first_init_via_lock_holding_path_does_not_deadlock(self, api):
        """A lock-holding API method triggers the very first lazy cache init.

        If _get_cache re-acquired a non-reentrant lock already held by the
        caller, this would deadlock. It must not.
        """
        import threading

        from tests.python.helpers import make_part as _mp
        from tests.python.helpers import write_ledger as _wl

        _wl(api, [_mp(lcsc="C100000", qty=10)])
        assert api._cache_conn is None  # no cache yet → first access happens under lock

        done = threading.Event()

        def run():
            api.adjust_part("add", "C100000", 5)
            done.set()

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10)
        assert done.is_set(), "adjust_part deadlocked on first lazy cache init"


class TestShutdown:
    def test_closes_open_cache_connection(self, api):
        import sqlite3

        conn = api._get_cache()
        api.shutdown()
        assert api._cache_conn is None
        # Operating on a closed connection raises ProgrammingError.
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_idempotent_after_cache_created(self, api):
        api._get_cache()
        api.shutdown()
        # Second call must be a safe no-op.
        api.shutdown()
        assert api._cache_conn is None

    def test_safe_when_no_cache_created(self, api):
        # No _get_cache() call → connection never opened.
        assert api._cache_conn is None
        api.shutdown()  # must not raise
        assert api._cache_conn is None


class TestDetectColumns:
    def test_digikey_headers(self, api):
        headers = ["Digi-Key Part Number", "Manufacturer Part Number",
                    "Manufacturer", "Quantity", "Unit Price", "Extended Price"]
        mapping = api.detect_columns(headers)
        assert mapping.get("0") == "Digikey Part Number"
        assert mapping.get("1") == "Manufacture Part Number"
        assert mapping.get("3") == "Quantity"

    def test_lcsc_headers(self, api):
        headers = ["LCSC Part Number", "Quantity", "Description"]
        mapping = api.detect_columns(headers)
        assert mapping.get("0") == "LCSC Part Number"
        assert mapping.get("1") == "Quantity"

    def test_mouser_headers(self, api):
        """Mouser cart XLS headers are detected correctly."""
        headers = ["", "Mouser #", "Mfr. #", "Manufacturer", "Customer #",
                    "Description", "RoHS", "Lifecycle", "Order Qty.",
                    "Price (USD)", "Ext.: (USD)"]
        mapping = api.detect_columns(headers)
        assert mapping.get("1") == "Mouser Part Number"
        assert mapping.get("2") == "Manufacture Part Number"
        assert mapping.get("3") == "Manufacturer"
        assert mapping.get("8") == "Quantity"
        assert mapping.get("9") == "Unit Price($)"
        assert mapping.get("10") == "Ext.Price($)"

    def test_no_match(self, api):
        headers = ["foo", "bar", "baz"]
        mapping = api.detect_columns(headers)
        assert mapping == {}

    def test_json_string_input(self, api):
        headers_json = json.dumps(["LCSC Part Number", "Quantity"])
        mapping = api.detect_columns(headers_json)
        assert mapping.get("0") == "LCSC Part Number"


class TestLoadFile:
    def test_existing_file(self, api, tmp_path):
        test_file = tmp_path / "test.csv"
        test_file.write_text("col1,col2\na,b\n", encoding="utf-8")
        result = api.load_file(str(test_file))
        assert result is not None
        assert result["name"] == "test.csv"
        assert "col1,col2" in result["content"]
        assert result["directory"] == str(tmp_path)
        assert result["path"] == str(test_file)

    def test_missing_file(self, api):
        result = api.load_file("/nonexistent/path/file.csv")
        assert result is None

    def test_empty_path(self, api):
        assert api.load_file("") is None
        assert api.load_file(None) is None

    def test_sidecar_links(self, api, tmp_path):
        test_file = tmp_path / "bom.csv"
        test_file.write_text("h1,h2\n1,2\n", encoding="utf-8")
        links_file = tmp_path / "bom.links.json"
        links_file.write_text('{"manualLinks": [{"bomKey": "a", "invPartKey": "b"}]}', encoding="utf-8")
        result = api.load_file(str(test_file))
        assert result is not None
        assert "links" in result
        assert result["links"]["manualLinks"][0]["bomKey"] == "a"


class TestConfirmClose:
    def test_force_close_flag(self, api):
        assert api._force_close is False

    def test_closing_flag_default(self, api):
        assert api._closing is False

    def test_bom_dirty_flag_default(self, api):
        assert api._bom_dirty is False

    def test_set_bom_dirty(self, api):
        api.set_bom_dirty(True)
        assert api._bom_dirty is True
        api.set_bom_dirty(False)
        assert api._bom_dirty is False

    def test_set_bom_dirty_coerces(self, api):
        api.set_bom_dirty(1)
        assert api._bom_dirty is True
        api.set_bom_dirty(0)
        assert api._bom_dirty is False

    def test_confirm_close_sets_flag(self, api, monkeypatch):
        mock_win = types.SimpleNamespace(destroy=lambda: None)
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        assert api._force_close is True


class TestConvertXls:
    def test_mouser_cart_xls(self, api):
        """Convert a committed Mouser-style cart XLS fixture to CSV.

        The fixture (tests/fixtures/mouser_cart_sample.xls) is a small, valid
        BIFF workbook generated once with xlwt and committed to the repo, so
        this test runs unconditionally without depending on a non-committed
        real export. xlrd (used by convert_xls_to_csv) is in requirements-dev.
        """
        xls_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "fixtures", "mouser_cart_sample.xls",
        )
        assert os.path.exists(xls_path), f"missing XLS fixture: {xls_path}"
        result = api.convert_xls_to_csv(xls_path)
        assert result is not None
        assert result["row_count"] >= 1
        # Header detection found the Mouser cart header row.
        assert any("mouser" in h.lower() for h in result["headers"])
        assert result["csv_text"]  # non-empty


def test_get_po_with_items(api):
    api.import_purchases('[{"Manufacture Part Number":"X","Quantity":"5","Unit Price($)":"1.00","po_id":"po_test"}]')
    # Manually create a matching PO row
    import csv as _csv
    po_csv = api._po_csv
    with open(po_csv, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=[
            "po_id", "vendor_id", "source_file_hash", "source_file_ext",
            "purchase_date", "notes",
        ])
        w.writeheader()
        w.writerow({"po_id": "po_test", "vendor_id": "v_unknown",
                    "source_file_hash": "", "source_file_ext": "",
                    "purchase_date": "2026-04-15", "notes": "x"})
    result = api.get_po_with_items("po_test")
    assert result["po"]["po_id"] == "po_test"
    assert len(result["line_items"]) == 1
    assert result["line_items"][0]["mpn"] == "X"

    def test_confirm_close_calls_destroy(self, api, monkeypatch):
        import types
        destroyed = []
        mock_win = types.SimpleNamespace(destroy=lambda: destroyed.append(True))
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        assert len(destroyed) == 1

    def test_confirm_close_double_call(self, api, monkeypatch):
        import types
        destroyed = []
        mock_win = types.SimpleNamespace(destroy=lambda: destroyed.append(True))
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()
        api.confirm_close()
        assert len(destroyed) == 1

    def test_confirm_close_destroy_exception(self, api, monkeypatch):
        import types

        def exploding_destroy():
            raise RuntimeError("window already destroyed")

        mock_win = types.SimpleNamespace(destroy=exploding_destroy)
        mock_webview = types.SimpleNamespace(windows=[mock_win])
        monkeypatch.setitem(__import__("sys").modules, "webview", mock_webview)
        api.confirm_close()  # should not raise
        assert api._force_close is True
        assert api._closing is True


def test_list_vendors_returns_seeded(api):
    """First call seeds built-ins."""
    result = api.list_vendors()
    ids = {v["id"] for v in result}
    assert {"v_self", "v_salvage", "v_unknown"}.issubset(ids)


def test_create_and_update_vendor(api):
    new_v = api.update_vendor(
        vendor_id="",  # empty → create
        name="MDT", url="https://tmr-sensors.com",
    )
    assert new_v["type"] == "real"
    assert new_v["url"] == "https://tmr-sensors.com"


def test_create_vendor_url_only_derives_name(api):
    """User pastes just a URL into the vendor field — facade fills in a hostname-based name."""
    v = api.update_vendor(vendor_id="", name="", url="https://tmr-sensors.com/")
    assert v["name"] == "tmr-sensors.com"
    assert v["url"] == "https://tmr-sensors.com/"
    assert v["type"] == "real"


def test_match_part_returns_status(api):
    api.import_purchases(
        '[{"Manufacture Part Number":"TMR2615","Manufacturer":"MDT","Quantity":"50","Unit Price($)":"4.20"}]')
    result = api.match_part(mpn="TMR2615", manufacturer="MDT")
    assert result["status"] == "definite"


def test_parse_source_file_csv(api, tmp_path):
    p = tmp_path / "po.csv"
    p.write_text("Manufacture Part Number,Manufacturer,Quantity,Unit Price($)\n"
                 "TMR2615,MDT,50,4.20\n", encoding="utf-8")
    rows = api.parse_source_file(str(p))
    assert len(rows) == 1
    assert rows[0]["mpn"] == "TMR2615"


def test_parse_source_file_b64_csv(api):
    csv_text = "Manufacture Part Number,Manufacturer,Quantity,Unit Price($)\nTMR2615,MDT,50,4.20\n"
    b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    rows = api.parse_source_file_b64(b64, "po.csv")
    assert len(rows) == 1
    assert rows[0]["mpn"] == "TMR2615"


def test_create_purchase_order_writes_files(api):
    """Manual entry — no source file."""
    new_v = api.update_vendor("", name="MDT", url="https://tmr-sensors.com")
    inv = api.create_purchase_order_with_items(
        vendor_id=new_v["id"],
        source_file_b64="", source_file_name="",
        purchase_date="2026-04-15", notes="",
        line_items_json='[{"mpn":"TMR2615","manufacturer":"MDT","package":"",'
                         '"quantity":50,"unit_price":4.20,"match":"new"}]',
    )
    # Returns fresh inventory
    assert any(p["mpn"] == "TMR2615" for p in inv)


def test_delete_last_purchase_order_removes_most_recent(api):
    """delete_last_purchase_order removes the most-recently-created PO."""
    new_v = api.update_vendor("", name="Acme", url="https://acme.example.com")
    api.create_purchase_order_with_items(
        vendor_id=new_v["id"],
        source_file_b64="", source_file_name="",
        purchase_date="2026-01-01", notes="",
        line_items_json='[{"mpn":"PART-A","manufacturer":"Acme","package":"",'
                         '"quantity":5,"unit_price":1.0,"match":"new"}]',
    )
    # Part is visible in inventory
    assert any(p["mpn"] == "PART-A" for p in api.rebuild_inventory())
    inv = api.delete_last_purchase_order()
    assert not any(p["mpn"] == "PART-A" for p in inv)


def test_delete_last_purchase_order_raises_when_none(api):
    """delete_last_purchase_order raises when there are no POs."""
    with pytest.raises(Exception):
        api.delete_last_purchase_order()
