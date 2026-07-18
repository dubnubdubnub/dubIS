"""Auth layer: loopback trust + bearer tokens + tailnet allowlist + identity
stamping (design doc `docs/plans/2026-07-16-phase1c-remote-deploy-design.md` §1).

`off` mode (default, no `DUBIS_AUTH_MODE` set) must be byte-identical to
today's behavior — proven by the rest of `tests/python/server/` passing
untouched (no env var is set anywhere else in this suite). This module only
exercises `on` mode, driven entirely by env + `TestClient(..., client=(host,
port))` to control the simulated peer address per request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

LOOPBACK = ("127.0.0.1", 51234)
REMOTE = ("100.64.1.2", 51234)
# Simulated tailscale-operator-proxy peer -- the only source that should be
# allowed to assert Tailscale-User-Login.
PROXY = ("10.42.2.176", 51234)
# Simulated in-cluster pod hitting the ClusterIP directly -- the forge vector
# the source-IP gate exists to close. Deliberately NOT the same host as
# REMOTE so these tests can't accidentally pass via some other allowlist.
OTHER_POD = ("10.42.9.9", 51234)


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


# ── off mode (default) ───────────────────────────────────────────────────────


def test_off_mode_unset_allows_remote_peer_without_credentials(tmp_path, monkeypatch):
    """Zero DUBIS_AUTH_MODE -> middleware not installed -> no gating at all,
    even for a simulated non-loopback peer with no credentials."""
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/health")
    assert r.status_code == 200


def test_off_mode_explicit_allows_remote_peer_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "off")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts")
    assert r.status_code == 200


# ── on mode: resolution order ────────────────────────────────────────────────


def test_loopback_peer_allowed_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=LOOPBACK) as c:
        r = c.get("/v1/parts")
    assert r.status_code == 200


def test_bearer_token_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123,openpnp:xyz789")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts", headers={"Authorization": "Bearer abc123"})
    assert r.status_code == 200


def test_bearer_token_wrong_value_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_bearer_scheme_is_case_insensitive(tmp_path, monkeypatch):
    """RFC 7235: the auth-scheme token ('Bearer') is case-insensitive, only
    the credential (the token itself) is case-sensitive."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts", headers={"Authorization": "bearer abc123"})
    assert r.status_code == 200


def test_token_scheme_allowed(tmp_path, monkeypatch):
    """KiCad's HTTP library client sends `Authorization: Token <token>`
    (DRF TokenAuthentication convention) rather than `Bearer`, despite the
    token being resolved against the same DUBIS_TOKENS map. Widening the
    scheme check must resolve it to the same identity as an equivalent
    Bearer header would."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "kicad-isaac:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        bearer_resp = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 1, "note": "", "source": "bearer-check"},
            headers={"Authorization": "Bearer abc123"},
        )
        assert bearer_resp.status_code == 200
        token_resp = c.get("/v1/parts", headers={"Authorization": "Token abc123"})
        assert token_resp.status_code == 200

        adjust_resp = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 1, "note": "", "source": "mcp"},
            headers={"Authorization": "Token abc123"},
        )
        assert adjust_resp.status_code == 200
        history = c.get(
            "/v1/parts/C100000/history",
            headers={"Authorization": "Token abc123"},
        ).json()
    # Token scheme resolves to the same identity as Bearer would for the
    # same token value -- both get suffixed as "@kicad-isaac".
    assert history[-1]["source"] == "mcp@kicad-isaac"


def test_token_scheme_wrong_value_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "kicad-isaac:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts", headers={"Authorization": "Token nope"})
    assert r.status_code == 401


def test_token_scheme_is_case_insensitive(tmp_path, monkeypatch):
    """Scheme-name case-insensitivity (RFC 7235) must apply to the new
    'Token' scheme exactly as it already does for 'Bearer'."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "kicad-isaac:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        lower = c.get("/v1/parts", headers={"Authorization": "token abc123"})
        upper = c.get("/v1/parts", headers={"Authorization": "TOKEN abc123"})
        mixed = c.get("/v1/parts", headers={"Authorization": "Token abc123"})
    assert lower.status_code == 200
    assert upper.status_code == 200
    assert mixed.status_code == 200


