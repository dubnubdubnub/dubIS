"""server/sources.py — the roster, its validation, and how `server_url` and
`active_source` coexist.

Two things are being pinned. The coexistence: `remote_mode.resolve_remote_base_url`,
`app_restart.relaunch_env` and `tools/dubis-cli` all read `server_url` and know
nothing about sources, so every write here has to leave that key meaning exactly
what it always meant — "the single remote server my data comes from". And the
fact that what these keys hold is a **default**, not live state: nothing in this
module is mutable server-side, so no window can move another window's data.
"""

from __future__ import annotations

import json
import os

import pytest

from dubis_errors import SourceConfigError, SourceNotFoundError
from remote_mode import resolve_remote_base_url
from server import sources as sources_mod
from server import token_store
from tests.python.helpers import make_api

BENCH = "http://bench.local:7891"
SHOP = "https://dubis-server.example.ts.net"


@pytest.fixture
def api(tmp_path):
    inst = make_api(tmp_path)
    yield inst
    inst.shutdown()


def _prefs(api) -> dict:
    return json.loads(open(api.prefs_json, encoding="utf-8").read())


# ── normalization mirrors js/servers-logic.js ────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [
    ("http://x", "http://x"),
    ("https://x/", "https://x"),
    ("https://x///", "https://x"),
    ("  https://x  ", "https://x"),
    ("HTTP://X", "HTTP://X"),
    ("x.local", ""),          # no scheme -> would resolve against the hub's own origin
    ("ftp://x", ""),
    ("", ""),
    (None, ""),
])
def test_normalize_url_matches_the_js_rule(raw, expected):
    assert sources_mod.normalize_url(raw) == expected


# ── loading ──────────────────────────────────────────────────────────────────


def test_local_is_always_present_even_with_no_preferences():
    registry = sources_mod.load_registry({})
    assert [s.id for s in registry.sources] == ["local"]
    assert registry.default == "local"
    assert registry.default_url == ""


def test_roster_reads_the_existing_three_key_shape():
    registry = sources_mod.load_registry({"servers": [{"id": "bench", "name": "Bench", "url": BENCH}]})
    bench = registry.require("bench")
    assert (bench.name, bench.url, bench.token, bench.enabled) == ("Bench", BENCH, "", True)


def test_optional_token_and_enabled_are_read_when_present():
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "name": "Shop", "url": SHOP, "token": "t0k", "enabled": False}],
    })
    shop = registry.require("shop")
    assert shop.token == "t0k"
    assert shop.enabled is False
    assert shop not in registry.enabled_sources


@pytest.mark.parametrize("roster", [
    [{"id": "", "url": BENCH}],                                    # missing id
    [{"id": "local", "url": BENCH}],                               # reserved id
    [{"id": "a", "url": "not-a-url"}],                             # no scheme
    ["a string, not an object"],
    [{"id": "a", "url": BENCH}, {"id": "a", "url": SHOP}],         # duplicate id
    [{"id": "a", "url": BENCH}, {"id": "b", "url": BENCH + "/"}],  # duplicate url
])
def test_malformed_entries_are_dropped_not_crashed(roster):
    """preferences.json is hand-editable; a bad entry must not break every read."""
    registry = sources_mod.load_registry({"servers": roster})
    assert len(registry.sources) <= 2  # local, plus at most the one good entry
    assert registry.sources[0].is_local


def test_servers_that_is_not_a_list_is_ignored():
    assert [s.id for s in sources_mod.load_registry({"servers": "nope"}).sources] == ["local"]


# ── server_url <-> active_source ─────────────────────────────────────────────


def test_the_default_is_derived_from_server_url_when_active_source_is_absent():
    """A preferences file written by the existing JS server picker only has
    `server_url`. It must still select that server."""
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "name": "Shop", "url": SHOP}],
        "server_url": SHOP,
    })
    assert registry.default == "shop"
    assert registry.default_source.url == SHOP


def test_a_server_url_outside_the_roster_is_adopted_as_a_source():
    """`DUBIS_URL`-seeded launches point at a server nobody added to the roster;
    ignoring it would silently serve local data while claiming otherwise."""
    registry = sources_mod.load_registry({"server_url": SHOP})
    adopted = registry.default_source
    assert adopted.url == SHOP
    assert adopted.id != "local"
    assert registry.default == adopted.id


def test_active_source_key_wins_over_server_url_when_both_are_set():
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "url": SHOP}, {"id": "bench", "url": BENCH}],
        "server_url": SHOP,
        "active_source": "bench",
    })
    assert registry.default == "bench"


def test_merged_is_a_valid_default_with_no_single_server_url():
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "url": SHOP}], "active_source": "merged",
    })
    assert registry.default_is_merged
    assert registry.default_source is None
    assert registry.default_url == ""


