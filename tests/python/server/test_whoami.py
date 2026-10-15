"""`GET /v1/whoami` — the identity diagnostic, and the mechanism behind it.

Why this route exists: the bug it was written for is a server that answered
`{"identity":"root"}` to every tailnet caller, and the identity string alone
could not say *why*. So the response carries the evidence, and this file pins
one thing per identity path — that the mechanism named is the one that actually
resolved it, for each of the six ways in.

Two properties that are easy to lose later, pinned here rather than left to
`test_health_cors.py`'s sweep:

* it is **not** auth-exempt. Identity is per-caller information, so a caller
  who has proved nothing gets the same 401 as on any other route. `/v1/health`
  is the only route that answers unauthenticated, and it answers with a
  constant.
* it is **local-only**: a hub never proxies it, because the proxied answer
  would describe the hub's hop (its token, its socket) and call that the
  caller.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import peercred, proxy
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

LOOPBACK = ("127.0.0.1", 51234)
REMOTE = ("100.64.1.2", 51234)
PROXY = ("10.42.2.176", 51234)
ORIGIN = "https://fremont.example.ts.net"
TAILNET_LOGIN = "alice@example.com"


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


class _ScopeStamper:
    """Stand in for `server/uds.py`'s per-connection scope state (see the same
    shim in test_peercred.py) so a Unix-socket identity can be tested without
    binding a socket."""

    def __init__(self, app, identity, uid=None):
        self.app = app
        self.identity = identity
        self.uid = uid

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = dict(scope.get("state") or {})
            state[peercred.PEER_IDENTITY_KEY] = self.identity
            if self.uid is not None:
                state[peercred.PEER_UID_KEY] = self.uid
            scope = {**scope, "state": state}
        await self.app(scope, receive, send)


# ── auth off: the answer an operator gets when nothing is resolved ───────────


def test_off_mode_reports_no_identity_and_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    try:
        with TestClient(create_app(api), client=LOOPBACK) as c:
            r = c.get("/v1/whoami")
    finally:
        api.shutdown()

    assert r.status_code == 200
    body = r.json()
    assert body["identity"] is None
    assert body["mechanism"] == "auth_disabled"
    assert body["auth_mode"] == "off"
    assert "DUBIS_AUTH_MODE" in body["detail"], "the detail must name what to change"


def test_off_mode_still_registers_the_route(tmp_path, monkeypatch):
    """A diagnostic that 404s in the mode where identity is degenerate is a
    poor diagnostic — `POST /v1/auth/session` may 404 there, this may not."""
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    try:
        spec = create_app(api).openapi()
        assert "/v1/whoami" in spec["paths"]
    finally:
        api.shutdown()


# ── auth on: one mechanism per way in ───────────────────────────────────────


def _whoami(api, *, client=None, app=None, headers=None):
    target = app if app is not None else create_app(api)
    with TestClient(target, client=client) as c:
        r = c.get("/v1/whoami", headers=headers or {})
    assert r.status_code == 200, r.json()
    return r.json()


def test_loopback_peer_reports_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    try:
        body = _whoami(api, client=LOOPBACK)
    finally:
        api.shutdown()
    assert body == {
        "identity": "local",
        "mechanism": "loopback",
        "detail": "loopback TCP peer 127.0.0.1",
        "auth_mode": "on",
        "socket_uid": None,
    }


def test_bearer_token_reports_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    try:
        body = _whoami(api, client=REMOTE, headers={"Authorization": "Bearer abc123"})
    finally:
        api.shutdown()
    assert body["identity"] == "ci"
    assert body["mechanism"] == "bearer_token"
    assert body["socket_uid"] is None


def test_cookie_session_reports_the_cookie(tmp_path, monkeypatch):
    """The cookie path is a *different* mechanism from the token that minted
    it: an operator debugging "why does the browser say ci" needs to know it is
    the cookie carrying it, not the header."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            assert c.post("/v1/auth/session",
                          headers={"Authorization": "Bearer abc123"}).status_code == 200
            r = c.get("/v1/whoami")  # no Authorization this time
    finally:
        api.shutdown()
    assert r.status_code == 200
    assert r.json()["identity"] == "ci"
    assert r.json()["mechanism"] == "session_cookie"


