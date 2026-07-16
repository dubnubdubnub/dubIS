"""Tests for scripts/gen-api-client.py."""
from __future__ import annotations

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


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


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


def test_mutating_ops_default_to_inventory_unwrap(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    entry = api_map["adjust_part"]
    assert entry["mutating"] is True
    assert entry["unwrap"] == "inventory"


def test_scalar_unwrap_overrides_applied(spec: dict) -> None:
    api_map = gen_api_client.build_api_map(spec)
    assert api_map["get_last_po_quantity"]["unwrap"] == "quantity"
    assert api_map["get_generic_group_names"]["unwrap"] == "groups"
    assert api_map["has_purchase_history"]["unwrap"] == "has_purchase_history"
    assert api_map["extract_spec"]["unwrap"] == "spec"
    assert api_map["resolve_bom_spec"]["unwrap"] == "match"


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
