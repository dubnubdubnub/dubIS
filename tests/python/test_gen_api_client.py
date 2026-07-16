"""Tests for scripts/gen-api-client.py."""
from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

# Make the script importable as a module
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

gen_api_client = importlib.import_module("gen-api-client")

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "docs" / "openapi-v1.json"
ROUTES_DIR = REPO_ROOT / "server" / "routes"


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


# ── AST-walk of server/routes/*.py: source of truth for which operation_ids
# actually call finish_mutation(...), independent of the hand-maintained
# FINISH_MUTATION_OPERATION_IDS allowlist in gen-api-client.py. If the two
# ever diverge, `unwrap` silently defaults wrong for the drifted op_id (see
# the big comment above FINISH_MUTATION_OPERATION_IDS) — a live-only
# regression no route-mocked test can catch. This scan makes the allowlist
# self-checking. ──────────────────────────────────────────────────────────

def _decorator_operation_id(decorator: ast.expr) -> str | None:
    """Extract `operation_id="..."` from a `@router.<verb>(...)` decorator call."""
    if not isinstance(decorator, ast.Call):
        return None
    for kw in decorator.keywords:
        if kw.arg == "operation_id" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _calls_finish_mutation(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id == "finish_mutation":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "finish_mutation":
                return True
    return False


def _hand_builds_ok_detail_envelope(node: ast.AST) -> bool:
    """True if any `return {...}` in the function body is a dict literal with
    BOTH string keys "ok" and "detail" (the finish_mutation-shaped envelope,
    built by hand instead of via the helper)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            keys = {
                k.value for k in sub.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if {"ok", "detail"} <= keys:
                return True
    return False


def _scan_routes() -> tuple[set[str], set[str]]:
    """Return (finish_mutation_op_ids, hand_built_envelope_op_ids) discovered
    by AST-walking every route function in server/routes/*.py."""
    finish_mutation_op_ids: set[str] = set()
    hand_built_envelope_op_ids: set[str] = set()
    for path in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            op_id = None
            for decorator in node.decorator_list:
                op_id = _decorator_operation_id(decorator)
                if op_id is not None:
                    break
            if op_id is None:
                continue
            if _calls_finish_mutation(node):
                finish_mutation_op_ids.add(op_id)
            elif _hand_builds_ok_detail_envelope(node):
                hand_built_envelope_op_ids.add(op_id)
    return finish_mutation_op_ids, hand_built_envelope_op_ids


def test_finish_mutation_route_set_matches_allowlist() -> None:
    """FINISH_MUTATION_OPERATION_IDS must exactly match the set of route
    functions whose body actually calls finish_mutation(...). This is the
    guard the big comment above the allowlist asks for: keep it in sync with
    `grep -rn 'finish_mutation(' server/routes/*.py` — this test does that
    grep (via AST, so decorator-detached helpers like pnp.py's `_consume`
    don't get miscounted) and fails loud on any drift in either direction.
    """
    finish_mutation_op_ids, _ = _scan_routes()
    assert finish_mutation_op_ids == gen_api_client.FINISH_MUTATION_OPERATION_IDS


def test_hand_built_envelope_routes_resolve_to_detail_unwrap(spec: dict) -> None:
    """Routes that hand-build an `{"ok", "detail"}` envelope WITHOUT calling
    finish_mutation (currently: create_saved_search, delete_saved_search) are
    NOT in FINISH_MUTATION_OPERATION_IDS, so the mutating-keyed default would
    give them `unwrap: None` — wrong. Each such op_id must have an explicit
    UNWRAP_OVERRIDES entry resolving to "detail", or the generated API_MAP
    silently returns the whole envelope instead of `detail` to every caller.
    """
    _, hand_built_envelope_op_ids = _scan_routes()
    assert hand_built_envelope_op_ids == {"create_saved_search", "delete_saved_search"}

    api_map = gen_api_client.build_api_map(spec)
    for op_id in hand_built_envelope_op_ids:
        assert op_id not in gen_api_client.FINISH_MUTATION_OPERATION_IDS, (
            f"{op_id} calls finish_mutation and hand-builds an envelope — "
            "pick one, both is a contradiction the scan can't resolve"
        )
        assert api_map[op_id]["unwrap"] == "detail", (
            f"{op_id} hand-builds an {{ok, detail}} envelope but is missing "
            "from UNWRAP_OVERRIDES (or overridden to the wrong value) — "
            "callers would get the whole envelope instead of `detail`"
        )


# ── coverage of the real committed spec ────────────────────────────────

def test_build_api_map_covers_every_v1_operation(spec: dict) -> None:
    """Every non-infra /v1 operationId ends up with an API_MAP entry."""
    api_map = gen_api_client.build_api_map(spec)
    for path, methods in spec["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for op in methods.values():
            op_id = op.get("operationId")
            if op_id is None or op_id in gen_api_client.SKIP_OPERATION_IDS:
                continue
            assert op_id in api_map, f"{op_id} missing from generated API_MAP"


def test_build_api_map_includes_declared_aliases(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    for alias in gen_api_client.ALIASES:
        assert alias in api_map


def test_alias_entries_bake_fixed_path_params(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["fetch_lcsc_product"]["path"] == "/v1/distributors/lcsc/product/{code}"
    assert api_map["fetch_lcsc_product"]["pathParams"] == ["code"]
    assert api_map["fetch_lcsc_product"]["argOrder"] == ["code"]


def test_list_parts_unwraps_inventory(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["list_parts"]["unwrap"] == "inventory"
    assert api_map["rebuild_inventory"]["unwrap"] == "inventory"


def test_mutating_ops_default_to_detail_unwrap(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    entry = api_map["adjust_part"]
    assert entry["mutating"] is True
    assert entry["unwrap"] == "detail"


def test_non_finish_mutation_post_routes_do_not_default_to_detail_unwrap(spec: dict) -> None:
    """Regression: `mutating` must be an explicit allowlist keyed off
    FINISH_MUTATION_OPERATION_IDS, not a verb-based heuristic. A POST/PUT/
    PATCH/DELETE route that never calls server.mutations.finish_mutation
    returns its payload raw (un-enveloped) — defaulting its unwrap to
    "detail" silently returns `undefined` to every real caller (only a live
    server, not a route-mocked test, can catch this, because mocks build
    their envelope from these same api-map entries). This broke
    detect_columns/save_preferences/match_part/etc. once, live-only — see
    docs/plans Task 10 report.
    """
    api_map = gen_api_client.build_api_map(spec)
    for op_id in (
        "detect_columns", "match_part", "ocr_overlay", "parse_import_source",
        "start_scan_session", "extract_spec_from_value",
        "validate_digikey_session", "sync_digikey_cookies",
        "set_mouser_api_key", "clear_mouser_api_key", "logout_digikey",
        "save_preferences", "pnp_consume",
    ):
        entry = api_map[op_id]
        assert entry["mutating"] is False, f"{op_id} wrongly marked mutating"
        assert entry["unwrap"] is None, f"{op_id} wrongly defaults to a 'detail' unwrap"


def test_scalar_unwrap_overrides_applied(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["get_last_po_quantity"]["unwrap"] == "quantity"
    assert api_map["get_generic_group_names"]["unwrap"] == "groups"
    assert api_map["has_purchase_history"]["unwrap"] == "has_purchase_history"
    assert api_map["extract_spec"]["unwrap"] == "spec"
    assert api_map["resolve_bom_spec"]["unwrap"] == "match"
    assert api_map["ocr_engine_available"]["unwrap"] == "available"


def test_raw_body_op_has_single_synthetic_arg(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    entry = api_map["save_preferences"]
    assert entry["rawBody"] is True
    assert entry["argOrder"] == ["prefs"]
    assert entry["bodyParams"] == []


def test_single_param_ops_are_order_derivable_without_arg_order(spec: dict) -> None:
    """delete_part has one positional param (part_key) — no ARG_ORDER entry needed."""
    assert "delete_part" not in gen_api_client.ARG_ORDER
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["delete_part"]["argOrder"] == ["part_key"]


def test_multi_param_ops_use_hand_maintained_arg_order(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["adjust_part"]["argOrder"] == [
        "adj_type", "part_key", "quantity", "note", "source",
    ]
    assert api_map["merge_vendors"]["argOrder"] == ["src_id", "dst_id"]


# ── completeness assertion (fail-loud on new/uncovered multi-param routes) ──

def test_missing_arg_order_for_new_multiparam_route_raises(spec: dict) -> None:
    """A synthetic new op with 2+ params and no ARG_ORDER entry must fail loud."""
    synthetic = copy.deepcopy(spec)
    synthetic["paths"]["/v1/synthetic/{a}"] = {
        "post": {
            "operationId": "synthetic_uncovered_op",
            "parameters": [{"in": "path", "name": "a", "required": True, "schema": {"type": "string"}}],
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/SetMouserKeyBody"}},
                },
                "required": True,
            },
        },
    }
    with pytest.raises(gen_api_client.GenerationError, match="synthetic_uncovered_op"):
        gen_api_client.build_api_map(synthetic)


def test_arg_order_mismatched_params_raises(spec: dict) -> None:
    """An ARG_ORDER entry whose names don't match the operation's actual params fails loud."""
    original = gen_api_client.ARG_ORDER.get("merge_vendors")
    gen_api_client.ARG_ORDER["merge_vendors"] = ["src_id", "totally_wrong_name"]
    try:
        with pytest.raises(gen_api_client.GenerationError, match="merge_vendors"):
            gen_api_client.build_api_map(spec)
    finally:
        gen_api_client.ARG_ORDER["merge_vendors"] = original


def test_unknown_alias_target_raises(spec: dict) -> None:
    gen_api_client.ALIASES["bogus_alias"] = {"target": "does_not_exist", "arg_order": []}
    try:
        with pytest.raises(gen_api_client.GenerationError, match="bogus_alias"):
            gen_api_client.build_api_map(spec)
    finally:
        del gen_api_client.ALIASES["bogus_alias"]


# ── rendering ────────────────────────────────────────────────────────────

def test_render_js_has_header_and_export(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    rendered = gen_api_client.render_js(api_map)
    assert rendered.startswith("// AUTO-GENERATED")
    assert "export const API_MAP = " in rendered
    assert rendered.endswith("\n")


def test_render_js_keys_sorted(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    rendered = gen_api_client.render_js(api_map)
    start = rendered.index("{")
    parsed = json.loads(rendered[start:rendered.rindex(";")])
    assert list(parsed.keys()) == sorted(parsed.keys())


# ── CLI / --check ──────────────────────────────────────────────────────

def test_main_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "api-map.js"
    rc = gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert "API_MAP" in out.read_text(encoding="utf-8")


def test_main_check_passes_when_fresh(tmp_path: Path) -> None:
    out = tmp_path / "api-map.js"
    rc1 = gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out)])
    assert rc1 == 0
    rc2 = gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out), "--check"])
    assert rc2 == 0


def test_main_check_fails_when_missing(tmp_path: Path) -> None:
    out = tmp_path / "api-map.js"  # never created
    rc = gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out), "--check"])
    assert rc == 1


def test_main_check_fails_when_stale(tmp_path: Path) -> None:
    out = tmp_path / "api-map.js"
    gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out)])
    out.write_text("// stale\nexport const API_MAP = {};\n", encoding="utf-8")

    rc = gen_api_client.main(["--spec", str(SPEC_PATH), "--out", str(out), "--check"])
    assert rc == 1


def test_committed_map_is_up_to_date(spec: dict) -> None:
    """js/api-map.js must already match what the generator would produce."""
    rc = gen_api_client.main(["--spec", str(SPEC_PATH), "--check"])
    assert rc == 0
