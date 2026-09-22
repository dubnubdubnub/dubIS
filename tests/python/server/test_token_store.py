"""`server/token_store.py` and the privacy contract around it.

What is being pinned here is one sentence: **a source's bearer token never
leaves this hub**. It is not in the file the browser reads, it is not in the
file the browser writes back, it is not in any response body, and it is not in
the log.

That matters because the failure it replaces is invisible. `/v1/health` is
exempt from `AuthMiddleware`, so pointing the desktop app at a server running
`DUBIS_AUTH_MODE=on` produced a green reachability dot next to a server that
401'd every request carrying data — and the credential that would have fixed it
had nowhere to live that survived a preferences save.
"""

from __future__ import annotations

import json
import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from server import sources as sources_mod
from server import token_store
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

SHOP = "https://fremont.example.ts.net"
SECRET = "sup3r-s3cret-t0ken"


@pytest.fixture
def api(tmp_path):
    inst = make_api(tmp_path)
    yield inst
    inst.shutdown()


@pytest.fixture
def data_dir(api) -> str:
    return os.path.dirname(api.prefs_json)


@pytest.fixture
def hub(tmp_path):
    """A real `/v1` app over a tmp data dir, for the route-level contracts."""
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    app = create_app(api)
    app.state.source_clients = sources_mod.SourceClients()
    with TestClient(app) as client:
        client.api = api
        yield client
    api.shutdown()


# ── the file itself ──────────────────────────────────────────────────────────


def test_missing_file_is_no_tokens_not_an_error(data_dir):
    """The overwhelmingly common case — nobody has configured a credential."""
    assert token_store.load_tokens(data_dir) == {}


def test_a_round_trip_keeps_what_was_written(data_dir):
    token_store.save_tokens(data_dir, {"shop": SECRET, "bench": "other"})
    assert token_store.load_tokens(data_dir) == {"shop": SECRET, "bench": "other"}


def test_the_file_is_not_world_readable(data_dir):
    """A shared machine is exactly where a plaintext credential file matters."""
    token_store.save_tokens(data_dir, {"shop": SECRET})
    mode = stat.S_IMODE(os.stat(token_store.token_path(data_dir)).st_mode)
    assert mode == 0o600, f"expected 0600, got {mode:o}"


def test_saving_nothing_removes_the_file(data_dir):
    """Nothing to protect means nothing on disk to leak."""
    token_store.save_tokens(data_dir, {"shop": SECRET})
    token_store.save_tokens(data_dir, {})
    assert not os.path.exists(token_store.token_path(data_dir))


def test_blank_entries_are_dropped_rather_than_stored(data_dir):
    token_store.save_tokens(data_dir, {"shop": "  ", "bench": "", "": SECRET})
    assert token_store.load_tokens(data_dir) == {}


