"""`/v1/sources` over HTTP: roster CRUD, reachability, and the default.

`PUT /v1/sources/active` is the one whose name lies, so it gets the most
attention here: it saves a *preference* and changes nothing live. Where a
request is actually served from is per-request (`X-Dubis-Source`), and that is
pinned in test_source_dispatch.py.
"""

from __future__ import annotations

import queue

import httpx
import pytest
from fastapi.testclient import TestClient

from server import events
from server import sources as sources_mod
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

BENCH = "http://bench.local:7891"
SHOP = "https://dubis-server.example.ts.net"


class Upstream:
    """A stub dubIS server reachable only through `httpx.MockTransport`.

    `up` flips a source between answering and refusing connections, which is the
    state a merged view exists to tolerate.
    """

    def __init__(self) -> None:
        self.up: dict[str, bool] = {}
        self.requests: list[httpx.Request] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        if not self.up.get(origin, True):
            raise httpx.ConnectError("connection refused", request=request)
        canned = self.responses.get((origin, request.url.path))
        if canned is not None:
            return httpx.Response(canned.status_code, content=canned.content,
                                  headers=canned.headers)
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True},
                                  headers={"access-control-allow-origin": "*"})
        return httpx.Response(200, json={"from": origin, "path": request.url.path})

    def paths_for(self, origin: str) -> list[str]:
        return [
            r.url.path for r in self.requests
            if f"{r.url.scheme}://{r.url.netloc.decode()}" == origin
        ]


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def hub(tmp_path, upstream):
    """A hub whose outbound HTTP goes to `upstream` instead of the network."""
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    app = create_app(api)
    app.state.source_clients = sources_mod.SourceClients(transport=upstream.transport())
    with TestClient(app) as client:
        client.api = api
        yield client
    api.shutdown()


# ── GET /v1/sources ──────────────────────────────────────────────────────────


def test_local_is_implicit_and_never_listed(hub):
    """`local` is this process. Both halves of the frontend synthesize it
    (js/servers-logic.js and js/server-tabs-logic.js both reserve the id), so
    reporting it would render two Local tabs."""
    body = hub.get("/v1/sources").json()
    assert body["active"] == "local"
    assert body["sources"] == []


def test_reachability_is_probed_per_source_from_the_hub(hub, upstream):
    sources_mod.add_source(hub.api, url=BENCH, source_id="bench")
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    upstream.up[BENCH] = False

    by_id = {s["id"]: s for s in hub.get("/v1/sources").json()["sources"]}
    assert by_id["bench"]["reachable"] is False
    assert by_id["bench"]["detail"] == "ConnectError", "a red dot must be able to say why"
    assert by_id["shop"]["reachable"] is True
    assert by_id["shop"]["detail"] == ""
    assert upstream.paths_for(SHOP) == ["/v1/health"], "the probe uses the unauthenticated route"


def test_the_token_is_never_echoed_back(hub):
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", token="sup3r-s3cret")
    body = hub.get("/v1/sources").text
    assert "sup3r-s3cret" not in body
    assert [s for s in hub.get("/v1/sources").json()["sources"] if s["id"] == "shop"][0]["has_token"] is True


# ── roster CRUD ──────────────────────────────────────────────────────────────


def test_post_creates_a_source(hub):
    r = hub.post("/v1/sources", json={"url": SHOP, "id": "shop", "name": "Shop", "token": "t"})
    assert r.status_code == 200
    assert r.json()["detail"]["source"] == {"id": "shop", "name": "Shop", "url": SHOP,
                                            "enabled": True, "has_token": True}
    assert sources_mod.load_registry_from_api(hub.api).require("shop").token == "t"


def test_post_rejects_a_url_with_no_scheme(hub):
    r = hub.post("/v1/sources", json={"url": "bench.local:7891"})
    assert r.status_code == 400
    assert r.json()["code"] == "source_config"


def test_post_rejects_a_duplicate_id(hub):
    hub.post("/v1/sources", json={"url": BENCH, "id": "bench"})
    r = hub.post("/v1/sources", json={"url": SHOP, "id": "bench"})
    assert r.status_code == 400
    assert "already exists" in r.json()["error"]


def test_patch_updates_and_delete_removes(hub):
    hub.post("/v1/sources", json={"url": BENCH, "id": "bench"})
    r = hub.patch("/v1/sources/bench", json={"name": "Renamed", "enabled": False})
    assert r.json()["detail"]["source"]["name"] == "Renamed"
    assert r.json()["detail"]["source"]["enabled"] is False

    assert hub.delete("/v1/sources/bench").status_code == 200
    assert hub.get("/v1/sources").json()["sources"] == []


