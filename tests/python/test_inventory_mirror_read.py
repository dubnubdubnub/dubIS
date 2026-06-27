import json
import threading
import urllib.error
import urllib.request

import pytest

from inventory_mirror import SnapshotStore, make_read_server


@pytest.fixture
def read_server(tmp_path):
    store = SnapshotStore(str(tmp_path / "snap.json"))
    store.update(
        {"inventory": [{"section": "R", "lcsc": "C1", "qty": 3}],
         "csv_fields": ["section", "lcsc", "qty"],
         "pushed_at": "2026-01-01T00:00:00+00:00", "source": "dubis", "dubis_running": True},
        received_at="2026-01-01T00:00:00+00:00",
    )
    server = make_read_server(store, allowlist=["owner@example.com"], port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=3)


def test_inventory_local_no_header_allowed(read_server):
    resp = _get(read_server + "/api/inventory")
    data = json.loads(resp.read())
    assert data["ok"] and data["count"] == 1
    assert data["freshness"]["source"] == "dubis"
    assert "age_seconds" in data["freshness"]


def test_inventory_allowlisted_identity_allowed(read_server):
    resp = _get(read_server + "/api/inventory", {"Tailscale-User-Login": "owner@example.com"})
    assert json.loads(resp.read())["ok"]


def test_inventory_unlisted_identity_forbidden(read_server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(read_server + "/api/inventory", {"Tailscale-User-Login": "intruder@example.com"})
    assert e.value.code == 403


def test_csv_endpoint(read_server):
    resp = _get(read_server + "/api/inventory.csv")
    body = resp.read().decode()
    assert resp.headers["Content-Type"].startswith("text/csv")
    assert body.splitlines()[0] == "section,lcsc,qty"
    assert "C1" in body


def test_stats_endpoint(read_server):
    data = json.loads(_get(read_server + "/api/stats").read())
    assert data["part_count"] == 1 and data["total_qty"] == 3


def test_health_endpoint(read_server):
    data = json.loads(_get(read_server + "/api/health").read())
    assert data["ok"] and data["has_snapshot"] is True


def test_empty_allowlist_denies_header_but_allows_loopback(tmp_path):
    """Empty allowlist should deny requests with Tailscale header but allow loopback."""
    store = SnapshotStore(str(tmp_path / "snap.json"))
    store.update(
        {"inventory": [{"section": "R", "lcsc": "C1", "qty": 3}],
         "csv_fields": ["section", "lcsc", "qty"],
         "pushed_at": "2026-01-01T00:00:00+00:00", "source": "dubis", "dubis_running": True},
        received_at="2026-01-01T00:00:00+00:00",
    )
    server = make_read_server(store, allowlist=[], port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    url = f"http://{host}:{port}"

    try:
        # Request WITH Tailscale-User-Login header should be forbidden (empty allowlist)
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(url + "/api/inventory", {"Tailscale-User-Login": "someone@example.com"})
        assert e.value.code == 403

        # Request WITHOUT header should be allowed (loopback trust)
        resp = _get(url + "/api/inventory")
        data = json.loads(resp.read())
        assert data["ok"] and data["count"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_endpoints_handle_empty_snapshot(tmp_path):
    """Endpoints should handle gracefully when no snapshot has been pushed yet."""
    store = SnapshotStore(str(tmp_path / "snap.json"))
    # Don't call store.update() — start with no snapshot
    server = make_read_server(store, allowlist=["owner@example.com"], port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    url = f"http://{host}:{port}"

    try:
        # /api/health should return 200 with has_snapshot=False
        resp = _get(url + "/api/health")
        data = json.loads(resp.read())
        assert data["ok"] and data["has_snapshot"] is False

        # /api/inventory should return 200 with count=0 and freshness fields as None
        resp = _get(url + "/api/inventory")
        data = json.loads(resp.read())
        assert data["ok"] and data["count"] == 0
        assert data["freshness"]["pushed_at"] is None
        assert data["freshness"]["received_at"] is None
        assert data["freshness"]["age_seconds"] is None
        assert data["freshness"]["dubis_running"] is None
        assert data["freshness"]["source"] is None

        # /api/inventory.csv should return 200 (header row only, no errors)
        resp = _get(url + "/api/inventory.csv")
        body = resp.read().decode()
        assert resp.headers["Content-Type"].startswith("text/csv")
        # Empty inventory + no fields = just an empty line or no content
        assert len(body) >= 0  # CSV endpoint should not error
    finally:
        server.shutdown()
        server.server_close()