def test_a_corrupt_file_degrades_to_no_tokens_and_does_not_raise(data_dir, caplog):
    """The registry is rebuilt on EVERY /v1 request. Raising here would take the
    whole app down over a file whose worst honest outcome is "re-enter your
    token"."""
    with open(token_store.token_path(data_dir), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with caplog.at_level(logging.WARNING):
        assert token_store.load_tokens(data_dir) == {}
    assert "unreadable" in caplog.text


def test_a_json_file_that_is_not_an_object_is_ignored(data_dir):
    with open(token_store.token_path(data_dir), "w", encoding="utf-8") as fh:
        fh.write('["not", "a", "mapping"]')
    assert token_store.load_tokens(data_dir) == {}


def test_nothing_ever_logs_the_token(data_dir, caplog):
    """Every function reports WHETHER a token exists, never what it is."""
    with caplog.at_level(logging.DEBUG):
        token_store.save_tokens(data_dir, {"shop": SECRET})
        token_store.load_tokens(data_dir)
        prefs = {"servers": [{"id": "bench", "url": "http://b.local:1", "token": SECRET}]}
        token_store.migrate_legacy_tokens(prefs, data_dir)
    assert SECRET not in caplog.text


# ── migration off the old home ───────────────────────────────────────────────


def test_migration_moves_a_legacy_token_and_strips_the_key(data_dir):
    prefs = {"servers": [{"id": "shop", "name": "Shop", "url": SHOP, "token": SECRET}]}
    assert token_store.migrate_legacy_tokens(prefs, data_dir) is True
    assert "token" not in prefs["servers"][0]
    assert token_store.load_tokens(data_dir) == {"shop": SECRET}


def test_migration_is_a_no_op_when_there_is_nothing_to_move(data_dir):
    prefs = {"servers": [{"id": "shop", "name": "Shop", "url": SHOP}]}
    assert token_store.migrate_legacy_tokens(prefs, data_dir) is False
    assert not os.path.exists(token_store.token_path(data_dir))


def test_the_token_file_wins_over_a_stale_preferences_copy(data_dir):
    """The file is the writer now. A preferences copy that still carries an old
    credential must not resurrect it over one the user has since changed."""
    token_store.save_tokens(data_dir, {"shop": "current"})
    prefs = {"servers": [{"id": "shop", "url": SHOP, "token": "stale"}]}
    token_store.migrate_legacy_tokens(prefs, data_dir)
    assert token_store.load_tokens(data_dir) == {"shop": "current"}


# ── /v1/preferences: the browser's round trip ────────────────────────────────


def test_get_preferences_never_serves_a_token(hub):
    """`GET /v1/preferences` hands the whole object to the browser on startup.
    A credential in it is readable from the page's console and visible in the
    DevTools network pane of every window the user opens."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", token=SECRET)
    body = hub.get("/v1/preferences").text
    assert SECRET not in body
    assert hub.get("/v1/preferences").json()["servers"][0]["id"] == "shop"


def test_get_preferences_strips_a_legacy_token_it_has_not_migrated_yet(hub):
    """Belt to the token file's braces: a preferences.json written by the build
    that stored tokens in the roster, read before anything triggered the
    migration, must still not hand the credential to the browser."""
    prefs = hub.api.load_preferences()
    prefs["servers"] = [{"id": "shop", "name": "Shop", "url": SHOP, "token": SECRET}]
    hub.api.save_preferences(prefs)

    assert SECRET not in hub.get("/v1/preferences").text
    # And the file on disk is untouched by a mere GET — the registry's migration
    # owns rewriting it, not a read path.
    assert SECRET in open(hub.api.prefs_json, encoding="utf-8").read()


def test_a_preferences_save_cannot_erase_a_token(hub):
    """THE bug this whole change exists to fix.

    `js/store.js` posts its whole in-memory preferences object, and its roster
    loader knows only `{id, name, url}`. While the token lived beside the roster,
    every save of any unrelated preference — a slider nudge — erased it, and the
    symptom was a server that had worked yesterday 401ing today with nothing on
    screen to say why. It cannot happen now: the token is not in that file.
    """
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", token=SECRET)

    roster = hub.get("/v1/preferences").json()["servers"]
    assert hub.put("/v1/preferences", json={"servers": roster, "ui_zoom": 1.25}).status_code == 200

    assert sources_mod.load_registry_from_api(hub.api).require("shop").token == SECRET


def test_a_token_posted_to_preferences_is_dropped_not_persisted(hub, caplog):
    """`/v1/sources` is the only writer of a credential. One arriving here — a
    stale client, a hand-rolled PUT — must not land back in the file we spent
    the effort keeping clean."""
    with caplog.at_level(logging.WARNING):
        hub.put("/v1/preferences", json={
            "servers": [{"id": "shop", "name": "Shop", "url": SHOP, "token": SECRET}],
        })
    assert SECRET not in open(hub.api.prefs_json, encoding="utf-8").read()
    assert "dropped" in caplog.text
    assert SECRET not in caplog.text, "the warning must name the key, never the value"


def test_the_roster_survives_the_round_trip_for_a_server_with_no_token(hub):
    """The no-credential case — every existing user — is byte-identical."""
    sources_mod.add_source(hub.api, url=SHOP, source_id="shop", name="Shop")
    before = json.loads(open(hub.api.prefs_json, encoding="utf-8").read())["servers"]
    hub.put("/v1/preferences", json={"servers": hub.get("/v1/preferences").json()["servers"]})
    after = json.loads(open(hub.api.prefs_json, encoding="utf-8").read())["servers"]
    assert before == after == [{"id": "shop", "name": "Shop", "url": SHOP}]
