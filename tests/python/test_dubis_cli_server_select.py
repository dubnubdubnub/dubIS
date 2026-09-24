"""`dubis --server` / `DUBIS_SERVER`: choose which configured source the hub serves from.

Two real /v1 servers, no HTTP mocking: a *hub* whose roster names a second
server as `srv_bench_lab` / "bench-lab", and that second server holding
different parts. Which parts come back is therefore proof of which source the
hub served — the header was sent, and sent with the resolved id.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.python.helpers import make_api, make_part, write_ledger
from tests.python.server.conftest import start_live_server
from tools.dubis_client import (
    ServerSelectionError,
    V1Client,
    resolve_server,
    select_server,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "tools" / "dubis-cli" / "dubis.py"

if "dubis_cli" in sys.modules:
    dubis_cli = sys.modules["dubis_cli"]
else:
    _spec = importlib.util.spec_from_file_location("dubis_cli", str(CLI_PATH))
    dubis_cli = importlib.util.module_from_spec(_spec)
    sys.modules["dubis_cli"] = dubis_cli
    _spec.loader.exec_module(dubis_cli)

REMOTE_ID = "srv_bench_lab"
REMOTE_NAME = "bench-lab"


def _stop(server, thread, api):
    server.should_exit = True
    thread.join(timeout=5)
    api.shutdown()


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    """(hub data dir, hub base url). The hub's default stays `local`."""
    remote_dir = tmp_path_factory.mktemp("remote")
    remote_api = make_api(remote_dir)
    write_ledger(remote_api, [make_part(lcsc="C-REMOTE", qty=7, desc="Remote-only part")])
    r_server, r_thread, remote_url = start_live_server(remote_api)

    hub_dir = tmp_path_factory.mktemp("hub")
    hub_api = make_api(hub_dir)
    write_ledger(hub_api, [make_part(lcsc="C-HUB", qty=3, desc="Hub-only part")])
    prefs = hub_api.load_preferences()
    prefs["servers"] = [{"id": REMOTE_ID, "name": REMOTE_NAME, "url": remote_url}]
    hub_api.save_preferences(prefs)
    h_server, h_thread, hub_url = start_live_server(hub_api)
    (hub_dir / ".v1_port").write_text(hub_url.rsplit(":", 1)[1], encoding="utf-8")
    try:
        yield hub_dir, hub_url
    finally:
        _stop(h_server, h_thread, hub_api)
        _stop(r_server, r_thread, remote_api)


@pytest.fixture
def cli(hub, monkeypatch, capsys):
    hub_dir, _ = hub
    monkeypatch.delenv("DUBIS_URL", raising=False)
    monkeypatch.delenv("DUBIS_SERVER", raising=False)

    def _run(*argv, expect=0):
        code = dubis_cli.main(["--data-dir", str(hub_dir), *argv])
        out = capsys.readouterr()
        assert code == expect, f"exit {code} (expected {expect}); stderr={out.err}"
        payload = json.loads(out.out) if out.out.strip() else None
        return payload, out.err

    return _run


def _lcscs(payload) -> set[str]:
    return {m["part_key"] for m in payload["matches"]}


# ── header sent ──────────────────────────────────────────────────────────────


def test_no_server_is_served_from_the_hub_default(cli):
    payload, _ = cli("search")
    assert _lcscs(payload) == {"C-HUB"}


def test_server_id_sends_the_header_on_a_curated_command(cli):
    payload, _ = cli("--server", REMOTE_ID, "search")
    assert _lcscs(payload) == {"C-REMOTE"}


def test_server_sends_the_header_on_a_generated_command(cli):
    payload, _ = cli("parts", "list", "--server", REMOTE_NAME)
    assert {row["lcsc"] for row in payload} == {"C-REMOTE"}


def test_server_reaches_curated_get_which_aggregates_several_routes(cli):
    payload, _ = cli("--server", REMOTE_NAME, "get", "C-REMOTE")
    assert payload["part_key"] == "C-REMOTE"
    assert payload["qty"] == 7


def test_client_puts_the_header_on_every_request(hub):
    _, hub_url = hub
    client = V1Client(hub_url)
    try:
        select_server(client, REMOTE_NAME, "flag")
        assert client._client.headers["X-Dubis-Source"] == REMOTE_ID
        rows = client.get("/v1/parts")["inventory"]
        assert {r["lcsc"] for r in rows} == {"C-REMOTE"}
    finally:
        client.close()


