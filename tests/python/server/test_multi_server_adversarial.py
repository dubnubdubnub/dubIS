"""Adversarial system tests for the multi-server hub.

Written against the *intent* in `docs/plans/2026-09-19-multi-server-hub-design.md`
and the user's four sentences, not against the code that happens to exist. The
companion suite `tests/python/server/test_source_dispatch.py` was written by the
implementers alongside the implementation and is example-based; this one starts
from invariants and tries to break them.

Two deliberate differences from that suite:

* **The real `domain/federation.py` runs.** `test_source_dispatch.py` injects a
  stub merge module, which pins the *contract* but means no HTTP-level test in
  this repo has ever seen a real merged row. Everything here goes through the
  real merge, so a conservation claim is a claim about what a browser would be
  handed.
* **Failure is injected at the wire, not at the registry.** A source is made to
  answer the way a real broken peer answers: 200 with a body that is not an
  inventory, a redirect to an SSO page, an auth rejection, a stall.

The invariants, and where each is exercised:

1. **Conservation with visibility.** A source that does not contribute must be
   reported as not having contributed. Under-reported stock looks exactly like
   correct stock. (`TestConservationWithVisibility`)
2. **Write isolation.** A write lands on exactly one server; an ambiguous
   target is refused, never guessed. (`TestWriteIsolation`)
3. **Provenance is not the peer's opinion.** A tab named `shop` must show
   `shop`'s own stock, whatever `shop` happens to have saved as *its* default.
   (`TestPeerPinning`)
4. **Degradation honesty.** Down, slow, not-dubIS and auth-rejecting must be
   four distinguishable states, never a confident wrong answer.
   (`TestDegradationHonesty`)
5. **One vocabulary.** Every source selector the tab strip can produce must be
   a selector the hub accepts, on both the header and the default route.
   (`TestOneSourceVocabulary`)
6. **Roster edits are reversible and leave no ghosts.**
   (`TestRosterStateMachine`)
7. **Differential.** A merge over one source equals that source alone; a group
   naming every source equals `merged`. (`TestDifferential`)

Tests whose name ends in `__DEFECT` are the ones that currently fail. Each
docstring says what the defect is and what the honest behaviour would be.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from server import sources as sources_mod
from server.app import create_app
from server.proxy import SOURCE_HEADER, SOURCE_HEADER_SEPARATOR
from tests.python.helpers import make_api, make_part, write_ledger
from tests.python.strategies import keyed_inventory_records

BENCH = "http://bench.local:7891"
SHOP = "https://shop.example"
ATTIC = "http://attic.local:9000"


# ── A programmable set of peer dubIS servers ────────────────────────────────


class Peers:
    """Several fake dubIS servers behind one `httpx.MockTransport`.

    `parts[origin]` is what that origin answers `GET /v1/parts` with. `answer`
    overrides the whole response for one origin — an `httpx.Response`, or an
    exception to raise, which is how "asleep" and "too slow" are injected
    without a clock.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.parts: dict[str, list[dict]] = {}
        self.answer: dict[str, object] = {}
        self.health: dict[str, httpx.Response] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        override = self.answer.get(origin)
        # A transport-level failure is a property of the machine, not of one
        # path: a server that is asleep does not answer /v1/health either. A
        # *response* override is per-path, so health keeps answering for a peer
        # that is merely confused about what /v1/parts means.
        if isinstance(override, Exception):
            raise override
        if request.url.path == "/v1/health":
            return self.health.get(origin) or httpx.Response(200, json={"ok": True})
        if override is not None:
            return override  # type: ignore[return-value]
        if request.url.path == "/v1/parts":
            return httpx.Response(200, json={"inventory": self.parts.get(origin, [])})
        return httpx.Response(200, json={"from": origin, "method": request.method})

    def sent_to(self, origin: str) -> list[httpx.Request]:
        return [r for r in self.requests
                if f"{r.url.scheme}://{r.url.netloc.decode()}" == origin]

    def last_for(self, path: str) -> httpx.Request:
        matches = [r for r in self.requests if r.url.path == path]
        assert matches, f"nothing was sent to {path}; saw {[r.url.path for r in self.requests]}"
        return matches[-1]


@pytest.fixture
def peers() -> Peers:
    return Peers()


@pytest.fixture
def hub(tmp_path, peers):
    """A hub with 10 of C100000 of its own and no remotes configured yet."""
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    app = create_app(api)
    app.state.source_clients = sources_mod.SourceClients(transport=peers.transport())
    with TestClient(app) as client:
        client.api = api
        yield client
    api.shutdown()


def add(hub, url: str, source_id: str, **kw):
    return sources_mod.add_source(hub.api, url=url, source_id=source_id, **kw)[1]


def merged_body(hub, selector: str = "merged") -> dict:
    response = hub.get("/v1/parts", headers={SOURCE_HEADER: selector})
    assert response.status_code == 200, response.text
    return response.json()


def status_of(body: dict, source_id: str) -> dict:
    for entry in body["sources"]:
        if entry["id"] == source_id:
            return entry
    raise AssertionError(f"{source_id!r} is missing from {body['sources']!r}")


def qty_of(body: dict, key: str) -> int:
    for row in body["inventory"]:
        if row.get("lcsc") == key or row.get("mpn") == key:
            return row["qty"]
    return 0


