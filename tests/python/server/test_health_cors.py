"""`GET /v1/health` is cross-origin readable; nothing else is.

The server picker in Preferences (js/server-list.js) shows a live reachability
dot per configured server, probed from the browser with a cross-origin fetch of
each candidate's `/v1/health`. Without `Access-Control-Allow-Origin` on that
response the browser hides a perfectly good 200 behind a CORS error, which is
indistinguishable from the server being down — every dot would read red.

The other half is just as important: the header must not spread. Every route
except health carries real inventory data, and `*` on any of them would let any
web page a user visits read it.
"""

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

# A simulated non-loopback peer, i.e. a browser on another machine.
REMOTE = ("100.64.0.7", 51234)
ORIGIN = "https://dubis-server.example.ts.net"


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


def test_health_allows_any_origin(client):
    r = client.get("/v1/health", headers={"Origin": ORIGIN})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"


def test_health_allow_origin_is_present_without_an_origin_header(client):
    # The header is set unconditionally rather than echoed back per-request, so
    # it does not depend on the probe sending an Origin (nor on any Vary).
    r = client.get("/v1/health")
    assert r.headers["access-control-allow-origin"] == "*"


def test_health_does_not_allow_credentials(client):
    # `*` and credentials are mutually exclusive by spec, and js/server-probe.js
    # sends `credentials: 'omit'` to match. If this ever grew an
    # Access-Control-Allow-Credentials the browser would reject the response
    # outright, turning every dot red.
    r = client.get("/v1/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-credentials" not in r.headers


def test_health_readable_cross_origin_from_a_remote_peer(tmp_path, monkeypatch):
    """The probe is unauthenticated, so it has to work against a token-gated
    server too — a 401 would read as "down" for a server that is fine."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:secret")
    api = _api(tmp_path)
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            r = c.get("/v1/health", headers={"Origin": ORIGIN})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert r.headers["access-control-allow-origin"] == "*"
    finally:
        api.shutdown()


@pytest.mark.parametrize("path", ["/v1/meta", "/v1/parts", "/v1/preferences"])
def test_other_routes_are_not_cross_origin_readable(client, path):
    r = client.get(path, headers={"Origin": ORIGIN})
    assert r.status_code == 200, f"{path} should exist for this test to mean anything"
    assert "access-control-allow-origin" not in r.headers


def test_health_carries_nothing_worth_protecting(client):
    # The justification for opening this one route: a constant payload. If this
    # ever grows a field, the `*` above needs re-deciding, not extending.
    assert client.get("/v1/health").json() == {"ok": True}