def test_tailscale_header_allowed_when_trusted_and_allowlisted(tmp_path, monkeypatch):
    """Header honored only when the peer IS the configured trusted proxy."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com,bob@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=PROXY) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 200


def test_tailscale_header_login_not_in_allowlist_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=PROXY) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "eve@example.com"})
    assert r.status_code == 401


def test_tailscale_header_ignored_when_trust_flag_unset_spoof(tmp_path, monkeypatch):
    """Spoof regression: an attacker sending Tailscale-User-Login directly
    must NOT be trusted unless DUBIS_TRUST_TAILSCALE_HEADER=1 — the header is
    only meaningful when a real tailscale proxy injects (and can't be spoofed
    past) it."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.delenv("DUBIS_TRUST_TAILSCALE_HEADER", raising=False)
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=PROXY) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 401


def test_tailscale_header_ignored_when_peer_is_not_the_trusted_proxy(tmp_path, monkeypatch):
    """The key security regression: trust=1 + a valid, allowlisted login are
    NOT enough on their own -- an in-cluster pod hitting the ClusterIP
    directly (bypassing the tailscale operator proxy that legitimately
    injects this header) must not be able to forge its way in just because it
    knows a real tailnet login name."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=OTHER_POD) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 401


def test_tailscale_header_ignored_failsafe_when_trusted_proxy_ips_unset(tmp_path, monkeypatch):
    """Fail-safe: trust=1 with an otherwise-valid, allowlisted login but no
    DUBIS_TRUSTED_PROXY_IPS configured at all must NOT fall back to trusting
    every peer -- it must ignore the header entirely."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.delenv("DUBIS_TRUSTED_PROXY_IPS", raising=False)
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    # Even the "real" proxy peer address must not help -- there's no
    # configured trusted-proxy list to match it against.
    with TestClient(create_app(api), client=PROXY) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 401


def test_tailscale_header_ignored_failsafe_when_trusted_proxy_ips_empty_string(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=PROXY) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 401


def test_tailscale_header_failsafe_logs_one_warning(tmp_path, monkeypatch, caplog):
    """The fail-safe path must not crash and must surface exactly one clear
    warning explaining why the header is being ignored."""
    import logging as _logging

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.delenv("DUBIS_TRUSTED_PROXY_IPS", raising=False)
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with caplog.at_level(_logging.WARNING):
        create_app(api)
    warnings = [
        rec for rec in caplog.records
        if rec.levelno == _logging.WARNING and "DUBIS_TRUSTED_PROXY_IPS" in rec.message
    ]
    assert len(warnings) == 1


def test_tailscale_header_cidr_peer_inside_range_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.0.0/16")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=("10.42.7.42", 51234)) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 200


def test_tailscale_header_cidr_peer_outside_range_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.0.0/16")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=("10.43.0.1", 51234)) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 401


def test_tailscale_header_multiple_entries_comma_separated(tmp_path, monkeypatch):
    """A bare IP entry (parsed as /32) alongside a CIDR entry in the same
    comma-separated DUBIS_TRUSTED_PROXY_IPS value."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176,192.168.5.0/24")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "alice@example.com")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=("192.168.5.9", 51234)) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": "alice@example.com"})
    assert r.status_code == 200


def test_resolve_helper_handles_none_client_gracefully():
    """`request.client` can be None (malformed/missing peer info per ASGI
    spec). `_is_trusted_proxy` -- the seam AuthMiddleware._resolve calls --
    must treat that as untrusted rather than raising."""
    from server.auth import _is_trusted_proxy, _parse_trusted_proxies

    networks = _parse_trusted_proxies("10.42.2.176")
    assert _is_trusted_proxy(None, networks) is False
    assert _is_trusted_proxy("not-an-ip", networks) is False
    assert _is_trusted_proxy("10.42.2.176", networks) is True
    assert _is_trusted_proxy("10.42.2.176", ()) is False


def test_no_credentials_401_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/parts")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "Authentication required"
    assert body["code"] == "unauthorized"
    assert "detail" in body


def test_health_exempt_even_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_static_frontend_gated_in_on_mode(tmp_path, monkeypatch):
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api, static_dir=repo_root), client=REMOTE) as c:
        r = c.get("/")
    assert r.status_code == 401


def test_static_frontend_reachable_by_loopback_in_on_mode(tmp_path, monkeypatch):
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api, static_dir=repo_root), client=LOOPBACK) as c:
        r = c.get("/")
    assert r.status_code == 200


# ── identity stamping ────────────────────────────────────────────────────────


def test_source_stamped_with_identity_for_non_local_bearer_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 1, "note": "", "source": "mcp"},
            headers={"Authorization": "Bearer abc123"},
        )
        assert r.status_code == 200
        history = c.get(
            "/v1/parts/C100000/history",
            headers={"Authorization": "Bearer abc123"},
        ).json()
    assert history[-1]["source"] == "mcp@ci"


def test_source_stamped_with_bare_identity_when_client_sends_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 1, "note": ""},
            headers={"Authorization": "Bearer abc123"},
        )
        assert r.status_code == 200
        history = c.get(
            "/v1/parts/C100000/history",
            headers={"Authorization": "Bearer abc123"},
        ).json()
    assert history[-1]["source"] == "ci"


def test_source_not_stamped_for_loopback_caller(tmp_path, monkeypatch):
    """Loopback identity is `local` -- source stays exactly what the client
    sent, so desktop ledger rows are unaffected by turning auth on."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=LOOPBACK) as c:
        r = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 1, "note": "", "source": "manual"},
        )
        assert r.status_code == 200
        history = c.get("/v1/parts/C100000/history").json()
    assert history[-1]["source"] == "manual"


