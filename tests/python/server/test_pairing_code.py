"""The `.<port>` suffix on pairing codes: minted by the routes, stripped by the stores."""

from __future__ import annotations

import re
import types

import pytest

import digikey_session
import jlc_session
from pairing_code import strip_code_port
from server.routes.distributors import _with_port


@pytest.mark.parametrize("code,expected", [
    ("abc.55200", "abc"),
    ("DK-x_y-z.7891", "DK-x_y-z"),
    ("abc", "abc"),                  # an older dubIS's code, or a Unix-socket one
    ("abc.123456", "abc.123456"),    # six digits is not a port
    ("", ""),
])
def test_strip_code_port(code, expected):
    assert strip_code_port(code) == expected


def _request(server):
    return types.SimpleNamespace(scope={"server": server})


def test_with_port_appends_the_listening_port():
    assert _with_port({"nonce": "abc", "ttl": 600}, _request(("127.0.0.1", 55200))) == {
        "nonce": "abc.55200", "ttl": 600}


@pytest.mark.parametrize("server", [None, ("127.0.0.1", None), ("127.0.0.1", 0), ("x", 70000), "/run/dubis.sock"])
def test_with_port_leaves_a_portless_server_alone(server):
    # A Unix-socket server has no port; the extension then uses its Options address.
    assert _with_port({"nonce": "abc", "ttl": 600}, _request(server))["nonce"] == "abc"


@pytest.mark.parametrize("store", [jlc_session, digikey_session])
def test_each_store_redeems_the_displayed_code_once(store):
    store._nonces.clear()
    try:
        nonce = store.mint_nonce()
        assert "." not in nonce  # base64url has no dot, so the suffix is unambiguous
        assert store.consume_nonce(f"{nonce}.55200") is True
        assert store.consume_nonce(f"{nonce}.55200") is False
        assert store.consume_nonce(nonce) is False
    finally:
        store._nonces.clear()


@pytest.mark.parametrize("path", ["/v1/distributors/jlcpcb/pairing", "/v1/distributors/digikey/pairing"])
def test_the_pairing_routes_hand_out_a_code_with_the_port(client, path):
    nonce = client.post(path).json()["nonce"]
    # TestClient's server is ("testserver", 80).
    assert re.search(r"\.80$", nonce), nonce


def test_a_displayed_digikey_code_round_trips_through_the_push(client, monkeypatch):
    monkeypatch.setattr(digikey_session, "validate_session_http", lambda cookies: True)
    code = client.post("/v1/distributors/digikey/pairing").json()["nonce"]
    r = client.post("/v1/distributors/digikey/push",
                    json={"nonce": code, "cookies": [{"name": "s", "value": "v", "domain": ".digikey.com"}]})
    assert r.status_code == 200, r.text
    assert r.json()["logged_in"] is True
