"""The whole of `/v1`'s cross-origin surface, enumerated — and nothing else.

Three clauses, and the file exists to make the third one true:

1. **`GET /v1/health` is readable by any origin** (`Access-Control-Allow-Origin:
   *`). The server picker in Preferences (js/server-list.js) shows a live
   reachability dot per configured server, probed from the browser with a
   cross-origin fetch of each candidate's `/v1/health`. Without the header the
   browser hides a perfectly good 200 behind a CORS error, indistinguishable
   from the server being down — every dot would read red.

2. **The two JLCPCB credential-intake routes answer a CORS *preflight* for
   exactly one pinned `chrome-extension://` origin**, and refuse every other
   origin with a bare 403. This is the deliberate exception the plan's "phase
   2+ needs its own tested exception" was holding the door for, cashed in on
   2026-09-20 for the *local* flow: the jlc-bridge extension holds no host
   permission for any dubIS origin (Chrome match patterns cannot name a port,
   so `http://127.0.0.1/*` would grant fetch + cookie-read for every loopback
   service — a far worse trade), so its `application/json` POST preflights, and
   a 405 with no headers made it fail as "Failed to fetch". Those same two
   paths — and only those two — also stamp the grant onto every *response*,
   errors included, via `BridgeCorsMiddleware`; that is a path-scoped
   middleware, not an app-wide one, which is why the sweeps below still find
   nothing anywhere else. The behavioural tests for it live next door in
   `test_jlcpcb_routes.py`; what lives HERE is the statement that those two
   paths are the only ones.

3. **Every other route carries no CORS header at all.** That is the clause with
   teeth. Every route but health serves real inventory data, and a `*` on any
   of them would let any web page the user visits read their parts, their
   purchase history and their vendor list. The two enumerating tests below
   sweep the whole route table rather than a sample, and `_cors_writers_in_the_server_package`
   catches a widening that no request happens to hit.

The plan the enumeration implements — what may be open, and what it cost to
open it — is docs/plans/2026-09-20-extension-credential-capture.md.
"""

from __future__ import annotations

import re
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.routes.distributors import BRIDGE_EXTENSION_ORIGIN
from tests.python.helpers import make_api, make_part, write_ledger

# A simulated non-loopback peer, i.e. a browser on another machine.
REMOTE = ("100.64.0.7", 51234)
ORIGIN = "https://fremont.example.ts.net"

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_PKG = REPO_ROOT / "server"

# ── THE PLAN ─────────────────────────────────────────────────────────────────
# The complete cross-origin surface of /v1. Adding a row is a security
# decision; the tests below quote this comment back at you when one appears.
#
#   GET  /v1/health                          -> Access-Control-Allow-Origin: *
#   OPTIONS /v1/distributors/jlcpcb/pairing  -> preflight, pinned extension only
#   OPTIONS /v1/distributors/jlcpcb/session  -> preflight, pinned extension only
#   ANY  those same two paths                -> allow-origin on EVERY response,
#                                               errors included, pinned
#                                               extension only
#   everything else                          -> no Access-Control-* header
WILDCARD_CORS_PATHS = {"/v1/health"}
PREFLIGHT_PATHS = {
    "/v1/distributors/jlcpcb/pairing",
    "/v1/distributors/jlcpcb/session",
}
# Where a CORS header may be written in server/. meta.py holds health's `*`;
# distributors.py holds the pinned-extension preflight AND the path-scoped
# `BridgeCorsMiddleware` that stamps the same grant onto that pair's real
# responses — deliberately the same module, so opening this surface still costs
# nothing but the two entries already here. proxy.py is listed because it
# STRIPS every `access-control-*` header an upstream sends, which is the same
# invariant defended from the hub side.
CORS_WRITER_MODULES = {"routes/meta.py", "routes/distributors.py", "proxy.py"}

_PLAN = (
    "The cross-origin surface of /v1 is exactly: `*` on GET /v1/health, a "
    "preflight for one pinned chrome-extension origin on "
    f"{sorted(PREFLIGHT_PATHS)}, and nothing anywhere else. See the module "
    "docstring here and docs/plans/2026-09-20-extension-credential-capture.md."
)


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


