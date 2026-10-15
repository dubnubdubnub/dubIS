"""`/v1/thresholds` — the route surface, the auth matrix, and the merged refusal.

The store semantics are pinned in tests/python/domain/test_threshold_snapshots.py;
this module is about what the HTTP layer guarantees:

* a fresh server answers exactly snapshot 0 (no migration, no seeded file);
* writes append and are attributed `source@identity` the way every other
  mutation is (token, Unix-socket peer, loopback `local`);
* anonymous is 401 in auth-`on` mode — writes come from authenticated callers
  only, never from whoever knocks;
* a merged/grouped selector on a WRITE is the dispatch middleware's existing
  400 `source_config` refusal (nothing here re-implements it), while a READ on
  the same selector falls back to the hub exactly like every other non-parts
  read (server/dispatch.py's read/write asymmetry);
* undo with nothing to undo is a loud 409, never a 200 that changed nothing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import peercred
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

REMOTE = ("100.64.1.2", 51234)
LOOPBACK = ("127.0.0.1", 51234)


class _ScopeStamper:
    """ASGI shim standing in for what server/uds.py does: put a peer identity
    in the scope state. Same shape as tests/python/server/test_peercred.py's,
    so a Unix-socket peer can be exercised without a real socket."""

    def __init__(self, app, identity):
        self.app = app
        self.identity = identity

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = dict(scope.get("state") or {})
            state[peercred.PEER_IDENTITY_KEY] = self.identity
            scope = {**scope, "state": state}
        await self.app(scope, receive, send)


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


def _put(client, values, **kwargs):
    return client.put("/v1/thresholds", json={"values": values, **kwargs})


# ── GET: the fresh server ────────────────────────────────────────────────────


def test_fresh_server_answers_snapshot_zero_only(client):
    body = client.get("/v1/thresholds").json()
    assert body["active_id"] == 0
    assert [s["id"] for s in body["snapshots"]] == [0]
    assert body["snapshots"][0]["seeded"] is True
    assert body["active"]["values"] == client.get("/v1/meta").json()["default_thresholds"]


def test_get_resolves_every_served_category(client, api):
    body = client.get("/v1/thresholds").json()
    assert set(body["resolved"]) == set(api.FLAT_SECTION_ORDER)
    # With snapshot 0 active, resolution is the shipped order: a tuned
    # category answers its default (parent inheritance included), and a
    # base-by-decision name stays at the base.
    assert body["resolved"]["Passives - Resistors"] == 4
    assert body["resolved"]["Passives - Resistors > Chip Resistors"] == 4  # parent default
    assert body["resolved"]["ICs - Microcontrollers"] == 50  # base by decision


# ── PUT: append, resolve, attribute ──────────────────────────────────────────


def test_put_appends_a_snapshot_and_moves_the_resolved_profile(client):
    created = _put(client, {"Diodes": 7}, note="tuned").json()
    assert created["active_id"] == 1
    assert created["resolved"]["Diodes"] == 7
    assert created["active"]["note"] == "tuned"
    again = client.get("/v1/thresholds").json()
    assert [s["id"] for s in again["snapshots"]] == [0, 1]
    assert again["snapshots"][0]["active"] is False


def test_put_null_removal_hands_the_category_back_to_snapshot_zero(client):
    _put(client, {"Diodes": 7})
    body = _put(client, {"Diodes": None}).json()
    assert "Diodes" not in body["active"]["values"]
    # Snapshot 0 still names it at 10, so resolution falls through to that.
    assert body["resolved"]["Diodes"] == 10


def test_put_full_mode_records_the_profile_as_is(client):
    body = _put(client, {"Diodes": 1}, mode="full").json()
    assert body["active"]["values"] == {"Diodes": 1}


def test_put_rejects_an_unknown_category_with_400(client):
    r = _put(client, {"Not A Section": 5})
    assert r.status_code == 400
    assert r.json()["code"] == "value_error"
    assert "not a category this server serves" in r.json()["error"]


def test_put_rejects_a_negative_threshold_with_400(client):
    assert _put(client, {"Diodes": -1}).status_code == 400


def test_put_rejects_a_null_in_full_mode_with_422_or_400(client):
    # Pydantic types `int | None` so the null reaches the domain, which
    # refuses it — either status is a loud no; a 200 would be the bug.
    assert _put(client, {"Diodes": None}, mode="full").status_code in (400, 422)


def test_put_bad_mode_is_a_validation_error(client):
    assert _put(client, {"Diodes": 1}, mode="vibes").status_code == 422


# ── undo / activate ──────────────────────────────────────────────────────────


def test_undo_appends_a_copy_of_the_previous_snapshot(client):
    _put(client, {"Diodes": 7})
    _put(client, {"Diodes": 8})
    body = client.post("/v1/thresholds/undo", json={}).json()
    assert body["active_id"] == 3
    assert body["active"]["values"]["Diodes"] == 7
    assert body["active"]["note"].startswith("undo: copy of snapshot 1")
    # The undone snapshot is still there, untouched — undo is never destructive.
    undone = next(s for s in body["snapshots"] if s["id"] == 2)
    assert undone["values"]["Diodes"] == 8 and undone["active"] is False


def test_undo_with_nothing_to_undo_is_a_loud_409(client):
    r = client.post("/v1/thresholds/undo", json={})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "nothing_to_undo"
    assert body["detail"] == {"active_id": 0}


def test_activate_appends_a_copy_of_the_target(client):
    _put(client, {"Diodes": 7})
    _put(client, {"Diodes": 8})
    body = client.post("/v1/thresholds/activate", json={"snapshot_id": 1}).json()
    assert body["active_id"] == 3
    assert body["active"]["values"]["Diodes"] == 7


def test_activate_unknown_snapshot_is_404(client):
    r = client.post("/v1/thresholds/activate", json={"snapshot_id": 42})
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_activate_the_already_active_snapshot_is_400(client):
    _put(client, {"Diodes": 7})
    r = client.post("/v1/thresholds/activate", json={"snapshot_id": 1})
    assert r.status_code == 400
    assert "already active" in r.json()["error"]


# ── auth matrix (DUBIS_AUTH_MODE=on) ─────────────────────────────────────────


def test_anonymous_remote_is_401_on_read_and_write(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        assert c.get("/v1/thresholds").status_code == 401
        assert _put(c, {"Diodes": 7}).status_code == 401
    assert not (tmp_path / "threshold_snapshots.json").exists(), \
        "a refused write must not have touched the store"


def test_loopback_local_may_write_unattributed(tmp_path, monkeypatch):
    """The desktop on its own loopback is `local` — the operator, as every
    other mutation route already treats them. stamp_source leaves the source
    bare for local, so created_by is exactly the tag the caller sent."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=LOOPBACK) as c:
        r = _put(c, {"Diodes": 7}, source="desktop")
        assert r.status_code == 200
    assert r.json()["active"]["created_by"] == "desktop"


