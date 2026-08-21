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


# ── board count ──────────────────────────────────────────────────────────────


def test_board_count_defaults_to_one_and_round_trips(client):
    cid = _create_cart(client)
    assert client.get(f"/v1/carts/{cid}").json()["board_count"] == 1

    r = client.put(f"/v1/carts/{cid}/board-count", json={"board_count": 25})
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["board_count"] == 25
    assert client.get(f"/v1/carts/{cid}").json()["board_count"] == 25


def test_board_count_is_rejected_at_the_boundary_not_silently_clamped(client):
    """A cart building zero boards would zero every derived quantity, so the
    request is refused rather than quietly turned into one board."""
    cid = _create_cart(client)
    for bad in (0, -3):
        assert client.put(f"/v1/carts/{cid}/board-count",
                          json={"board_count": bad}).status_code == 422
    assert client.get(f"/v1/carts/{cid}").json()["board_count"] == 1


def test_a_misspelled_board_count_field_is_refused_not_ignored(client):
    cid = _create_cart(client)
    r = client.put(f"/v1/carts/{cid}/board-count", json={"boardcount": 25})
    assert r.status_code == 422


def test_setting_the_board_count_publishes_carts_updated(client):
    """It changes every derived quantity in the cart, so other panels have to
    hear about it."""
    cid = _create_cart(client)
    q = events.subscribe()
    try:
        client.put(f"/v1/carts/{cid}/board-count", json={"board_count": 10})
        name, _data = q.get(timeout=2)
        assert name == "carts.updated"
    finally:
        events.unsubscribe(q)


# ── per-line sourcing fields ─────────────────────────────────────────────────


def test_item_carries_preset_packaging_and_per_board_qty(client):
    cid = _create_cart(client)
    r = client.post(f"/v1/carts/{cid}/items", json={
        "part_id": "C100000", "qty": 200, "per_board_qty": 8,
        "preset": "reel", "target_packaging": "Tape & Reel",
    })
    assert r.status_code == 200, r.text
    item = client.get(f"/v1/carts/{cid}").json()["items"][0]
    assert item["per_board_qty"] == 8
    assert item["preset"] == "reel"
    assert item["target_packaging"] == "Tape & Reel"


def test_patching_one_field_leaves_the_others_alone(client):
    """Changing a preset must not require restating the quantity."""
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={
        "part_id": "C100000", "qty": 200, "per_board_qty": 8, "preset": "min"})
    ref = client.get(f"/v1/carts/{cid}").json()["items"][0]["ref"]

    r = client.patch(f"/v1/carts/{cid}/items/{ref}", json={"preset": "reel"})
    assert r.status_code == 200, r.text
    item = client.get(f"/v1/carts/{cid}").json()["items"][0]
    assert item["preset"] == "reel"
    assert (item["qty"], item["per_board_qty"]) == (200, 8)


def test_a_misspelled_item_field_is_refused_not_dropped(client):
    """Every field is None-means-leave-alone, so a dropped typo would answer
    200 for an edit that never happened."""
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={"part_id": "C100000", "qty": 5})
    ref = client.get(f"/v1/carts/{cid}").json()["items"][0]["ref"]
    r = client.patch(f"/v1/carts/{cid}/items/{ref}", json={"pre_set": "reel"})
    assert r.status_code == 422


def test_an_empty_preset_clears_it_back_to_the_cart_default(client):
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items", json={"part_id": "C100000", "preset": "reel"})
    ref = client.get(f"/v1/carts/{cid}").json()["items"][0]["ref"]
    client.patch(f"/v1/carts/{cid}/items/{ref}", json={"preset": ""})
    assert client.get(f"/v1/carts/{cid}").json()["items"][0]["preset"] is None


# ── the plan ─────────────────────────────────────────────────────────────────