def test_an_unknown_default_falls_back_rather_than_breaking_every_request():
    registry = sources_mod.load_registry({"active_source": "ghost"})
    assert registry.default == "local"


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_add_source_persists_the_js_compatible_shape(api):
    _registry, source = sources_mod.add_source(api, url=BENCH, source_id="bench", name="Bench")
    assert source.id == "bench"
    entries = _prefs(api)["servers"]
    assert entries == [{"id": "bench", "name": "Bench", "url": BENCH}]


def test_a_token_never_lands_in_preferences(api):
    """preferences.json is served whole to the browser by `GET /v1/preferences`
    and posted back whole by js/store.js. A credential in it is readable from
    the page's console — and, because the JS roster loader knows only
    `{id, name, url}`, was erased by the next save of any unrelated preference.
    Both problems are gone by construction: the token is not in this file."""
    sources_mod.add_source(api, url=BENCH, source_id="bench", token="t0k", enabled=False)
    entry = _prefs(api)["servers"][0]
    assert "token" not in entry
    assert "t0k" not in json.dumps(_prefs(api))
    assert entry["enabled"] is False

    sources_mod.add_source(api, url=SHOP, source_id="shop")
    plain = _prefs(api)["servers"][1]
    assert set(plain) == {"id", "name", "url"}, "a plain entry must stay byte-compatible with the JS shape"


def test_the_token_is_still_loaded_back_onto_the_source(api):
    """Keeping it out of preferences is only half the job — the registry still
    has to hand it to the outbound client, or the source silently 401s."""
    sources_mod.add_source(api, url=BENCH, source_id="bench", token="t0k")
    assert sources_mod.load_registry_from_api(api).require("bench").token == "t0k"


def test_removing_a_source_takes_its_token_with_it(api):
    """A token left behind under a dead id would be handed straight to whoever
    next reused that id."""
    sources_mod.add_source(api, url=BENCH, source_id="bench", token="t0k")
    sources_mod.remove_source(api, "bench")
    assert token_store.load_tokens(os.path.dirname(api.prefs_json)) == {}


def test_a_legacy_token_in_preferences_is_migrated_out(api):
    """The upgrade path for anyone who ran the build that stored tokens in the
    roster. Read once, moved, and the roster rewritten without it."""
    prefs = api.load_preferences()
    prefs["servers"] = [{"id": "bench", "name": "Bench", "url": BENCH, "token": "legacy"}]
    api.save_preferences(prefs)

    assert sources_mod.load_registry_from_api(api).require("bench").token == "legacy"
    assert "legacy" not in json.dumps(_prefs(api))
    assert token_store.load_tokens(os.path.dirname(api.prefs_json)) == {"bench": "legacy"}


def test_add_source_derives_an_id_and_a_name_from_the_url(api):
    _registry, source = sources_mod.add_source(api, url=SHOP)
    assert source.id == "dubis-server-example-ts-net"
    assert source.name == "dubis-server.example.ts.net"


def test_add_source_rejects_a_url_without_a_scheme(api):
    with pytest.raises(SourceConfigError, match="http"):
        sources_mod.add_source(api, url="bench.local:7891")


def test_add_source_rejects_a_duplicate_id(api):
    sources_mod.add_source(api, url=BENCH, source_id="bench")
    with pytest.raises(SourceConfigError, match="already exists"):
        sources_mod.add_source(api, url=SHOP, source_id="bench")


def test_add_source_rejects_a_duplicate_url(api):
    sources_mod.add_source(api, url=BENCH, source_id="bench")
    with pytest.raises(SourceConfigError, match="already points at"):
        sources_mod.add_source(api, url=BENCH + "/", source_id="bench2")


@pytest.mark.parametrize("reserved", ["local", "merged"])
def test_add_source_rejects_the_reserved_ids(api, reserved):
    with pytest.raises(SourceConfigError, match="reserved"):
        sources_mod.add_source(api, url=BENCH, source_id=reserved)


def test_update_source_changes_only_what_it_is_given(api):
    sources_mod.add_source(api, url=BENCH, source_id="bench", name="Bench", token="t0k")
    _registry, updated = sources_mod.update_source(api, "bench", enabled=False)
    assert (updated.name, updated.url, updated.token, updated.enabled) == ("Bench", BENCH, "t0k", False)


def test_update_source_rejects_a_url_that_collides(api):
    sources_mod.add_source(api, url=BENCH, source_id="bench")
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    with pytest.raises(SourceConfigError, match="already points at"):
        sources_mod.update_source(api, "shop", url=BENCH)


def test_local_can_be_neither_edited_nor_removed(api):
    with pytest.raises(SourceConfigError, match="local"):
        sources_mod.update_source(api, "local", name="nope")
    with pytest.raises(SourceConfigError, match="local"):
        sources_mod.remove_source(api, "local")


