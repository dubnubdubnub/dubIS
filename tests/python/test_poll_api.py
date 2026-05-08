"""Tests for the local poll API."""

import csv
import io
import json
import urllib.error
import urllib.request

import pytest

from poll_api import (
    _inventory_stats,
    _inventory_to_csv,
    restart_poll_server,
    start_poll_server,
)
from tests.python.helpers import make_part as _make_part
from tests.python.helpers import write_ledger as _write_ledger

# ── Fixtures ──


@pytest.fixture
def poll_server(api):
    server = start_poll_server(api, port=0)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    yield server, base_url, api
    server.shutdown()


def _http_get(url):
    """GET request, return (status, headers, raw bytes)."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _http_get_json(url):
    status, headers, raw = _http_get(url)
    return status, headers, json.loads(raw)


# ── TestInventoryToCsv ──


class TestInventoryToCsv:
    def test_empty_inventory_writes_header_only(self):
        text = _inventory_to_csv([])
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) == 1
        assert rows[0][0] == "section"
        assert "lcsc" in rows[0]
        assert "qty" in rows[0]

    def test_single_row_round_trips(self):
        text = _inventory_to_csv([{
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

    def test_extra_keys_are_ignored(self):
        # po_history is a list — DictWriter would choke without extrasaction="ignore"
        text = _inventory_to_csv([{
            "section": "X",
            "lcsc": "C1",
            "qty": 5,
            "po_history": [{"date": "2026-01-01"}],
        }])
        rows = list(csv.DictReader(io.StringIO(text)))
        assert rows[0]["lcsc"] == "C1"
        assert "po_history" not in rows[0]


# ── TestInventoryStats ──


class TestInventoryStats:
    def test_empty(self):
        stats = _inventory_stats([])
        assert stats == {"part_count": 0, "total_qty": 0, "section_counts": {}}

    def test_counts_by_section_and_total_qty(self):
        inv = [
            {"section": "Resistors", "qty": 10},
            {"section": "Resistors", "qty": 5},
            {"section": "Capacitors", "qty": 20},
        ]
        stats = _inventory_stats(inv)
        assert stats["part_count"] == 3
        assert stats["total_qty"] == 35
        assert stats["section_counts"] == {"Resistors": 2, "Capacitors": 1}

    def test_missing_qty_treated_as_zero(self):
        stats = _inventory_stats([{"section": "X", "qty": None}, {"section": "X"}])
        assert stats["total_qty"] == 0
        assert stats["part_count"] == 2


# ── TestPollServerEndpoints ──


class TestPollServerEndpoints:
    def test_health(self, poll_server):
        _, base_url, _ = poll_server
        status, _, body = _http_get_json(f"{base_url}/api/health")
        assert status == 200
        assert body == {"ok": True}

    def test_root_lists_endpoints(self, poll_server):
        _, base_url, _ = poll_server
        status, _, body = _http_get_json(f"{base_url}/")
        assert status == 200
        assert "/api/inventory" in body["endpoints"]
        assert "/api/inventory.csv" in body["endpoints"]

    def test_unknown_route(self, poll_server):
        _, base_url, _ = poll_server
        status, _, body = _http_get_json(f"{base_url}/api/unknown")
        assert status == 404
        assert body["ok"] is False

    def test_inventory_empty(self, poll_server):
        _, base_url, _ = poll_server
        status, _, body = _http_get_json(f"{base_url}/api/inventory")
        assert status == 200
        assert body["ok"] is True
        assert body["count"] == 0
        assert body["inventory"] == []

    def test_inventory_seeded(self, poll_server):
        _, base_url, api = poll_server
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()
        status, _, body = _http_get_json(f"{base_url}/api/inventory")
        assert status == 200
        assert body["count"] == 2
        keys = {item["lcsc"] for item in body["inventory"]}
        assert keys == {"C100000", "C200000"}

    def test_inventory_csv_is_csv(self, poll_server):
        _, base_url, api = poll_server
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()
        status, headers, raw = _http_get(f"{base_url}/api/inventory.csv")
        assert status == 200
        assert headers["Content-Type"].startswith("text/csv")
        assert 'attachment; filename="inventory.csv"' in headers["Content-Disposition"]
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
        assert len(rows) == 2
        assert {r["lcsc"] for r in rows} == {"C100000", "C200000"}

    def test_inventory_csv_empty(self, poll_server):
        _, base_url, _ = poll_server
        status, headers, raw = _http_get(f"{base_url}/api/inventory.csv")
        assert status == 200
        text = raw.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) == 1  # header only
        assert "lcsc" in rows[0]

    def test_stats(self, poll_server):
        _, base_url, api = poll_server
        _write_ledger(api, [
            _make_part(lcsc="C100000", qty=10),
            _make_part(lcsc="C200000", qty=20, desc="Capacitor 100nF 25V"),
        ])
        api.rebuild_inventory()
        status, _, body = _http_get_json(f"{base_url}/api/stats")
        assert status == 200
        assert body["part_count"] == 2
        assert body["total_qty"] == 30
        assert sum(body["section_counts"].values()) == 2

    def test_query_string_is_ignored(self, poll_server):
        _, base_url, _ = poll_server
        status, _, body = _http_get_json(f"{base_url}/api/health?foo=bar")
        assert status == 200
        assert body == {"ok": True}


# ── TestPollServerSecurity ──


class TestPollServerSecurity:
    def test_bound_to_loopback(self, poll_server):
        server, _, _ = poll_server
        host, _ = server.server_address
        assert host == "127.0.0.1"

    def test_cors_header_present(self, poll_server):
        _, base_url, _ = poll_server
        _, headers, _ = _http_get(f"{base_url}/api/health")
        assert headers.get("Access-Control-Allow-Origin") == "*"


# ── TestRestartPollServer ──


class TestRestartPollServer:
    def test_restart_changes_port_and_keeps_serving(self, api):
        original = start_poll_server(api, port=0)
        original_port = original.server_address[1]
        try:
            new_server = restart_poll_server(api, port=0)
        finally:
            # Original should already be shut down by restart, but be defensive
            try:
                original.shutdown()
            except Exception:
                pass
        new_port = new_server.server_address[1]
        try:
            assert api._poll_server is new_server
            # Old port should no longer respond
            with pytest.raises(urllib.error.URLError):
                urllib.request.urlopen(f"http://127.0.0.1:{original_port}/api/health", timeout=0.5)
            # New port should respond
            status, _, body = _http_get_json(f"http://127.0.0.1:{new_port}/api/health")
            assert status == 200
            assert body == {"ok": True}
        finally:
            new_server.shutdown()

    def test_restart_with_no_existing_server_works(self, api):
        # Sanity: no _poll_server set yet
        assert getattr(api, "_poll_server", None) is None
        server = restart_poll_server(api, port=0)
        try:
            assert api._poll_server is server
            status, _, body = _http_get_json(f"http://127.0.0.1:{server.server_address[1]}/api/health")
            assert status == 200
        finally:
            server.shutdown()


# ── TestPollApiInfoMethods ──


class TestPollApiInfoMethods:
    def test_get_info_when_not_running(self, api):
        info = api.get_poll_api_info()
        assert info["running"] is False
        assert info["url"] == ""
        assert info["port"] is None
        assert info["default_port"] == 7891

    def test_get_info_when_running(self, api):
        server = start_poll_server(api, port=0)
        try:
            info = api.get_poll_api_info()
            assert info["running"] is True
            assert info["host"] == "127.0.0.1"
            assert info["port"] == server.server_address[1]
            assert info["url"].startswith("http://127.0.0.1:")
        finally:
            server.shutdown()

    def test_set_port_persists_to_preferences(self, api):
        # Use OS-assigned port to avoid collisions with anything else
        # but assert prefs got written with the integer we asked for.
        # We can't easily test a specific port without races, so use a
        # high port unlikely to collide.
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        try:
            info = api.set_poll_api_port(free_port)
            assert info["port"] == free_port
            assert info["configured_port"] == free_port
            prefs = api.load_preferences()
            assert prefs["pollApiPort"] == free_port
        finally:
            srv = getattr(api, "_poll_server", None)
            if srv is not None:
                srv.shutdown()

    def test_set_port_rejects_out_of_range(self, api):
        with pytest.raises(ValueError, match="out of range"):
            api.set_poll_api_port(80)
        with pytest.raises(ValueError, match="out of range"):
            api.set_poll_api_port(70000)

    def test_set_port_rejects_non_integer(self, api):
        with pytest.raises(ValueError, match="must be an integer"):
            api.set_poll_api_port("not-a-port")
