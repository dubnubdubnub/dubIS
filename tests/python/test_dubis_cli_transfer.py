"""`dubis transfer` — move stock of one part between two configured sources.

Three real /v1 servers, no HTTP mocking, following
`tests/python/test_dubis_cli_server_select.py`: a *hub* whose roster names two
peers (`alpha`, `beta`), each holding different parts. Which server's stock moves
is therefore proof of which source the hub routed each write to, and the peer
names are invented here — nothing in this file can reach fremont, aspen or
reality-labs, because the only roster it ever builds points at loopback ports
bound by `start_live_server`.

These servers are in-process over tmp directories rather than a subprocess
started with `--test-source <tag> --rollback-on-exit`: that mode exists to clean
up a *shared* data directory, and each of these directories is discarded when the
fixture ends. The `--source` tag is still exercised, because the tag is a product
feature (an undoable session), not a test fixture — see
`test_rollback_source_undoes_the_whole_session`.

The tests are order-independent on purpose: each one reads the current quantity
off the server instead of assuming what the previous test left behind.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from tests.python.helpers import make_api, make_part, write_ledger
from tests.python.server.conftest import start_live_server
from tools.dubis_client import V1Client

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "tools" / "dubis-cli" / "dubis.py"

if "dubis_cli" in sys.modules:
    dubis_cli = sys.modules["dubis_cli"]
else:
    _spec = importlib.util.spec_from_file_location("dubis_cli", str(CLI_PATH))
    dubis_cli = importlib.util.module_from_spec(_spec)
    sys.modules["dubis_cli"] = dubis_cli
    _spec.loader.exec_module(dubis_cli)

ALPHA = "srv_alpha"
BETA = "srv_beta"
# What a person types, and what the store actually calls the part. The whole
# point of resolving on both sides is that these are not the same string.
TYPED = "TMR2615F"
KEY = "TMR2615F-AAC-1.500-500"


class Fleet:
    """The hub's data dir plus the three APIs, so a test can read the CSVs."""

    def __init__(self, hub_dir, hub, alpha, beta):
        self.hub_dir = hub_dir
        self.hub = hub
        self.alpha = alpha
        self.beta = beta


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    alpha_dir = tmp_path_factory.mktemp("alpha")
    alpha = make_api(alpha_dir)
    write_ledger(alpha, [
        make_part(lcsc="", mpn=KEY, qty=50, digikey="628-1234-ND",
                  desc="Linear Hall-Effect Sensor 1.5T", pkg="SOIC-8",
                  unit_price="0.4200", ext_price="21.00"),
        make_part(lcsc="", mpn="SOLO-9", qty=5, desc="Rare thing"),
        make_part(lcsc="", mpn="TWICE-1", qty=20, digikey="DK-XPN-1",
                  desc="Both sides have it"),
        make_part(lcsc="", mpn="XPN-1", qty=15, digikey="DK-ONLY-BETA",
                  desc="Beta knows it only by the DigiKey number"),
    ])
    a_server, a_thread, alpha_url = start_live_server(alpha)

    beta_dir = tmp_path_factory.mktemp("beta")
    beta = make_api(beta_dir)
    write_ledger(beta, [
        make_part(lcsc="", mpn="TWICE-1", qty=3, desc="Both sides have it"),
        make_part(lcsc="", mpn="", digikey="DK-ONLY-BETA", qty=2,
                  desc="found only by its DigiKey number"),
        make_part(lcsc="C-BETA-ONLY", qty=7, desc="Beta-only part"),
    ])
    b_server, b_thread, beta_url = start_live_server(beta)

    hub_dir = tmp_path_factory.mktemp("hub")
    hub = make_api(hub_dir)
    write_ledger(hub, [make_part(lcsc="C-HUB", qty=4, desc="Hub-only part")])
    prefs = hub.load_preferences()
    prefs["servers"] = [
        {"id": ALPHA, "name": "alpha", "url": alpha_url},
        {"id": BETA, "name": "beta", "url": beta_url},
    ]
    hub.save_preferences(prefs)
    h_server, h_thread, hub_url = start_live_server(hub)
    (hub_dir / ".v1_port").write_text(hub_url.rsplit(":", 1)[1], encoding="utf-8")

    try:
        yield Fleet(hub_dir, hub, alpha, beta)
    finally:
        for server, thread, api in ((h_server, h_thread, hub),
                                    (a_server, a_thread, alpha),
                                    (b_server, b_thread, beta)):
            server.should_exit = True
            thread.join(timeout=5)
            api.shutdown()