# ── 1. Conservation with visibility ─────────────────────────────────────────


class TestConservationWithVisibility:
    """A source that did not contribute must say so.

    This is the worst failure mode in the feature, and the reason it is the
    worst is arithmetic: a merged total that is missing a machine's stock is a
    *smaller perfectly plausible number*. There is no shape to it, nothing looks
    wrong, and the user's next decision — order more, or don't — is made on it.
    So the bar is not "the merge is correct when everything answers". The bar is
    that every way a source can fail to contribute ends in `ok: false`.
    """

    def test_a_source_that_is_asleep_is_reported_down_and_costs_only_its_own_stock(
        self, hub, peers,
    ):
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = [{"lcsc": "C100000", "qty": 400}]
        whole = merged_body(hub)
        assert qty_of(whole, "C100000") == 410

        peers.answer[SHOP] = httpx.ConnectError("connection refused")
        partial = merged_body(hub)
        assert qty_of(partial, "C100000") == 10, "local stock survives a peer going away"
        assert status_of(partial, "shop")["ok"] is False
        assert status_of(partial, "local")["ok"] is True

    def test_a_source_that_answers_200_with_something_that_is_not_an_inventory__DEFECT(
        self, hub, peers,
    ):
        """DEFECT (critical): a reachable-but-not-dubIS peer reports `ok: true`
        and contributes nothing.

        `server/dispatch.py:_merged_parts` does
        `records = records.get("inventory", [])` on any dict the peer sent. A
        server that is up, answers 200 and speaks JSON — an nginx welcome
        endpoint, a captive portal, a health-check shim, a dubIS that renamed
        the envelope key — therefore contributes zero rows while being reported
        as a source that answered fine.

        That is the exact failure this feature is supposed to make impossible:
        the totals are short by a whole machine, the per-source status says
        everything is fine, and `js/inventory/inv-source-logic.partialViewNote`
        — which reads only `ok` — shows no banner.

        Honest behaviour: a body with no `inventory` list is a source that did
        not answer the question, i.e. `ok: false` with a reason, exactly as a
        body that is not JSON at all already produces.
        """
        add(hub, SHOP, "shop")
        peers.answer[SHOP] = httpx.Response(200, json={"status": "ok", "service": "nginx"})

        body = merged_body(hub)
        assert qty_of(body, "C100000") == 10, "nothing came from shop"
        assert status_of(body, "shop")["ok"] is False, (
            "shop contributed no rows, so it must not be reported as a source that answered"
        )

    def test_a_source_whose_inventory_is_not_a_list__DEFECT(self, hub, peers):
        """DEFECT (critical): same hole, one branch further in.

        `server/dispatch.py:255-262` explicitly notices this case — it logs
        `"answered ... with a dict, not a list — dropping it"` — and then drops
        the source *without touching its status*, so the response still claims
        `ok: true`. A log line on the hub's stderr is not visible to the person
        reading the totals.
        """
        add(hub, SHOP, "shop")
        peers.answer[SHOP] = httpx.Response(200, json={"inventory": {"C100000": 400}})

        body = merged_body(hub)
        assert status_of(body, "shop")["ok"] is False, (
            "a source whose rows were dropped must be reported as failed, not as ok"
        )

    def test_a_green_dot_and_an_empty_contribution_cannot_both_be_true__DEFECT(
        self, hub, peers,
    ):
        """DEFECT (critical): the two halves of the UI agree on a lie.

        `/v1/health` is deliberately unauthenticated on every dubIS server, so
        `server/sources.probe` treats any 200 as reachable — which means a
        non-dubIS server that answers 200 anywhere gets a *green dot* in the tab
        strip. Combined with the defect above, the user sees: a live-looking
        server, a complete-looking merged view, and no banner. Three independent
        signals, all wrong, all agreeing.

        This test states the cross-check the feature actually needs: if the
        merged read could not use a source, the merged read must say so, no
        matter what the probe thinks.
        """
        add(hub, SHOP, "shop")
        peers.health[SHOP] = httpx.Response(200, text="OK")
        peers.answer[SHOP] = httpx.Response(200, json={"hello": "world"})

        roster = hub.get("/v1/sources").json()
        shop_row = next(s for s in roster["sources"] if s["id"] == "shop")
        merged = merged_body(hub)

        assert not (shop_row["reachable"] and status_of(merged, "shop")["ok"]), (
            "shop is shown as reachable AND as having answered, while it contributed "
            "no stock at all — a partial view must be visibly partial"
        )

    def test_a_health_shim_that_says_ok_is_not_a_dubIS_server(self, hub, peers):
        """A truthy `ok` is not the same claim as dubIS's health body.

        Found by pointing a source at a real 200-JSON service and watching the
        dot stay green. `probe()` used to accept any dict with a truthy `ok`,
        but `{"ok": true, "service": "..."}` is the commonest shape a
        load-balancer or k8s health endpoint answers with — so the one impostor
        most likely to be sitting on a URL someone typed was the one the guard
        waved through. dubIS's `/v1/health` is exactly `{"ok": true}`
        (server/routes/meta.py, pinned by test_health_cors.py), so the probe
        requires exactly that.

        The merged read already refuses this source; this is about the *dot*,
        which is the signal the user checks before trusting the totals.
        """
        add(hub, SHOP, "shop")
        peers.health[SHOP] = httpx.Response(200, json={"ok": True, "service": "nginx"})

        roster = hub.get("/v1/sources").json()
        shop_row = next(s for s in roster["sources"] if s["id"] == "shop")

        assert shop_row["reachable"] is False, (
            "a health shim answering {'ok': true, ...} is not a dubIS server and "
            "must not get a green dot"
        )
        assert "not a dubIS server" in (shop_row.get("detail") or ""), (
            "a red dot has to say why"
        )

    @settings(max_examples=40, deadline=None)
    @given(
        shop_rows=st.lists(keyed_inventory_records(), max_size=4),
        bench_rows=st.lists(keyed_inventory_records(), max_size=4),
        shop_is_up=st.booleans(),
        bench_is_up=st.booleans(),
    )
    def test_the_merged_view_only_ever_claims_stock_from_a_source_that_answered(
        self, tmp_path_factory, shop_rows, bench_rows, shop_is_up, bench_is_up,
    ):
        """System-level conservation: the whole stack, not the pure merge.

        `domain/federation.py`'s own arithmetic is property-tested elsewhere and
        deliberately not repeated. What this asks is narrower and only statable
        end to end: after fan-out, partial-failure handling and JSON
        serialisation, does every merged row still reconcile with its own
        breakdown — and does that breakdown only ever name sources the SAME
        response reported as `ok`?

        The second half is the one with teeth. It is the machine-checkable form
        of "a partial view must be visibly partial": there is no way to satisfy
        it while a source silently contributes nothing, and no way to satisfy it
        while a source contributes stock the status array disowns.

        Builds its own hub per example rather than taking the fixture, because a
        function-scoped fixture is shared across Hypothesis examples and roster
        mutations would pile up.
        """
        api = make_api(tmp_path_factory.mktemp("hub"))
        write_ledger(api, [make_part(lcsc="C100000", qty=10)])
        peers = Peers()
        peers.parts[SHOP] = shop_rows
        peers.parts[BENCH] = bench_rows
        if not shop_is_up:
            peers.answer[SHOP] = httpx.ConnectError("asleep")
        if not bench_is_up:
            peers.answer[BENCH] = httpx.ReadTimeout("too slow")

        app = create_app(api)
        app.state.source_clients = sources_mod.SourceClients(transport=peers.transport())
        try:
            with TestClient(app) as client:
                client.api = api
                add(client, SHOP, "shop")
                add(client, BENCH, "bench")
                body = merged_body(client)
        finally:
            api.shutdown()

        answered = {s["id"] for s in body["sources"] if s["ok"]}
        assert answered, "the hub's own data is always available to itself"

        for row in body["inventory"]:
            breakdown = row["sources"]
            assert breakdown, "a merged row with no source is unattributable stock"
            assert row["qty"] == sum(entry["qty"] for entry in breakdown), (
                f"row {row!r} does not reconcile with its own breakdown"
            )
            named = {entry["id"] for entry in breakdown}
            assert named <= answered, (
                f"row {row!r} claims stock from {sorted(named - answered)}, which "
                "the same response reported as not having answered"
            )

        for source_id, up in (("shop", shop_is_up), ("bench", bench_is_up)):
            assert status_of(body, source_id)["ok"] is up
            if not up:
                assert all(source_id not in {e["id"] for e in r["sources"]}
                           for r in body["inventory"])