def _seed_ladders(api):
    """Two real LCSC ladders for C100000: cut tape (reelable for $3) and a
    5,000-piece factory reel."""
    import domain.pricing as pricing

    api.rebuild_inventory()  # populates `stock`, which is where on-hand comes from
    rows = [
        {"part_id": "C100000", "distributor": "lcsc", "unit_price": u, "moq": q,
         "currency": "USD", "source": "test", "note": "",
         "packaging": pkg, "is_reel": reel, "reel_qty": 5000, "reel_fee": fee}
        for pkg, reel, fee, ladder in (
            ("Cut Tape", False, 3.0, [(1, 0.0082), (100, 0.0041), (500, 0.0033), (1000, 0.0029)]),
            ("Tape & Reel", True, "", [(5000, 0.0021), (10000, 0.0018)]),
        )
        for q, u in ladder
    ]
    pricing.record_observations(api.events_dir, rows)


def test_the_plan_explains_every_line(client, api):
    _seed_ladders(api)
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items",
                json={"part_id": "C100000", "qty": 0, "per_board_qty": 8})
    client.put(f"/v1/carts/{cid}/board-count", json={"board_count": 25})

    r = client.get(f"/v1/carts/{cid}/plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    line = plan["lines"][0]
    assert plan["board_count"] == 25
    assert (line["per_board_qty"], line["gross_qty"]) == (8, 200)
    # The seeded ledger puts 10 of C100000 on the shelf.
    assert line["covered_by_stock"] == 10
    assert line["required_qty"] == 190
    assert line["selected"]["spend"] > 0
    assert len(line["candidates"]) > 1
    assert plan["totals"]["spend"] == line["selected"]["spend"]


def test_the_plan_is_read_only_and_publishes_nothing(client, api):
    """Re-planning after a price refresh must never rewrite a decision the
    user already committed."""
    _seed_ladders(api)
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items",
                json={"part_id": "C100000", "qty": 200, "per_board_qty": 8})
    before = client.get(f"/v1/carts/{cid}").json()

    q = events.subscribe()
    try:
        assert client.get(f"/v1/carts/{cid}/plan").status_code == 200
        assert q.empty()
    finally:
        events.unsubscribe(q)

    assert client.get(f"/v1/carts/{cid}").json() == before


def test_the_preset_query_parameter_changes_the_recommendation(client, api):
    _seed_ladders(api)
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items",
                json={"part_id": "C100000", "qty": 0, "per_board_qty": 8})

    client.put(f"/v1/carts/{cid}/board-count", json={"board_count": 25})

    cheapest = client.get(f"/v1/carts/{cid}/plan", params={"preset": "min"}).json()
    reeled = client.get(f"/v1/carts/{cid}/plan",
                        params={"preset": "reel", "reel_ceiling": 80}).json()
    assert reeled["lines"][0]["selected"]["is_reel"] is True
    assert cheapest["lines"][0]["selected"]["spend"] <= reeled["lines"][0]["selected"]["spend"]


def test_an_unknown_preset_is_a_client_error_not_a_silent_default(client, api):
    _seed_ladders(api)
    cid = _create_cart(client)
    client.post(f"/v1/carts/{cid}/items",
                json={"part_id": "C100000", "qty": 0, "per_board_qty": 8})
    client.put(f"/v1/carts/{cid}/board-count", json={"board_count": 25})
    r = client.get(f"/v1/carts/{cid}/plan", params={"preset": "cheapest"})
    assert r.status_code == 400
    assert "unknown preset" in r.text


def test_a_negative_reel_ceiling_is_refused(client):
    cid = _create_cart(client)
    assert client.get(f"/v1/carts/{cid}/plan",
                      params={"reel_ceiling": -5}).status_code == 422


def test_planning_an_unknown_cart_is_a_404(client):
    assert client.get("/v1/carts/cart_nope/plan").status_code == 404


def test_a_line_covered_by_stock_costs_nothing(client, api):
    _seed_ladders(api)
    cid = _create_cart(client)
    # 10 on the shelf, one board, one placement -- nothing to buy.
    client.post(f"/v1/carts/{cid}/items",
                json={"part_id": "C100000", "qty": 0, "per_board_qty": 1})
    plan = client.get(f"/v1/carts/{cid}/plan").json()
    assert plan["lines"][0]["required_qty"] == 0
    assert plan["lines"][0]["selected"] is None
    assert plan["totals"]["spend"] == 0.0
    assert plan["totals"]["covered_by_stock"] == 1
