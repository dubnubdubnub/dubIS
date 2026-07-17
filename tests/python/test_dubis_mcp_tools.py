"""tools/dubis-mcp/server.py's seven read tools (Task 2 of
docs/plans/2026-07-16-phase2-mcp-server-plan.md).

Session-scoped: ONE real /v1 server (in-process uvicorn thread via
tests/python/server/conftest.py's start_live_server), seeded once with a
handful of parts, is shared across every test in this module. The dubis-mcp
server module is loaded under a distinct module name ("dubis_mcp_server")
rather than "server" — the repo already has a top-level `server` package
(the /v1 FastAPI app), and tools/dubis-mcp/server.py would collide with it
if imported under the same name. Its module-global `_client` is set directly
to a V1Client pointed at the test server, bypassing connect()'s discovery/
spawn entirely (this process already has the real server up).

No HTTP mocking — every tool call in this file makes a real /v1 request.
Tool functions are called directly (they're plain functions under
@mcp.tool() — the decorator returns the function unchanged), no MCP stdio
transport involved.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.python.helpers import make_api, make_part, write_ledger
from tests.python.server.conftest import start_live_server

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = REPO_ROOT / "tools" / "dubis-mcp"

# v1client.py is imported by tools/dubis-mcp/server.py as a bare `import
# v1client` (top-level module, not a package-relative import) — its directory
# must be on sys.path before we exec the server module below.
sys.path.insert(0, str(MCP_DIR))

_spec = importlib.util.spec_from_file_location("dubis_mcp_server", str(MCP_DIR / "server.py"))
dubis_mcp_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dubis_mcp_server)

from v1client import V1Client  # noqa: E402


@pytest.fixture(scope="session")
def mcp_client(tmp_path_factory):
    """Seed a tmp data dir with a few parts, start a real /v1 server against
    it, and point the dubis-mcp server module's cached client at it."""
    tmp_path = tmp_path_factory.mktemp("dubis-mcp-tools")
    api = make_api(tmp_path)
    write_ledger(
        api,
        [
            # 100nF 0402 capacitor — the real spec_search hit.
            make_part(lcsc="C1000", mpn="CL05B104KO5NNNC", qty=500,
                      desc="Capacitor MLCC 100nF 16V X7R 0402", pkg="0402",
                      unit_price="0.002", ext_price="1.00"),
            # A second capacitor so search_parts has >1 "capacitor" hit.
            make_part(lcsc="C1001", mpn="CL10A106KP8NNNC", qty=2,
                      desc="Capacitor MLCC 10uF 10V X5R 0603", pkg="0603",
                      unit_price="0.01", ext_price="0.02"),
            # A resistor with zero stock (low_stock candidate).
            make_part(lcsc="C2000", mpn="RC0402FR-0710KL", qty=0,
                      desc="Resistor 10kOhm 1% 0402", pkg="0402",
                      unit_price="0.001", ext_price="0.00"),
            # A part with no LCSC PN, keyed by MPN, healthy stock.
            make_part(lcsc="", mpn="LM358DR", qty=25,
                      desc="Op-Amp Dual General Purpose", pkg="SOIC-8",
                      unit_price="0.30", ext_price="7.50"),
        ],
    )
    server, thread, base_url = start_live_server(api)
    client = V1Client(base_url)
    dubis_mcp_server._client = client

    # A real generic group so spec_search has an actual hit: resolve_bom_spec
    # matches the query spec against a *generic part's* spec+strictness (not
    # directly against inventory), then resolves to the best in-stock member
    # of that group. create_generic_part auto-matches existing real parts
    # whose extracted spec (type/value/package) satisfies the strictness
    # rules, so C1000 (100nF 0402) is added as a member automatically.
    generic = client.post(
        "/v1/generic-parts",
        json={
            "name": "100nF 0402 Cap",
            "part_type": "capacitor",
            "spec": {"value": "100nF", "package": "0402"},
            "strictness": {"required": ["value", "package"]},
        },
    )
    assert generic["detail"]["generic_part_id"]  # sanity: creation succeeded

    try:
        yield dubis_mcp_server
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        api.shutdown()


# ── search_parts ─────────────────────────────────────────────────────────────


def test_search_parts_matches_description_case_insensitive(mcp_client):
    result = mcp_client.search_parts(query="capacitor")
    assert result["total_count"] == 2
    assert result["returned"] == 2
    keys = {m["part_key"] for m in result["matches"]}
    assert keys == {"C1000", "C1001"}


def test_search_parts_matches_mpn(mcp_client):
    result = mcp_client.search_parts(query="LM358DR")
    assert result["total_count"] == 1
    assert result["matches"][0]["part_key"] == "LM358DR"


def test_search_parts_projection_key_set(mcp_client):
    result = mcp_client.search_parts(query="capacitor", max_results=1)
    assert set(result.keys()) == {"matches", "total_count", "returned"}
    assert set(result["matches"][0].keys()) == {
        "part_key", "description", "qty", "section", "package", "unit_price",
    }