def test_operations_on_an_unknown_id_are_404_shaped(api):
    with pytest.raises(SourceNotFoundError):
        sources_mod.update_source(api, "ghost", name="x")
    with pytest.raises(SourceNotFoundError):
        sources_mod.remove_source(api, "ghost")
    with pytest.raises(SourceNotFoundError):
        sources_mod.set_default(api, "ghost")


# ── switching ────────────────────────────────────────────────────────────────


def test_setting_the_default_writes_both_keys_so_the_cli_follows(api):
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    registry = sources_mod.set_default(api, "shop")

    assert registry.default == "shop"
    prefs = _prefs(api)
    assert prefs["active_source"] == "shop"
    assert prefs["server_url"] == SHOP
    # The whole point: remote_mode (and therefore tools/dubis-cli and
    # app_restart) keeps answering without knowing sources exist.
    assert resolve_remote_base_url({}, prefs) == SHOP


def test_defaulting_back_to_local_clears_server_url(api):
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    sources_mod.set_default(api, "shop")
    sources_mod.set_default(api, "local")

    prefs = _prefs(api)
    assert prefs["active_source"] == "local"
    assert prefs["server_url"] == ""
    assert resolve_remote_base_url({}, prefs) is None


def test_merged_writes_an_empty_server_url_pointing_readers_at_the_hub(api):
    """There is no single remote url for a merged view. `""` sends
    remote_mode/tools/dubis-cli to the hub itself via `<data_dir>/.v1_port` —
    which is exactly where the merged view is served, so the CLI sees the merge
    rather than one arbitrary member of it."""
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    sources_mod.set_default(api, "merged")

    prefs = _prefs(api)
    assert prefs["active_source"] == "merged"
    assert prefs["server_url"] == ""
    assert resolve_remote_base_url({}, prefs) is None


def test_a_disabled_source_cannot_be_made_the_default(api):
    sources_mod.add_source(api, url=SHOP, source_id="shop", enabled=False)
    with pytest.raises(SourceConfigError, match="disabled"):
        sources_mod.set_default(api, "shop")


def test_removing_the_default_source_resets_the_default_to_local(api):
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    sources_mod.set_default(api, "shop")

    registry = sources_mod.remove_source(api, "shop")
    assert registry.default == "local"
    assert [s.id for s in registry.sources] == ["local"]
    prefs = _prefs(api)
    assert prefs["server_url"] == "", "a removed source must not stay in server_url"


def test_removing_another_source_leaves_the_default_alone(api):
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    sources_mod.add_source(api, url=BENCH, source_id="bench")
    sources_mod.set_default(api, "shop")

    registry = sources_mod.remove_source(api, "bench")
    assert registry.default == "shop"
    assert _prefs(api)["server_url"] == SHOP


def test_roster_writes_preserve_unrelated_preferences(api):
    api.save_preferences({"ui_zoom": 1.25, "distributor_filter": ["lcsc"]})
    sources_mod.add_source(api, url=BENCH, source_id="bench")

    prefs = _prefs(api)
    assert prefs["ui_zoom"] == 1.25
    assert prefs["distributor_filter"] == ["lcsc"]


# ── the launch-time seed (app_launch.ACTIVE_SOURCE_SEAM) ─────────────────────


@pytest.fixture(autouse=True)
def _no_leftover_seed():
    """The seed is process state by necessity (see below); never leak it."""
    sources_mod._reset_boot_default_for_tests()
    yield
    sources_mod._reset_boot_default_for_tests()


def test_app_launch_can_resolve_the_seam_it_names():
    """`app_launch.py` looks this function up by name at boot. A rename here
    would degrade an env-launched session to local data with only a log line."""
    import app_launch

    module = __import__(app_launch.SEAM_MODULE, fromlist=[app_launch.SEAM_FUNCTION])
    assert getattr(module, app_launch.SEAM_FUNCTION, None) is sources_mod.seed_initial_active_source
    assert app_launch.seed_initial_active_source(SHOP) == "seeded"


def test_the_boot_default_outranks_both_persisted_keys(api):
    """`DUBIS_URL` beats preferences — which is the whole reason the seam exists,
    since the env var is deliberately never written to preferences."""
    sources_mod.add_source(api, url=BENCH, source_id="bench")
    sources_mod.set_default(api, "bench")

    sources_mod.seed_initial_active_source(SHOP)
    registry = sources_mod.load_registry_from_api(api)
    assert registry.default_source.url == SHOP


def test_the_boot_default_is_never_written_to_preferences(api):
    """`app_restart.relaunch_env` strips DUBIS_URL so a one-off override cannot
    outlive the session; persisting it here would smuggle it back in."""
    sources_mod.seed_initial_active_source(SHOP)
    sources_mod.add_source(api, url=BENCH, source_id="bench")

    prefs = _prefs(api)
    assert prefs.get("server_url", "") == ""
    assert "active_source" not in prefs


