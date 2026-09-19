"""Where a `/v1` request is served from: locally, proxied, or merged — and the
property that makes multiple windows on multiple servers safe, namely that the
answer is a function of the request and of nothing else.

Covers `server/dispatch.py` (the routing decision), `server/proxy.py` (header and
token forwarding, and the paths that must never leave this hub) and
`server/fanout.py` (partial failure degrades the merged view rather than failing
it).

`domain/federation.py` is a separate, pure module owned elsewhere; the merged
tests inject a stub module under its exact import path so what is pinned here is
the *contract* — the symbol names `merge_inventories`/`SourceInfo` and the
`[(SourceInfo(id, name), records), ...]` argument — not the merge arithmetic,
which is unit-tested on its own.
"""

from __future__ import annotations

import queue
import sys
import types
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient

# Bound to the REAL module at import time, before the `federation` fixture below
# shadows `domain.federation` in sys.modules with its stub.
from domain.federation import UnkeyablePartError
from dubis_errors import SourceConfigError
from server import fanout, proxy
from server import sources as sources_mod
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

BENCH = "http://bench.local:7891"
SHOP = "https://dubis-server.example.ts.net"


class Upstream:
    def __init__(self) -> None:
        self.up: dict[str, bool] = {}
        self.requests: list[httpx.Request] = []
        self.parts: dict[str, list[dict]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        if not self.up.get(origin, True):
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/parts":
            return httpx.Response(200, json={"inventory": self.parts.get(origin, [])})
        return httpx.Response(
            200,
            json={"from": origin, "method": request.method,
                  "body": request.content.decode() or None},
            # An upstream that is more permissive than us must not widen this
            # hub's exposure through the proxy.
            headers={
                "access-control-allow-origin": "*",
                "set-cookie": "upstream_session=abc; Path=/",
                "x-upstream": "yes",
            },
        )

    def last_for(self, path: str) -> httpx.Request:
        matches = [r for r in self.requests if r.url.path == path]
        assert matches, f"no upstream request for {path}; saw {[r.url.path for r in self.requests]}"
        return matches[-1]

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def hub(tmp_path, upstream):
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    app = create_app(api)
    app.state.source_clients = sources_mod.SourceClients(transport=upstream.transport())
    with TestClient(app) as client:
        client.api = api
        yield client
    api.shutdown()


@pytest.fixture
def hub_on_shop(hub):
    """A hub whose persisted DEFAULT is `shop` — i.e. what a client that sends
    no `X-Dubis-Source` gets. Requests below send no header unless they say so."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", name="Shop", token="shop-t0ken")
    sources_mod.set_default(hub.api, "shop")
    return hub


# ── served from a remote source ──────────────────────────────────────────────


def test_a_headerless_read_is_served_from_the_persisted_default(hub_on_shop):
    body = hub_on_shop.get("/v1/parts").json()
    assert body == {"inventory": []}, "the remote's inventory, not the hub's own"


def test_the_header_alone_decides_when_it_is_present(hub, upstream):
    """No default is set here at all — the header is the whole decision."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    assert hub.get("/v1/sources").json()["default"] == "local"

    body = hub.get("/v1/parts", headers={"X-Dubis-Source": "shop"}).json()
    assert body == {"inventory": []}


def test_two_clients_are_served_from_two_servers_in_the_same_breath(hub, upstream):
    """The requirement the whole design exists for: two windows, two servers, at
    the same time, neither able to move the other."""
    upstream.parts[SHOP] = [{"lcsc": "C-SHOP"}]
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")

    window_a = hub.get("/v1/parts", headers={"X-Dubis-Source": "shop"}).json()
    window_b = hub.get("/v1/parts", headers={"X-Dubis-Source": "local"}).json()

    assert [r["lcsc"] for r in window_a["inventory"]] == ["C-SHOP"]
    assert [r["lcsc"] for r in window_b["inventory"]] == ["C100000"]

    # And again, interleaved, to show neither request left anything behind.
    assert hub.get("/v1/parts", headers={"X-Dubis-Source": "shop"}).json() == window_a
    assert hub.get("/v1/parts", headers={"X-Dubis-Source": "local"}).json() == window_b


def test_resolve_target_is_a_pure_function_of_the_request(hub):
    """Same registry + same request -> same answer, with nothing in between.

    Also pins the header's four shapes: one id, a set, the `merged` keyword, and
    absent (which falls back to the persisted default)."""
    from starlette.datastructures import Headers

    from server.dispatch import resolve_target

    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "url": SHOP}, {"id": "bench", "url": BENCH}],
    })

    def target(source, path="/v1/parts", method="GET"):
        class _Req:
            headers = Headers({"x-dubis-source": source} if source else {})
            url = type("U", (), {"path": path})()
        _Req.method = method
        return resolve_target(registry, _Req())

    assert target("shop").id == "shop"
    assert target("local").id == "local"
    assert target(None).id == "local", "no header -> the persisted default"
    assert [s.id for s in target("merged").included] == ["local", "shop", "bench"]
    assert [s.id for s in target("bench,shop").included] == ["shop", "bench"], (
        "roster order, not tab order"
    )
    assert [s.id for s in target(" bench , shop ").included] == ["shop", "bench"]