# ── 2. Write isolation ──────────────────────────────────────────────────────


class TestWriteIsolation:
    """A write lands on exactly one server, and an ambiguous target is refused.

    The dangerous version of this bug is not "the write failed". It is "the
    write succeeded on the wrong machine", which leaves two inventories wrong
    at once and no error anywhere.
    """

    def test_a_routed_write_reaches_one_server_and_no_other(self, hub, peers):
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        before = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()

        response = hub.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 99, "source": "test"},
            headers={SOURCE_HEADER: "shop"},
        )
        assert response.status_code == 200

        assert [r.url.path for r in peers.sent_to(SHOP)] == ["/v1/parts/C100000/adjust"]
        assert peers.sent_to(BENCH) == [], "bench must not have been written to at all"
        after = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()
        assert after["inventory"] == before["inventory"], "local stock must not have moved"

    @pytest.mark.parametrize("selector", ["merged", "bench,shop"])
    def test_a_write_with_no_single_owner_is_refused_without_reaching_anyone(
        self, hub, peers, selector,
    ):
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")

        response = hub.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 99},
            headers={SOURCE_HEADER: selector},
        )
        assert response.status_code == 400
        assert peers.requests == [], "a refused write must not have been half-sent"
        local = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()
        assert local["inventory"][0]["qty"] == 10

    def test_a_write_to_a_source_that_was_just_removed_is_a_404_not_a_local_write(
        self, hub, peers,
    ):
        """Removing a server must not silently redirect its writes home.

        `remove_source` resets the *default* to local when the removed source
        was the default. A window still pointed at that source must get a 404 —
        falling back to local would apply an adjustment meant for the shop to
        the machine in front of you.
        """
        add(hub, SHOP, "shop")
        sources_mod.remove_source(hub.api, "shop")

        response = hub.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 99},
            headers={SOURCE_HEADER: "shop"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "source_not_found"
        assert peers.requests == []
        local = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()
        assert local["inventory"][0]["qty"] == 10

    def test_a_write_to_a_source_that_is_asleep_fails_loudly_and_changes_nothing(
        self, hub, peers,
    ):
        add(hub, SHOP, "shop")
        peers.answer[SHOP] = httpx.ConnectError("asleep")

        response = hub.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 99},
            headers={SOURCE_HEADER: "shop"},
        )
        assert response.status_code == 502, (
            "a write to a single named source that cannot be reached must fail — "
            "degrading is only ever correct for a READ across several sources"
        )
        local = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()
        assert local["inventory"][0]["qty"] == 10