def test_the_boot_default_still_outranks_a_later_preference_write(api):
    """`DUBIS_URL` beats preferences, exactly as `remote_mode` documents — an env
    override is meant to win for the session it was given for. The preference
    write is still recorded, for the next launch without the env var."""
    sources_mod.seed_initial_active_source(SHOP)
    sources_mod.set_default(api, "local")

    assert _prefs(api)["server_url"] == "", "the preference write landed"
    assert sources_mod.load_registry_from_api(api).default_source.url == SHOP


def test_an_unusable_boot_default_is_loud_but_not_fatal(api, caplog):
    """Dying on the boot thread would leave the user staring at a splash that
    times out — a worse message about the same typo."""
    with caplog.at_level("ERROR"):
        sources_mod.seed_initial_active_source("dubis-server.example.ts.net")
    assert "must start with http" in caplog.text
    assert sources_mod.load_registry_from_api(api).default == "local"


# ── the header's grammar (server/proxy.parse_source_header) ──────────────────


@pytest.mark.parametrize(("raw", "expected"), [
    ("shop", ["shop"]),
    ("bench,shop", ["bench", "shop"]),
    ("  bench , shop  ", ["bench", "shop"]),
    ("bench,,shop", ["bench", "shop"]),
    ("merged", ["merged"]),
    ("", []),
    ("   ", []),
    (None, []),
])
def test_parse_source_header(raw, expected):
    from server.proxy import parse_source_header

    assert parse_source_header(raw) == expected


def test_a_group_is_a_valid_default():
    """One vocabulary: the tab strip serializes a grouped tab to `shop,bench`,
    and `active_source` has to hold every selector the strip can emit — a
    default only the header understood would 404 every grouped tab click and
    leave `server_url` naming the single server picked before it."""
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "url": SHOP}, {"id": "bench", "url": BENCH}],
        "active_source": "shop,bench",
    })
    assert registry.default == "shop,bench"
    assert registry.default_selectors == ["shop", "bench"]
    assert registry.default_source is None, "a group has no single source..."
    assert registry.default_url == "", "...so it has no single server_url either"


def test_a_default_naming_an_unknown_member_falls_back():
    registry = sources_mod.load_registry({
        "servers": [{"id": "shop", "url": SHOP}],
        "active_source": "shop,ghost",
    })
    assert registry.default == "local"


# ── a hub may not be its own source ──────────────────────────────────────────


def _write_port_file(api, port: int) -> None:
    """Stand in for `server/run.py`'s startup write of `<data_dir>/.v1_port`."""
    import os

    with open(os.path.join(os.path.dirname(api.prefs_json), ".v1_port"), "w",
              encoding="utf-8") as f:
        f.write(str(port))


def test_a_hub_cannot_be_added_as_its_own_source(api):
    """A cycle: every merged read would ask itself for its own inventory, and
    two hubs listing each other are the same thing one hop further out."""
    _write_port_file(api, 7891)
    with pytest.raises(SourceConfigError, match="this server"):
        sources_mod.add_source(api, url="http://127.0.0.1:7891")


def test_another_dubis_on_another_loopback_port_is_a_perfectly_good_source(api):
    """The guard has to be narrow: bench and shop on one machine is the
    documented setup, so a loopback host alone must not disqualify a URL."""
    _write_port_file(api, 7891)
    _registry, source = sources_mod.add_source(api, url="http://127.0.0.1:7892")
    assert source.url == "http://127.0.0.1:7892"


def test_without_a_port_file_the_check_is_skipped_rather_than_guessed(api):
    """A server started with no data dir, or still binding, does not know its
    own address — and a guess would reject a legitimate source."""
    _registry, source = sources_mod.add_source(api, url="http://127.0.0.1:7891")
    assert source.url == "http://127.0.0.1:7891"


def test_the_url_of_the_default_source_cannot_be_edited_into_the_hub_itself(api):
    _write_port_file(api, 7891)
    sources_mod.add_source(api, url=SHOP, source_id="shop")
    with pytest.raises(SourceConfigError, match="this server"):
        sources_mod.update_source(api, "shop", url="http://localhost:7891")


# ── a source that is not in the roster is not editable ───────────────────────


def test_a_source_adopted_from_server_url_cannot_be_edited_or_removed(api):
    """It is not in `servers`, so persisting an edit would write a roster that
    never contained it — silently dropping the entry instead of changing it."""
    api.save_preferences({"server_url": SHOP})
    registry = sources_mod.load_registry_from_api(api)
    adopted = registry.default_source
    assert adopted.synthetic is True

    with pytest.raises(SourceConfigError, match="not in the roster"):
        sources_mod.update_source(api, adopted.id, name="nope")
    with pytest.raises(SourceConfigError, match="not in the roster"):
        sources_mod.remove_source(api, adopted.id)
