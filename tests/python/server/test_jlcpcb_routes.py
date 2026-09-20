"""JLCPCB credential-capture + library /v1 routes.

Three things this file is really guarding, all from the threat model in
`docs/plans/2026-09-20-extension-credential-capture.md`:

* **Write-only.** No route response — body or declared schema — may carry a
  cookie value. Rule 4.
* **Local-only.** The pairing and receive routes are in
  `proxy.LOCAL_ONLY_PATHS` (a hub never forwards a credential upstream) *and*
  call `auth.require_loopback` (a remote caller never reaches the handler).
  Rule 5.
* **A CORS surface of exactly one origin.** The intake routes answer a
  preflight for the pinned `chrome-extension://…` id and refuse every other
  origin with a bare 403 — the allowlist the extension's push needs, and
  nothing wider. Every response from those two paths carries the grant,
  including error ones (`BridgeCorsMiddleware`), so the extension can read the
  401 dubIS wrote for it to display; that is a decided trade, argued where it
  is asserted below. Read routes stay closed, `/v1/health` keeps its `*`, and
  the extension reaching a *remote* dubIS is still phase 2+. The enumerating
  version of this invariant lives in `test_health_cors.py`; the behavioural
  half is the "Rule 5's CORS exception" section at the bottom of this file.

The facade is monkeypatched at the `InventoryApi` method level — the same level
the handlers call — so nothing here touches the network, matching the style of
`test_distributors_routes.py`.
"""

from __future__ import annotations

import json
import types

import pytest
from fastapi.testclient import TestClient

import jlc_session
from server import proxy
from server.app import create_app
from server.routes.distributors import BRIDGE_EXTENSION_ID, BridgeCorsMiddleware

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


