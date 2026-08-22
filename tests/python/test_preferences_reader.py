"""Reader preferences — `reader_mode` / `reader_url` in preferences.json.

Mirrors `server_url` (commit a060a1a): a key that lives in the free-form
preferences file, is normalized on write, and has a documented default when the
file predates it. The difference is that reader_mode has a *closed vocabulary*,
so an unknown value is rejected rather than coerced — writing a typo'd mode
would otherwise silently leave the reader off (or, worse, look enabled) with
nothing to point at.

The default is `off` on purpose: `local` mode downloads multi-GB model weights,
which must never happen because a clean install happened.
"""

from __future__ import annotations

import json

import pytest

from domain.api_preferences import (
    READER_MODES,
    READER_MODE_DEFAULT,
    resolve_reader_mode,
    resolve_reader_url,
)

# A preferences.json as written by the current JS store: every key the loader
# carries, and no reader keys at all. This is the file on every existing
# install, so it is the backwards-compatibility case that matters.
LEGACY_PREFS = {
    "thresholds": {"C100000": 25},
    "inventory_view": {
        "group_level": 1,
        "sort_column": "qty",
        "sort_scope": None,
        "vendor_group_scope": None,
    },
    "shortcuts": {"vimNav": True},
    "behavior": {"autoCopySelection": False, "reelCeiling": 80},
    "saved_views": [{"id": "v1", "name": "Low stock"}],
    "server_url": "https://dubis.example.ts.net",
}


@pytest.fixture
def client(api):
    """TestClient over /v1, backed by the same temp-dir InventoryApi.

    Defined locally rather than reused from tests/python/server/conftest.py so
    the route assertions below share the plain `api` fixture (and its empty
    preferences file) with the facade assertions above.
    """
    from fastapi.testclient import TestClient

    from server.app import create_app

    with TestClient(create_app(api)) as c:
        yield c
    api.shutdown()