def test_mutations_are_forwarded_verbatim(hub_on_shop, upstream):
    r = hub_on_shop.post("/v1/parts/C100000/adjust",
                         json={"adj_type": "add", "quantity": 5, "source": "cli"})
    assert r.status_code == 200
    forwarded = upstream.last_for("/v1/parts/C100000/adjust")
    assert forwarded.method == "POST"
    assert b'"quantity"' in forwarded.content


def test_the_query_string_survives_the_hop(hub_on_shop, upstream):
    hub_on_shop.get("/v1/carts/1/export?distributor=lcsc&format=csv")
    assert upstream.last_for("/v1/carts/1/export").url.query == b"distributor=lcsc&format=csv"


def test_the_sources_bearer_token_is_attached(hub_on_shop, upstream):
    hub_on_shop.get("/v1/parts")
    assert upstream.last_for("/v1/parts").headers["authorization"] == "Bearer shop-t0ken"


def test_a_source_with_no_token_sends_no_authorization(hub, upstream):
    """The deployed server on the tailnet answers with no token at all — identity
    comes from the tailscale operator proxy, so a token is per-source optional."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "shop")
    hub.get("/v1/parts")
    assert "authorization" not in upstream.last_for("/v1/parts").headers


def test_the_callers_own_credentials_never_leak_upstream(hub_on_shop, upstream):
    hub_on_shop.get("/v1/parts", headers={"Authorization": "Bearer hub-token",
                                          "Cookie": "dubis_session=mine"})
    forwarded = upstream.last_for("/v1/parts")
    assert forwarded.headers["authorization"] == "Bearer shop-t0ken"
    assert "cookie" not in forwarded.headers


def test_upstream_cors_and_cookies_are_stripped_from_the_proxied_response(hub_on_shop):
    r = hub_on_shop.get("/v1/warnings")
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers, "the proxy must add no CORS surface"
    assert "set-cookie" not in r.headers
    assert r.headers["x-upstream"] == "yes", "ordinary upstream headers still pass through"


def test_an_unreachable_source_is_a_loud_502(hub_on_shop, upstream):
    upstream.up[SHOP] = False
    r = hub_on_shop.get("/v1/parts")
    assert r.status_code == 502
    assert r.json()["code"] == "source_unavailable"
    assert "shop" in r.json()["error"]


# ── the paths that never leave this hub ──────────────────────────────────────


LOCAL_ONLY = ["/v1/health", "/v1/sources", "/v1/preferences"]


@pytest.mark.parametrize("path", LOCAL_ONLY)
def test_local_only_paths_are_answered_here_even_when_a_remote_is_selected(hub_on_shop, upstream, path):
    r = hub_on_shop.get(path)
    assert r.status_code == 200
    assert path not in upstream.paths(), f"{path} must never be proxied"


def test_health_still_answers_about_this_hub(hub_on_shop):
    r = hub_on_shop.get("/v1/health")
    assert r.json() == {"ok": True}
    # The one route that is cross-origin readable, unchanged by the hub.
    assert r.headers["access-control-allow-origin"] == "*"


def test_preferences_stay_local_so_the_roster_cannot_move_with_the_source(hub_on_shop):
    """The wart this design removes: today preferences are read from whichever
    server is active (js/store.js:311), so the server roster itself moves when
    you switch, stranding you on the remote."""
    prefs = hub_on_shop.get("/v1/preferences").json()
    assert prefs["active_source"] == "shop"  # the persisted default key
    assert prefs["servers"][0]["id"] == "shop"


@pytest.mark.parametrize("path", [
    "/v1/health", "/v1/sources", "/v1/sources/active", "/v1/sources/shop",
    "/v1/preferences", "/v1/import/parse", "/v1/events", "/v1/auth/session",
])
def test_the_local_only_allowlist(path):
    assert proxy.is_local_only(path) is True


@pytest.mark.parametrize("path", ["/v1/parts", "/v1/carts", "/v1/parts/C1/history", "/v1/warnings"])
def test_data_paths_are_proxyable(path):
    assert proxy.is_local_only(path) is False


def test_forward_refuses_a_local_only_path_outright():
    """Belt and braces: the dispatcher filters these out before ever calling
    forward, so this pins the second half of the guard, where the rule lives."""
    import anyio
    from starlette.requests import Request as StarletteRequest

    source = sources_mod.Source(id="shop", name="Shop", url=SHOP)
    clients = sources_mod.SourceClients(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    scope = {"type": "http", "method": "GET", "path": "/v1/preferences", "query_string": b"",
             "headers": [], "scheme": "https", "server": ("hub", 80)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run():
        with pytest.raises(SourceConfigError, match="never proxied"):
            await proxy.forward(StarletteRequest(scope, receive), source, clients)

    anyio.run(run)


# ── X-Dubis-Source ───────────────────────────────────────────────────────────


def test_the_header_pins_a_request_to_a_named_source(hub, upstream):
    """How a write routes in merged mode: a merged row knows its owner."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "merged")

    r = hub.post("/v1/parts/C100000/adjust", json={"adj_type": "add", "quantity": 1},
                 headers={"X-Dubis-Source": "shop"})
    assert r.status_code == 200
    assert r.json()["from"] == SHOP