def test_patch_and_delete_on_an_unknown_id_are_404(hub):
    assert hub.patch("/v1/sources/ghost", json={"name": "x"}).status_code == 404
    r = hub.delete("/v1/sources/ghost")
    assert r.status_code == 404
    assert r.json()["code"] == "source_not_found"


def test_deleting_the_default_source_resets_the_default(hub):
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    hub.put("/v1/sources/active", json={"source": "shop"})
    assert hub.get("/v1/sources").json()["default"] == "shop"

    body = hub.delete("/v1/sources/shop").json()["detail"]
    assert body["default"] == "local"
    assert body["server_url"] == "", "a removed source must not stay in server_url"
    assert hub.get("/v1/sources").json()["default"] == "local"


def test_a_window_still_naming_a_deleted_source_gets_a_loud_404(hub):
    """Not silently reassigned to the default — that would show one server's
    stock under another's name. 404 is what lets the tab strip fall back."""
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    hub.delete("/v1/sources/shop")

    r = hub.get("/v1/parts", headers={"X-Dubis-Source": "shop"})
    assert r.status_code == 404
    assert r.json()["code"] == "source_not_found"


# ── PUT /v1/sources/active ───────────────────────────────────────────────────


@pytest.mark.parametrize(("verb", "path", "body"), [
    ("post", "/v1/sources", {"url": SHOP}),
    ("patch", "/v1/sources/shop", {"enabled": False}),
    ("delete", "/v1/sources/shop", None),
])
def test_roster_edits_publish(hub, verb, path, body):
    """In `merged` mode the roster IS the inventory's shape — the merged view is
    the union of the enabled sources — so every roster edit changes what
    GET /v1/parts answers and has to reach the SSE debounce."""
    if verb != "post":
        sources_mod.add_source(hub.api, url=SHOP, source_id="shop")
    q = events.subscribe()
    try:
        r = getattr(hub, verb)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 200
        name, data = q.get(timeout=1)
    finally:
        events.unsubscribe(q)
    assert name == "inventory.updated"
    assert data["source"] == "local", "a local roster write is news about the hub"


def test_setting_the_default_saves_a_preference(hub):
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    r = hub.put("/v1/sources/active", json={"source": "shop"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": {"default": "shop", "server_url": SHOP}}
    assert hub.get("/v1/sources").json()["default"] == "shop"
    assert hub.api.load_preferences()["server_url"] == SHOP


def test_setting_the_default_publishes_nothing(hub):
    """It moves no data any window is showing — every window names its own
    source per request — so announcing it would make every OTHER window
    re-fetch data that did not change."""
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    q = events.subscribe()
    try:
        assert hub.put("/v1/sources/active", json={"source": "shop"}).status_code == 200
        with pytest.raises(queue.Empty):
            q.get(timeout=0.2)
    finally:
        events.unsubscribe(q)


def test_setting_the_default_does_not_move_a_window_that_named_its_source(hub, upstream):
    """The property the whole design turns on: window A writing the default
    cannot change what window B — which names `local` on every request — sees."""
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    pinned = {"X-Dubis-Source": "local"}
    before = hub.get("/v1/parts", headers=pinned).json()

    hub.put("/v1/sources/active", json={"source": "shop"})

    assert hub.get("/v1/parts", headers=pinned).json() == before
    assert "/v1/parts" not in [r.url.path for r in upstream.requests]


def test_merged_is_a_valid_default(hub):
    r = hub.put("/v1/sources/active", json={"source": "merged"})
    assert r.status_code == 200
    assert r.json()["detail"] == {"default": "merged", "server_url": ""}
    assert hub.get("/v1/sources").json()["default"] == "merged"


def test_the_default_still_answers_under_the_legacy_active_key(hub):
    """js/server-tabs-logic.js reads `active`; both names carry the same value."""
    body = hub.get("/v1/sources").json()
    assert body["active"] == body["default"]


def test_an_unknown_default_is_404(hub):
    assert hub.put("/v1/sources/active", json={"source": "ghost"}).status_code == 404


def test_the_default_applies_to_a_headerless_client_on_the_very_next_request(hub, upstream):
    """`tools/dubis-cli`, curl, and any client written before this feature send
    no header — the persisted default is what keeps them working unchanged."""
    hub.post("/v1/sources", json={"url": SHOP, "id": "shop"})
    assert hub.get("/v1/parts").json()["inventory"][0]["lcsc"] == "C100000"

    hub.put("/v1/sources/active", json={"source": "shop"})
    assert hub.get("/v1/parts").json() == {"from": SHOP, "path": "/v1/parts"}

    hub.put("/v1/sources/active", json={"source": "local"})
    assert hub.get("/v1/parts").json()["inventory"][0]["lcsc"] == "C100000"