@pytest.fixture
def cli(fleet, monkeypatch, capsys):
    monkeypatch.delenv("DUBIS_URL", raising=False)
    monkeypatch.delenv("DUBIS_SERVER", raising=False)

    def _run(*argv, expect=0):
        code = dubis_cli.main(["--data-dir", str(fleet.hub_dir), *argv])
        out = capsys.readouterr()
        assert code == expect, f"exit {code} (expected {expect}); stderr={out.err}"
        payload = json.loads(out.out) if out.out.strip() else None
        return payload, out.err

    _run.hub_dir = fleet.hub_dir
    return _run


@pytest.fixture
def posts(monkeypatch):
    """Every POST the CLI sends, with the source header it carried.

    The order assertion needs the wire, not the payload: `transfer` reports what
    it did, and a report written after both writes cannot distinguish "credited
    then debited" from "debited then credited" — which is the one distinction
    that decides whether a failure in the middle double-counts the stock or
    loses it.
    """
    calls: list[dict] = []
    original = V1Client.post

    def spy(self, path, json=None):
        calls.append({
            "path": path,
            "source": self._client.headers.get("X-Dubis-Source"),
            "body": json,
        })
        return original(self, path, json)

    monkeypatch.setattr(V1Client, "post", spy)
    return calls


def qty(cli, server: str, part: str) -> int | None:
    payload, _ = cli("--server", server, "parts", "list")
    for row in payload:
        if part in {row.get(f) for f in ("lcsc", "mpn", "digikey", "mouser", "pololu")}:
            return row["qty"]
    return None


def row_on(cli, server: str, part: str) -> dict | None:
    payload, _ = cli("--server", server, "parts", "list")
    for row in payload:
        if part in {row.get(f) for f in ("lcsc", "mpn", "digikey", "mouser", "pololu")}:
            return row
    return None


def writes(calls) -> list[dict]:
    return [c for c in calls if c["path"].endswith("/adjust") or c["path"] == "/v1/parts"]


# ── dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_resolves_the_key_on_both_sides(cli, posts):
    payload, _ = cli("transfer", TYPED, "--quantity", "10",
                     "--from", "alpha", "--server", "beta", "--dry-run")
    assert payload["dry_run"] is True
    resolved = payload["resolved"]
    assert resolved["requested"] == TYPED
    assert resolved["source"]["part_key"] == KEY, "the store's key, not what was typed"
    assert resolved["source"]["server"] == "alpha"
    assert resolved["destination"]["known"] is False
    assert resolved["destination"]["server"] == "beta", "the destination is named even when it has never heard of the part"
    assert resolved["destination_will_create"] is True


def test_dry_run_prints_both_halves_of_the_plan(cli, posts):
    payload, _ = cli("transfer", TYPED, "--quantity", "10",
                     "--from", "alpha", "--server", "beta", "--dry-run")
    plan = payload["plan"]
    assert [step["path"] for step in plan] == [
        "/v1/parts",                    # identity on the destination
        f"/v1/parts/{KEY}/adjust",      # credit the destination
        f"/v1/parts/{KEY}/adjust",      # ... then debit the source
    ]
    assert [step["side"] for step in plan] == ["destination", "destination", "source"]
    assert plan[0]["body"]["mpn"] == KEY
    assert plan[0]["body"]["digikey"] == "628-1234-ND"
    assert plan[1]["body"]["adj_type"] == "add"
    assert plan[1]["body"]["quantity"] == 10
    assert plan[2]["body"]["adj_type"] == "remove"
    assert payload["expected"] == {"alpha": 40, "beta": 10}


def test_dry_run_sends_no_write(cli, posts, fleet):
    cli("transfer", TYPED, "--quantity", "10", "--from", "alpha", "--server", "beta",
        "--dry-run")
    assert writes(posts) == [], f"a dry run wrote: {posts!r}"
    assert qty(cli, "alpha", KEY) == 50
    assert row_on(cli, "beta", KEY) is None, "the destination must still not know the part"


# ── the real transfer ────────────────────────────────────────────────────────


def test_transfer_moves_stock_and_verifies_both_sides(cli):
    before_alpha, before_beta = qty(cli, "alpha", KEY), qty(cli, "beta", KEY) or 0
    payload, _ = cli("transfer", TYPED, "--quantity", "10",
                     "--from", "alpha", "--server", "beta", "--source", "xfer-check")
    assert payload["verified"] is True
    table = {row["server"]: row for row in payload["verification"]}
    assert table["alpha"]["side"] == "source"
    assert table["alpha"]["expected"] == before_alpha - 10
    assert table["alpha"]["actual"] == before_alpha - 10
    assert table["beta"]["expected"] == before_beta + 10
    assert table["beta"]["actual"] == before_beta + 10
    assert qty(cli, "alpha", KEY) == table["alpha"]["actual"]
    assert qty(cli, "beta", KEY) == table["beta"]["actual"]