def test_the_header_can_pin_back_to_the_hub_while_a_remote_is_the_default(hub_on_shop, upstream):
    body = hub_on_shop.get("/v1/parts", headers={"X-Dubis-Source": "local"}).json()
    assert body["inventory"][0]["lcsc"] == "C100000"
    assert "/v1/parts" not in upstream.paths()


def test_an_unknown_header_target_is_404(hub):
    r = hub.get("/v1/parts", headers={"X-Dubis-Source": "ghost"})
    assert r.status_code == 404
    assert r.json()["code"] == "source_not_found"


def test_merged_is_a_valid_header_for_the_one_path_it_means_something_on(hub, upstream, federation):
    upstream.parts[SHOP] = [{"lcsc": "C1"}]
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", name="Shop")
    body = hub.get("/v1/parts", headers={"X-Dubis-Source": "merged"}).json()
    assert {r["source_id"] for r in body["inventory"]} == {"local", "shop"}


@pytest.mark.parametrize(("verb", "path"), [
    ("get", "/v1/carts"),
    ("post", "/v1/parts/C100000/adjust"),
])
def test_merged_is_rejected_where_it_has_no_answer(hub, verb, path):
    """A caller that explicitly asked for `merged` on a write is asking for
    something with no answer; landing it on one arbitrary server silently is the
    wrong-answer-that-looks-right this design exists to remove."""
    r = getattr(hub, verb)(path, headers={"X-Dubis-Source": "merged"},
                           **({"json": {}} if verb == "post" else {}))
    assert r.status_code == 400
    assert r.json()["code"] == "source_config"