def _cors_headers(response) -> list[str]:
    return sorted(h.lower() for h in response.headers if h.lower().startswith("access-control-"))


def _all_paths() -> list[str]:
    """Every path the app serves, from its own OpenAPI document."""
    spec = create_app(types.SimpleNamespace()).openapi()
    return sorted(spec["paths"])


def _concrete(path: str) -> str:
    """`/v1/carts/{cart_id}/plan` -> `/v1/carts/x/plan`, so it routes."""
    return re.sub(r"\{[^}]+\}", "x", path)


# ── Clause 1: health ─────────────────────────────────────────────────────────


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


def test_health_carries_nothing_worth_protecting(client):
    # The justification for opening this one route: a constant payload. If this
    # ever grows a field, the `*` above needs re-deciding, not extending.
    assert client.get("/v1/health").json() == {"ok": True}


# ── Clause 2: the one pinned extension origin, on two paths ──────────────────


@pytest.mark.parametrize("path", sorted(PREFLIGHT_PATHS))
def test_the_intake_preflight_names_one_origin_and_never_a_wildcard(client, path):
    """The exception is an allowlist of one, not a second `*`.

    `*` here would be strictly worse than on health: these routes accept a live
    JLC session cookie, and the nonce that authorizes one is minted by a route
    any page could then call.
    """
    r = client.options(path, headers={"Origin": BRIDGE_EXTENSION_ORIGIN})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == BRIDGE_EXTENSION_ORIGIN
    assert r.headers["access-control-allow-origin"] != "*"
    # Per-origin answer, so it must not be cached origin-blind.
    assert r.headers["vary"] == "Origin"
    # `*` and credentials are exclusive; a named origin and credentials are not,
    # so this one has to be asserted rather than inferred. The extension sends
    # its cookies in the BODY, deliberately — nothing needs ambient credentials.
    assert "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize("path", sorted(PREFLIGHT_PATHS))
def test_the_intake_preflight_refuses_a_web_origin(client, path):
    r = client.options(path, headers={"Origin": ORIGIN})
    assert r.status_code == 403
    assert not _cors_headers(r), _PLAN


# ── Clause 3: the spreading guard, swept over every route ────────────────────


def _unsweepable(path: str) -> bool:
    """GET routes this file must not actually call.

    `/v1/events` is an SSE stream — a GET never returns. Everything under
    `/v1/distributors/` reaches a real distributor (or a 20s browser probe) on
    the way to its response. Both are covered instead by the OPTIONS sweep
    below, which never enters a handler, and by the static module guard.
    """
    return path == "/v1/events" or path.startswith("/v1/distributors/")


def test_the_unsweepable_set_is_exactly_what_it_claims():
    """Pins the exclusion above so it cannot quietly grow to hide a route.

    An exclusion list is the natural place for a widening to go unnoticed, so
    the list itself is an assertion: 5 distributor paths plus the event stream,
    named individually.
    """
    spec = create_app(types.SimpleNamespace()).openapi()
    excluded = {p for p, m in spec["paths"].items() if "get" in m and _unsweepable(p)}
    assert excluded == {
        "/v1/events",
        "/v1/distributors/digikey/session",
        "/v1/distributors/jlcpcb/library",
        "/v1/distributors/jlcpcb/sessions",
        "/v1/distributors/mouser/key",
        "/v1/distributors/{name}/product/{code}",
    }, (
        f"{_PLAN}\nThe set of GET routes this file cannot call live changed: "
        f"{sorted(excluded)}. A new one needs its own cross-origin test "
        "(see tests/python/server/test_jlcpcb_routes.py), not just an entry here."
    )


def test_no_route_but_health_carries_a_wildcard(client):
    """Sweep, not a sample: `*` may appear on exactly one path.

    Read off real responses, so it catches a header a handler sets rather than
    one a decorator declares. The handful of GETs that cannot be called live
    are named and justified in `_unsweepable`.
    """
    spec = create_app(types.SimpleNamespace()).openapi()
    offenders = {}
    for path, methods in spec["paths"].items():
        if path in WILDCARD_CORS_PATHS or "get" not in methods or _unsweepable(path):
            continue
        r = client.get(_concrete(path), headers={"Origin": ORIGIN})
        if r.headers.get("access-control-allow-origin") is not None:
            offenders[path] = r.headers["access-control-allow-origin"]
    assert not offenders, f"{_PLAN}\nThese GET routes answered with one: {offenders}"