def test_search_parts_max_results_caps_but_reports_total(mcp_client):
    result = mcp_client.search_parts(query="capacitor", max_results=1)
    assert result["returned"] == 1
    assert result["total_count"] == 2


def test_search_parts_section_filter(mcp_client):
    all_results = mcp_client.search_parts(query="")
    some_section = all_results["matches"][0]["section"]
    scoped = mcp_client.search_parts(query="", section=some_section)
    assert all(m["section"] == some_section for m in scoped["matches"])


def test_search_parts_no_match(mcp_client):
    result = mcp_client.search_parts(query="does-not-exist-xyz")
    assert result == {"matches": [], "total_count": 0, "returned": 0}


# ── get_part ─────────────────────────────────────────────────────────────────


def test_get_part_aggregates_detail(mcp_client):
    detail = mcp_client.get_part("C1000")
    assert isinstance(detail, dict)
    assert detail["part_key"] == "C1000"
    assert detail["description"] == "Capacitor MLCC 100nF 16V X7R 0402"
    assert detail["qty"] == 500
    assert "prices" in detail
    assert "has_purchase_history" in detail
    assert isinstance(detail["groups"], list)
    assert isinstance(detail["recent_history"], list)
    assert len(detail["recent_history"]) <= 5


def test_get_part_by_mpn_when_no_lcsc(mcp_client):
    detail = mcp_client.get_part("LM358DR")
    assert isinstance(detail, dict)
    assert detail["part_key"] == "LM358DR"
    assert detail["qty"] == 25


def test_get_part_unknown_returns_error_string(mcp_client):
    result = mcp_client.get_part("C-does-not-exist")
    assert isinstance(result, str)
    assert "C-does-not-exist" in result


def test_get_part_projection_key_set(mcp_client):
    detail = mcp_client.get_part("C1000")
    assert set(detail.keys()) == {
        "part_key", "description", "qty", "section", "package", "manufacturer",
        "unit_price", "ext_price", "primary_vendor_id", "po_history",
        "prices", "has_purchase_history", "groups", "recent_history",
    }


# ── spec_search ──────────────────────────────────────────────────────────────


def test_spec_search_numeric_value_hit(mcp_client):
    result = mcp_client.spec_search(part_type="capacitor", value=1e-7, package="0402")
    assert result["match"] is not None
    assert result["match"]["best_part_id"] == "C1000"


def test_spec_search_display_string_routes_through_extract_and_hits(mcp_client):
    # "100nF" is not numeric -- spec_search must route it through
    # POST /v1/spec/extract to parse the value before calling resolve-spec.
    result = mcp_client.spec_search(part_type="capacitor", value="100nF", package="0402")
    assert result["match"] is not None
    assert result["match"]["best_part_id"] == "C1000"


def test_spec_search_wrong_package_misses(mcp_client):
    result = mcp_client.spec_search(part_type="capacitor", value="100nF", package="0603")
    assert result == {"match": None}


def test_spec_search_unparseable_string_misses_cleanly(mcp_client):
    result = mcp_client.spec_search(part_type="capacitor", value="not-a-value", package="0402")
    assert result == {"match": None}


# ── low_stock ────────────────────────────────────────────────────────────────


def test_low_stock_explicit_threshold(mcp_client):
    result = mcp_client.low_stock(threshold=2)
    keys = {p["part_key"] for p in result["parts"]}
    assert keys == {"C1001", "C2000"}  # qty 2 and qty 0
    assert result["count"] == 2
    assert all(p["threshold"] == 2 for p in result["parts"])


def test_low_stock_default_threshold_zero_when_no_prefs(mcp_client):
    result = mcp_client.low_stock()
    keys = {p["part_key"] for p in result["parts"]}
    assert keys == {"C2000"}  # only the zero-stock resistor


def test_low_stock_projection_key_set(mcp_client):
    result = mcp_client.low_stock(threshold=1000)
    assert set(result.keys()) == {"parts", "count"}
    sample = result["parts"][0]
    assert set(sample.keys()) == {
        "part_key", "description", "qty", "section", "package", "unit_price", "threshold",
    }


# ── price_summary ────────────────────────────────────────────────────────────


def test_price_summary_shape(mcp_client):
    result = mcp_client.price_summary("C1000")
    assert set(result.keys()) == {"part_key", "distributors", "last_po_quantity"}
    assert result["part_key"] == "C1000"
    assert isinstance(result["distributors"], dict)


# ── part_history ─────────────────────────────────────────────────────────────


def test_part_history_shape_and_limit(mcp_client):
    result = mcp_client.part_history("C1000", limit=3)
    assert set(result.keys()) == {"part_key", "history"}
    assert result["part_key"] == "C1000"
    assert len(result["history"]) <= 3


# ── list_generic_parts ───────────────────────────────────────────────────────


def test_list_generic_parts_returns_group_with_best_member(mcp_client):
    result = mcp_client.list_generic_parts()
    assert set(result.keys()) == {"groups"}
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert set(group.keys()) == {
        "generic_part_id", "name", "part_type", "member_count", "best_member",
    }
    assert group["part_type"] == "capacitor"
    assert group["member_count"] == 1
    assert group["best_member"]["part_id"] == "C1000"
    assert group["best_member"]["quantity"] == 500


