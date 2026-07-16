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


def test_put_overwrites_previous_preferences(client):
    client.put("/v1/preferences", json={"a": 1})
    client.put("/v1/preferences", json={"b": 2})

    resp = client.get("/v1/preferences")
    assert resp.json() == {"b": 2}
