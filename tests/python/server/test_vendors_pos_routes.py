"""Vendors + purchase-orders routes: list vendors (seeded builtins), PO
create/get/patch/delete roundtrip, delete-last-before-{po_id} precedence,
source-download 404, and one publish assertion.

Vendor bodies in these tests omit `url` so `update_vendor` never triggers a
network favicon fetch (see domain/api_vendors.py: fetch only happens when
`v.get("url")` is truthy).
"""

import base64

import vendors
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


def test_po_source_download_success_roundtrip(client, api):
    csv_bytes = b"mpn,qty,price\nMPN1,5,0.10\n"
    b64 = base64.b64encode(csv_bytes).decode("ascii")

    r = client.post(
        "/v1/purchase-orders",
        json={
            "vendor_id": "mfg:digikey",
            "notes": "with source",
            "source_file_b64": b64,
            "source_file_name": "invoice.csv",
            "line_items": [
                {"mpn": "MPN1", "manufacturer": "Acme", "quantity": 5, "unit_price": 0.1},
            ],
        },
    )
    assert r.status_code == 200, r.text

    pos = client.get("/v1/purchase-orders").json()
    assert len(pos) == 1
    po_id = pos[0]["po_id"]

    r2 = client.get(f"/v1/purchase-orders/{po_id}/source")
    assert r2.status_code == 200, r2.text

    # source_sanitizer.sanitize() does not modify .csv content (only images are
    # re-encoded to strip EXIF), so the archived bytes equal what was uploaded.
    import purchase_orders
    archived_path = purchase_orders.resolve_source_path(
        api._sources_dir, po_id, api._po_csv,
    )
    assert archived_path is not None
    with open(archived_path, "rb") as f:
        archived_bytes = f.read()
    assert archived_bytes == csv_bytes
    assert r2.content == archived_bytes

    content_disposition = r2.headers.get("content-disposition", "")
    assert "attachment" in content_disposition
    assert f"{po_id}.csv" in content_disposition


def test_delete_vendor_ok_then_gone(client):
    r = client.put("/v1/vendors", json={"name": "Deletable Co"})
    assert r.status_code == 200, r.text
    vendor_id = r.json()["detail"]["id"]

    r2 = client.delete(f"/v1/vendors/{vendor_id}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True

    remaining = client.get("/v1/vendors").json()
    assert all(v["id"] != vendor_id for v in remaining)


def test_delete_vendor_with_pos_rejected(client):
    r = client.put("/v1/vendors", json={"name": "Has A PO"})
    assert r.status_code == 200, r.text
    vendor_id = r.json()["detail"]["id"]

    po_r = client.post(
        "/v1/purchase-orders",
        json={
            "vendor_id": vendor_id,
            "notes": "ties vendor down",
            "line_items": [
                {"mpn": "MPN1", "manufacturer": "Acme", "quantity": 1, "unit_price": 1.0},
            ],
        },
    )
    assert po_r.status_code == 200, po_r.text

    r2 = client.delete(f"/v1/vendors/{vendor_id}")
    assert r2.status_code == 400, r2.text
    body = r2.json()
    assert "error" in body and body["code"] == "value_error"

    remaining = client.get("/v1/vendors").json()
    assert any(v["id"] == vendor_id for v in remaining)


def test_merge_vendors_ok_src_gone_dst_present(client):
    r_src = client.put("/v1/vendors", json={"name": "Duplicate Vendor A"})
    r_dst = client.put("/v1/vendors", json={"name": "Duplicate Vendor B"})
    assert r_src.status_code == 200, r_src.text
    assert r_dst.status_code == 200, r_dst.text
    src_id = r_src.json()["detail"]["id"]
    dst_id = r_dst.json()["detail"]["id"]

    r = client.post("/v1/vendors/merge", json={"src_id": src_id, "dst_id": dst_id})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    remaining = client.get("/v1/vendors").json()
    ids = [v["id"] for v in remaining]
    assert src_id not in ids
    assert dst_id in ids


def test_fetch_favicon_route_offline(client, monkeypatch, tmp_path):
    fake_path = str(tmp_path / "fake_favicon.ico")
    with open(fake_path, "wb") as f:
        f.write(b"fake-icon-bytes")

    def _fake_fetch_favicon(url, favicons_dir):
        assert url == "https://example.com"
        return fake_path

    monkeypatch.setattr(vendors, "fetch_favicon", _fake_fetch_favicon)

    r = client.post("/v1/vendors/favicon", json={"url": "https://example.com"})
    assert r.status_code == 200, r.text
    assert r.json() == {"path": fake_path}