def test_list_generic_parts_type_filter_excludes_other_types(mcp_client):
    result = mcp_client.list_generic_parts(part_type="resistor")
    assert result == {"groups": []}


def test_list_generic_parts_type_filter_matches(mcp_client):
    result = mcp_client.list_generic_parts(part_type="capacitor")
    assert len(result["groups"]) == 1


# ── adjust_stock ─────────────────────────────────────────────────────────────
#
# Mutation tests use their own seeded parts (not C1000/C1001/etc, which the
# read-tool tests above assert exact quantities against) so tests stay
# order-independent within this shared session-scoped server.


def test_adjust_stock_add_roundtrip(mcp_client):
    # "add"/"remove" on a part_key with no prior ledger/adjustment row is a
    # no-op at the domain layer (domain/inventory_ops.apply_adjustments only
    # materializes a brand-new part_key on adj_type == "set" with a positive
    # quantity), so seed with a positive "set" first, mirroring the UI's own
    # adjust-modal flow for new parts.
    mcp_client.adjust_stock(part_key="C3000", adj_type="set", quantity=1)
    result = mcp_client.adjust_stock(part_key="C3000", adj_type="add", quantity=7, note="mcp test add")
    assert result == {"part_key": "C3000", "new_qty": 8}


def test_adjust_stock_set_then_remove_roundtrip(mcp_client):
    mcp_client.adjust_stock(part_key="C3001", adj_type="set", quantity=50)
    result = mcp_client.adjust_stock(part_key="C3001", adj_type="remove", quantity=20)
    assert result == {"part_key": "C3001", "new_qty": 30}


def test_adjust_stock_records_source_mcp_visible_via_history(mcp_client):
    mcp_client.adjust_stock(part_key="C3002", adj_type="set", quantity=1)
    mcp_client.adjust_stock(part_key="C3002", adj_type="add", quantity=3, note="source check")
    history = mcp_client.part_history("C3002")["history"]
    assert history
    assert history[0]["source"] == "mcp"


def test_adjust_stock_bad_part_key_error_surfaces(mcp_client):
    # "bad/part/key" matches no seeded part, so the existence precheck raises
    # before any /v1 request is sent (see the next two tests) — this used to
    # only error by accident (the slashes broke the /v1 URL's route match),
    # which no longer applies now that the precheck runs first.
    with pytest.raises(ValueError, match="Part not found: bad/part/key"):
        mcp_client.adjust_stock(part_key="bad/part/key", adj_type="add", quantity=1)


def test_adjust_stock_add_on_unknown_key_raises_clear_error(mcp_client):
    with pytest.raises(ValueError, match="Part not found: C3099"):
        mcp_client.adjust_stock(part_key="C3099", adj_type="add", quantity=5)


def test_adjust_stock_remove_on_unknown_key_raises_clear_error(mcp_client):
    with pytest.raises(ValueError, match="Part not found: C3098"):
        mcp_client.adjust_stock(part_key="C3098", adj_type="remove", quantity=5)


def test_adjust_stock_set_on_new_key_still_creates_part(mcp_client):
    result = mcp_client.adjust_stock(part_key="C3097", adj_type="set", quantity=12)
    assert result == {"part_key": "C3097", "new_qty": 12}


# ── consume_bom ──────────────────────────────────────────────────────────────


def test_consume_bom_roundtrip(mcp_client):
    mcp_client.adjust_stock(part_key="C3010", adj_type="set", quantity=100)
    result = mcp_client.consume_bom(
        matches=[{"part_key": "C3010", "bom_qty": 2}],
        board_qty=3,
        bom_name="mcp-test-bom",
    )
    assert result == {
        "bom_name": "mcp-test-bom",
        "board_qty": 3,
        "consumed": [{"part_key": "C3010", "bom_qty": 2}],
    }
    detail = mcp_client.get_part("C3010")
    assert detail["qty"] == 100 - (2 * 3)


def test_consume_bom_records_source_mcp(mcp_client):
    mcp_client.adjust_stock(part_key="C3011", adj_type="set", quantity=10)
    mcp_client.consume_bom(matches=[{"part_key": "C3011", "bom_qty": 1}], board_qty=1)
    history = mcp_client.part_history("C3011")["history"]
    assert history
    assert history[0]["source"] == "mcp"


def test_consume_bom_defaults(mcp_client):
    mcp_client.adjust_stock(part_key="C3012", adj_type="set", quantity=5)
    result = mcp_client.consume_bom(matches=[{"part_key": "C3012", "bom_qty": 1}])
    assert result["board_qty"] == 1
    assert result["bom_name"] == "mcp-bom"


def test_consume_bom_bad_match_error_surfaces(mcp_client):
    from v1client import V1Error

    with pytest.raises(V1Error):
        mcp_client.consume_bom(matches=[{"part_key": "C3020"}], board_qty=1)
