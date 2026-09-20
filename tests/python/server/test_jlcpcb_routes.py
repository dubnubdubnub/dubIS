"""JLCPCB credential-capture + library /v1 routes.

Three things this file is really guarding, all from the threat model in
`docs/plans/2026-09-20-extension-credential-capture.md`:

* **Write-only.** No route response — body or declared schema — may carry a
  cookie value. Rule 4.
* **Local-only.** The pairing and receive routes are in
  `proxy.LOCAL_ONLY_PATHS` (a hub never forwards a credential upstream) *and*
  call `auth.require_loopback` (a remote caller never reaches the handler).
  Rule 5.
* **No new CORS surface.** `/v1/health` stays the only cross-origin-readable
  route; the extension reaching a *remote* dubIS is phase 2+ and needs its own
  deliberate exception. Re-asserted here against the new paths so adding them
  cannot have widened it.

The facade is monkeypatched at the `InventoryApi` method level — the same level
the handlers call — so nothing here touches the network, matching the style of
`test_distributors_routes.py`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import jlc_session
from server import proxy
from server.app import create_app

COOKIE_VALUE = "uuid-that-must-never-come-back"
COOKIE = {"name": "JLCPCB_SESSION_ID", "value": COOKIE_VALUE, "domain": ".jlcpcb.com"}
ACCOUNT = "12625901A"

PAIRING_PATH = "/v1/distributors/jlcpcb/pairing"
SESSION_PATH = "/v1/distributors/jlcpcb/session"
SESSIONS_PATH = "/v1/distributors/jlcpcb/sessions"
LIBRARY_PATH = "/v1/distributors/jlcpcb/library"

# A simulated non-loopback peer, i.e. a browser on another machine.
REMOTE = ("100.64.0.7", 51234)
ORIGIN = "https://dubis-server.example.ts.net"


@pytest.fixture(autouse=True)
def _clean_nonces():
    jlc_session._nonces.clear()
    yield
    jlc_session._nonces.clear()


def _public(account=ACCOUNT):
    return {"account": account, "label": "impossible_hardware",
            "added_at": "2026-09-20T02:04:59Z", "last_ok": "2026-09-20T02:05:03Z"}


# ── Pairing ──────────────────────────────────────────────────────────────────


def test_pairing_mints_a_nonce_and_ttl(client):
    r = client.post(PAIRING_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["nonce"]
    assert body["ttl"] == jlc_session.NONCE_TTL_SECONDS


def test_two_pairings_are_distinct(client):
    assert client.post(PAIRING_PATH).json()["nonce"] != client.post(PAIRING_PATH).json()["nonce"]


# ── Receive ──────────────────────────────────────────────────────────────────


def test_receive_round_trips_through_the_facade(api, client, monkeypatch):
    seen = {}

    def _receive(nonce, account="", cookies=None, label=""):
        seen.update(nonce=nonce, account=account, cookies=cookies, label=label)
        return {**_public(), "item_count": 191, "state": "valid"}

    monkeypatch.setattr(api, "receive_jlc_session", _receive)
    nonce = client.post(PAIRING_PATH).json()["nonce"]
    r = client.post(SESSION_PATH, json={"nonce": nonce, "account": ACCOUNT, "cookies": [COOKIE]})
    assert r.status_code == 200
    assert r.json()["item_count"] == 191
    assert seen["nonce"] == nonce
    assert seen["cookies"] == [COOKIE]


def test_receive_accepts_the_extensions_real_body(api, client, monkeypatch):
    """`chrome.cookies.getAll` returns booleans and a number alongside the
    value, and `account` is null when the validation response had no rows.
    A stricter body model would 422 the real extension."""
    monkeypatch.setattr(
        api, "receive_jlc_session",
        lambda *a, **k: {**_public(), "item_count": 0, "state": "valid"},
    )
    nonce = client.post(PAIRING_PATH).json()["nonce"]
    r = client.post(SESSION_PATH, json={
        "nonce": nonce,
        "account": None,
        "cookies": [{**COOKIE, "path": "/", "secure": True, "httpOnly": True,
                     "expirationDate": 1790000000.5}],
    })
    assert r.status_code == 200


def test_receive_without_a_nonce_is_422(client):
    assert client.post(SESSION_PATH, json={"cookies": []}).status_code == 422


def test_receive_with_a_bad_nonce_is_401(api, client):
    # DistributorAuthError -> 401 via server/errors.py, so the extension can
    # tell "your nonce went stale" from "dubIS is broken".
    r = client.post(SESSION_PATH, json={"nonce": "nope", "cookies": [COOKIE]})
    assert r.status_code == 401
    assert r.json()["code"] == "distributor_auth"


# ── Sessions list + revoke ───────────────────────────────────────────────────


def test_sessions_lists_accounts(api, client, monkeypatch):
    monkeypatch.setattr(api, "list_jlc_sessions", lambda: {
        "logged_in": True, "supported": True, "message": "1 JLC account(s) paired",
        "accounts": [_public()],
    })
    r = client.get(SESSIONS_PATH)
    assert r.status_code == 200
    assert r.json()["accounts"] == [_public()]


def test_sessions_on_a_fresh_install_is_a_truthful_200(client):
    # Real facade, no store: the frontend hits this on every startup, and a
    # raise here is the 500 that has already shipped twice (CLAUDE.md Traps).
    r = client.get(SESSIONS_PATH)
    assert r.status_code == 200
    assert r.json() == {
        "logged_in": False, "supported": True,
        "message": "No JLC account paired", "accounts": [],
    }


def test_revoke(api, client, monkeypatch):
    monkeypatch.setattr(api, "revoke_jlc_session", lambda account: {
        "account": account, "revoked": True, "accounts": [],
    })
    r = client.delete(f"{SESSIONS_PATH}/{ACCOUNT}")
    assert r.status_code == 200
    assert r.json() == {"account": ACCOUNT, "revoked": True, "accounts": []}


def test_revoking_an_unknown_account_is_404(client):
    r = client.delete(f"{SESSIONS_PATH}/NOSUCH")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


# ── Library ──────────────────────────────────────────────────────────────────


def _record(**over):
    from domain.schema import INVENTORY_FIELDS

    rec = {f.py_key: (list(f.default) if isinstance(f.default, list) else f.default)
           for f in INVENTORY_FIELDS if f.to_js}
    rec.update(over)
    return rec


def test_library_returns_records(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_jlc_library", lambda account="": {
        "account": ACCOUNT, "total": 1,
        "records": [_record(lcsc="C1525", qty=4820, section="Capacitors")],
    })
    r = client.get(LIBRARY_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["account"] == ACCOUNT
    assert body["records"][0]["lcsc"] == "C1525"
    assert body["records"][0]["unit_price"] == 0


def test_library_forwards_the_account_query(api, client, monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "fetch_jlc_library", lambda account="": (
        seen.update(account=account) or {"account": account, "total": 0, "records": []}
    ))
    client.get(LIBRARY_PATH, params={"account": "OTHER9Z"})
    assert seen["account"] == "OTHER9Z"


def test_library_without_a_paired_account_is_401(client):
    r = client.get(LIBRARY_PATH)
    assert r.status_code == 401
    assert r.json()["code"] == "distributor_auth"


# ── Rule 4: no route ever returns a credential ───────────────────────────────


def test_no_response_body_contains_a_cookie(api, client, monkeypatch):
    """Even when the facade leaks one, the declared response models filter it.

    This is the belt-and-braces half of `jlc_session.public_session`: FastAPI
    drops any field the response model does not declare, so a future facade
    change that starts returning `cookies` cannot ship a credential to a
    caller.
    """
    leaky = {**_public(), "item_count": 1, "state": "valid", "cookies": [COOKIE]}
    monkeypatch.setattr(api, "receive_jlc_session", lambda *a, **k: leaky)
    monkeypatch.setattr(api, "list_jlc_sessions", lambda: {
        "logged_in": True, "supported": True, "message": "",
        "accounts": [{**_public(), "cookies": [COOKIE]}],
    })
    monkeypatch.setattr(api, "revoke_jlc_session", lambda account: {
        "account": account, "revoked": True,
        "accounts": [{**_public(), "cookies": [COOKIE]}],
    })
    nonce = client.post(PAIRING_PATH).json()["nonce"]
    responses = [
        client.post(SESSION_PATH, json={"nonce": nonce, "cookies": [COOKIE]}),
        client.get(SESSIONS_PATH),
        client.delete(f"{SESSIONS_PATH}/{ACCOUNT}"),
    ]
    for r in responses:
        assert r.status_code == 200, r.text
        assert COOKIE_VALUE not in r.text
        assert "cookie" not in r.text.lower()


def test_no_response_schema_declares_a_credential_field():
    """No /v1 *response* schema may have a credential-shaped property.

    Walks every operation's response schemas through their `$ref`s rather than
    scanning the whole component list, so a REQUEST body legitimately carrying
    `cookies` (`JlcSessionBody`) does not trip it.
    """
    import types

    spec = create_app(types.SimpleNamespace()).openapi()
    components = spec.get("components", {}).get("schemas", {})
    forbidden = {"cookie", "cookies", "value", "token", "secret", "api_key", "key",
                 "password", "credential", "credentials"}

    def _walk(schema, seen):
        if not isinstance(schema, dict):
            return set()
        ref = schema.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                return set()
            seen.add(name)
            return _walk(components.get(name, {}), seen)
        found = set(schema.get("properties", {}))
        for sub in list(schema.get("properties", {}).values()) + schema.get("allOf", []) \
                + schema.get("anyOf", []) + schema.get("oneOf", []):
            found |= _walk(sub, seen)
        if "items" in schema:
            found |= _walk(schema["items"], seen)
        if isinstance(schema.get("additionalProperties"), dict):
            found |= _walk(schema["additionalProperties"], seen)
        return found

    offenders = {}
    for path, methods in spec["paths"].items():
        for verb, op in methods.items():
            if verb not in {"get", "post", "put", "patch", "delete"}:
                continue
            names = set()
            for response in op.get("responses", {}).values():
                for media in response.get("content", {}).values():
                    names |= _walk(media.get("schema", {}), set())
            hits = {n for n in names if n.lower() in forbidden}
            if hits:
                offenders[f"{verb.upper()} {path}"] = sorted(hits)
    assert not offenders, (
        "response schema(s) declare a credential-shaped property — no /v1 route "
        f"may hand a stored credential back: {offenders}"
    )


# ── Rule 5: local-only, two mechanisms ───────────────────────────────────────


@pytest.mark.parametrize("path", [PAIRING_PATH, SESSION_PATH])
def test_credential_intake_paths_are_never_proxied(path):
    # A hub that forwarded these would hand a user's live JLC cookie to a
    # different machine — and would let an upstream mint the nonce that
    # authorizes the push.
    assert path in proxy.LOCAL_ONLY_PATHS
    assert proxy.is_local_only(path)


def test_the_read_only_jlc_paths_are_not_local_only():
    # Deliberate asymmetry: listing accounts and reading a library are ordinary
    # data reads and may be served from whichever source is active.
    assert not proxy.is_local_only(SESSIONS_PATH)
    assert not proxy.is_local_only(LIBRARY_PATH)


@pytest.mark.parametrize("path", [PAIRING_PATH, SESSION_PATH])
def test_credential_intake_refuses_a_remote_caller(tmp_path, monkeypatch, path):
    """`require_loopback` — the other half of rule 5. The allowlist stops the
    hub forwarding; this stops a token-bearing remote caller reaching the
    handler at all."""
    from tests.python.helpers import make_api, make_part, write_ledger

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:secret")
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            r = c.post(path, json={"nonce": "x", "cookies": []},
                       headers={"Authorization": "Bearer secret"})
        assert r.status_code == 403
        assert r.json()["code"] == "loopback_only"
    finally:
        api.shutdown()


def test_credential_intake_is_gated_only_in_auth_on_mode(tmp_path, monkeypatch):
    """The other side of rule 5, pinned so it is known rather than discovered.

    `require_loopback` reads `request.state.identity`, which only exists when
    `AuthMiddleware` is installed — i.e. when `DUBIS_AUTH_MODE=on`
    (`server/auth.py:296-315`, `server/app.py`). With auth `off` (the default)
    there is no identity to reject, so the guard passes everything and a
    non-loopback peer CAN mint a pairing nonce and push a credential.

    That is the house's existing semantics, identical to `/v1/import/parse`'s:
    in `off` mode the network boundary is the guard, and the deployed server
    sets `on` (`docs/deploy-runbook.md`). It is asserted here because the
    phase-1 prose states the loopback gate unconditionally, and a reader who
    believes that would mis-judge an `off`-mode server bound to `0.0.0.0`.
    Tightening this later — refusing a non-loopback peer regardless of mode —
    is a deliberate change that should start by editing this test.
    """
    from tests.python.helpers import make_api, make_part, write_ledger

    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            r = c.post(PAIRING_PATH)
        assert r.status_code == 200, r.text
        assert r.json()["nonce"]
    finally:
        api.shutdown()


def test_the_hub_still_refuses_to_forward_intake_whatever_the_auth_mode():
    """`LOCAL_ONLY_PATHS` is mode-independent, which is why rule 5 needs both
    mechanisms: the allowlist holds even where `require_loopback` is a no-op."""
    assert proxy.is_local_only(PAIRING_PATH)
    assert proxy.is_local_only(SESSION_PATH)


# ── The CORS invariant still holds ───────────────────────────────────────────


@pytest.mark.parametrize("path", [SESSIONS_PATH, LIBRARY_PATH])
def test_jlc_routes_are_not_cross_origin_readable(client, path):
    # Mirrors tests/python/server/test_health_cors.py's spread guard: only
    # /v1/health carries Access-Control-Allow-Origin. The extension talking to
    # a REMOTE dubIS is phase 2+ and needs its own deliberate exception, not a
    # header that arrived by accident.
    r = client.get(path, headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("path", [PAIRING_PATH, SESSION_PATH])
def test_jlc_intake_routes_are_not_cross_origin_readable(client, path):
    r = client.post(path, json={"nonce": "x", "cookies": []}, headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_health_is_still_the_only_cors_route(client):
    assert client.get("/v1/health", headers={"Origin": ORIGIN}).headers[
        "access-control-allow-origin"] == "*"


# ── End to end against the real facade ───────────────────────────────────────


def test_pair_then_list_then_revoke(api, client, monkeypatch):
    """One pass through the real facade, with only JLC's own API stubbed."""
    monkeypatch.setattr(
        jlc_session, "validate",
        lambda cookies, **kw: {"state": jlc_session.VALID, "account": ACCOUNT,
                               "total": 191, "message": "ok"},
    )
    nonce = client.post(PAIRING_PATH).json()["nonce"]
    accepted = client.post(SESSION_PATH, json={
        "nonce": nonce, "account": None, "label": "impossible_hardware",
        "cookies": [COOKIE],
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["account"] == ACCOUNT
    assert COOKIE_VALUE not in accepted.text

    listed = client.get(SESSIONS_PATH).json()
    assert listed["logged_in"] is True
    assert [a["account"] for a in listed["accounts"]] == [ACCOUNT]
    assert COOKIE_VALUE not in json.dumps(listed)

    revoked = client.delete(f"{SESSIONS_PATH}/{ACCOUNT}").json()
    assert revoked == {"account": ACCOUNT, "revoked": True, "accounts": []}
    assert client.get(SESSIONS_PATH).json()["logged_in"] is False


def test_the_stored_file_is_0600(api, client, monkeypatch):
    import os
    import stat

    monkeypatch.setattr(
        jlc_session, "validate",
        lambda cookies, **kw: {"state": jlc_session.VALID, "account": ACCOUNT,
                               "total": 1, "message": "ok"},
    )
    nonce = client.post(PAIRING_PATH).json()["nonce"]
    client.post(SESSION_PATH, json={"nonce": nonce, "cookies": [COOKIE]})
    store = jlc_session.store_path(api.base_dir)
    assert os.path.exists(store)
    assert stat.S_IMODE(os.stat(store).st_mode) == 0o600
