"""Cart CRUD + item + split/consolidate/export route tests, plus the
`carts.updated` SSE publish contract (mutations publish it, GET/export do not)."""

from server import events


def _create_cart(client, name="Route Cart"):
    r = client.post("/v1/carts", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["detail"]["id"]


def test_cart_route_crud(client):
    cid = _create_cart(client)

    r = client.post(f"/v1/carts/{cid}/items", json={"part_id": "C100000", "qty": 5})
    assert r.status_code == 200, r.text

    r = client.get(f"/v1/carts/{cid}")
    body = r.json()
    items = body["items"]
    assert items[0]["qty"] == 5

    r = client.get(f"/v1/carts/{cid}/export", params={"distributor": "lcsc", "format": "paste"})
    assert r.status_code == 200, r.text
    assert "C100000" in r.json()["content"]


def test_list_carts_includes_active_cart_id(client):
    cid = _create_cart(client)
    r = client.post(f"/v1/carts/{cid}/active")
    assert r.status_code == 200, r.text

    r = client.get("/v1/carts")
    assert r.status_code == 200
    body = r.json()
    assert body["active_cart_id"] == cid
    assert any(c["id"] == cid for c in body["carts"])


def test_rename_cart(client):
    cid = _create_cart(client)
    r = client.put(f"/v1/carts/{cid}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["name"] == "Renamed"


def test_update_and_remove_cart_item(client):
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={"part_id": "C15742", "qty": 5})

    r = client.patch(f"/v1/carts/{cid}/items/C15742", json={"qty": 9})
    assert r.status_code == 200, r.text
    items = r.json()["detail"]["items"]
    assert next(i for i in items if i["ref"] == "C15742")["qty"] == 9

    r = client.delete(f"/v1/carts/{cid}/items/C15742")
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["items"] == []


def test_clear_cart(client):
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={"part_id": "C15742", "qty": 5})
    r = client.post(f"/v1/carts/{cid}/clear")
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["items"] == []


def test_add_bom_missing_to_cart(client):
    cid = _create_cart(client)
    r = client.post(
        f"/v1/carts/{cid}/add-bom-missing",
        json={"missing": [{"part_id": "C15742", "qty": 3}]},
    )
    assert r.status_code == 200, r.text
    items = r.json()["detail"]["items"]
    assert any(i["part_id"] == "C15742" and i["qty"] == 3 for i in items)


def test_split_and_consolidate_cart(client):
    cid = _create_cart(client)
    client.post(
        f"/v1/carts/{cid}/items",
        json={"part_id": "C15742", "qty": 5, "target_distributor": "lcsc"},
    )

    r = client.post(
        f"/v1/carts/{cid}/split",
        json={"distributor": "lcsc", "new_name": "LCSC split"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()["detail"]
    assert "source" in detail and "new" in detail

    r = client.post(f"/v1/carts/{cid}/consolidate", json={"distributor": "lcsc"})
    assert r.status_code == 200, r.text
    assert "unresolved" in r.json()["detail"]


def test_delete_cart(client):
    cid = _create_cart(client)
    r = client.delete(f"/v1/carts/{cid}")
    assert r.status_code == 200, r.text

    r = client.get(f"/v1/carts/{cid}")
    assert r.status_code == 404


def test_get_missing_cart_is_404(client):
    r = client.get("/v1/carts/does-not-exist")
    assert r.status_code == 404


def test_create_cart_publishes_carts_updated(client):
    q = events.subscribe()
    try:
        _create_cart(client)
        name, data = q.get(timeout=2)
        assert name == "carts.updated"
    finally:
        events.unsubscribe(q)


def test_export_cart_does_not_publish(client):
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={"part_id": "C15742", "qty": 5})
    q = events.subscribe()
    try:
        r = client.get(f"/v1/carts/{cid}/export", params={"distributor": "lcsc", "format": "paste"})
        assert r.status_code == 200
        assert q.empty()
    finally:
        events.unsubscribe(q)


def test_get_cart_does_not_publish(client):
    cid = _create_cart(client)
    q = events.subscribe()
    try:
        r = client.get(f"/v1/carts/{cid}")
        assert r.status_code == 200
        assert q.empty()
    finally:
        events.unsubscribe(q)