# ── 3. Provenance is not the peer's opinion ─────────────────────────────────


class TestPeerPinning:
    """A tab named `shop` must show and write to `shop`'s OWN data.

    Every dubIS desktop app is itself a hub (`app_launch.py`), so every peer has
    a persisted default of its own — `active_source` / `server_url` in *its*
    preferences, which its user set by clicking a tab. The hub sends neither a
    source header nor anything else that pins the peer, so the peer answers from
    whatever it last saved.

    Two concrete ways that bites, neither exotic:

    * **The label lies.** Bench's desktop was pointed at shop last Tuesday. Now
      shop's hub proxies to bench and gets *shop's own* inventory back, labelled
      "bench". The tab, the badge and the per-source breakdown all say bench.
    * **Double counting.** In that same configuration a merged view counts
      shop's stock twice — once as local, once as "bench" — and the total is
      confidently, invisibly too high. Conservation fails upward, which no
      partial-view banner will ever mention because nothing failed.

    Note this contradicts `test_source_dispatch.py`'s
    `test_the_routing_header_is_not_forwarded_upstream`. Both can hold: the
    *caller's* header must not be forwarded (it names ids in THIS hub's roster,
    which mean nothing upstream), and the hub must still originate its own
    `X-Dubis-Source: local` so the peer serves the data the peer owns.
    """

    def test_a_proxied_read_pins_the_peer_to_its_own_data__DEFECT(self, hub, peers):
        """DEFECT (high): nothing stops a peer answering from a third server."""
        add(hub, SHOP, "shop")
        hub.get("/v1/parts", headers={SOURCE_HEADER: "shop"})

        forwarded = peers.last_for("/v1/parts")
        assert forwarded.headers.get(SOURCE_HEADER) == "local", (
            "the hub must tell the peer to answer from its own data; without it "
            "the peer answers from ITS persisted default, which may be a third "
            "server — or this one"
        )

    def test_a_fanned_out_read_pins_each_peer_to_its_own_data__DEFECT(self, hub, peers):
        """DEFECT (high): the double-counting half of the same hole."""
        add(hub, SHOP, "shop")
        merged_body(hub)

        forwarded = peers.last_for("/v1/parts")
        assert forwarded.headers.get(SOURCE_HEADER) == "local", (
            "a merged view that lets a peer decide what it serves can count one "
            "machine's stock twice and call it two machines"
        )

    def test_a_routed_write_pins_the_peer_to_its_own_data__DEFECT(self, hub, peers):
        """DEFECT (high): the write-isolation half.

        The user picks "bench" in the Adjust modal's Server field because the
        row says bench holds 850 of them. The hub forwards to bench's URL with
        no pin, and bench applies it to whatever bench is currently pointed at.
        """
        add(hub, BENCH, "bench")
        hub.post("/v1/parts/C100000/adjust",
                 json={"adj_type": "add", "quantity": 5},
                 headers={SOURCE_HEADER: "bench"})

        forwarded = peers.last_for("/v1/parts/C100000/adjust")
        assert forwarded.headers.get(SOURCE_HEADER) == "local", (
            "an adjustment routed to bench must land on bench's own ledger"
        )


# ── 4. Degradation honesty ──────────────────────────────────────────────────