def test_every_copy_of_the_two_intake_paths_agrees():
    """The two path strings are written down four times; a rename that updates
    only some of them breaks the handshake SILENTLY.

    Where they live:
      1. the `@router.post` / `@router.options` decorators in
         `server/routes/distributors.py` — what the app actually serves;
      2. `distributors.INTAKE_PATHS` — what `BridgeCorsMiddleware` matches, so
         a stale copy means no allow-origin header on any response, which the
         extension reports as "Failed to fetch" with NO server-side trace at
         all. That exact failure mode already cost an hour once;
      3. `proxy.LOCAL_ONLY_PATHS` — a stale copy means the hub starts
         FORWARDING a live JLC cookie upstream (rule 5);
      4. `PAIRING_PATH` / `SESSION_PATH` here, which every other test in this
         file is written against.

    Deliberately an assertion over four copies rather than a derivation from
    one. The two candidate single sources are both worse: deriving
    `INTAKE_PATHS` from `LOCAL_ONLY_PATHS` would not notice (1) drifting, which
    is the copy a rename actually touches; and deriving it from the router's
    own table (every POST under `/v1/distributors/jlcpcb/`) would make a
    security allowlist grow BY DEFAULT — a third intake-shaped route would join
    the cross-origin surface without anyone deciding to. An allowlist that must
    be enumerated is the point; this test is what keeps the enumerations equal.
    """
    from collections import defaultdict

    from server.routes.distributors import INTAKE_PATHS, router

    # Read off the decorators themselves — the OPTIONS handlers are
    # `include_in_schema=False`, so the OpenAPI document cannot see them.
    served = defaultdict(set)
    for route in router.routes:
        served[route.path] |= set(getattr(route, "methods", ()) or ())

    hint = (
        "A rename must update every copy: the route decorators in "
        "server/routes/distributors.py, its INTAKE_PATHS, "
        "server/proxy.py's LOCAL_ONLY_PATHS, and the constants in this file. "
        "Miss the INTAKE_PATHS copy and the extension's POST loses its "
        "allow-origin header, which shows up as 'Failed to fetch' in the "
        "browser and nothing at all in the server log; miss the "
        "LOCAL_ONLY_PATHS copy and the hub forwards a live JLC cookie upstream."
    )

    assert INTAKE_PATHS == {PAIRING_PATH, SESSION_PATH}, hint
    assert INTAKE_PATHS == {
        p for p in proxy.LOCAL_ONLY_PATHS if p.startswith("/v1/distributors/")
    }, hint
    for path in sorted(INTAKE_PATHS):
        assert {"POST", "OPTIONS"} <= served[path], (
            f"{path} is matched by BridgeCorsMiddleware but the router serves "
            f"it with {sorted(served[path])}. {hint}"
        )


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
        # No Origin on the request, so no grant on the refusal: the stamp is
        # conditional on the pinned Origin even here.
        assert "access-control-allow-origin" not in r.headers
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
    # Mirrors tests/python/server/test_health_cors.py's spread guard. The
    # intake routes' scoped preflight is deliberately NOT inherited by these
    # two: they are reads, nothing pushes to them, and a JLC library is exactly
    # the sort of data the closed default exists for. The extension talking to
    # a REMOTE dubIS is still phase 2+ and needs its own deliberate exception,
    # not a header that spread from the route next door.
    r = client.get(path, headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("path", [PAIRING_PATH, SESSION_PATH])
def test_jlc_intake_routes_are_not_cross_origin_readable(client, path):
    r = client.post(path, json={"nonce": "x", "cookies": []}, headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_health_still_carries_the_only_wildcard_cors(client):
    assert client.get("/v1/health", headers={"Origin": ORIGIN}).headers[
        "access-control-allow-origin"] == "*"


# ── Rule 5's CORS exception: the pinned extension's preflight ────────────────
#
# Why any of this exists, recorded so the next reader does not re-derive it:
# the extension's `host_permissions` is `*://*.jlcpcb.com/*` and nothing else,
# so Chrome treats its POST to `http://127.0.0.1:<port>` as an ordinary
# cross-origin request; `Content-Type: application/json` makes it non-simple,
# so it preflights. `/v1` answered OPTIONS with 405 and no `Access-Control-*`
# headers, and the extension failed with "Failed to fetch" — verified live on
# 2026-09-20, with no trace at all on the server side.
#
# The fix is scoped to the ONE pinned extension id rather than widening the
# extension, because Chrome match patterns cannot name a port: the alternative,
# `http://127.0.0.1/*` in `host_permissions`, permanently grants fetch AND
# cookie-read for every service on loopback. See
# docs/plans/2026-09-20-extension-credential-capture.md, "Resolved: the scoped
# CORS preflight".

EXTENSION_ORIGIN = f"chrome-extension://{BRIDGE_EXTENSION_ID}"
OTHER_EXTENSION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INTAKE_PATHS = [PAIRING_PATH, SESSION_PATH]


@pytest.mark.parametrize("path", INTAKE_PATHS)
def test_preflight_from_the_pinned_extension_is_a_complete_204(client, path):
    """All five headers, or the browser blocks the POST it was asked about.

    Each one is load-bearing and none is a default:
      * allow-origin  — who may send it (echoing the pinned id, never `*`)
      * allow-methods — POST is what the extension sends
      * allow-headers — `content-type`, the header that made it preflight
      * max-age       — so the handshake is not two round trips every retry
      * allow-private-network — Chrome's Private Network Access check, run for
        ANY request into loopback and evaluated BEFORE the CORS result. Without
        it the fetch fails with the CORS answer sitting right there, correct
        and unread.
    `Vary: Origin` because the response body differs by Origin; a cache that
    missed it could serve the 403 to the extension or the 204 to a web page.
    """
    r = client.options(path, headers={
        "Origin": EXTENSION_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 204, r.text
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert r.headers["access-control-allow-methods"] == "POST, OPTIONS"
    assert r.headers["access-control-allow-headers"] == "content-type"
    assert r.headers["access-control-max-age"] == "600"
    assert r.headers["access-control-allow-private-network"] == "true"
    assert r.headers["vary"] == "Origin"


@pytest.mark.parametrize("path", INTAKE_PATHS)
@pytest.mark.parametrize(
    "origin",
    [
        OTHER_EXTENSION_ORIGIN,   # a different extension in the same browser
        "https://evil.example",   # any page the user happens to have open
        "http://127.0.0.1:7891",  # even another loopback service
        "null",                   # a sandboxed iframe / file:// document
    ],
)
def test_preflight_from_any_other_origin_is_403_with_no_cors_at_all(client, path, origin):
    """This is what makes it an allowlist rather than an opening.

    A *bare* 403: not one `Access-Control-*` header, so the browser blocks the
    request exactly as it did before this exception existed. Answering with
    headers and a non-2xx status, or echoing the requester's Origin, would make
    every origin's preflight succeed at the only thing preflights decide.
    """
    r = client.options(path, headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 403
    leaked = [h for h in r.headers if h.lower().startswith("access-control-")]
    assert not leaked, (
        f"{path} answered a preflight from {origin} with {leaked}; the scoped "
        "exception is for exactly one pinned extension id and must refuse "
        "every other origin bare-handed."
    )


def test_a_preflight_without_an_origin_is_refused(client):
    """No Origin is not the pinned Origin. A same-origin OPTIONS is not a
    thing any dubIS client does, so `None` falls to the refusing branch rather
    than being treated as trusted-by-absence."""
    r = client.options(PAIRING_PATH)
    assert r.status_code == 403
    assert not [h for h in r.headers if h.lower().startswith("access-control-")]


def test_the_real_post_carries_allow_origin_back_to_the_extension(client):
    """A passed preflight is not enough — the browser withholds the *body*
    from the extension unless the actual response repeats the grant.

    Without it the extension sees an opaque failure whether dubIS accepted the
    session or rejected it, which is the difference between "paired" and "try
    again" being visible at all.
    """
    r = client.post(PAIRING_PATH, headers={"Origin": EXTENSION_ORIGIN})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert r.headers["vary"] == "Origin"
    assert r.json()["nonce"]


def test_the_session_post_carries_allow_origin_back_to_the_extension(api, client, monkeypatch):
    monkeypatch.setattr(
        api, "receive_jlc_session",
        lambda *a, **k: {**_public(), "item_count": 191, "state": "valid"},
    )
    nonce = client.post(PAIRING_PATH, headers={"Origin": EXTENSION_ORIGIN}).json()["nonce"]
    r = client.post(
        SESSION_PATH,
        json={"nonce": nonce, "account": ACCOUNT, "cookies": [COOKIE]},
        headers={"Origin": EXTENSION_ORIGIN},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert r.json()["item_count"] == 191


@pytest.mark.parametrize("path", INTAKE_PATHS)
def test_a_post_from_another_origin_gets_no_allow_origin(client, path):
    # The header is echoed per-request, never set unconditionally, so a
    # response that reached some other origin's fetch still hands back nothing.
    r = client.post(path, json={"nonce": "x", "cookies": []},
                    headers={"Origin": OTHER_EXTENSION_ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_an_error_response_is_readable_by_the_pinned_extension(client):
    """Decided 2026-09-20: error bodies on these two paths ARE cross-origin
    readable, by exactly one extension.

    This was a known gap and is now closed. The old handler-level helper wrote
    onto FastAPI's injected `Response`, which is merged only into a value the
    handler *returns*; a raise — the common one being `DistributorAuthError`
    for an expired nonce — is rendered by `server/errors.py` into a fresh
    `JSONResponse` that never saw the header. The browser therefore withheld
    the body and the extension showed "Failed to fetch" where dubIS meant to
    say "your pairing code went stale". A nonce TTL is short and users are
    slow, so that is the error path most likely to be hit: the commonest error
    masqueraded as a connectivity failure.

    What the grant costs, recorded so it stays a decision: these bodies carry
    an error string, a `code` and an optional structured `detail` — no
    credential, no inventory data — and only the pinned origin ever receives
    the header. CORS restrains browsers, not clients, so it grants no access a
    local process did not already have. `require_loopback` and the single-use
    nonce remain the entire gate.

    `BridgeCorsMiddleware` (server/routes/distributors.py, registered outermost
    in server/app.py) is what makes it true for every response shape.
    """
    r = client.post(SESSION_PATH, json={"nonce": "stale", "cookies": [COOKIE]},
                    headers={"Origin": EXTENSION_ORIGIN})
    assert r.status_code == 401
    assert r.json()["code"] == "distributor_auth"
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert r.headers["vary"] == "Origin"


def test_an_error_response_is_not_readable_by_any_other_origin(client):
    """The grant above is an allowlist of one, on the error path too — the
    same request from another extension still hands back nothing."""
    r = client.post(SESSION_PATH, json={"nonce": "stale", "cookies": [COOKIE]},
                    headers={"Origin": OTHER_EXTENSION_ORIGIN})
    assert r.status_code == 401
    assert not [h for h in r.headers if h.lower().startswith("access-control-")]


def test_a_validation_error_is_readable_by_the_pinned_extension(client):
    """The other raise-path: a malformed body is rejected by Pydantic BEFORE
    the handler runs at all, so no header a handler could write would ever have
    reached it. A middleware outside the whole stack does.
    """
    r = client.post(SESSION_PATH, json={"cookies": [COOKIE]},
                    headers={"Origin": EXTENSION_ORIGIN})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN


def test_the_allow_origin_is_never_sent_twice(client):
    """The preflight handler sets the header and so does the middleware; two
    `Access-Control-Allow-Origin` values is a CORS failure, which is why the
    middleware assigns rather than appends."""
    r = client.options(SESSION_PATH, headers={"Origin": EXTENSION_ORIGIN})
    assert r.headers.get_list("access-control-allow-origin") == [EXTENSION_ORIGIN]
    assert r.headers.get_list("vary") == ["Origin"]


def test_the_bridge_cors_middleware_is_outermost(monkeypatch):
    """Ordering is the whole mechanism, so it is asserted rather than assumed.

    Starlette builds its stack so the LAST-added middleware is outermost, and
    `user_middleware[0]` is that one. Outermost is required: anything inside it
    can raise, and an error rendered by `server/errors.py` — or a refusal from
    `AuthMiddleware` itself — must still carry the header.
    """
    assert create_app(types.SimpleNamespace()).user_middleware[0].cls is (
        BridgeCorsMiddleware)

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:secret")
    stack = [m.cls.__name__ for m in create_app(types.SimpleNamespace()).user_middleware]
    assert stack[0] == "BridgeCorsMiddleware", (
        f"AuthMiddleware must sit INSIDE the CORS stamp, not outside it: {stack}")
    assert "AuthMiddleware" in stack


@pytest.mark.parametrize("path", INTAKE_PATHS)
def test_the_right_origin_does_not_let_a_remote_peer_in(tmp_path, monkeypatch, path):
    """CORS restrains browsers, not clients — it must not have become the gate.

    `require_loopback` is still the thing that decides who may push a
    credential (rule 5). Anything that speaks HTTP can set `Origin` to the
    pinned extension's; a curl from the tailnet does it in one flag. If this
    ever passes, the exception stopped being cosmetic and became access.

    The 403 now carries the allow-origin header when the Origin matches, and
    that is deliberate rather than the accident of ordering it used to be: the
    stamp is a middleware outside the whole stack, so it sees this response
    too. The body is a fixed constant naming the gate, and a browser that
    somehow provokes it is better off reading the real reason than an opaque
    failure. What must never change is the pair of assertions above it — the
    status, and the refusal.
    """
    from tests.python.helpers import make_api, make_part, write_ledger

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:secret")
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            r = c.post(path, json={"nonce": "x", "cookies": []}, headers={
                "Origin": EXTENSION_ORIGIN,
                "Authorization": "Bearer secret",
            })
        assert r.status_code == 403
        assert r.json()["code"] == "loopback_only"
        # Only the header changed; the gate did not. See the docstring.
        assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    finally:
        api.shutdown()


def test_an_auth_refusal_is_readable_by_the_pinned_extension(tmp_path, monkeypatch):
    """Outermost means outside `AuthMiddleware` too, so its 401 is stamped.

    One hop wider than the `403 loopback_only` next door, and signed off on the
    same reasoning: the body is the same class of fixed constant, only the
    pinned origin can read it, and CORS changes *readability*, not
    *reachability* — the request happened either way. Pinned here so the extra
    hop is a decision on record rather than something a later reader discovers.
    """
    from tests.python.helpers import make_api, make_part, write_ledger

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:secret")
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    try:
        with TestClient(create_app(api), client=REMOTE) as c:
            # No Authorization at all: refused by auth, not by require_loopback.
            r = c.post(SESSION_PATH, json={"nonce": "x", "cookies": []},
                       headers={"Origin": EXTENSION_ORIGIN})
        assert r.status_code == 401
        assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    finally:
        api.shutdown()


def test_the_preflight_itself_is_not_a_credential_oracle(client):
    """OPTIONS answers the same 204 whether or not anything is paired, and
    carries no body — so the handshake cannot be used to probe for sessions."""
    r = client.options(SESSION_PATH, headers={"Origin": EXTENSION_ORIGIN})
    assert r.status_code == 204
    assert r.content == b""


def test_only_the_two_intake_routes_answer_a_preflight(client):
    """The other JLC routes are reads, and reads stay closed."""
    for path in (SESSIONS_PATH, LIBRARY_PATH):
        r = client.options(path, headers={"Origin": EXTENSION_ORIGIN})
        assert not [h for h in r.headers if h.lower().startswith("access-control-")], (
            f"{path} answered a preflight; only the two credential-INTAKE "
            "routes may, because only they are what the extension POSTs to."
        )


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
