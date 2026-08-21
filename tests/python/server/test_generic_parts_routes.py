"""Generic parts + saved searches routes: create/add/exclude/preferred/list
roundtrips against the seeded fixture part (C100000), plus saved-search
create/list/delete roundtrip and one publish assertion.

Also covers the membership-review surface: propose/approve/reject with a
rationale, the 409 a re-proposal of a rejected alternate gets, and the
unreviewed default a member carries until somebody actually reviews it."""

from server import events


def _create_generic_part(client, name="10k Resistor"):
    r = client.post(
        "/v1/generic-parts",
        json={
            "name": name,
            "part_type": "resistor",
            "spec": {"value": "10k", "package": "0402"},
            "strictness": {"required": ["value", "package"]},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body["detail"]["generic_part_id"]


def test_create_generic_part_roundtrip(client):
    gid = _create_generic_part(client)

    r = client.get("/v1/generic-parts")
    assert r.status_code == 200
    ids = [g["generic_part_id"] for g in r.json()]
    assert gid in ids


def test_create_generic_part_publishes_inventory_updated(client):
    q = events.subscribe()
    try:
        _create_generic_part(client)
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data["reason"] == "generic-parts"
    finally:
        events.unsubscribe(q)


def test_add_member_exclude_preferred_roundtrip(client):
    client.get("/v1/parts")  # populate the cache's parts table (FK target for members)
    gid = _create_generic_part(client)

    r = client.post(f"/v1/generic-parts/{gid}/members", json={"part_id": "C100000"})
    assert r.status_code == 200, r.text
    members = r.json()["detail"]
    assert any(m["part_id"] == "C100000" for m in members)

    r2 = client.put(f"/v1/generic-parts/{gid}/members/C100000/preferred")
    assert r2.status_code == 200, r2.text
    preferred_members = r2.json()["detail"]
    member = next(m for m in preferred_members if m["part_id"] == "C100000")
    assert member["preferred"]

    r3 = client.post(f"/v1/generic-parts/{gid}/members/C100000/exclude")
    assert r3.status_code == 200, r3.text

    r4 = client.get("/v1/generic-parts")
    group = next(g for g in r4.json() if g["generic_part_id"] == gid)
    member_after_exclude = next(m for m in group["members"] if m["part_id"] == "C100000")
    assert member_after_exclude["source"] == "excluded"


def test_remove_member(client):
    client.get("/v1/parts")  # populate the cache's parts table (FK target for members)
    gid = _create_generic_part(client)
    client.post(f"/v1/generic-parts/{gid}/members", json={"part_id": "C100000"})

    r = client.delete(f"/v1/generic-parts/{gid}/members/C100000")
    assert r.status_code == 200, r.text
    members = r.json()["detail"]
    assert all(m["part_id"] != "C100000" for m in members)


def test_update_generic_part(client):
    gid = _create_generic_part(client)

    r = client.put(
        f"/v1/generic-parts/{gid}",
        json={
            "name": "10k Resistor v2",
            "spec": {"value": "10k", "package": "0402"},
            "strictness": {"required": ["value", "package"]},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["name"] == "10k Resistor v2"


def test_saved_search_create_list_delete_roundtrip(client):
    gid = _create_generic_part(client)

    r = client.post(
        f"/v1/generic-parts/{gid}/saved-searches",
        json={"name": "my search", "tag_state": {"tag1": "include"}, "search_text": "resistor"},
    )
    assert r.status_code == 200, r.text
    search = r.json()["detail"]
    assert search["name"] == "my search"
    search_id = search["id"]

    r2 = client.get(f"/v1/generic-parts/{gid}/saved-searches")
    assert r2.status_code == 200
    ids = [s["id"] for s in r2.json()]
    assert search_id in ids

    r3 = client.delete(f"/v1/saved-searches/{search_id}")
    assert r3.status_code == 200
    assert r3.json()["detail"]["search_id"] == search_id

    r4 = client.get(f"/v1/generic-parts/{gid}/saved-searches")
    ids_after = [s["id"] for s in r4.json()]
    assert search_id not in ids_after


def test_saved_search_create_accepts_array_tag_state(client):
    """The JS caller sends `tag_state` as an array (js/group-flyout/
    flyout-events.js), not a dict — the route must accept both shapes rather
    than 422ing on an array."""
    gid = _create_generic_part(client)

    r = client.post(
        f"/v1/generic-parts/{gid}/saved-searches",
        json={
            "name": "array tag state",
            "tag_state": [{"tag": "value-10k", "state": "include"}],
            "search_text": "resistor",
        },
    )
    assert r.status_code == 200, r.text
    search = r.json()["detail"]
    assert search["tag_state"] == [{"tag": "value-10k", "state": "include"}]

    r2 = client.get(f"/v1/generic-parts/{gid}/saved-searches")
    assert r2.status_code == 200
    found = next(s for s in r2.json() if s["id"] == search["id"])
    assert found["tag_state"] == [{"tag": "value-10k", "state": "include"}]


def test_saved_search_create_does_not_publish(client):
    gid = _create_generic_part(client)
    q = events.subscribe()
    try:
        client.post(
            f"/v1/generic-parts/{gid}/saved-searches",
            json={"name": "no publish", "tag_state": {}},
        )
        assert q.empty()
    finally:
        events.unsubscribe(q)


# ── Membership reviews (rationale + approval state) ─────────────────────────


def _seed_group_with_member(client):
    """A group containing the fixture part C100000, cache populated."""
    client.get("/v1/parts")  # populate the cache's parts table (FK target)
    gid = _create_generic_part(client)
    r = client.post(f"/v1/generic-parts/{gid}/members", json={"part_id": "C100000"})
    assert r.status_code == 200, r.text
    return gid


def test_members_default_to_unreviewed(client):
    """A freshly added member is 'unreviewed' — never 'approved'."""
    gid = _seed_group_with_member(client)

    group = next(g for g in client.get("/v1/generic-parts").json()
                 if g["generic_part_id"] == gid)
    member = next(m for m in group["members"] if m["part_id"] == "C100000")
    assert member["review"]["approval"] == "unreviewed"
    assert member["review"]["rationale"] == ""
    assert client.get(f"/v1/generic-parts/{gid}/reviews").json() == []


def test_review_propose_then_approve_roundtrip(client):
    gid = _seed_group_with_member(client)

    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={
            "approval": "proposed",
            "rationale": "same 10k 0402 thin film; check the tempco",
            "spec_deltas": [{"field": "tempco", "kind": "parametric",
                             "reference": "25ppm", "candidate": "100ppm",
                             "blocking": False}],
            "asserted_by": "isaac",
        },
    )
    assert r.status_code == 200, r.text
    detail = r.json()["detail"]
    assert detail["review"]["approval"] == "proposed"
    assert detail["review"]["spec_deltas"][0]["field"] == "tempco"
    assert any(m["part_id"] == "C100000" for m in detail["members"])

    r2 = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "approved", "rationale": "tempco is fine on this rail",
              "asserted_by": "isaac"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["detail"]["review"]["approval"] == "approved"

    reviews = client.get(f"/v1/generic-parts/{gid}/reviews").json()
    assert len(reviews) == 1
    assert reviews[0]["part_id"] == "C100000"
    assert reviews[0]["approval"] == "approved"
    assert reviews[0]["is_member"] is True
    assert [h["approval"] for h in reviews[0]["history"]] == ["proposed"]


def test_review_defaults_asserted_by_to_the_caller_identity(client):
    gid = _seed_group_with_member(client)

    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "approved", "rationale": "identical part, second source"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["detail"]["review"]["asserted_by"] == "local"


def test_reject_then_repropose_returns_409_with_the_prior_verdict(client):
    gid = _seed_group_with_member(client)
    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "rejected",
              "rationale": "mirrored pinout: pin 1 is OUT, not IN+",
              "asserted_by": "isaac"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["review"]["approval"] == "rejected"

    r2 = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "approved", "rationale": "same family, much cheaper"},
    )

    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body["code"] == "alternate_rejected"
    assert "mirrored pinout" in body["error"]
    assert body["detail"]["review"]["approval"] == "rejected"
    assert body["detail"]["part_id"] == "C100000"
    # The refused write changed nothing.
    reviews = client.get(f"/v1/generic-parts/{gid}/reviews").json()
    assert reviews[0]["approval"] == "rejected"


def test_repropose_with_acknowledgement_succeeds(client):
    gid = _seed_group_with_member(client)
    client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "rejected", "rationale": "mirrored pinout",
              "asserted_by": "isaac"},
    )

    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "proposed", "rationale": "re-checking against rev B",
              "acknowledge_rejection": True},
    )

    assert r.status_code == 200, r.text
    review = r.json()["detail"]["review"]
    assert review["approval"] == "proposed"
    assert [h["approval"] for h in review["history"]] == ["rejected"]


def test_review_rejects_a_verdict_with_no_rationale(client):
    gid = _seed_group_with_member(client)
    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "approved"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "value_error"
    assert "rationale" in r.json()["error"]


def test_review_rejects_an_unknown_approval_state(client):
    gid = _seed_group_with_member(client)
    r = client.post(
        f"/v1/generic-parts/{gid}/members/C100000/review",
        json={"approval": "blessed", "rationale": "looks fine"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "value_error"


def test_review_publishes_inventory_updated(client):
    gid = _seed_group_with_member(client)
    q = events.subscribe()
    try:
        client.post(
            f"/v1/generic-parts/{gid}/members/C100000/review",
            json={"approval": "approved", "rationale": "identical second source"},
        )
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data["reason"] == "generic-parts"
    finally:
        events.unsubscribe(q)


def test_review_on_an_unknown_group_returns_404(client):
    client.get("/v1/parts")
    r = client.post(
        "/v1/generic-parts/nope/members/C100000/review",
        json={"approval": "proposed", "rationale": "looks similar"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "not_found"