def test_a_merged_DEFAULT_serves_everything_else_locally_instead_of_failing(hub, upstream):
    """The asymmetry with the test above is deliberate: a window sitting on the
    All tab still has to be able to read its carts."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "merged")

    assert hub.get("/v1/carts").status_code == 200
    assert "/v1/carts" not in upstream.paths()


def test_the_callers_source_ids_are_replaced_by_the_hubs_own_pin(hub_on_shop, upstream):
    """Two halves of one rule, and they only look contradictory.

    The caller's `X-Dubis-Source` must not be forwarded: its ids name entries in
    THIS hub's roster and mean nothing upstream. But the hub must still send its
    own `local`, because every desktop dubIS is itself a hub with its own saved
    default — without the pin, asking `shop` for its inventory can return
    whatever server shop's window was last switched to, labelled shop in our tab,
    our badge and our breakdown, and counted twice in a merged total that is then
    invisibly too high.
    """
    hub_on_shop.get("/v1/parts", headers={"X-Dubis-Source": "shop"})
    forwarded = upstream.last_for("/v1/parts").headers["x-dubis-source"]
    assert forwarded == "local"
    assert forwarded != "shop", "the caller's roster ids must not travel upstream"


def test_a_fanned_out_read_pins_each_peer_too(hub, upstream, federation):
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "merged")
    hub.get("/v1/parts")
    assert upstream.last_for("/v1/parts").headers["x-dubis-source"] == "local"


# ── the StaticFiles mount and the /api aliases are untouched ─────────────────


def test_non_v1_paths_are_never_dispatched(hub_on_shop, upstream):
    assert hub_on_shop.get("/api/health").status_code == 200
    assert "/api/health" not in upstream.paths()


def test_static_assets_still_serve_while_a_remote_is_the_default(tmp_path, upstream):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>hub</h1>", encoding="utf-8")
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    sources_mod.set_default(api, "shop")

    app = create_app(api, static_dir=str(static))
    app.state.source_clients = sources_mod.SourceClients(transport=upstream.transport())
    try:
        with TestClient(app) as client:
            assert client.get("/index.html").text == "<h1>hub</h1>"
    finally:
        api.shutdown()
    assert "/index.html" not in upstream.paths()


# ── fan-out ──────────────────────────────────────────────────────────────────


def test_fanout_tolerates_a_source_being_down(upstream):
    import anyio

    upstream.up[BENCH] = False
    upstream.parts[SHOP] = [{"lcsc": "C1"}]
    clients = sources_mod.SourceClients(transport=upstream.transport())
    sources = [
        sources_mod.LOCAL_SOURCE,
        sources_mod.Source(id="bench", name="Bench", url=BENCH),
        sources_mod.Source(id="shop", name="Shop", url=SHOP),
    ]

    results = anyio.run(lambda: fanout.fetch_path(
        sources, "/v1/parts", clients=clients,
        local_fetch=lambda: {"inventory": [{"lcsc": "C0"}]},
    ))

    assert [r.source.id for r in results] == ["local", "bench", "shop"], "input order is kept"
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error.startswith("ConnectError")
    assert results[2].data == {"inventory": [{"lcsc": "C1"}]}
    assert results[1].as_status() == {"id": "bench", "name": "Bench", "ok": False,
                                      "error": results[1].error, "status": None}


def test_fanout_records_an_http_error_rather_than_raising(upstream):
    import anyio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope")

    clients = sources_mod.SourceClients(transport=httpx.MockTransport(handler))
    results = anyio.run(lambda: fanout.fetch_path(
        [sources_mod.Source(id="shop", name="Shop", url=SHOP)], "/v1/parts", clients=clients,
    ))
    assert results[0].ok is False
    assert results[0].status == 503


def test_the_local_source_is_served_in_process_not_over_http(upstream):
    import anyio

    clients = sources_mod.SourceClients(transport=upstream.transport())
    results = anyio.run(lambda: fanout.fetch_path(
        [sources_mod.LOCAL_SOURCE], "/v1/parts", clients=clients,
        local_fetch=lambda: {"inventory": [{"lcsc": "C0"}]},
    ))
    assert results[0].data == {"inventory": [{"lcsc": "C0"}]}
    assert upstream.requests == [], "the hub must never loop back through its own socket"


# ── merged GET /v1/parts ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class _SourceInfo:
    id: str
    name: str


@pytest.fixture
def federation(monkeypatch):
    """Stub `domain.federation` under its real import path.

    Pins the contract server/dispatch.py codes against — the module path, the
    two symbol names, and the `[(SourceInfo(id, name), records), ...]` shape —
    without duplicating (or waiting on) the pure merge itself.
    """
    calls: list = []

    def merge_inventories(by_source):
        calls.append(by_source)
        merged = []
        for info, records in by_source:
            assert isinstance(info, _SourceInfo), "merge is handed SourceInfo, not a Source"
            for record in records:
                merged.append({**record, "source_id": info.id, "source_name": info.name})
        return merged

    module = types.ModuleType("domain.federation")
    module.SourceInfo = _SourceInfo
    module.merge_inventories = merge_inventories
    monkeypatch.setitem(sys.modules, "domain.federation", module)
    import domain
    monkeypatch.setattr(domain, "federation", module, raising=False)
    module.calls = calls
    return module


def test_merged_parts_unions_every_enabled_source(hub, upstream, federation):
    upstream.parts[SHOP] = [{"lcsc": "C1"}]
    upstream.parts[BENCH] = [{"lcsc": "C2"}]
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", name="Shop")
    sources_mod.add_source(hub.api, url=BENCH, source_id="bench", name="Bench")
    sources_mod.set_default(hub.api, "merged")

    body = hub.get("/v1/parts").json()

    assert [(i.id, i.name) for i, _ in federation.calls[0]] == [
        ("local", "Local"), ("shop", "Shop"), ("bench", "Bench"),
    ]
    assert {r["source_id"] for r in body["inventory"]} == {"local", "shop", "bench"}
    assert {r["lcsc"] for r in body["inventory"]} == {"C100000", "C1", "C2"}
    assert [s["ok"] for s in body["sources"]] == [True, True, True]


def test_a_down_source_degrades_the_merged_view_rather_than_failing_it(hub, upstream, federation):
    upstream.up[BENCH] = False
    upstream.parts[SHOP] = [{"lcsc": "C1"}]
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", name="Shop")
    sources_mod.add_source(hub.api, url=BENCH, source_id="bench", name="Bench")
    sources_mod.set_default(hub.api, "merged")

    r = hub.get("/v1/parts")
    assert r.status_code == 200, "one asleep machine must not take the whole view down"
    body = r.json()
    assert {rec["source_id"] for rec in body["inventory"]} == {"local", "shop"}
    status = {s["id"]: s for s in body["sources"]}
    assert status["bench"]["ok"] is False
    assert status["bench"]["error"], "the UI has to be able to mark the view partial"


def test_a_disabled_source_is_left_out_of_the_merge_but_still_named_in_it(
    hub, upstream, federation,
):
    """Exclusion and failure are different reasons for the same missing stock,
    and both have to be sayable. A merged total short by a whole machine is a
    smaller perfectly plausible number — if the omission is not in `sources[]`
    there is nothing for the partial-view banner to render."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", enabled=False)
    sources_mod.set_default(hub.api, "merged")

    body = hub.get("/v1/parts").json()
    assert "/v1/parts" not in upstream.paths(), "it really was not fetched"
    shop = next(s for s in body["sources"] if s["id"] == "shop")
    assert shop["ok"] is False
    assert shop["error"] == "disabled"
    assert shop["excluded"] is True