# ── cookie session ────────────────────────────────────────────────────────────


def test_cookie_session_flow(tmp_path, monkeypatch):
    """POST /v1/auth/session with a valid bearer sets an HttpOnly cookie;
    subsequent requests on the same client succeed using only that cookie
    (TestClient carries a cookie jar across requests like a browser)."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/auth/session", headers={"Authorization": "Bearer abc123"})
        assert r.status_code == 200
        assert r.json() == {"identity": "ci"}
        assert "dubis_session" in r.cookies

        # No Authorization header this time -- cookie alone must carry it.
        r2 = c.get("/v1/parts")
    assert r2.status_code == 200


def test_cookie_session_route_not_registered_in_off_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/auth/session")
    assert r.status_code == 404


def test_forged_cookie_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        c.cookies.set("dubis_session", "local.deadbeef")
        r = c.get("/v1/parts")
    assert r.status_code == 401


def test_session_route_401_for_remote_peer_with_no_credentials(tmp_path, monkeypatch):
    """The session-bootstrap route is itself behind the middleware -- a
    remote caller with nothing to exchange (no bearer, no loopback, no
    tailnet header) must be turned away with the same 401 as any other
    route, not silently issue a session."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/auth/session")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "Authentication required"
    assert body["code"] == "unauthorized"


# ── pnp/legacy consume identity stamping ─────────────────────────────────────


def test_pnp_consume_source_stamped_with_identity_for_remote_bearer_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    headers = {"Authorization": "Bearer abc123"}
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/pnp/consume", json={"part_id": "C100000", "qty": 1}, headers=headers)
        assert r.status_code == 200
        history = c.get("/v1/parts/C100000/history", headers=headers).json()
    assert history[-1]["source"] == "openpnp@ci"


def test_legacy_consume_source_stamped_with_identity_for_remote_bearer_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "openpnp:xyz789")
    api = _api(tmp_path)
    headers = {"Authorization": "Bearer xyz789"}
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/api/consume", json={"part_id": "C100000", "qty": 1}, headers=headers)
        assert r.status_code == 200
        history = c.get("/v1/parts/C100000/history", headers=headers).json()
    assert history[-1]["source"] == "openpnp@openpnp"


def test_pnp_consume_source_unchanged_for_loopback_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=LOOPBACK) as c:
        r = c.post("/v1/pnp/consume", json={"part_id": "C100000", "qty": 1})
        assert r.status_code == 200
        history = c.get("/v1/parts/C100000/history").json()
    assert history[-1]["source"] == "openpnp"


def test_pnp_consume_source_unchanged_in_off_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/pnp/consume", json={"part_id": "C100000", "qty": 1})
        assert r.status_code == 200
        history = c.get("/v1/parts/C100000/history").json()
    assert history[-1]["source"] == "openpnp"