def test_tailnet_header_from_a_trusted_proxy_reports_the_header(tmp_path, monkeypatch):
    """fremont's path, and the one that already worked: the mechanism says so
    explicitly, which is what distinguishes it from aspen's `root`."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", TAILNET_LOGIN)
    api = _api(tmp_path)
    try:
        body = _whoami(api, client=PROXY, headers={"Tailscale-User-Login": TAILNET_LOGIN})
    finally:
        api.shutdown()
    assert body["identity"] == TAILNET_LOGIN
    assert body["mechanism"] == "tailnet_header"


def test_unix_socket_peer_reports_its_uid(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    try:
        app = _ScopeStamper(create_app(api), "bob", uid=1000)
        body = _whoami(api, app=app)
    finally:
        api.shutdown()
    assert body["identity"] == "bob"
    assert body["mechanism"] == "uds_peer_uid"
    assert body["socket_uid"] == 1000
    assert "SO_PEERCRED" in body["detail"]


def test_a_degraded_unix_socket_peer_reports_no_uid(tmp_path, monkeypatch):
    """`local` with no uid — the macOS/`SO_PEERCRED`-less answer. Reporting
    `socket_uid: null` is the point: an operator reading it knows the kernel
    never named anyone, rather than guessing uid 0."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    try:
        app = _ScopeStamper(create_app(api), peercred.LOCAL_IDENTITY)
        body = _whoami(api, app=app)
    finally:
        api.shutdown()
    assert body["identity"] == "local"
    assert body["mechanism"] == "uds_peer_uid"
    assert body["socket_uid"] is None


def test_the_proxy_hop_reports_itself_and_the_uid_it_replaced(tmp_path, monkeypatch):
    """The aspen case, made legible: the identity is the person, and the uid
    that used to be the whole answer is still shown beside it."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", TAILNET_LOGIN)
    monkeypatch.setenv("DUBIS_UDS_PROXY_UIDS", "0")
    api = _api(tmp_path)
    try:
        app = _ScopeStamper(create_app(api), "root", uid=0)
        body = _whoami(api, app=app, headers={"Tailscale-User-Login": TAILNET_LOGIN})
    finally:
        api.shutdown()
    assert body["identity"] == TAILNET_LOGIN
    assert body["mechanism"] == "uds_proxy_hop"
    assert body["socket_uid"] == 0
    assert "DUBIS_UDS_PROXY_UIDS" in body["detail"]


def test_the_mechanism_matches_what_a_mutation_would_be_stamped_with(
    tmp_path, monkeypatch,
):
    """The invariant that makes the diagnostic worth trusting: it reports the
    same identity `stamp_source()` is about to write, on the same request path.
    A diagnostic that disagreed with the ledger would be worse than none."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", TAILNET_LOGIN)
    monkeypatch.setenv("DUBIS_UDS_PROXY_UIDS", "0")
    api = _api(tmp_path)
    try:
        app = _ScopeStamper(create_app(api), "root", uid=0)
        with TestClient(app, client=None) as c:
            headers = {"Tailscale-User-Login": TAILNET_LOGIN}
            who = c.get("/v1/whoami", headers=headers).json()
            c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"},
                   headers=headers)
            history = c.get("/v1/parts/C100000/history", headers=headers).json()
    finally:
        api.shutdown()

    assert who["identity"] == TAILNET_LOGIN
    assert history[-1]["source"] == f"cli@{TAILNET_LOGIN}"


# ── what the route must never become ────────────────────────────────────────


def test_it_is_not_auth_exempt(tmp_path, monkeypatch):
    """`/v1/health` is the only route that answers a caller who proved nothing,
    and it answers with a constant. This one carries per-caller data."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            r = c.get("/v1/whoami")
    finally:
        api.shutdown()
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_it_carries_no_cors_header(tmp_path, monkeypatch):
    """`test_health_cors.py` sweeps the whole surface for this; named here so
    the failure points at the route that grew the header."""
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    try:
        with TestClient(create_app(api), client=LOOPBACK) as c:
            r = c.get("/v1/whoami", headers={"Origin": ORIGIN})
    finally:
        api.shutdown()
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_a_hub_never_proxies_it():
    """The answer must describe the connection the caller made. Proxied, it
    would describe the hub's hop instead — its token, its socket."""
    assert proxy.is_local_only("/v1/whoami") is True
