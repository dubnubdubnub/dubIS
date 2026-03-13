"""Tests for PnP consumption server."""

import json
import os
import threading
import types
import urllib.request

import pytest

from inventory_api import InventoryApi
from pnp_server import _load_part_map, _resolve_part_id, start_pnp_server


# ── Shared helpers (same convention as test_inventory_api.py) ──


def _write_ledger(api, rows):
    """Write rows to purchase_ledger.csv with standard fieldnames."""
    import csv
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=InventoryApi.FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = {fn: "" for fn in InventoryApi.FIELDNAMES}
            row.update(r)
            writer.writerow(row)


def _make_part(lcsc="", mpn="", qty=10, desc="Resistor 10kΩ", pkg="0402",
               unit_price="0.01", ext_price="0.10", digikey=""):
    return {
        "LCSC Part Number": lcsc,
        "Manufacture Part Number": mpn,
        "Digikey Part Number": digikey,
        "Quantity": str(qty),
        "Description": desc,
        "Package": pkg,
        "Unit Price($)": unit_price,
        "Ext.Price($)": ext_price,
    }


# ── Fixtures ──


@pytest.fixture
def api(tmp_path):
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    return inst


@pytest.fixture
def pnp_server(api):
    mock_window = types.SimpleNamespace(evaluate_js=lambda code: None)
    server = start_pnp_server(api, mock_window, port=0)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    yield server, base_url, mock_window
    server.shutdown()