def test_the_destination_is_credited_before_the_source_is_debited(cli, posts):
    """The order is the design, not a detail.

    If the second write fails, the parts are counted twice: visible in both
    grids, correctable by hand. If the first went second instead, the same
    failure leaves stock that physically exists and is recorded nowhere.
    """
    cli("transfer", TYPED, "--quantity", "1", "--from", "alpha", "--server", "beta",
        "--source", "xfer-order")
    adjustments = [c for c in posts if c["path"].endswith("/adjust")]
    assert [c["body"]["adj_type"] for c in adjustments] == ["add", "remove"]
    assert [c["source"] for c in adjustments] == [BETA, ALPHA], (
        "each write must carry its own side's source header, or both halves land "
        "on whichever server the hub defaults to"
    )


def test_transfer_carries_identity_to_a_destination_that_does_not_know_it(cli, fleet):
    payload, _ = cli("transfer", "SOLO-9", "--quantity", "2",
                     "--from", "alpha", "--server", "beta", "--source", "xfer-id")
    assert payload["created_on_destination"] is True

    row = row_on(cli, "beta", "SOLO-9")
    assert row is not None
    assert row["mpn"] == "SOLO-9"
    assert row["description"] == "Rare thing"
    assert row["package"] == "0402"
    assert row["qty"] == 2

    # ...and it arrived without the destination having bought it.
    assert not os.path.exists(os.path.join(fleet.beta.events_dir, "price_observations.csv")), (
        "the destination's events dir carries a price observation: the transferred "
        "part now looks as though beta sold it"
    )
    prices, _ = cli("--server", "beta", "prices", "SOLO-9")
    assert prices["distributors"] == {}


def test_the_digikey_number_travels_too(cli, fleet):
    """A part's identity is the whole PN set, not just the key.

    `transfer` builds the identity from `part_numbers()`, the same helper
    `search` and `get` use, so the numbers a reel label carries cannot be
    dropped on the way to the other server.
    """
    cli("transfer", TYPED, "--quantity", "1", "--from", "alpha", "--server", "beta",
        "--source", "xfer-pn")
    row = row_on(cli, "beta", KEY)
    assert row["digikey"] == "628-1234-ND"
    assert row["manufacturer"] == ""  # never on the source row either


def test_a_destination_that_already_knows_the_part_just_moves_stock(cli):
    before_beta = qty(cli, "beta", "TWICE-1")
    payload, _ = cli("transfer", "TWICE-1", "--quantity", "4",
                     "--from", "alpha", "--server", "beta", "--source", "xfer-known")
    assert payload["created_on_destination"] is False
    assert payload["resolved"]["destination"]["part_key"] == "TWICE-1"
    assert qty(cli, "beta", "TWICE-1") == before_beta + 4
    assert payload["verified"] is True


# ── refusals ─────────────────────────────────────────────────────────────────


def test_refuses_to_remove_more_than_the_source_holds(cli, posts, fleet):
    before = qty(cli, "alpha", "SOLO-9")
    _, err = cli("transfer", "SOLO-9", "--quantity", str(before + 500),
                 "--from", "alpha", "--server", "beta", expect=3)
    assert str(before) in err
    assert writes(posts) == [], (
        "removing more than is on hand must fail before the first write: the "
        "server floors a removal at zero, so the difference would vanish"
    )
    assert qty(cli, "alpha", "SOLO-9") == before
    assert row_on(cli, "beta", "SOLO-9") is not None  # created by an earlier test, qty unchanged


def test_refuses_a_zero_or_negative_quantity(cli, posts):
    cli("transfer", TYPED, "--quantity", "0", "--from", "alpha", "--server", "beta",
        expect=3)
    cli("transfer", TYPED, "--quantity", "-3", "--from", "alpha", "--server", "beta",
        expect=3)
    assert writes(posts) == []


@pytest.mark.parametrize("selector", ["merged", f"{ALPHA},{BETA}", "alpha,beta"])
def test_refuses_a_view_as_either_end(cli, posts, selector):
    """"Which server" has to name one store; a merged view has no stock to move.

    The hub refuses a write against a view too, but only after the CLI has read
    the part from somewhere arbitrary — so the CLI refuses first, and says which
    end was the problem.
    """
    _, err = cli("transfer", TYPED, "--quantity", "1", "--from", selector,
                 "--server", "beta", expect=3)
    assert "merged view" in err
    _, err = cli("transfer", TYPED, "--quantity", "1", "--from", "alpha",
                 "--server", selector, expect=3)
    assert "merged view" in err
    assert writes(posts) == []