def test_merged_mode_serves_everything_else_locally(hub, upstream, federation):
    """A merged view is a view of *parts*: there is no sensible union of two
    servers' carts, and nothing may silently pick one of them."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "merged")

    assert hub.get("/v1/carts").status_code == 200
    assert "/v1/carts" not in upstream.paths()


def test_a_merge_refusal_keeps_the_error_contract(hub, upstream, federation, monkeypatch):
    """A middleware sits outside Starlette's ExceptionMiddleware, so an error
    raised here would otherwise come back as an opaque 500 with no `code`."""

    def boom(_by_source):
        raise UnkeyablePartError("source 'shop' returned a record with no part number")

    monkeypatch.setattr(federation, "merge_inventories", boom)
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    sources_mod.set_default(hub.api, "merged")

    r = hub.get("/v1/parts")
    assert r.status_code == 502
    assert r.json()["code"] == "unkeyable_part"
    assert set(r.json()) == {"error", "code", "detail"}


# ── SSE is tagged with the source, not with a server-global idea of "current" ──


def test_a_local_mutation_is_tagged_local(hub):
    from server import events

    q = events.subscribe()
    try:
        assert hub.post("/v1/parts/C100000/adjust",
                        json={"adj_type": "add", "quantity": 1}).status_code == 200
        _name, data = q.get(timeout=1)
    finally:
        events.unsubscribe(q)
    assert data["source"] == "local"


def test_a_proxied_mutation_is_tagged_with_its_source(hub_on_shop):
    """One stream serves every window, so it has to say *whose* data moved.
    Without the tag a window on the bench re-fetches whenever the shop is
    written to — and with several windows open that is most of the time."""
    from server import events

    q = events.subscribe()
    try:
        assert hub_on_shop.post("/v1/parts/C1/adjust",
                                json={"adj_type": "add", "quantity": 1}).status_code == 200
        name, data = q.get(timeout=1)
    finally:
        events.unsubscribe(q)
    assert name == "inventory.updated"
    assert data["source"] == "shop"


def test_a_proxied_read_announces_nothing(hub_on_shop):
    from server import events

    q = events.subscribe()
    try:
        assert hub_on_shop.get("/v1/parts").status_code == 200
        with pytest.raises(queue.Empty):
            q.get(timeout=0.2)
    finally:
        events.unsubscribe(q)


def test_a_proxied_write_that_failed_upstream_announces_nothing(hub, upstream):
    from server import events

    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    upstream.up[SHOP] = False
    q = events.subscribe()
    try:
        r = hub.post("/v1/parts/C1/adjust", json={"adj_type": "add", "quantity": 1},
                     headers={"X-Dubis-Source": "shop"})
        assert r.status_code == 502
        with pytest.raises(queue.Empty):
            q.get(timeout=0.2)
    finally:
        events.unsubscribe(q)


# ── tab groups: X-Dubis-Source as an arbitrary subset ────────────────────────


@pytest.fixture
def five_sources(hub, upstream):
    """Five configured sources, so "exactly these two" is a real claim."""
    for i, url in enumerate([SHOP, BENCH, "http://c.local", "http://d.local", "http://e.local"]):
        upstream.parts[url] = [{"lcsc": f"C-{i}"}]
        sources_mod.add_source(hub.api, url=url, source_id=f"s{i}", name=f"S{i}")
    return hub


def test_a_group_merges_exactly_the_named_sources(five_sources, upstream, federation):
    """Ctrl-clicking two tabs must give the union of those two, not of all five."""
    body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,s3"}).json()

    assert [s["id"] for s in body["sources"]] == ["s0", "s3"]
    assert {r["lcsc"] for r in body["inventory"]} == {"C-0", "C-3"}
    assert "C100000" not in {r["lcsc"] for r in body["inventory"]}, "local was not in the group"
    assert sorted(upstream.paths()) == ["/v1/parts", "/v1/parts"], "only two sources were fetched"


def test_a_group_may_include_local(five_sources, federation):
    body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "local,s1"}).json()
    assert [s["id"] for s in body["sources"]] == ["local", "s1"]
    assert {r["lcsc"] for r in body["inventory"]} == {"C100000", "C-1"}


def test_merged_still_means_every_enabled_source(five_sources, federation):
    """The keyword existing clients and the persisted default use."""
    body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "merged"}).json()
    assert [s["id"] for s in body["sources"]] == ["local", "s0", "s1", "s2", "s3", "s4"]


def test_disabled_means_the_same_thing_to_a_group_as_to_the_all_tab(five_sources, federation):
    """One rule, because two meanings produced an absurdity: `merged` used to
    exclude a disabled source while an enumerated group included it, so the
    group {s0, s1} showed MORE stock than the All tab that contains it — a
    strict superset showing less, with nothing on screen naming the omission.

    Now `enabled` governs every merged VIEW, and a view always names what it
    left out. Direct addressing is untouched: a disabled source named as the
    sole selector is still served."""
    sources_mod.update_source(five_sources.api, "s1", enabled=False)

    def rows(selector):
        body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": selector}).json()
        return {s["id"]: s for s in body["sources"]}

    for selector in ("merged", "s0,s1"):
        assert rows(selector)["s1"]["ok"] is False
        assert rows(selector)["s1"]["error"] == "disabled"
    assert rows("merged")["s0"]["ok"] is True

    # ...and it is still directly addressable.
    alone = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s1"})
    assert alone.status_code == 200
    assert alone.json() == {"inventory": [{"lcsc": "C-1"}]}


def test_the_all_tab_is_never_beaten_by_one_of_its_own_subsets(five_sources, federation):
    """The invariant the reconciliation above exists to hold."""
    sources_mod.update_source(five_sources.api, "s1", enabled=False)

    def total(selector):
        body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": selector}).json()
        return len(body["inventory"])

    assert total("merged") >= total("s0,s1")


def test_a_group_is_merged_in_roster_order_not_the_order_named(five_sources, federation):
    """`domain/federation.py` resolves a scalar conflict by first non-empty in
    source order. Dragging a tab must not silently change which server's
    description wins."""
    forwards = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,s3"}).json()
    backwards = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s3,s0"}).json()
    assert [s["id"] for s in forwards["sources"]] == [s["id"] for s in backwards["sources"]]


def test_an_unknown_id_in_a_group_is_an_error_not_a_silent_drop(five_sources):
    """Dropping it would UNDER-REPORT stock in the merged totals — the failure
    that is hardest to notice and most expensive to believe."""
    r = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,ghost"})
    assert r.status_code == 404
    assert r.json()["code"] == "source_not_found"


def test_an_unreachable_member_still_only_degrades_the_group(five_sources, upstream, federation):
    """The other half of the rule above: an id that does not EXIST is a client
    error; an id that does not ANSWER is Tuesday."""
    upstream.up[SHOP] = False  # s0

    r = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,s3"})
    assert r.status_code == 200
    status = {s["id"]: s for s in r.json()["sources"]}
    assert status["s0"]["ok"] is False
    assert status["s3"]["ok"] is True
    assert {rec["lcsc"] for rec in r.json()["inventory"]} == {"C-3"}


@pytest.mark.parametrize(("verb", "path"), [
    ("get", "/v1/carts"),
    ("post", "/v1/parts/C100000/adjust"),
])
def test_a_group_is_rejected_where_it_has_no_answer(five_sources, verb, path):
    """There is no union of two servers' carts, and a write must land on exactly
    one server."""
    r = getattr(five_sources, verb)(path, headers={"X-Dubis-Source": "s0,s3"},
                                    **({"json": {}} if verb == "post" else {}))
    assert r.status_code == 400
    assert r.json()["code"] == "source_config"


def test_merged_cannot_be_combined_with_named_sources(five_sources):
    r = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,merged"})
    assert r.status_code == 400
    assert "cannot be combined" in r.json()["error"]


def test_a_source_named_twice_is_rejected(five_sources):
    """federation counts one entry per contributing source; a repeat would
    double its stock in the breakdown."""
    r = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0,s0"})
    assert r.status_code == 400
    assert "twice" in r.json()["error"]


def test_a_one_element_group_is_just_that_source(five_sources, upstream):
    """No merge, no envelope change — `s0` alone still means "serve from s0"."""
    body = five_sources.get("/v1/parts", headers={"X-Dubis-Source": "s0"}).json()
    assert body == {"inventory": [{"lcsc": "C-0"}]}
    assert "sources" not in body