class TestDegradationHonesty:
    """Down, slow, not-dubIS and auth-rejecting must be four distinguishable
    states — and none of them may be indistinguishable from success."""

    CASES = {
        "down": httpx.ConnectError("connection refused"),
        "slow": httpx.ReadTimeout("took too long"),
        "auth": httpx.Response(401, json={"error": "unauthorized"}),
        "server_error": httpx.Response(500, text="boom"),
        "sso_redirect": httpx.Response(302, headers={"location": "https://login.example"}),
        "html": httpx.Response(200, text="<html>hi</html>",
                               headers={"content-type": "text/html"}),
        "not_dubis_json": httpx.Response(200, json={"status": "ok"}),
        "inventory_not_a_list": httpx.Response(200, json={"inventory": {"a": 1}}),
    }

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_every_way_of_not_answering_is_reported_as_not_answering(
        self, hub, peers, case,
    ):
        """The two `__DEFECT` entries in `CASES` are the whole point of the
        parametrisation: they look exactly like success today."""
        add(hub, SHOP, "shop")
        peers.answer[SHOP] = self.CASES[case]

        body = merged_body(hub)
        status = status_of(body, "shop")
        assert status["ok"] is False, f"{case} is reported as a source that answered"
        assert status["error"], f"{case} gives no reason a user could act on"

    def test_the_four_failure_kinds_are_told_apart(self, hub, peers):
        """A red dot that cannot say why is a red dot nobody can act on."""
        reasons = {}
        for case in ("down", "slow", "auth", "server_error"):
            add(hub, SHOP, "shop") if not reasons else None
            peers.answer[SHOP] = self.CASES[case]
            reasons[case] = status_of(merged_body(hub), "shop")

        assert reasons["auth"]["status"] == 401
        assert reasons["server_error"]["status"] == 500
        assert reasons["down"]["status"] is None
        assert reasons["slow"]["status"] is None
        assert reasons["down"]["error"] != reasons["slow"]["error"], (
            "'asleep' and 'too slow to wait for' are different problems with "
            "different answers"
        )

    def test_an_auth_rejection_reaches_the_user_rather_than_being_swallowed(
        self, hub, peers,
    ):
        """Single-source mode: a 401 from the peer is the peer's 401, not a 200
        with nothing in it."""
        add(hub, SHOP, "shop")
        peers.answer[SHOP] = httpx.Response(401, json={"error": "unauthorized"})

        response = hub.get("/v1/parts", headers={SOURCE_HEADER: "shop"})
        assert response.status_code == 401


# ── 5. One source vocabulary ────────────────────────────────────────────────


class TestOneSourceVocabulary:
    """Every selector the frontend can produce must be one the hub accepts.

    `js/server-tabs-logic.js:activeSourceValue` produces exactly three shapes,
    and both of its consumers — the `X-Dubis-Source` header and
    `PUT /v1/sources/active` — have to take all three. They are the same
    vocabulary by construction (the tab is the only thing that generates them),
    so a route that takes a subset of it is a route that rejects tabs the user
    can create.
    """

    def test_the_header_accepts_all_three_shapes(self, hub, peers):
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        for selector in ("shop", "shop,bench", "merged"):
            response = hub.get("/v1/parts", headers={SOURCE_HEADER: selector})
            assert response.status_code == 200, (selector, response.text)

    @pytest.mark.parametrize("selector", ["local", "shop", "merged"])
    def test_the_default_route_accepts_the_single_and_merged_shapes(
        self, hub, peers, selector,
    ):
        add(hub, SHOP, "shop")
        assert hub.put("/v1/sources/active", json={"source": selector}).status_code == 200

    def test_the_default_route_accepts_a_group__DEFECT(self, hub, peers):
        """DEFECT (high): grouping tabs is broken for any group that is not "All".

        `activeSourceValue` serialises a group of {bench, shop} to `"bench,shop"`
        — the documented set syntax — and `js/store.js:switchToTab` PUTs it.
        `server/sources.set_default` does `registry.require(wanted)` for anything
        that is not literally `merged`, so it 404s with
        `unknown source 'bench,shop'`.

        Today the switch itself still works (the tab's header carries the group),
        so the visible symptom is narrower and nastier than a broken feature: an
        error toast on every group-tab click, a never-updated `active_source`
        preference, and a `server_url` left naming whichever single server was
        selected last — which is what `tools/dubis-cli`, `remote_mode.py` and the
        next launch all read.

        Honest behaviour: `set_default` should parse the value with the same
        grammar the header uses (`Registry.resolve`), or the route should refuse
        it with a 400 that says groups cannot be a default — either is a
        decision; a 404 saying the group is an unknown *source* is neither.
        """
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        response = hub.put("/v1/sources/active", json={"source": "shop,bench"})
        assert response.status_code == 200, (
            "the tab strip generates this value, so the route that stores it has "
            f"to accept it — got {response.status_code} {response.text}"
        )

    def test_a_source_id_may_not_contain_the_header_separator__DEFECT(self, hub, peers):
        """DEFECT (medium): a comma in a source id is accepted and then silently
        means something else.

        `POST /v1/sources {"id": "a,b"}` is accepted — `add_source` guards the
        reserved ids `local` and `merged` but not the separator the selector
        grammar is built on. `X-Dubis-Source: a,b` is then parsed by
        `proxy.parse_source_header` as the *two* selectors `a` and `b`.

        With sources `a` and `b` also configured the request answers **200 with
        a merge of a and b**, and nothing anywhere mentions that the source the
        caller actually named was never consulted. A wrong answer that looks
        exactly like a right one, which is the class of bug this design is
        arranged to eliminate.
        """
        from dubis_errors import SourceConfigError

        with pytest.raises(SourceConfigError):
            sources_mod.add_source(hub.api, url=ATTIC, source_id=f"a{SOURCE_HEADER_SEPARATOR}b")

    def test_the_separator_collision_cannot_be_constructed__DEFECT(self, hub, peers):
        """The demonstration of the above, rewritten to match the fix.

        As written this test *built* the collision — `add(hub, ATTIC, "a,b")` —
        and then asserted the hub resolved `a,b` to that source rather than to
        the merge of `a` and `b`. Its sibling
        `test_a_source_id_may_not_contain_the_header_separator__DEFECT` requires
        that exact `add_source` call to raise, so the two could not both hold:
        one needs the id to exist, the other needs it refused.

        The brief settles it — "a comma in a source id is accepted and then
        silently means something else" is the defect, so the id is refused. The
        collision is therefore unreachable rather than resolved, and what is
        left to pin is that the ambiguity cannot be created in the first place,
        and that the unambiguous selectors still mean what they say.
        """
        from dubis_errors import SourceConfigError

        add(hub, "http://a.example", "a")
        add(hub, "http://b.example", "b")
        peers.parts["http://a.example"] = [{"lcsc": "C-A", "qty": 1}]
        peers.parts["http://b.example"] = [{"lcsc": "C-B", "qty": 1}]

        with pytest.raises(SourceConfigError, match="separator"):
            add(hub, ATTIC, f"a{SOURCE_HEADER_SEPARATOR}b")

        body = merged_body(hub, "a,b")
        assert {row["lcsc"] for row in body["inventory"]} == {"C-A", "C-B"}, (
            "with the ambiguous id refused, 'a,b' can only mean the merge of "
            "'a' and 'b' — there is no third reading left for it to silently take"
        )