def write_prefs(api, prefs: dict) -> None:
    with open(api.prefs_json, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


# ── Defaults / backwards compatibility ──


def test_default_mode_is_off_when_the_key_was_never_written(api):
    write_prefs(api, LEGACY_PREFS)
    loaded = api.load_preferences()
    assert "reader_mode" not in loaded
    assert resolve_reader_mode(loaded) == "off"
    assert READER_MODE_DEFAULT == "off"


def test_default_mode_is_off_with_no_preferences_file_at_all(api):
    assert resolve_reader_mode(api.load_preferences()) == "off"
    assert resolve_reader_url(api.load_preferences()) == ""


def test_loading_a_legacy_file_does_not_invent_reader_keys(api):
    """load_preferences must keep returning the file verbatim.

    The JS store posts the WHOLE in-memory object back on the next save, so a
    key injected here would be written to disk by the next slider touch. The
    default belongs in the resolver, not in the loader.
    """
    write_prefs(api, LEGACY_PREFS)
    assert api.load_preferences() == LEGACY_PREFS


def test_empty_or_null_mode_reads_as_the_default(api):
    # A hand-edited file, or a UI that cleared the field: absence, not a typo.
    assert resolve_reader_mode({"reader_mode": ""}) == "off"
    assert resolve_reader_mode({"reader_mode": None}) == "off"


# ── Round-trips ──


@pytest.mark.parametrize("mode", ["off", "local", "remote", "auto"])
def test_every_mode_round_trips_through_save_and_load(api, mode):
    api.save_preferences({"reader_mode": mode})
    assert api.load_preferences()["reader_mode"] == mode
    assert resolve_reader_mode(api.load_preferences()) == mode


def test_the_four_modes_are_the_whole_vocabulary():
    assert READER_MODES == ("off", "local", "remote", "auto")


def test_mode_round_trips_from_a_json_string_body(api):
    # inventory_api.save_preferences accepts a JSON string too (the old bridge
    # convention), and validation must not be reachable only via dicts.
    api.save_preferences(json.dumps({"reader_mode": "auto"}))
    assert api.load_preferences()["reader_mode"] == "auto"


def test_mode_is_normalized_case_insensitively(api):
    api.save_preferences({"reader_mode": "  Local "})
    assert api.load_preferences()["reader_mode"] == "local"


# ── Rejection ──


def test_an_unknown_mode_is_rejected_and_nothing_is_written(api):
    write_prefs(api, LEGACY_PREFS)
    with pytest.raises(ValueError) as exc:
        api.save_preferences({**LEGACY_PREFS, "reader_mode": "locl"})
    message = str(exc.value)
    assert "reader_mode" in message
    assert "locl" in message
    # The clear error names the alternatives rather than just saying "invalid".
    for mode in READER_MODES:
        assert mode in message
    # Rejected, not coerced: the file is untouched, so the bad value never
    # becomes a persisted "off" that looks deliberate.
    assert api.load_preferences() == LEGACY_PREFS


def test_a_non_string_mode_is_rejected(api):
    with pytest.raises(ValueError):
        api.save_preferences({"reader_mode": 3})


def test_a_rejected_mode_does_not_create_a_preferences_file(api):
    import os

    with pytest.raises(ValueError):
        api.save_preferences({"reader_mode": "on"})
    assert not os.path.exists(api.prefs_json)


# ── reader_url ──


def test_reader_url_round_trips(api):
    api.save_preferences({"reader_mode": "remote", "reader_url": "http://y740.ts.net:8080"})
    loaded = api.load_preferences()
    assert loaded["reader_url"] == "http://y740.ts.net:8080"
    assert resolve_reader_url(loaded) == "http://y740.ts.net:8080"


def test_empty_reader_url_round_trips_and_means_fleet_discovery(api):
    # Empty is not "unset by accident" — in remote mode it is the instruction to
    # discover a node through the fleet registry (fleet_client.py) instead.
    api.save_preferences({"reader_mode": "remote", "reader_url": ""})
    loaded = api.load_preferences()
    assert loaded["reader_url"] == ""
    assert resolve_reader_url(loaded) == ""


def test_reader_url_trailing_slash_is_stripped(api):
    api.save_preferences({"reader_url": "http://y740.ts.net:8080///"})
    assert api.load_preferences()["reader_url"] == "http://y740.ts.net:8080"


@pytest.mark.parametrize(
    "bad",
    [
        "y740.ts.net:8080",  # no scheme: would resolve against our own origin
        "ftp://y740.ts.net",
        "not a url at all",
        "http://",  # scheme but no host
        7,
    ],
)
def test_an_obviously_bad_reader_url_is_rejected(api, bad):
    with pytest.raises(ValueError) as exc:
        api.save_preferences({"reader_url": bad})
    assert "reader_url" in str(exc.value)


def test_resolve_reader_url_tolerates_a_hand_edited_bad_value(api):
    # save_preferences guards the write path; a file edited by hand behind our
    # back must not crash the read path — it reads as "no explicit endpoint",
    # which in remote mode falls back to fleet discovery.
    assert resolve_reader_url({"reader_url": "y740.ts.net"}) == ""
    assert resolve_reader_url({"reader_url": None}) == ""
    assert resolve_reader_url({}) == ""


def test_resolve_reader_mode_tolerates_a_hand_edited_bad_value():
    assert resolve_reader_mode({"reader_mode": "locl"}) == "off"


# ── Coexistence with the rest of the file ──


def test_a_reader_mode_write_does_not_clobber_unrelated_preferences(api):
    write_prefs(api, LEGACY_PREFS)
    # The real mechanism: the client loads the whole object, sets one key, and
    # posts the whole object back.
    prefs = api.load_preferences()
    prefs["reader_mode"] = "auto"
    prefs["reader_url"] = "http://y740.ts.net:8080"
    api.save_preferences(prefs)

    after = api.load_preferences()
    for key, value in LEGACY_PREFS.items():
        assert after[key] == value, f"{key} was clobbered by the reader-mode write"
    assert after["reader_mode"] == "auto"
    assert after["reader_url"] == "http://y740.ts.net:8080"
    assert after["server_url"] == LEGACY_PREFS["server_url"]


def test_saving_unrelated_preferences_still_leaves_the_file_verbatim(api):
    # No reader key present -> none invented. Preserves the exact-equality
    # contract the existing /v1 preferences round-trip tests assert.
    api.save_preferences({"threshold": 5, "columns": ["a", "b"]})
    assert api.load_preferences() == {"threshold": 5, "columns": ["a", "b"]}


# ── Over /v1 ──


def test_put_reader_mode_over_v1_round_trips(client):
    resp = client.put("/v1/preferences", json={"reader_mode": "auto", "reader_url": ""})
    assert resp.status_code == 200
    assert client.get("/v1/preferences").json() == {"reader_mode": "auto", "reader_url": ""}


def test_put_an_invalid_reader_mode_over_v1_is_a_400(client):
    resp = client.put("/v1/preferences", json={"reader_mode": "locl"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "value_error"
    assert "reader_mode" in body["error"]
    # Nothing persisted.
    assert "reader_mode" not in client.get("/v1/preferences").json()
