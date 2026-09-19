"""Tests for /v1/preferences GET/PUT."""

from __future__ import annotations


def test_get_preferences_defaults_to_empty_dict(client):
    resp = client.get("/v1/preferences")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_put_then_get_roundtrips(client):
    prefs = {"threshold": 5, "columns": ["a", "b"]}
    put_resp = client.put("/v1/preferences", json=prefs)
    assert put_resp.status_code == 200
    assert put_resp.json() == {"ok": True}

    get_resp = client.get("/v1/preferences")
    assert get_resp.status_code == 200
    assert get_resp.json() == prefs


def test_put_merges_per_key_instead_of_replacing_the_file(client):
    """Two dubIS windows now share one hub, and therefore one preferences file
    (`app_launch.py`'s attached mode). Each posts the whole object it loaded at
    startup, so a whole-file replace made every save a wholesale revert of the
    other window's work — see server/routes/preferences.py."""
    client.put("/v1/preferences", json={"a": 1})
    client.put("/v1/preferences", json={"b": 2})

    assert client.get("/v1/preferences").json() == {"a": 1, "b": 2}


def test_put_overwrites_the_keys_it_does_send(client):
    client.put("/v1/preferences", json={"a": 1, "b": 2})
    client.put("/v1/preferences", json={"b": 3})

    assert client.get("/v1/preferences").json() == {"a": 1, "b": 3}


def test_a_key_is_cleared_by_sending_an_empty_value_not_by_omitting_it(client):
    """The cost of merging, stated so it cannot be discovered by accident."""
    client.put("/v1/preferences", json={"columns": ["a", "b"]})
    client.put("/v1/preferences", json={"columns": []})

    assert client.get("/v1/preferences").json() == {"columns": []}