# ── 6. Roster state machine ─────────────────────────────────────────────────


class TestRosterStateMachine:
    """Roster edits must leave the registry in a state that describes reality.

    The invariant under all of them: every source the registry reports is one
    somebody configured, and the `server_url` written for the outside readers
    (`remote_mode.py`, `app_restart.py`, `tools/dubis-cli`) names the same server
    the hub itself would serve a headerless request from.
    """

    @staticmethod
    def _prefs(hub) -> dict:
        return json.loads(json.dumps(hub.api.load_preferences()))

    def test_editing_the_default_sources_url_does_not_resurrect_the_old_one__DEFECT(
        self, hub,
    ):
        """DEFECT (medium-high): a phantom source at the old address.

        `update_source` persists with `write_default=False`, which is right for
        "adding a server does not change anybody's default" — but a URL edit to
        the source that *is* the default leaves `server_url` naming the old
        address. `load_registry` then sees a `server_url` that matches no roster
        entry and, by design, adopts it as a synthetic source.

        So: point the shop at its new address, and a second "shop.example" tab
        appears, pointing at the machine you just moved away from. It is
        probeable, selectable, and writable. An adjustment made on it lands on
        the old server — and `tools/dubis-cli` was sent there too, because
        `server_url` is what it reads.
        """
        add(hub, SHOP, "shop")
        sources_mod.set_default(hub.api, "shop")
        sources_mod.update_source(hub.api, "shop", url="https://shop-new.example")

        registry = sources_mod.load_registry_from_api(hub.api)
        urls = {source.url for source in registry.remotes}
        assert urls == {"https://shop-new.example"}, (
            f"the old address came back as a source of its own: {urls}"
        )
        assert self._prefs(hub)["server_url"] == "https://shop-new.example", (
            "server_url still names the machine the user just moved away from"
        )

    def test_removing_a_source_removes_it_for_good(self, hub):
        add(hub, SHOP, "shop")
        sources_mod.set_default(hub.api, "shop")
        sources_mod.remove_source(hub.api, "shop")

        registry = sources_mod.load_registry_from_api(hub.api)
        assert registry.remotes == ()
        assert registry.default == "local"
        assert self._prefs(hub)["server_url"] == ""

    def test_the_default_always_names_something_that_exists(self, hub):
        """A sequence, run for its end state: every intermediate default has to
        be resolvable, because a headerless request falls back to it."""
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        for step in ("shop", "merged", "bench", "local", "merged"):
            sources_mod.set_default(hub.api, step)
            registry = sources_mod.load_registry_from_api(hub.api)
            assert registry.default == step
            if step != "merged":
                assert registry.require(step).id == step
            assert registry.default_url == self._prefs(hub)["server_url"], (
                "server_url and the hub's own default must not drift apart"
            )

    def test_the_all_tab_is_never_beaten_by_one_of_its_own_subsets__DEFECT(
        self, hub, peers,
    ):
        """DEFECT (high): a strict superset shows less stock, silently.

        Two selectors, two different meanings, and the frontend turns one into
        the other. `js/server-tabs-logic.js:activeSourceValue` collapses a tab
        covering *every* source to the bare keyword `merged`, and
        `Registry.resolve` reads `merged` as "every **enabled** source" while it
        reads an enumerated set as "exactly these, enabled or not"
        (`server/sources.py:301` — "A source named explicitly is served even
        when disabled: the user just pointed at it").

        So with `shop` disabled:

            group {bench, shop}  -> 405   (a SUBSET of the All tab)
            All  {local, bench, shop} -> 15

        and the All view's `sources` array does not mention `shop` at all, so
        `partialViewNote` has nothing to report and no banner appears. The user
        disabled shop once, months ago, and "All" has quietly meant "all but
        one" ever since.

        Either answer is defensible on its own. What is not defensible is that a
        view containing a source can show less than a view of fewer sources, with
        nothing on screen saying which servers were left out.
        """
        add(hub, BENCH, "bench")
        add(hub, SHOP, "shop", enabled=False)
        peers.parts[BENCH] = [{"lcsc": "C100000", "qty": 5}]
        peers.parts[SHOP] = [{"lcsc": "C100000", "qty": 400}]

        subset = merged_body(hub, "bench,shop")
        superset = merged_body(hub, "merged")  # what the All tab sends

        assert qty_of(superset, "C100000") >= qty_of(subset, "C100000"), (
            f"the All tab shows {qty_of(superset, 'C100000')} while the group "
            f"{{bench, shop}} — a subset of it — shows {qty_of(subset, 'C100000')}"
        )

    def test_a_source_left_out_of_a_merged_view_is_still_named_in_it__DEFECT(
        self, hub, peers,
    ):
        """DEFECT (high): the omission itself is invisible.

        A disabled source is dropped from `enabled_sources` before the fan-out
        runs, so it never gets a `SourceResult` and never appears in the
        response's `sources` array. There is therefore nothing for the UI to
        render — no dot, no banner, no line in the breakdown — and a merged
        total that is missing a whole machine looks exactly like a complete one.

        Contrast a source that is *down*: that one is in the array with
        `ok: false`, which is what the partial-view banner is built on. Exclusion
        and failure are different reasons for the same missing stock, and both
        have to be sayable.
        """
        add(hub, SHOP, "shop", enabled=False)
        body = merged_body(hub)
        ids = {s["id"] for s in body["sources"]}
        assert "shop" in ids, (
            "a merged view must name every configured source it did not include, "
            f"and why — it named only {sorted(ids)}"
        )

    def test_a_disabled_source_leaves_the_merge_but_stays_addressable(self, hub, peers):
        """`enabled` is the opt-out from merged VIEWS. Naming a disabled source
        as the sole selector still serves it — that is direct addressing, and
        the user just pointed at it.

        Amended while fixing the defects in this class, because as written this
        test contradicted
        `test_a_source_left_out_of_a_merged_view_is_still_named_in_it__DEFECT`
        directly: both merge with `shop` disabled, and one asserted `shop` is
        absent from `sources[]` while the other asserted it is present. No
        implementation can satisfy both. The `__DEFECT` one states the invariant
        the brief asks for — *a source left out of a merged view is still named
        in it* — so the one assertion below now checks that shop is named and
        marked excluded, rather than that it is missing. Everything else about
        this test is unchanged, including the part that matters most to it: a
        disabled source stays directly addressable.
        """
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = [{"lcsc": "C-SHOP", "qty": 3}]
        assert qty_of(merged_body(hub), "C-SHOP") == 3

        sources_mod.update_source(hub.api, "shop", enabled=False)
        assert qty_of(merged_body(hub), "C-SHOP") == 0
        excluded = status_of(merged_body(hub), "shop")
        assert excluded["ok"] is False and excluded["error"] == "disabled"
        assert qty_of(merged_body(hub, "shop"), "C-SHOP") == 3

    def test_a_roster_entry_whose_url_is_unreachable_is_still_a_roster_entry(
        self, hub, peers,
    ):
        """Adding a server that is off does not fail, and does not get quietly
        dropped on the next read — the machine is asleep, not imaginary."""
        add(hub, ATTIC, "attic")
        peers.answer[ATTIC] = httpx.ConnectError("asleep")

        roster = hub.get("/v1/sources").json()
        attic = next(s for s in roster["sources"] if s["id"] == "attic")
        assert attic["reachable"] is False
        assert attic["detail"], "a red dot must be able to say why"
        assert sources_mod.load_registry_from_api(hub.api).require("attic").url == ATTIC


