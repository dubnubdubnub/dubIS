"""tools/dubis-cli/dubis.py: argument handling, dispatch, and exit codes.

Ported from the retired tests/python/test_dubis_mcp_tools.py, which exercised
the same behaviours through MCP tool functions. The CLI's entry point is
main(argv) -> int, so tests call it directly; no subprocess, no stdio
transport. A real /v1 server backs every dispatching test — no HTTP mocking,
per this repo's live-server-harness policy.

The CLI module lives in a hyphenated directory and cannot be imported as a
package, so it is loaded by file location under a distinct name.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.python.helpers import make_api, make_part, write_ledger
from tests.python.server.conftest import start_live_server

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "tools" / "dubis-cli" / "dubis.py"

_spec = importlib.util.spec_from_file_location("dubis_cli", str(CLI_PATH))
dubis_cli = importlib.util.module_from_spec(_spec)
sys.modules["dubis_cli"] = dubis_cli
_spec.loader.exec_module(dubis_cli)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """One real /v1 server, seeded once, plus the data dir the CLI discovers
    it through. The part carries both an LCSC key and an MPN alias so
    canonical-key normalization has something to normalize."""
    tmp_path = tmp_path_factory.mktemp("dubis-cli")
    api = make_api(tmp_path)
    write_ledger(api, [
        make_part(lcsc="C1000", mpn="CL05B104KO5NNNC", qty=500,
                  desc="Capacitor MLCC 100nF 16V X7R 0402", pkg="0402",
                  unit_price="0.002", ext_price="1.00"),
        make_part(lcsc="", mpn="LM358DR", qty=25,
                  desc="Op-Amp Dual General Purpose", pkg="SOIC-8",
                  unit_price="0.30", ext_price="7.50"),
    ])
    server, thread, base_url = start_live_server(api)
    port = base_url.rsplit(":", 1)[1]
    (tmp_path / ".v1_port").write_text(port, encoding="utf-8")
    try:
        yield tmp_path
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        api.shutdown()


@pytest.fixture
def cli(live, monkeypatch, capsys):
    monkeypatch.delenv("DUBIS_URL", raising=False)

    def _run(*argv, expect=0):
        code = dubis_cli.main(["--data-dir", str(live), *argv])
        out = capsys.readouterr()
        assert code == expect, f"exit {code} (expected {expect}); stderr={out.err}"
        payload = json.loads(out.out) if out.out.strip() else None
        return payload, out.err

    return _run


# ── reads ────────────────────────────────────────────────────────────────────


def test_parts_list_returns_inventory(cli):
    payload, _ = cli("parts", "list")
    keys = {item.get("lcsc") or item.get("mpn") for item in payload}
    assert keys == {"C1000", "LM358DR"}


def test_json_flag_emits_single_line(live, monkeypatch, capsys):
    monkeypatch.delenv("DUBIS_URL", raising=False)
    assert dubis_cli.main(["--data-dir", str(live), "--json", "parts", "list"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_global_flag_accepted_after_subcommand(live, monkeypatch, capsys):
    """`parts list --json` must work as well as `--json parts list`; an agent
    should not have to remember which position argparse allows."""
    monkeypatch.delenv("DUBIS_URL", raising=False)
    assert dubis_cli.main(["--data-dir", str(live), "parts", "list", "--json"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


# ── writes, source tagging, prechecks ────────────────────────────────────────


def test_adjust_add_applies_and_tags_source(cli):
    cli("--source", "cli-test", "parts", "adjust", "C1000",
        "--adj-type", "add", "--quantity", "10")
    history, _ = cli("parts", "get-history", "C1000")
    latest = history[-1]
    assert latest["qty_delta"] == 10
    assert latest["source"] == "cli-test"


def test_source_defaults_to_cli(cli):
    cli("parts", "adjust", "C1000", "--adj-type", "add", "--quantity", "1")
    history, _ = cli("parts", "get-history", "C1000")
    assert history[-1]["source"] == "cli"


def test_quantity_is_sent_as_an_integer(cli):
    """The spec types quantity as integer; without coercion argparse would
    hand /v1 the string "3" and the delta would be wrong or rejected."""
    before, _ = cli("parts", "get-history", "C1000")
    cli("parts", "adjust", "C1000", "--adj-type", "add", "--quantity", "3")
    after, _ = cli("parts", "get-history", "C1000")
    assert after[-1]["qty_delta"] == 3
    assert len(after) == len(before) + 1


def test_adjust_add_on_unknown_part_exits_3(cli):
    """/v1 silently no-ops add/remove on an unknown key, returning a
    misleading success. The precheck must turn that into a real failure."""
    _, err = cli("parts", "adjust", "GHOST-999",
                 "--adj-type", "add", "--quantity", "5", expect=3)
    assert "Part not found" in err


def test_adjust_remove_on_unknown_part_exits_3(cli):
    cli("parts", "adjust", "GHOST-999", "--adj-type", "remove",
        "--quantity", "5", expect=3)


def test_adjust_set_on_unknown_part_creates_it(cli):
    """`set` creates parts on purpose and must stay exempt from the precheck."""
    cli("parts", "adjust", "BRAND-NEW", "--adj-type", "set", "--quantity", "4")
    payload, _ = cli("parts", "list")
    assert any(item.get("mpn") == "BRAND-NEW" for item in payload)


def test_adjust_through_alias_pn_hits_the_canonical_part(cli):
    """POST /v1/parts/{k}/adjust keys straight off the path value, so an alias
    MPN would create a disconnected row instead of adjusting C1000."""
    before, _ = cli("parts", "get-history", "C1000")
    cli("parts", "adjust", "CL05B104KO5NNNC", "--adj-type", "add", "--quantity", "7")
    after, _ = cli("parts", "get-history", "C1000")
    assert len(after) == len(before) + 1
    assert after[-1]["qty_delta"] == 7


def test_get_history_through_alias_pn_resolves(cli):
    canonical, _ = cli("parts", "get-history", "C1000")
    aliased, _ = cli("parts", "get-history", "CL05B104KO5NNNC")
    assert aliased == canonical


# ── dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_does_not_contact_the_server(monkeypatch, capsys):
    """Pointed at a URL nothing is listening on: if --dry-run sent anything,
    this would fail rather than exit 0."""
    monkeypatch.setenv("DUBIS_URL", "http://127.0.0.1:1")
    code = dubis_cli.main(["parts", "adjust", "C1000", "--adj-type", "add",
                           "--quantity", "5", "--dry-run"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["dry_run"] is True
    assert payload["path"] == "/v1/parts/C1000/adjust"
    assert payload["body"]["quantity"] == 5


def test_dry_run_leaves_inventory_untouched(cli):
    before, _ = cli("parts", "get-history", "C1000")
    cli("parts", "adjust", "C1000", "--adj-type", "add", "--quantity", "99", "--dry-run")
    after, _ = cli("parts", "get-history", "C1000")
    assert after == before


def test_dry_run_on_a_read_only_command_says_so(cli):
    _, err = cli("parts", "list", "--dry-run")
    assert "read-only" in err


# ── exit codes ───────────────────────────────────────────────────────────────


def test_unknown_verb_exits_2_and_lists_valid_choices(capsys):
    with pytest.raises(SystemExit) as exc_info:
        dubis_cli.main(["parts", "not-a-verb"])
    assert exc_info.value.code == 2
    assert "adjust" in capsys.readouterr().err


def test_no_server_exits_4(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DUBIS_URL", raising=False)
    code = dubis_cli.main(["--data-dir", str(tmp_path), "parts", "list"])
    assert code == 4
    assert "dubis serve" in capsys.readouterr().err


def test_server_error_exits_3(cli):
    cli("parts", "adjust", "C1000", "--adj-type", "nonsense",
        "--quantity", "1", expect=3)


def test_exit_codes_are_distinct():
    """An agent that retries a 4 (start a server) the way it retries a 2 (fix
    your arguments) loops forever, so these must not collapse."""
    codes = {
        dubis_cli.EXIT_OK,
        dubis_cli.EXIT_USAGE,
        dubis_cli.EXIT_SERVER,
        dubis_cli.EXIT_NO_SERVER,
    }
    assert len(codes) == 4


# ── schema / discovery surface ───────────────────────────────────────────────


def test_schema_dumps_every_command(capsys):
    assert dubis_cli.main(["schema", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == dubis_cli.COMMANDS
    assert len(payload) > 50


def test_schema_entries_carry_what_a_caller_needs(capsys):
    assert dubis_cli.main(["schema", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    entry = payload["parts adjust"]
    assert entry["httpVerb"] == "POST"
    assert entry["writes"] is True
    assert entry["params"]["quantity"]["type"] == "integer"


def test_builtin_commands_do_not_collide_with_generated_resources():
    resources = {cmd["resource"] for cmd in dubis_cli.COMMANDS.values()}
    assert not resources.intersection(dubis_cli._BUILTIN)


# ── serve ────────────────────────────────────────────────────────────────────


def test_serve_starts_the_server_where_connect_looks(monkeypatch):
    """`python -m server` defaults --data-dir to "." (the repo root), one level
    above the <repo>/data that connect() probes. If serve let that default
    stand, `dubis serve` followed by any other command would exit 4 while the
    repo root collected .v1_port and .dubis_lock files."""
    from tools.dubis_client import default_data_dir

    captured = {}
    monkeypatch.setattr(dubis_cli.subprocess, "call",
                        lambda cmd, **kw: captured.setdefault("cmd", cmd) and 0 or 0)
    dubis_cli.main(["serve"])

    cmd = captured["cmd"]
    assert "--data-dir" in cmd
    served = cmd[cmd.index("--data-dir") + 1]
    assert served == default_data_dir(str(dubis_cli._REPO_ROOT))


def test_serve_honours_an_explicit_data_dir(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(dubis_cli.subprocess, "call",
                        lambda cmd, **kw: captured.setdefault("cmd", cmd) and 0 or 0)
    dubis_cli.main(["--data-dir", str(tmp_path), "serve"])

    cmd = captured["cmd"]
    assert cmd[cmd.index("--data-dir") + 1] == str(tmp_path)


# ── curated hot-path commands ────────────────────────────────────────────────
#
# Hand-written rather than generated because they are compositions, not
# routes: `get` aggregates five calls, `search` filters client-side (there is
# no /v1 search route), `low-stock` resolves a per-section threshold across
# /v1/preferences and /v1/meta.


def test_search_matches_description_case_insensitively(cli):
    payload, _ = cli("search", "capacitor")
    assert payload["total_count"] == 1
    assert payload["matches"][0]["part_key"] == "C1000"


def test_search_matches_mpn(cli):
    payload, _ = cli("search", "LM358DR")
    assert payload["matches"][0]["part_key"] == "LM358DR"


def test_search_empty_query_returns_everything(cli):
    payload, _ = cli("search")
    assert payload["total_count"] >= 2


def test_search_respects_max_results_without_lying_about_the_total(cli):
    payload, _ = cli("search", "--max-results", "1")
    assert payload["returned"] == 1
    assert payload["total_count"] >= 2


def test_search_returns_only_the_compact_projection(cli):
    payload, _ = cli("search", "capacitor")
    assert set(payload["matches"][0]) == {
        "part_key", "description", "qty", "section", "package", "unit_price",
    }


# ── search --category-hit ─────────────────────────────────────────────────────
#
# The CLI's version of the app's `behavior.categorySearch` toggle. It is a FLAG
# rather than a default because the CLI has no preferences file: there is no
# stored toggle to honour, and defaulting on would make the same command mean
# two things on two machines. The rule itself is shared with the frontend
# (domain/category_match.py, pinned by tests/fixtures/category-name/cases.json).
#
# The seeded C1000 is the real bug in miniature: its section is
# "Passives - Capacitors > MLCC" and its description says "Capacitor", so the
# plural — the word a person types when they want the category — matched nothing.


def test_search_category_hit_finds_a_category_the_text_never_names(cli):
    assert cli("search", "capacitors")[0]["total_count"] == 0
    payload, _ = cli("search", "capacitors", "--category-hit")
    assert [m["part_key"] for m in payload["matches"]] == ["C1000"]
    assert payload["matches"][0]["section"] == "Passives - Capacitors > MLCC"


def test_search_category_hit_does_not_swallow_everything(cli):
    """Two letters name nine IC sections as a substring, which is why the
    category rule matches whole tokens. Note the plain text search DOES hit here
    — "ic" is inside the seeded part's "SOIC-8" package — so what the flag must
    not do is ADD anything: same rows with it, same rows without it."""
    plain, _ = cli("search", "ic")
    with_flag, _ = cli("search", "ic", "--category-hit")
    assert [m["part_key"] for m in with_flag["matches"]] == [
        m["part_key"] for m in plain["matches"]
    ]
    # …and it is not the whole inventory either.
    assert with_flag["total_count"] < cli("search")[0]["total_count"]
    # The capacitor row's category is not named by "ic", so it stays out.
    assert "C1000" not in [m["part_key"] for m in with_flag["matches"]]


def test_search_category_hit_leaves_a_non_matching_category_out(cli):
    """The flag ADDS the named category, it does not return the inventory."""
    assert cli("search", "leds", "--category-hit")[0]["total_count"] == 0


def test_search_category_hit_composes_with_section(cli):
    """--section narrows first, then the category rule decides what survives the
    term — so naming a category the section filter already excluded is empty."""
    payload, _ = cli("search", "capacitors", "--category-hit",
                     "--section", "Passives - Capacitors > MLCC")
    assert payload["total_count"] == 1
    assert cli("search", "capacitors", "--category-hit", "--section", "Other")[0][
        "total_count"] == 0


def test_search_without_the_flag_is_unchanged(cli):
    """Off is the default, so the plain substring behaviour the flag documents
    over must still be exactly what it was."""
    assert [m["part_key"] for m in cli("search", "capacitor")[0]["matches"]] == ["C1000"]
    assert cli("search", "op-amp")[0]["total_count"] == 1


def test_get_aggregates_the_detail_card(cli):
    payload, _ = cli("get", "C1000")
    assert payload["part_key"] == "C1000"
    for field in ("prices", "groups", "recent_history", "has_purchase_history"):
        assert field in payload


def test_get_resolves_an_alias_pn(cli):
    payload, _ = cli("get", "CL05B104KO5NNNC")
    assert payload["part_key"] == "C1000"


def test_get_on_a_miss_exits_3_rather_than_succeeding(cli):
    """The MCP tool returned "Part not found: X" as a SUCCESSFUL result. A CLI
    caller reads the exit code, so a miss reported as 0 is a silent failure."""
    _, err = cli("get", "NOT-A-PART", expect=3)
    assert "Part not found" in err


def test_prices_and_history_also_exit_3_on_a_miss(cli):
    cli("prices", "NOT-A-PART", expect=3)
    cli("history", "NOT-A-PART", expect=3)


def test_history_honours_limit(cli):
    cli("parts", "adjust", "LM358DR", "--adj-type", "add", "--quantity", "1")
    cli("parts", "adjust", "LM358DR", "--adj-type", "add", "--quantity", "1")
    payload, _ = cli("history", "LM358DR", "--limit", "1")
    assert len(payload["history"]) == 1


def test_low_stock_flags_by_explicit_threshold(cli):
    payload, _ = cli("low-stock", "--threshold", "100000")
    assert payload["count"] >= 2
    assert all("threshold" in part for part in payload["parts"])


def test_low_stock_without_threshold_uses_the_shipped_default(cli):
    """No preferences exist in this data dir, so each part is judged against
    the default its category ships with — the same number the inventory grid
    colours with. C1000 is a capacitor at qty 500 against its category's $20,
    so it is not low; LM358DR's description categorises to "Other", which has
    no default, so the $50 base judges its qty of 25 and it is.

    This replaces a test asserting the opposite, written when an unconfigured
    section fell through to 0 and only a zero-stock part was ever flagged — a
    default the desktop window beside it never agreed with."""
    payload, _ = cli("low-stock")
    flagged = {part["part_key"]: part["threshold"] for part in payload["parts"]}
    assert flagged["LM358DR"] == 50
    assert "C1000" not in flagged


def test_low_stock_takes_the_defaults_off_meta_rather_than_its_own_table(cli, live):
    """The CLI is an HTTP client of /v1: what a remote server considers the
    default is that server's business, so the answer has to arrive over the
    wire. Dropping `default_thresholds` from /v1/meta would silently send every
    remote query back to the base."""
    import urllib.request

    port = (live / ".v1_port").read_text(encoding="utf-8").strip()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/meta", timeout=10) as resp:
        meta = json.load(resp)
    assert meta["default_thresholds"]
    assert isinstance(meta["base_threshold"], int)


def test_low_stock_prefers_the_callers_override_over_the_default(cli, live):
    prefs = live / "preferences.json"
    prefs.write_text(json.dumps({"thresholds": {"Other": 5}}), encoding="utf-8")
    try:
        payload, _ = cli("low-stock")
        assert not any(part["part_key"] == "LM358DR" for part in payload["parts"])
    finally:
        prefs.unlink()


def test_low_stock_inherits_a_parent_override_for_a_sub_category(cli, live):
    """The CLI used to look up the exact section string only, so an override on
    "Passives - Capacitors" moved the desktop and not this command. Both sides
    now resolve through domain/thresholds.py, inheritance included."""
    prefs = live / "preferences.json"
    prefs.write_text(json.dumps({"thresholds": {"Passives - Capacitors": 1000}}),
                     encoding="utf-8")
    try:
        payload, _ = cli("low-stock")
        flagged = {part["part_key"]: part["threshold"] for part in payload["parts"]}
        assert flagged["C1000"] == 1000
    finally:
        prefs.unlink()


def test_status_reports_the_discovered_server(cli):
    payload, _ = cli("status")
    assert payload["discovered_via"] == "port_file"
    assert payload["part_count"] >= 2
    assert payload["schema_version"]


def test_generic_groups_returns_a_list(cli):
    payload, _ = cli("generic-groups")
    assert isinstance(payload["groups"], list)


def test_spec_search_routes_a_display_string_through_extract(cli):
    """A display string is not a float, so it must go through POST
    /v1/spec/extract before resolve-spec rather than failing on float()."""
    payload, _ = cli("spec-search", "capacitor", "100nF", "--package", "0402")
    assert "match" in payload


def test_jlc_library_without_an_account_lists_the_paired_ones(cli):
    """No account named is a question about the roster, not an invitation to
    guess which warehouse to fetch — so it answers from the sessions route and
    makes no JLC round trip."""
    payload, _ = cli("jlc", "library")
    assert payload["accounts"] == []
    assert payload["count"] == 0
    assert "dubis jlc library" in payload["hint"]


def test_jlc_library_response_can_never_carry_a_credential(cli):
    """Threat-model rule 4 at the CLI: the sessions route is a projection, so
    nothing the store holds can print here."""
    payload, _ = cli("jlc", "library")
    assert "cookie" not in json.dumps(payload).lower()


def test_jlc_library_for_an_unpaired_account_exits_3(cli):
    _, err = cli("jlc", "library", "NOT-AN-ACCOUNT", expect=3)
    assert "NOT-AN-ACCOUNT" in err


def test_jlc_rejects_an_unknown_sub_noun_with_argparse_exit_2(capsys):
    with pytest.raises(SystemExit) as exc_info:
        dubis_cli.main(["jlc", "not-a-thing"])
    assert exc_info.value.code == 2
    assert "library" in capsys.readouterr().err


def test_curated_dry_run_declines_to_run(cli):
    payload, err = cli("search", "capacitor", "--dry-run")
    assert payload == {"dry_run": True, "command": "search"}
    assert "read-only" in err


def test_curated_names_do_not_collide_with_generated_resources():
    resources = {cmd["resource"] for cmd in dubis_cli.COMMANDS.values()}
    assert not resources.intersection(dubis_cli._RESERVED)


# ── scripts/dubis launcher ───────────────────────────────────────────────────
#
# Nothing exercised the shim until it shipped broken: it defaulted to a bare
# `python`, which macOS does not provide, so `scripts/dubis` died with
# "exec: python: not found" (exit 127) for anyone who had not set $PYTHON.
# Every test above calls main() directly and would have stayed green.

import os  # noqa: E402
import subprocess  # noqa: E402

LAUNCHER = REPO_ROOT / "scripts" / "dubis"


@pytest.mark.skipif(sys.platform == "win32", reason="bash shim; Windows runs the .py directly")
def test_launcher_runs_with_an_explicit_python():
    result = subprocess.run(
        [str(LAUNCHER), "--help"],
        env={**os.environ, "PYTHON": sys.executable},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "dubis" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="bash shim; Windows runs the .py directly")
def test_launcher_resolves_an_interpreter_on_its_own():
    """With $PYTHON unset the shim must either work, or fail with its own
    actionable exit 3 — never die on an interpreter it picked itself.

    Asserted as "0 or 3" rather than "0" because the deps genuinely may not be
    importable in every environment; what must never happen again is the 127
    that the bare-`python` default produced.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHON"}
    result = subprocess.run(
        [str(LAUNCHER), "--help"], env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode in (0, 3), (
        f"exit {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    if result.returncode == 3:
        assert "no Python interpreter" in result.stderr
        assert "PYTHON=" in result.stderr  # names the way out
    else:
        assert "dubis" in result.stdout


# ── `dubis serve --uds` ──────────────────────────────────────────────────────


def _serve_argv(monkeypatch, argv):
    """Run `dubis serve ...` with the subprocess stubbed, returning the
    command line it would have run."""
    captured = {}

    def _fake_call(cmd, **kwargs):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(dubis_cli.subprocess, "call", _fake_call)
    assert dubis_cli.main(argv) == 0
    return captured["cmd"]


def test_serve_defaults_to_tcp(monkeypatch):
    cmd = _serve_argv(monkeypatch, ["serve"])
    assert "--host" in cmd
    assert "--port" in cmd
    assert "--uds" not in cmd


def test_serve_uds_does_not_forward_host_or_port(monkeypatch):
    """`python -m server` treats --uds alongside --host/--port as a usage
    error, on purpose — so forwarding this parser's own defaults would make
    `dubis serve --uds` fail every time with an error about flags the user
    never typed."""
    cmd = _serve_argv(monkeypatch, ["serve", "--uds", "/tmp/dubis.sock"])
    assert "--uds" in cmd
    assert cmd[cmd.index("--uds") + 1] == "/tmp/dubis.sock"
    assert "--host" not in cmd
    assert "--port" not in cmd


def test_serve_always_passes_an_explicit_data_dir(monkeypatch):
    """Both transports: `python -m server`'s own --data-dir default is the
    repo root, one level above the `<repo>/data` connect() probes."""
    for argv in (["serve"], ["serve", "--uds", "/tmp/dubis.sock"]):
        cmd = _serve_argv(monkeypatch, argv)
        assert "--data-dir" in cmd
        assert cmd[cmd.index("--data-dir") + 1].endswith("data")
