"""Tests for PnP consume /v1 route + legacy (non-`/v1`) aliases."""

from server import events


def test_pnp_consume_happy_path_decrements_stock(client):
    r = client.post("/v1/pnp/consume", json={"part_id": "C100000", "qty": 3})
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "part_key": "C100000", "new_qty": 7}

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 7


def test_pnp_consume_publishes_both_events(client):
    q = events.subscribe()
    try:
        client.post("/v1/pnp/consume", json={"part_id": "C100000", "qty": 2})

        name1, data1 = q.get(timeout=2)
        assert name1 == "inventory.consumed"
        assert data1 == {"part_id": "C100000", "part_key": "C100000", "qty": 2, "new_qty": 8}

        name2, data2 = q.get(timeout=2)
        assert name2 == "inventory.updated"
        assert data2["reason"] == "pnp-consume"
    finally:
        events.unsubscribe(q)


def test_pnp_consume_unknown_part_is_404(client):
    r = client.post("/v1/pnp/consume", json={"part_id": "NO-SUCH-PART", "qty": 1})
    assert r.status_code == 404


def test_pnp_consume_uses_part_map(api, client, tmp_path):
    import json
    import os

    path = os.path.join(api.base_dir, "pnp_part_map.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"FEEDER-1": "C100000"}, f)

    r = client.post("/v1/pnp/consume", json={"part_id": "FEEDER-1", "qty": 1})
    assert r.status_code == 200
    assert r.json()["part_key"] == "C100000"


def test_legacy_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_legacy_parts_returns_organized_inventory(client):
    r = client.get("/api/parts")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    lcscs = {i["lcsc"] for i in body["parts"]}
    assert "C100000" in lcscs


def test_legacy_consume_matches_pnp_server_response_shape(client):
    r = client.post("/api/consume", json={"part_id": "C100000", "qty": 1})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"ok", "part_key", "new_qty"}
    assert r.json()["new_qty"] == 9