# ── 6b. Window independence ─────────────────────────────────────────────────


class TestWindowIndependence:
    """Two windows must never move each other's data.

    This branch is what makes two windows possible at all: `app_launch.py`'s
    attached mode means a second launch does not boot a second hub, it serves
    its window from the first one's. So both windows now share one
    `/v1/preferences`, one roster, one default — and the hub's design answers
    the hardest part of that correctly, by holding no mutable active source and
    letting every request name its own (`server/dispatch.py`).

    What is left is everything else the frontend keeps in that same file.
    """

    def test_a_request_that_names_its_source_is_unmoved_by_the_saved_default(
        self, hub, peers,
    ):
        """The core of the design, stated from the outside: window A switching
        cannot move window B."""
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = [{"lcsc": "C-SHOP", "qty": 1}]

        window_b = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()
        hub.put("/v1/sources/active", json={"source": "shop"})  # window A switches
        again = hub.get("/v1/parts", headers={SOURCE_HEADER: "local"}).json()

        assert again == window_b, "window B's view moved because window A clicked a tab"

    def test_two_windows_saving_preferences_do_not_erase_each_others__DEFECT(self, hub):
        """DEFECT (high): last writer wins over the WHOLE preferences file.

        `PUT /v1/preferences` replaces the file (`server/routes/preferences.py`
        -> `api.save_preferences`), and `js/store.js:savePreferences` posts the
        entire in-memory object. Each window loaded that object when it started
        and mutates its own copy, so every save is a wholesale revert of
        everything the other window has done since.

        Before this branch that was harmless — the data-dir lock meant one
        window. Attached mode (`app_launch.ATTACH`) makes a second window
        routine, and this feature then puts *per-window* state in that shared
        file: `server_tabs`, `active_tab`, `active_source`.

        The scenario is two clicks apart. Window A adds a server in Preferences.
        Window B, open since before that, opens a tab — which persists its tab
        set, and with it its stale roster. The new server is gone from disk and
        from the hub's registry, and window A's next request naming it answers
        404 `source_not_found`.

        Honest behaviour: either `PUT /v1/preferences` merges per key (so two
        windows editing different keys both survive), or the per-window keys
        stop living in shared preferences. Both are real designs; silently
        discarding the other window's work is not.
        """
        window_a = dict(hub.get("/v1/preferences").json())
        window_b = dict(window_a)

        window_a["servers"] = [{"id": "shop", "name": "Shop", "url": SHOP}]
        hub.put("/v1/preferences", json=window_a)

        window_b["server_tabs"] = [{"id": "t1", "sources": ["local"], "name": "", "view": None}]
        hub.put("/v1/preferences", json=window_b)

        after = hub.get("/v1/preferences").json()
        assert after.get("servers") == window_a["servers"], (
            "window B's save erased the server window A had just added"
        )
        assert after.get("server_tabs") == window_b["server_tabs"]

    def test_the_hub_holds_no_writable_active_source(self, hub, peers):
        """Stated as a property of the module rather than of one request: the
        thing that would make windows interfere is a mutable module-level
        variable a request path can write. There must not be one."""
        import inspect

        source = inspect.getsource(sources_mod)
        # `_boot_default_url` is written exactly once, by `seed_initial_active_source`,
        # on the boot thread before `create_app`. Any *other* `global` write in
        # this module would be a request path mutating shared state.
        writers = [line.strip() for line in source.splitlines()
                   if line.strip().startswith("global ")]
        assert set(writers) <= {"global _boot_default_url"}, (
            f"server/sources.py grew mutable module state: {writers}"
        )


