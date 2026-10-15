"""GET /v1/meta carries the threshold defaults.

They travel here because a client that judges stock has to reach the same
number the inventory grid colours with, and this is the route that already
answers "what are this server's categories". `dubis low-stock` is that client:
it used to carry its own fallback of 0, which made it disagree with the window
open beside it. See domain/thresholds.py.
"""

from __future__ import annotations

from domain.thresholds import BASE_THRESHOLD, PRESET_SHIPPED, PRESETS


def test_meta_carries_the_shipped_defaults(client, api):
    body = client.get("/v1/meta").json()
    assert body["default_thresholds"] == api.DEFAULT_THRESHOLDS
    assert body["default_thresholds"], "an empty default table means nothing was shipped"


def test_meta_carries_the_base(client):
    assert client.get("/v1/meta").json()["base_threshold"] == BASE_THRESHOLD


def test_every_default_names_a_category_this_server_serves(client):
    """A default for a category this server cannot produce is a typo, and the
    CLI would apply it to nothing while looking configured."""
    body = client.get("/v1/meta").json()
    served = set(body["flat_section_order"])
    assert set(body["default_thresholds"]) <= served


def test_the_defaults_are_plain_json_scalars(client):
    """The CLI reads this over HTTP and compares it to a qty, so a value that
    arrives as a string or a nested object is a contract break, not a detail."""
    body = client.get("/v1/meta").json()
    assert isinstance(body["base_threshold"], int)
    assert all(isinstance(v, int) for v in body["default_thresholds"].values())


def test_meta_carries_the_preset_bundles(client):
    """Presets are served on /v1/meta and never through /v1/preferences —
    serving them there would persist them into preferences.json as user state
    on the next whole-bag save (js/store.js). A client picks a bundle id from
    here and stores only the id plus its own sparse deltas."""
    body = client.get("/v1/meta").json()
    presets = body["threshold_presets"]
    assert [p["id"] for p in presets] == list(PRESETS)
    shipped = presets[0]
    assert shipped["id"] == PRESET_SHIPPED and shipped["thresholds"] == {}
    for entry in presets[1:]:
        assert entry["thresholds"], f"{entry['id']}: a bundle with nothing in it is the shipped sentinel"
        assert set(entry) == {"id", "name", "description", "thresholds"}


def test_preset_bundle_keys_name_categories_this_server_serves(client):
    body = client.get("/v1/meta").json()
    served = set(body["flat_section_order"])
    for entry in body["threshold_presets"]:
        assert set(entry["thresholds"]) <= served, entry["id"]


def test_preset_thresholds_are_plain_json_scalars(client):
    body = client.get("/v1/meta").json()
    for entry in body["threshold_presets"]:
        assert all(isinstance(v, int) for v in entry["thresholds"].values()), entry["id"]


# ── the snapshot-history additions (domain/threshold_snapshots.py) ───────────
#
# `default_thresholds` keeps meaning snapshot 0 — these tests pin that the
# new fields were ADDED alongside it, not swapped in for it: a client that
# knows nothing about snapshots must read exactly the old contract.


def test_meta_carries_the_active_profile_and_it_starts_as_snapshot_zero(client):
    body = client.get("/v1/meta").json()
    # A server with no writes: the active profile IS snapshot 0, which IS the
    # shipped defaults — one table, three names, no drift possible.
    assert body["active_threshold_profile"] == body["default_thresholds"]
    summary = body["threshold_snapshots"]
    assert summary["count"] == 1
    assert summary["active_id"] == 0
    assert summary["active_created_by"] == "server"
    assert summary["active_created_at"] == ""


def test_meta_active_profile_follows_a_write_but_default_thresholds_does_not(client):
    r = client.put("/v1/thresholds", json={"values": {"Diodes": 7}, "mode": "full"})
    assert r.status_code == 200
    body = client.get("/v1/meta").json()
    assert body["active_threshold_profile"]["Diodes"] == 7
    # The old key is snapshot 0 — the shipped table — and a write can never
    # move it. This is the "extend, do not reshape" contract in one assert.
    assert body["default_thresholds"]["Diodes"] == 10
    summary = body["threshold_snapshots"]
    assert summary["count"] == 2 and summary["active_id"] == 1
    assert summary["active_created_by"] == "api"  # the default tag, auth-off
