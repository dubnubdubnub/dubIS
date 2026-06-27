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