def test_local_passes_through_without_a_roster_lookup(cli):
    payload, _ = cli("--server", "local", "search")
    assert _lcscs(payload) == {"C-HUB"}


def test_merged_passes_through_and_merges_parts(cli):
    payload, err = cli("--server", "merged", "search")
    assert _lcscs(payload) == {"C-HUB", "C-REMOTE"}
    assert "merged view" in err


# ── name -> id resolution ────────────────────────────────────────────────────


def test_name_resolves_to_id(hub):
    _, hub_url = hub
    client = V1Client(hub_url)
    try:
        sel = resolve_server(client, REMOTE_NAME, "flag")
        assert sel.selector == REMOTE_ID
        # Case and _/- are forgiven; an id works as-is; a comma list resolves per member.
        assert resolve_server(client, "Bench_Lab", "flag").selector == REMOTE_ID
        assert resolve_server(client, REMOTE_ID, "flag").selector == REMOTE_ID
        assert resolve_server(client, f"local, {REMOTE_NAME}", "flag").selector == (
            f"local,{REMOTE_ID}"
        )
        assert resolve_server(client, "MERGED", "flag").selector == "merged"
    finally:
        client.close()


def test_status_reports_the_answering_source(cli):
    payload, _ = cli("--server", REMOTE_NAME, "status")
    assert payload["source"] == {
        "selector": REMOTE_ID, "names": [REMOTE_NAME],
        "requested": REMOTE_NAME, "via": "flag",
    }
    assert payload["part_count"] == 1


def test_status_without_server_reports_the_hub_default(cli):
    payload, _ = cli("status")
    assert payload["source"]["via"] == "hub-default"
    assert payload["source"]["selector"] == "local"
    assert payload["source"]["names"] == ["Local"]


# ── unknown server ───────────────────────────────────────────────────────────


def test_unknown_server_exits_3_and_never_falls_back(cli):
    payload, err = cli("--server", "nowhere", "search", expect=3)
    assert payload is None, "nothing may be printed from the default source"
    assert "unknown server 'nowhere'" in err
    assert REMOTE_ID in err and REMOTE_NAME in err, "the message lists what exists"


def test_unknown_member_of_a_set_exits_3(cli):
    _, err = cli("--server", f"{REMOTE_NAME},nowhere", "parts", "list", expect=3)
    assert "unknown server 'nowhere'" in err


def test_unknown_server_raises_from_the_client(hub):
    _, hub_url = hub
    client = V1Client(hub_url)
    try:
        with pytest.raises(ServerSelectionError, match="unknown server"):
            select_server(client, "nowhere", "flag")
        assert "X-Dubis-Source" not in client._client.headers
    finally:
        client.close()


def test_empty_server_flag_exits_3(cli):
    cli("--server", " ", "search", expect=3)


def test_server_flag_on_serve_is_a_usage_error(cli):
    cli("serve", "--server", REMOTE_NAME, expect=2)


# ── env var precedence ───────────────────────────────────────────────────────


def test_env_var_selects_the_server(cli, monkeypatch):
    monkeypatch.setenv("DUBIS_SERVER", REMOTE_NAME)
    payload, _ = cli("search")
    assert _lcscs(payload) == {"C-REMOTE"}
    status, _ = cli("status")
    assert status["source"]["via"] == "env"


def test_flag_beats_env(cli, monkeypatch):
    monkeypatch.setenv("DUBIS_SERVER", REMOTE_NAME)
    payload, _ = cli("--server", "local", "search")
    assert _lcscs(payload) == {"C-HUB"}
    status, _ = cli("status", "--server", "local")
    assert status["source"]["via"] == "flag"


def test_unknown_env_server_also_exits_3(cli, monkeypatch):
    monkeypatch.setenv("DUBIS_SERVER", "nowhere")
    _, err = cli("search", expect=3)
    assert "unknown server 'nowhere'" in err


def test_blank_env_var_is_unset(cli, monkeypatch):
    monkeypatch.setenv("DUBIS_SERVER", "  ")
    payload, _ = cli("search")
    assert _lcscs(payload) == {"C-HUB"}


def test_dry_run_names_the_unresolved_server(cli):
    payload, _ = cli("--server", REMOTE_NAME, "--dry-run", "parts", "list")
    assert payload["server"] == REMOTE_NAME