def _write_part_map(api, mapping):
    """Write pnp_part_map.json to api.base_dir."""
    path = os.path.join(api.base_dir, "pnp_part_map.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)


def _http_get(url):
    """GET request, return (status, parsed_json)."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _http_post(url, data):
    """POST JSON, return (status, parsed_json)."""
    body = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
    req = urllib.request.Request(
        url, body, {"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _http_post_raw(url, raw_bytes):
    """POST raw bytes, return (status, parsed_json)."""
    req = urllib.request.Request(
        url, raw_bytes, {"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ── TestLoadPartMap ──


class TestLoadPartMap:
    def test_missing_file_returns_empty(self, api):
        assert _load_part_map(api.base_dir) == {}

    def test_corrupt_json_returns_empty(self, api):
        path = os.path.join(api.base_dir, "pnp_part_map.json")
        with open(path, "w") as f:
            f.write("{bad json!!")
        assert _load_part_map(api.base_dir) == {}

    def test_valid_mapping(self, api):
        _write_part_map(api, {"R100k": "C123456"})
        assert _load_part_map(api.base_dir) == {"R100k": "C123456"}


# ── TestResolvePartId ──


class TestResolvePartId:
    def test_explicit_mapping_wins(self):
        part_map = {"R100k": "C111111"}
        inventory = [{"lcsc": "C111111", "mpn": "RC0402", "digikey": ""}]
        assert _resolve_part_id("R100k", part_map, inventory) == "C111111"

    def test_explicit_mapping_overrides_direct_match(self):
        part_map = {"C222222": "C111111"}
        inventory = [
            {"lcsc": "C111111", "mpn": "", "digikey": ""},
            {"lcsc": "C222222", "mpn": "", "digikey": ""},
        ]
        assert _resolve_part_id("C222222", part_map, inventory) == "C111111"

    def test_direct_match_by_lcsc(self):
        inventory = [{"lcsc": "C123456", "mpn": "RC0402", "digikey": "DK-1"}]
        assert _resolve_part_id("C123456", {}, inventory) == "C123456"

    def test_direct_match_by_mpn_returns_lcsc(self):
        inventory = [{"lcsc": "C123456", "mpn": "RC0402", "digikey": "DK-1"}]
        assert _resolve_part_id("RC0402", {}, inventory) == "C123456"

    def test_direct_match_by_digikey(self):
        inventory = [{"lcsc": "", "mpn": "", "digikey": "DK-1"}]
        assert _resolve_part_id("DK-1", {}, inventory) == "DK-1"

    def test_unknown_part_returns_none(self):
        inventory = [{"lcsc": "C123456", "mpn": "RC0402", "digikey": ""}]
        assert _resolve_part_id("UNKNOWN", {}, inventory) is None

    def test_empty_inventory_returns_none(self):
        assert _resolve_part_id("C123456", {}, []) is None


# ── TestPnPServerGET ──


class TestPnPServerGET:
    def test_health(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_get(f"{base_url}/api/health")
        assert status == 200
        assert body == {"ok": True}

    def test_parts_seeded(self, api, pnp_server):
        _, base_url, _ = pnp_server
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()
        status, body = _http_get(f"{base_url}/api/parts")
        assert status == 200
        assert body["ok"] is True
        assert len(body["parts"]) == 2

    def test_parts_empty(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_get(f"{base_url}/api/parts")
        assert status == 200
        assert body["parts"] == []

    def test_unknown_route(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_get(f"{base_url}/api/unknown")
        assert status == 404


# ── TestPnPServerConsume ──


class TestPnPServerConsume:
    @pytest.fixture(autouse=True)
    def _seed(self, api):
        _write_ledger(api, [
            _make_part(lcsc="C100000", mpn="RC0402-10K", qty=100),
            _make_part(lcsc="C200000", qty=50, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()

    def test_consume_by_lcsc(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C100000"})
        assert status == 200
        assert body["ok"] is True
        assert body["part_key"] == "C100000"
        assert body["new_qty"] == 99

    def test_consume_by_mpn(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "RC0402-10K"})
        assert status == 200
        assert body["part_key"] == "C100000"
        assert body["new_qty"] == 99

    def test_consume_via_mapping(self, api, pnp_server):
        _, base_url, _ = pnp_server
        _write_part_map(api, {"R100k_0402": "C100000"})
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "R100k_0402"})
        assert status == 200
        assert body["part_key"] == "C100000"
        assert body["new_qty"] == 99

    def test_custom_qty(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C100000", "qty": 5})
        assert status == 200
        assert body["new_qty"] == 95

    def test_unknown_part(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "NOPE"})
        assert status == 404
        assert "Unknown part ID" in body["error"]

    def test_missing_part_id(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {})
        assert status == 400
        assert "part_id is required" in body["error"]

    def test_empty_part_id(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "   "})
        assert status == 400
        assert "part_id is required" in body["error"]

    def test_bad_json(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post_raw(f"{base_url}/api/consume", b"not json{{{")
        assert status == 400
        assert "Bad JSON" in body["error"]

    def test_qty_zero(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C100000", "qty": 0})
        assert status == 400
        assert "qty must be positive" in body["error"]

    def test_qty_negative(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C100000", "qty": -1})
        assert status == 400
        assert "qty must be positive" in body["error"]

    def test_qty_non_numeric(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C100000", "qty": "abc"})
        assert status == 400
        assert "Bad request" in body["error"]

    def test_post_unknown_route(self, pnp_server):
        _, base_url, _ = pnp_server
        status, body = _http_post(f"{base_url}/api/unknown", {"part_id": "C100000"})
        assert status == 404


# ── TestPnPServerUIUpdate ──


class TestPnPServerUIUpdate:
    def test_evaluate_js_called(self, api, tmp_path):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.rebuild_inventory()
        calls = []
        mock_window = types.SimpleNamespace(evaluate_js=lambda code: calls.append(code))
        server = start_pnp_server(api, mock_window, port=0)
        port = server.server_address[1]
        try:
            _http_post(f"http://127.0.0.1:{port}/api/consume", {"part_id": "C100000"})
            assert len(calls) == 1
            assert "_pnpConsume" in calls[0]
        finally:
            server.shutdown()

    def test_evaluate_js_raises_still_200(self, api, tmp_path):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.rebuild_inventory()

        def exploding_js(code):
            raise RuntimeError("window closed")

        mock_window = types.SimpleNamespace(evaluate_js=exploding_js)
        server = start_pnp_server(api, mock_window, port=0)
        port = server.server_address[1]
        try:
            status, body = _http_post(
                f"http://127.0.0.1:{port}/api/consume", {"part_id": "C100000"},
            )
            assert status == 200
            assert body["ok"] is True
        finally:
            server.shutdown()


# ── TestPnPServerCORS ──


class TestPnPServerCORS:
    def test_cors_header_present(self, pnp_server):
        _, base_url, _ = pnp_server
        resp = urllib.request.urlopen(f"{base_url}/api/health")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ── TestThreadLock ──


class TestThreadLock:
    def test_api_has_threading_lock(self, api):
        assert isinstance(api._lock, type(threading.Lock()))

    def test_concurrent_adjustments(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=0)])
        api.rebuild_inventory()
        errors = []

        def add_one():
            try:
                api.adjust_part("add", "C100000", 1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add_one) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        inv = api._load_organized()
        part = next(r for r in inv if r["lcsc"] == "C100000")
        assert part["qty"] == 10


# ── TestPnPFullFlow ──


class TestPnPFullFlow:
    def test_full_integration(self, api):
        # 1. Seed 2 parts + 1 mapping
        _write_ledger(api, [
            _make_part(lcsc="C100000", mpn="RC0402-10K", qty=50),
            _make_part(lcsc="C200000", qty=30, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()
        _write_part_map(api, {"R100k_0402": "C100000"})

        mock_window = types.SimpleNamespace(evaluate_js=lambda code: None)
        server = start_pnp_server(api, mock_window, port=0)
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"

        try:
            # 2. Consume mapped part
            status, body = _http_post(f"{base_url}/api/consume", {"part_id": "R100k_0402"})
            assert status == 200
            assert body["part_key"] == "C100000"
            assert body["new_qty"] == 49

            # 3. Consume direct LCSC
            status, body = _http_post(f"{base_url}/api/consume", {"part_id": "C200000"})
            assert status == 200
            assert body["new_qty"] == 29

            # 4. Consume unknown → 404
            status, body = _http_post(f"{base_url}/api/consume", {"part_id": "NOPE"})
            assert status == 404

            # 5. GET /api/parts → verify updated quantities
            status, body = _http_get(f"{base_url}/api/parts")
            assert status == 200
            parts = {p["lcsc"]: p for p in body["parts"]}
            assert parts["C100000"]["qty"] == 49
            assert parts["C200000"]["qty"] == 29

            # 6. GET /api/health → still up
            status, body = _http_get(f"{base_url}/api/health")
            assert status == 200
            assert body["ok"] is True
        finally:
            server.shutdown()