def test_token_caller_is_attributed_source_at_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.put("/v1/thresholds",
                  json={"values": {"Diodes": 7}, "source": "cli"},
                  headers={"Authorization": "Bearer abc123"})
        assert r.status_code == 200
    assert r.json()["active"]["created_by"] == "cli@ci"


def test_uds_peer_is_attributed_by_username(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    app = _ScopeStamper(create_app(api), "alice")
    with TestClient(app, client=None) as c:
        r = c.put("/v1/thresholds", json={"values": {"Diodes": 7}, "source": "cli"})
        assert r.status_code == 200
    assert r.json()["active"]["created_by"] == "cli@alice"


# ── merged/grouped: the inherited refusal ────────────────────────────────────


def test_merged_write_is_refused_with_the_standard_400(client):
    r = client.put("/v1/thresholds", json={"values": {"Diodes": 7}},
                   headers={"X-Dubis-Source": "merged"})
    assert r.status_code == 400
    assert r.json()["code"] == "source_config"
    assert "merged view" in r.json()["error"]


def test_grouped_write_is_refused_too(client):
    # A two-server group is a view exactly like `merged` — Registry.resolve
    # refuses both on a write before the handler runs.
    r = client.post("/v1/thresholds/undo", json={},
                    headers={"X-Dubis-Source": "local,local"})
    assert r.status_code == 400
    assert r.json()["code"] == "source_config"


def test_merged_read_still_works(client):
    """The read half of dispatch's asymmetry: a window on the All tab reads
    the hub's profile rather than eating a 400 for a question that has a
    local answer."""
    r = client.get("/v1/thresholds", headers={"X-Dubis-Source": "merged"})
    assert r.status_code == 200
    assert r.json()["active_id"] == 0