def test_no_route_but_the_two_intake_paths_answers_a_preflight(client):
    """Sweep: OPTIONS from the pinned extension origin over the whole table.

    This is the test that catches the realistic regression — somebody adds
    `CORSMiddleware`, or copies the `@router.options` pair onto a third route
    "for symmetry", and every path starts answering the extension. Probing with
    the origin that IS allowed is the point: a guard that only ever sends a
    disallowed origin cannot see a surface that opened for the allowed one.
    """
    offenders = {}
    for path in _all_paths():
        if path in PREFLIGHT_PATHS:
            continue
        r = client.options(_concrete(path), headers={
            "Origin": BRIDGE_EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
        })
        headers = _cors_headers(r)
        if headers:
            offenders[path] = headers
    assert not offenders, f"{_PLAN}\nThese routes answered a preflight: {offenders}"


def test_an_unknown_route_answers_no_preflight(client):
    # The 404 branch goes through server/errors.py, which builds its own
    # response — worth pinning, since a middleware-shaped widening would show
    # up here first.
    r = client.options("/v1/no-such-route", headers={"Origin": BRIDGE_EXTENSION_ORIGIN})
    assert not _cors_headers(r), _PLAN


@pytest.mark.parametrize("path", ["/v1/meta", "/v1/parts", "/v1/preferences"])
def test_other_routes_are_not_cross_origin_readable(client, path):
    # Kept as named examples beside the sweep: these three are the ones whose
    # payload makes the rule worth having (inventory, preferences, server meta).
    r = client.get(path, headers={"Origin": ORIGIN})
    assert r.status_code == 200, f"{path} should exist for this test to mean anything"
    assert "access-control-allow-origin" not in r.headers


def test_only_the_planned_modules_write_a_cors_header():
    """Static half, for what no request in this file happens to reach.

    A sweep can only probe routes that exist and statuses it provokes; this
    reads the source instead, so a CORS header added to a WebSocket handler, an
    SSE stream, the static mount, or a branch behind a feature flag still trips
    a guard. Cheap, and the failure names the file.
    """
    offenders = {}
    for path in sorted(SERVER_PKG.rglob("*.py")):
        rel = path.relative_to(SERVER_PKG).as_posix()
        if rel in CORS_WRITER_MODULES or "__pycache__" in rel:
            continue
        hits = sorted({
            m.group(0)
            for m in re.finditer(r"[Aa]ccess-[Cc]ontrol-[A-Za-z-]+", path.read_text("utf-8"))
        })
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"{_PLAN}\nCORS headers appeared in module(s) outside "
        f"{sorted(CORS_WRITER_MODULES)}: {offenders}\n"
        "If the new one is deliberate, add it to CORS_WRITER_MODULES in the "
        "same change that argues for it — and to the PLAN comment above."
    )


def test_the_only_cors_middleware_is_scoped_to_the_two_intake_paths():
    """The pinned-extension grant is a middleware (it must see responses a
    handler never returned), so the "no app-wide CORS" rule needs a second
    clause: that middleware must decide on the path BEFORE anything else.

    Read off the constant it matches against, not a copy of it — a widening
    would have to change `INTAKE_PATHS`, and that is what this fails on.
    """
    from server.routes.distributors import INTAKE_PATHS as MIDDLEWARE_PATHS

    assert set(MIDDLEWARE_PATHS) == PREFLIGHT_PATHS, (
        f"{_PLAN}\nBridgeCorsMiddleware stamps paths outside the enumerated "
        f"surface: {sorted(set(MIDDLEWARE_PATHS) - PREFLIGHT_PATHS)}"
    )


def test_no_cors_middleware_is_installed():
    """`app.add_middleware(CORSMiddleware, ...)` is the one-line change that
    would open every route at once, and it would satisfy no test that only
    checks a handful of paths. Named here so the refusal is explicit."""
    app = create_app(types.SimpleNamespace())
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" not in names, (
        f"{_PLAN}\nCORSMiddleware applies to EVERY route; the two exceptions "
        "above are per-route on purpose."
    )
