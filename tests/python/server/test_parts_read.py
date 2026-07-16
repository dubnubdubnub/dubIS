"""GET /v1/parts and per-part read routes."""

from tests.python.helpers import make_part, write_ledger


def test_list_parts_matches_seeded_fixture(client, api):
    r = client.get("/v1/parts")
    assert r.status_code == 200
    body = r.json()
    lcscs = {item["lcsc"] for item in body["inventory"]}
    assert "C100000" in lcscs


def test_part_history_shape(client):
    r = client.get("/v1/parts/C100000/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_purchase_history_shape(client):
    r = client.get("/v1/parts/C100000/purchase-history")
    assert r.status_code == 200
    assert r.json() == {"has_purchase_history": True}


def test_groups_shape(client):
    r = client.get("/v1/parts/C100000/groups")
    assert r.status_code == 200
    assert r.json() == {"groups": []}


def test_prices_shape(client):
    r = client.get("/v1/parts/C100000/prices")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_distributors_shape(client):
    r = client.get("/v1/parts/C100000/distributors")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_spec_shape(client):
    r = client.get("/v1/parts/C100000/spec")
    assert r.status_code == 200
    assert isinstance(r.json()["spec"], dict)


def test_warnings(client):
    r = client.get("/v1/warnings")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_unknown_part_last_po_quantity_is_null(client):
    r = client.get("/v1/parts/UNKNOWN-KEY/last-po-quantity")
    assert r.status_code == 200
    assert r.json() == {"quantity": None}


def test_encoded_space_key_round_trips(api, client):
    write_ledger(api, [make_part(mpn="ABC 123", qty=5)])
    r = client.get("/v1/parts/ABC%20123/purchase-history")
    assert r.status_code == 200
    assert r.json() == {"has_purchase_history": True}