def test_refuses_the_same_server_on_both_ends(cli, posts):
    _, err = cli("transfer", TYPED, "--quantity", "1", "--from", "alpha",
                 "--server", "alpha", expect=3)
    assert "two different sources" in err
    _, err = cli("transfer", TYPED, "--quantity", "1", "--from", "local",
                 "--server", "local", expect=3)
    assert "two different sources" in err
    assert writes(posts) == []


def test_refuses_a_part_the_source_does_not_have(cli, posts):
    _, err = cli("transfer", "GHOST-404", "--quantity", "1",
                 "--from", "alpha", "--server", "beta", expect=3)
    assert "matches no part on alpha" in err
    assert writes(posts) == []


def test_a_destination_that_knows_the_part_by_another_number_is_found_not_duplicated(cli):
    """Two stores, one reel, different numbers recorded on each.

    Searching the destination for what the caller typed would miss it and create
    a SECOND part for the same physical stock — the one outcome a transfer should
    never produce, because from then on the two rows are moved by hand forever.
    """
    before_beta = qty(cli, "beta", "DK-ONLY-BETA")
    payload, _ = cli("transfer", "XPN-1", "--quantity", "5", "--from", "alpha",
                     "--server", "beta", "--source", "xfer-alias")
    assert payload["created_on_destination"] is False
    assert payload["resolved"]["destination"]["part_key"] == "DK-ONLY-BETA"
    assert qty(cli, "beta", "XPN-1") is None, "no second part was created"
    assert qty(cli, "beta", "DK-ONLY-BETA") == before_beta + 5


def test_an_ambiguous_fragment_is_refused_rather_than_guessed(cli, posts):
    """`TWICE` is a fragment of TWICE-1 on both sides; a fragment that names two
    rows on one side has no answer, and moving stock for the wrong part on the
    server that is not in front of you is the expensive kind of wrong."""
    _, err = cli("transfer", "T", "--quantity", "1", "--from", "alpha",
                 "--server", "beta", expect=3)
    assert "several parts" in err or "matches no part" in err
    assert writes(posts) == []


def test_from_is_required(cli):
    """argparse's own exit 2, raised as SystemExit from inside main()."""
    with pytest.raises(SystemExit) as excinfo:
        dubis_cli.main(["--data-dir", str(cli.hub_dir), "transfer", TYPED, "--quantity", "1"])
    assert excinfo.value.code == 2


# ── the session is undoable ──────────────────────────────────────────────────


def test_every_adjustment_carries_one_source_tag(cli):
    cli("transfer", "TWICE-1", "--quantity", "2", "--from", "alpha", "--server", "beta",
        "--source", "xfer-tag")
    for server in ("alpha", "beta"):
        history, _ = cli("--server", server, "parts", "get-history", "TWICE-1")
        assert history[-1]["source"] == "xfer-tag", (
            f"{server}'s latest movement is not tagged, so rollback-source would "
            "leave half the transfer in place"
        )


def test_rollback_source_undoes_the_whole_session(cli):
    before_alpha, before_beta = qty(cli, "alpha", "TWICE-1"), qty(cli, "beta", "TWICE-1")
    cli("transfer", "TWICE-1", "--quantity", "3", "--from", "alpha", "--server", "beta",
        "--source", "xfer-undo")
    assert qty(cli, "alpha", "TWICE-1") == before_alpha - 3
    assert qty(cli, "beta", "TWICE-1") == before_beta + 3

    cli("adjustments", "rollback-source", "xfer-undo", "--server", "alpha")
    cli("adjustments", "rollback-source", "xfer-undo", "--server", "beta")
    assert qty(cli, "alpha", "TWICE-1") == before_alpha
    assert qty(cli, "beta", "TWICE-1") == before_beta


# ── the destination defaults to the hub's own store ──────────────────────────


def test_destination_defaults_to_the_hub_default_and_says_so(cli):
    """No `--server` is not "somewhere arbitrary": it is the hub's persisted
    default, read back off `GET /v1/sources` and named in the output."""
    payload, _ = cli("transfer", "TWICE-1", "--quantity", "1", "--from", "beta",
                     "--source", "xfer-default", "--dry-run")
    assert payload["resolved"]["source"]["selector"] == BETA
    assert payload["resolved"]["destination"]["selector"] == "local"
    assert payload["resolved"]["destination"]["known"] is False
    before = qty(cli, "local", "TWICE-1") or 0
    assert payload["expected"]["Local"] == before + 1, "the hub's own store is `local`"