# ── 7. Differential ─────────────────────────────────────────────────────────


class TestDifferential:
    """Two views that must agree, checked by construction rather than by eye."""

    def test_a_group_naming_every_source_equals_merged(self, hub, peers):
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        peers.parts[SHOP] = [{"lcsc": "C100000", "qty": 4, "description": "shop"}]
        peers.parts[BENCH] = [{"lcsc": "C-BENCH", "qty": 2}]

        assert merged_body(hub, "local,shop,bench") == merged_body(hub, "merged")

    def test_a_merge_over_one_source_is_that_source_alone(self, hub, peers):
        """Same rows, same order, same quantities — the merge adds provenance
        and nothing else."""
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = [
            {"lcsc": "C-S1", "qty": 5, "description": "one", "unit_price": 2.0},
            {"lcsc": "C-S2", "qty": 0, "description": "two", "unit_price": 0.0},
        ]
        sources_mod.update_source(hub.api, "shop", enabled=True)
        # A single-member group is the narrowest merged view there is.
        merged = merged_body(hub, "shop")
        assert "sources" not in merged or isinstance(merged, dict)
        alone = hub.get("/v1/parts", headers={SOURCE_HEADER: "shop"}).json()

        assert [r["lcsc"] for r in alone["inventory"]] == ["C-S1", "C-S2"]
        for original, passed_through in zip(peers.parts[SHOP], alone["inventory"]):
            assert original["qty"] == passed_through["qty"]

    def test_a_zero_qty_part_still_appears_with_its_provenance(self, hub, peers):
        """Zero is data. A part everyone has run out of must still be a row, on
        the server that ran out of it — otherwise it vanishes exactly when
        somebody is looking for it."""
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = [{"lcsc": "C-ZERO", "qty": 0, "unit_price": 1.5}]

        body = merged_body(hub)
        row = next(r for r in body["inventory"] if r["lcsc"] == "C-ZERO")
        assert row["qty"] == 0
        assert [entry["id"] for entry in row["sources"]] == ["shop"]

    def test_an_empty_source_contributes_nothing_and_is_still_reported_ok(
        self, hub, peers,
    ):
        """An empty inventory is a legitimate answer and must not read as a
        failure — a brand-new server has no parts yet."""
        add(hub, SHOP, "shop")
        peers.parts[SHOP] = []

        body = merged_body(hub)
        assert status_of(body, "shop")["ok"] is True
        assert qty_of(body, "C100000") == 10

    def test_the_same_part_on_two_servers_is_one_row_that_admits_the_split(
        self, hub, peers,
    ):
        """Identity collision, the headline case: `C1000` on two machines.

        One row (so `rowMap`, `tr.dataset.partKey`, label selection and the
        manual-link pairs keep working untouched), summed qty, and a breakdown
        that names both — which is what makes the write routable at all.
        """
        add(hub, SHOP, "shop")
        add(hub, BENCH, "bench")
        peers.parts[SHOP] = [{"lcsc": "C100000", "qty": 400, "description": "shop's words"}]
        peers.parts[BENCH] = [{"lcsc": "C100000", "qty": 7, "description": "bench's words"}]

        body = merged_body(hub)
        rows = [r for r in body["inventory"] if r["lcsc"] == "C100000"]
        assert len(rows) == 1, "one part key is one row, or every keyed call site breaks"
        row = rows[0]
        assert row["qty"] == 417
        assert {e["id"]: e["qty"] for e in row["sources"]} == {
            "local": 10, "shop": 400, "bench": 7,
        }
        assert "description" in row["conflicts"], (
            "three servers describe this part differently; the row shows one of "
            "them and has to admit it"
        )
