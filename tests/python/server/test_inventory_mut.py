"""Inventory mutation routes: adjust/update/delete/import/consume + rollback."""

from server import events
from tests.python.helpers import make_part, write_ledger


def test_adjust_part_set_happy_path(client):
    r = client.post("/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 5})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 5


def test_adjust_part_add_happy_path(client):
    client.post("/v1/parts/C100000/adjust", json={"adj_type": "add", "quantity": 3})
    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 13


def test_adjust_part_remove_happy_path(client):
    client.post("/v1/parts/C100000/adjust", json={"adj_type": "remove", "quantity": 4})
    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 6


def test_adjust_part_publishes_inventory_updated(client):
    q = events.subscribe()
    try:
        client.post("/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 1})
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data["reason"] == "adjust"
    finally:
        events.unsubscribe(q)


def test_adjust_part_response_never_carries_inventory(client):
    # Phase 1b Task 10 removed the `?include=inventory` echo entirely — the
    # query param is now just an unknown/ignored param (FastAPI silently
    # drops it), and the response is always the plain `{"ok", "detail"}`
    # envelope, never an `inventory` key, whether or not the (now-inert)
    # query param is present. Frontend refresh is SSE-driven.
    r_with_include = client.post(
        "/v1/parts/C100000/adjust?include=inventory",
        json={"adj_type": "set", "quantity": 7},
    )
    assert r_with_include.status_code == 200
    body_with_include = r_with_include.json()
    assert "inventory" not in body_with_include
    assert body_with_include["detail"]["part_key"] == "C100000"

    r_without_include = client.post(
        "/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 1},
    )
    assert "inventory" not in r_without_include.json()


def test_update_part_fields_roundtrip(client):
    r = client.patch(
        "/v1/parts/C100000",
        json={"fields": {"description": "Updated Resistor"}},
    )
    assert r.status_code == 200

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["description"] == "Updated Resistor"


def test_delete_part_with_purchase_history_is_400(client):
    r = client.delete("/v1/parts/C100000")
    assert r.status_code == 400


def test_delete_part_without_purchase_history_succeeds(api, client):
    api.adjust_part("set", "ADJUSTONLY-1", 5, note="manual add", source="test")
    r = client.delete("/v1/parts/ADJUSTONLY-1")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_consume_bom_end_to_end(client):
    r = client.post(
        "/v1/bom/consume",
        json={
            "matches": [{"part_key": "C100000", "bom_qty": 2}],
            "board_qty": 3,
            "bom_name": "test.csv",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 4  # 10 - (2*3)


def test_rollback_source_removes_tagged_adjustments(api, client):
    api.adjust_part("add", "C100000", 5, note="test bump", source="test:session-1")

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 15

    r = client.delete("/v1/adjustments/by-source/test:session-1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["detail"]["removed"]) == 1

    r3 = client.get("/v1/parts")
    item = next(i for i in r3.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 10


def test_update_part_price(client):
    r = client.put("/v1/parts/C100000/price", json={"unit_price": 0.05, "ext_price": 0.5})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_import_purchases(client):
    r = client.post(
        "/v1/purchases/import",
        json={"rows": [make_part(lcsc="C200000", qty=20)]},
    )
    assert r.status_code == 200
    r2 = client.get("/v1/parts")
    lcscs = {i["lcsc"] for i in r2.json()["inventory"]}
    assert "C200000" in lcscs


def test_remove_last_purchases_requires_count(client):
    r = client.delete("/v1/purchases/last")
    assert r.status_code == 422


def test_remove_last_purchases(api, client):
    write_ledger(api, [make_part(lcsc="C100000", qty=10), make_part(lcsc="C300000", qty=1)])
    r = client.delete("/v1/purchases/last?count=1")
    assert r.status_code == 200


def test_remove_last_adjustments(api, client):
    api.adjust_part("add", "C100000", 1, note="bump", source="test")
    r = client.delete("/v1/adjustments/last?count=1")
    assert r.status_code == 200


def test_fetch_missing_descriptions(client):
    r = client.post("/v1/parts/fetch-missing-descriptions")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_record_fetched_prices(client):
    r = client.post(
        "/v1/parts/C100000/fetched-prices",
        json={"distributor": "lcsc", "price_tiers": [{"qty": 1, "price": 0.01}]},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_resolve_bom_spec_no_match_returns_null(client):
    r = client.post(
        "/v1/bom/resolve-spec",
        json={"part_type": "resistor", "value": 10000.0, "package": "0402"},
    )
    assert r.status_code == 200
    assert r.json() == {"match": None}


def test_extract_spec_from_value(client):
    r = client.post(
        "/v1/spec/extract",
        json={"part_type": "resistor", "value_str": "10k", "package_str": "0402"},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
