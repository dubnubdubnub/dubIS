"""GET /v1/meta carries the threshold defaults.

They travel here because a client that judges stock has to reach the same
number the inventory grid colours with, and this is the route that already
answers "what are this server's categories". `dubis low-stock` is that client:
it used to carry its own fallback of 0, which made it disagree with the window
open beside it. See domain/thresholds.py.
"""

from __future__ import annotations

from domain.thresholds import BASE_THRESHOLD, PRESETS, PRESET_SHIPPED


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
