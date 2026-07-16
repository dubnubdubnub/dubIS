"""Vendors + purchase-orders routes: list vendors (seeded builtins), PO
create/get/patch/delete roundtrip, delete-last-before-{po_id} precedence,
source-download 404, and one publish assertion.

Vendor bodies in these tests omit `url` so `update_vendor` never triggers a
network favicon fetch (see domain/api_vendors.py: fetch only happens when
`v.get("url")` is truthy).
"""

from server import events


def _create_po(client, notes="po one"):
    r = client.post(
        "/v1/purchase-orders",
        json={
            "vendor_id": "mfg:digikey",
            "notes": notes,
            "line_items": [
                {"mpn": "MPN1", "manufacturer": "Acme", "quantity": 5, "unit_price": 0.1},
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_list_vendors_seeds_builtins(client):
    r = client.get("/v1/vendors")
    assert r.status_code == 200
    ids = [v["id"] for v in r.json()]
    assert len(ids) > 0


def test_update_vendor_create_without_url(client):
    r = client.put("/v1/vendors", json={"name": "Acme Parts"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["detail"]["name"] == "Acme Parts"


def test_create_po_get_patch_delete_roundtrip(client):
    create_body = _create_po(client)
    assert create_body["ok"] is True

    r = client.get("/v1/purchase-orders")
    assert r.status_code == 200
    pos = r.json()
    assert len(pos) == 1
    po_id = pos[0]["po_id"]

    r2 = client.get(f"/v1/purchase-orders/{po_id}")
    assert r2.status_code == 200
    detail = r2.json()
    assert detail["po"]["po_id"] == po_id
    assert len(detail["line_items"]) == 1
    assert detail["line_items"][0]["mpn"] == "MPN1"

    r3 = client.patch(f"/v1/purchase-orders/{po_id}", json={"notes": "updated notes"})
    assert r3.status_code == 200, r3.text

    r4 = client.get(f"/v1/purchase-orders/{po_id}")
    assert r4.json()["po"]["notes"] == "updated notes"

    r5 = client.delete(f"/v1/purchase-orders/{po_id}")
    assert r5.status_code == 200, r5.text

    r6 = client.get("/v1/purchase-orders")
    assert r6.json() == []


def test_delete_last_registered_before_po_id(client):
    _create_po(client, notes="po one")
    _create_po(client, notes="po two")

    r = client.get("/v1/purchase-orders")
    pos = r.json()
    assert len(pos) == 2
    newest_po_id = pos[-1]["po_id"]

    r2 = client.delete("/v1/purchase-orders/last")
    assert r2.status_code == 200, r2.text

    r3 = client.get("/v1/purchase-orders")
    remaining = r3.json()
    assert len(remaining) == 1
    assert all(p["po_id"] != newest_po_id for p in remaining)


def test_po_source_download_404_when_missing(client):
    _create_po(client)
    r = client.get("/v1/purchase-orders")
    po_id = r.json()[0]["po_id"]

    r2 = client.get(f"/v1/purchase-orders/{po_id}/source")
    assert r2.status_code == 404
    body = r2.json()
    assert "error" in body and "code" in body


def test_create_po_publishes_inventory_updated(client):
    q = events.subscribe()
    try:
        _create_po(client)
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data["reason"] == "create_po"
    finally:
        events.unsubscribe(q)
