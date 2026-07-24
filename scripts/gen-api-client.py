#!/usr/bin/env python3
"""Generate js/api-map.js from docs/openapi-v1.json.

Usage:
    python scripts/gen-api-client.py          # write js/api-map.js
    python scripts/gen-api-client.py --check  # exit 1 if the map is stale

Emits ``export const API_MAP = {...}`` — one entry per bridge-style method
name, keyed the way ``js/api.js``'s ``api(method, ...args)`` looks methods up.
Each entry carries everything the HTTP transport needs to reconstruct the
request from positional arguments:

    {
      "verb": "POST",
      "path": "/v1/parts/{part_key}/adjust",   # {name} placeholders, server field names
      "argOrder": ["adj_type", "part_key", "quantity", "note", "source"],
      "pathParams": ["part_key"],
      "queryParams": [],
      "bodyParams": ["adj_type", "quantity", "note", "source"],
      "rawBody": false,
      "unwrap": "detail",
      "mutating": true
    }

``argOrder`` is the single source of truth for positional-argument order
(the union of pathParams/queryParams/bodyParams membership, in call order).
Most operations are "order-derivable": pathParams (path order) followed by
bodyParams in the order the openapi schema declares them (which — because
``gen-openapi.py`` renders with ``sort_keys=True`` — is alphabetical). That
derived order frequently does NOT match the original pywebview bridge's
positional signature (frozen in ``tests/python/test_api_surface.py``), so any
operation with 2+ total params MUST have an explicit entry in ``ARG_ORDER``
below — seeded by hand from the frozen signatures — or generation fails loud.
Operations with 0 or 1 params are unambiguous and skip that requirement.

``mutating`` is true exactly for operation_ids in
``FINISH_MUTATION_OPERATION_IDS`` — routes whose handler ends with
``return finish_mutation(...)`` (server/mutations.py), whose envelope is
always ``{"ok": true, "detail": ...}`` — never an inventory list. This is
deliberately NOT verb-based: many POST/PUT/PATCH/DELETE routes are pure
lookups/config toggles (detect_columns, match_part, save_preferences, etc.)
that return their payload raw, un-enveloped. The default ``unwrap`` is
mutating-keyed: mutating ops default to ``unwrap: "detail"``, everything
else defaults to ``unwrap: None`` — both defaults are overridden for
``list_parts`` (whose own GET body IS the inventory envelope) and for any
entry in ``UNWRAP_OVERRIDES``.

Special-cased alias methods (same route, different bridge method name) are
declared in ALIASES; they get their own API_MAP entries derived from the
target operation's route with a (possibly fixed/subset) argOrder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = REPO_ROOT / "docs" / "openapi-v1.json"
DEFAULT_OUT = REPO_ROOT / "js" / "api-map.js"

# Infra/legacy endpoints that are not part of the bridge-style method surface
# (no JS caller ever invokes them via api(method, ...args)).
SKIP_OPERATION_IDS = {
    "legacy_consume", "legacy_health", "legacy_parts",  # /api/* back-compat
    "health", "meta", "events_stream",  # infra, not bridge methods
    "get_openpnp_part",  # OpenPnP-side Jython script calls this, not dubIS's own JS
    "get_feeder_tag_sheet",  # binary PDF fetched directly by the browser, not via api()
    "get_feeder_tag_png",  # binary PNG fetched directly by the browser, not via api()
}

# Operations whose request body is a raw opaque JSON object (no named
# pydantic fields) rather than a set of body params. The whole first
# positional arg becomes the request body verbatim.
RAW_BODY_ARG_NAME = {
    "save_preferences": "prefs",
}

# Hand-maintained positional-argument order, seeded from the frozen pywebview
# signatures in tests/python/test_api_surface.py. REQUIRED for every /v1
# operation with 2+ total params (path + query[excl. include] + body) —
# generation asserts this and fails loud on new/uncovered multi-param routes.
ARG_ORDER: dict[str, list[str]] = {
    "add_bom_missing_to_cart": ["cart_id", "missing"],
    "add_cart_item": ["cart_id", "part_id", "raw", "qty", "target_distributor", "shortfall"],
    "add_generic_member": ["generic_part_id", "part_id"],
    "consolidate_cart": ["cart_id", "distributor"],
    "export_cart": ["cart_id", "distributor", "format"],
    "remove_cart_item": ["cart_id", "ref"],
    "rename_cart": ["cart_id", "name"],
    "split_cart": ["cart_id", "distributor", "new_name", "remove_from_source"],
    "update_cart_item": ["cart_id", "ref", "qty", "target_distributor"],
    "adjust_part": ["adj_type", "part_key", "quantity", "note", "source"],
    "consume_bom": ["matches", "board_qty", "bom_name", "note", "source"],
    "load_feeder_reel": ["tag_id", "part_key", "qty", "tape_width_mm"],
    "register_feeder": ["tag_id", "feeder_type"],
    "create_generic_part": ["name", "part_type", "spec", "strictness"],
    "create_purchase_order_with_items": [
        "vendor_id", "source_file_b64", "source_file_name",
        "purchase_date", "notes", "line_items",
    ],
    "create_saved_search": [
        "generic_part_id", "name", "tag_state", "search_text", "frozen_members",
    ],
    "exclude_generic_member": ["generic_part_id", "part_id"],
    "extract_spec_from_value": ["part_type", "value_str", "package_str"],
    "fetch_distributor_product": ["name", "code"],
    "match_part": ["mpn", "manufacturer"],
    "merge_vendors": ["src_id", "dst_id"],
    "ocr_overlay": ["file_b64", "file_name", "template"],
    "parse_import_source": ["file_b64", "file_name", "path", "template"],
    "pnp_consume": ["part_id", "qty"],
    "record_fetched_prices": ["part_key", "distributor", "price_tiers"],
    "remove_generic_member": ["generic_part_id", "part_id"],
    "resolve_bom_spec": ["part_type", "value", "package"],
    "set_preferred_member": ["generic_part_id", "part_id"],
    "update_generic_part": ["generic_part_id", "name", "spec", "strictness"],
    "update_part_fields": ["part_key", "fields"],
    "update_part_price": ["part_key", "unit_price", "ext_price"],
    "update_purchase_order": ["po_id", "vendor_id", "purchase_date", "notes"],
    "update_vendor": ["vendor_id", "name", "url", "favicon_path"],
}

# Operations whose route ends in `return finish_mutation(...)`
# (server/mutations.py) — the ONLY routes whose envelope is really
# `{"ok": true, "detail": ...}`. This must be an explicit allowlist, not a
# verb-based heuristic ("POST/PUT/PATCH/DELETE => mutating"): plenty of
# state-changing-verb routes are pure lookups/config toggles that return
# their payload raw, un-enveloped (detect_columns, match_part, ocr_overlay,
# parse_import_source, start_scan_session, extract_spec_from_value,
# validate_digikey_session, sync_digikey_cookies, set/clear_mouser_api_key,
# logout_digikey, save_preferences, pnp_consume, resolve_bom_spec,
# fetch_favicon, create_saved_search, delete_saved_search — none of these
# call finish_mutation). Getting this wrong makes `unwrap` default to
# "detail" for a route with no "detail" key, silently returning `undefined`
# to every caller — a live-only regression (route-mocked tests build their
# envelope from these SAME entries, so they can't catch this drift; only a
# real server response can). Keep this list in sync with
# `grep -rn 'finish_mutation(' server/routes/*.py`.
FINISH_MUTATION_OPERATION_IDS: set[str] = {
    # generic_parts.py
    "create_generic_part", "update_generic_part", "add_generic_member",
    "remove_generic_member", "exclude_generic_member", "set_preferred_member",
    # inventory_mut.py
    "adjust_part", "update_part_fields", "update_part_price", "delete_part",
    "fetch_missing_descriptions", "record_fetched_prices", "import_purchases",
    "remove_last_purchases", "remove_last_adjustments", "rollback_source",
    "consume_bom",
    # vendors_pos.py
    "update_vendor", "delete_vendor", "merge_vendors",
    "create_purchase_order_with_items", "delete_last_purchase_order",
    "update_purchase_order", "delete_purchase_order",
}

# Scalar-envelope unwraps for operations whose response shape isn't the
# mutating-keyed default (see build_api_map). `list_parts` is the one GET
# whose *own* body IS the inventory envelope (unwrap "inventory") even though
# GETs otherwise default to no unwrap.
#
# The entries below split into two different categories — don't conflate them:
#
# 1. finish_mutation-backed ops (mirroring FINISH_MUTATION_OPERATION_IDS):
#    update_vendor, create_generic_part, update_generic_part,
#    add_generic_member, remove_generic_member, exclude_generic_member,
#    set_preferred_member, record_fetched_prices, fetch_missing_descriptions.
#    These are listed here mainly for readability/documentation, since the
#    mutating-keyed default already produces "detail" for them — removing
#    them would NOT change behavior. Call sites (js/vendors-modal.js,
#    js/inventory/inv-mutations.js, js/group-flyout/flyout-drag.js,
#    js/group-flyout/flyout-events.js, js/inventory/
#    fetch-descriptions-command.js) consume the facade return via `detail`
#    directly (e.g. `v.name`, `result.generic_part_id`, `result.id`).
#
# 2. create_saved_search, delete_saved_search: LOAD-BEARING, NOT documentation.
#    Neither op_id is in FINISH_MUTATION_OPERATION_IDS — both hand-build a
#    plain `{"ok": True, "detail": ...}` dict in server/routes/generic_parts.py
#    without calling finish_mutation. Since they're not mutating (per that
#    allowlist), the mutating-keyed default would give them `unwrap: None`,
#    silently returning the whole envelope instead of `detail` to callers.
#    Removing these two entries WOULD break their JS call sites.
UNWRAP_OVERRIDES: dict[str, str] = {
    "list_parts": "inventory",
    "get_generic_group_names": "groups",
    "get_last_po_quantity": "quantity",
    "has_purchase_history": "has_purchase_history",
    "extract_spec": "spec",
    "resolve_bom_spec": "match",
    "fetch_favicon": "path",
    "ocr_engine_available": "available",
    # Category 1 (documentation only, see above):
    "update_vendor": "detail",
    "create_generic_part": "detail",
    "update_generic_part": "detail",
    "add_generic_member": "detail",
    "remove_generic_member": "detail",
    "exclude_generic_member": "detail",
    "set_preferred_member": "detail",
    "record_fetched_prices": "detail",
    "fetch_missing_descriptions": "detail",
    # Category 2 (load-bearing, see above):
    "create_saved_search": "detail",
    "delete_saved_search": "detail",
    # server/routes/carts.py: hand-builds {"ok": True, "detail": ...} for every
    # mutating cart route (like create/delete_saved_search above) without
    # calling finish_mutation — none of these are in
    # FINISH_MUTATION_OPERATION_IDS, so without this override the
    # mutating-keyed default would give them unwrap: None. get_cart,
    # list_carts, and export_cart are reads that return their payload raw
    # (no envelope), so they're deliberately NOT listed here.
    "create_cart": "detail",
    "rename_cart": "detail",
    "delete_cart": "detail",
    "set_active_cart": "detail",
    "add_cart_item": "detail",
    "update_cart_item": "detail",
    "remove_cart_item": "detail",
    "clear_cart": "detail",
    "add_bom_missing_to_cart": "detail",
    "split_cart": "detail",
    "consolidate_cart": "detail",
}

# Aliases: same route as `target`, different bridge method name, with an
# argOrder that may be a fixed-value subset of the target's full param set.
# `fixed` bakes a literal value into the target's path template at
# generation time (e.g. the distributor `name` path segment).
ALIASES: dict[str, dict[str, Any]] = {
    "rebuild_inventory": {"target": "list_parts", "arg_order": []},
    "check_digikey_session": {"target": "get_digikey_session", "arg_order": []},
    "get_digikey_login_status": {"target": "get_digikey_session", "arg_order": []},
    "fetch_lcsc_product": {
        "target": "fetch_distributor_product", "arg_order": ["code"],
        "fixed": {"name": "lcsc"},
    },
    "fetch_digikey_product": {
        "target": "fetch_distributor_product", "arg_order": ["code"],
        "fixed": {"name": "digikey"},
    },
    "fetch_mouser_product": {
        "target": "fetch_distributor_product", "arg_order": ["code"],
        "fixed": {"name": "mouser"},
    },
    "fetch_pololu_product": {
        "target": "fetch_distributor_product", "arg_order": ["code"],
        "fixed": {"name": "pololu"},
    },
    "parse_source_file": {"target": "parse_import_source", "arg_order": ["path", "template"]},
    "parse_source_file_b64": {
        "target": "parse_import_source", "arg_order": ["file_b64", "file_name", "template"],
    },
    "ocr_overlay_b64": {"target": "ocr_overlay", "arg_order": ["file_b64", "file_name", "template"]},
}


class GenerationError(Exception):
    """Raised when the openapi spec can't be turned into a complete API_MAP."""


def _resolve_body_params(op: dict, schemas: dict) -> tuple[list[str], bool]:
    """Return (bodyParams, raw_body) for an operation's requestBody, if any."""
    rb = op.get("requestBody")
    if not rb:
        return [], False
    schema = rb["content"]["application/json"]["schema"]
    ref = schema.get("$ref")
    if ref:
        schema_name = ref.split("/")[-1]
        return list(schemas[schema_name].get("properties", {}).keys()), False
    # Raw opaque object body (e.g. save_preferences) — no named fields.
    return [], True


def _build_base_entries(spec: dict) -> dict[str, dict[str, Any]]:
    """One entry per non-aliased /v1 operation, keyed by operationId."""
    schemas = spec.get("components", {}).get("schemas", {})
    entries: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for path, methods in spec["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for verb, op in methods.items():
            op_id = op.get("operationId")
            if op_id is None or op_id in SKIP_OPERATION_IDS:
                continue

            params = op.get("parameters", [])
            path_params = [p["name"] for p in params if p["in"] == "path"]
            query_params = [p["name"] for p in params if p["in"] == "query"]
            mutating = op_id in FINISH_MUTATION_OPERATION_IDS
            body_params, raw_body = _resolve_body_params(op, schemas)

            if raw_body:
                arg_name = RAW_BODY_ARG_NAME.get(op_id)
                if arg_name is None:
                    errors.append(
                        f"{op_id}: raw-body operation with no RAW_BODY_ARG_NAME entry"
                    )
                    continue
                arg_order = [arg_name]
            else:
                total = len(path_params) + len(query_params) + len(body_params)
                default_order = [*path_params, *query_params, *body_params]
                if total >= 2:
                    if op_id not in ARG_ORDER:
                        errors.append(
                            f"{op_id}: {total} positional params but no ARG_ORDER entry "
                            f"(default order would be {default_order!r}) — add one, "
                            "seeded from tests/python/test_api_surface.py"
                        )
                        continue
                    arg_order = ARG_ORDER[op_id]
                    expected = set(default_order)
                    got = set(arg_order)
                    if expected != got:
                        errors.append(
                            f"{op_id}: ARG_ORDER {sorted(got)} doesn't match the operation's "
                            f"actual params {sorted(expected)}"
                        )
                        continue
                else:
                    arg_order = default_order

            if op_id == "list_parts":
                unwrap = "inventory"
            elif op_id in UNWRAP_OVERRIDES:
                unwrap = UNWRAP_OVERRIDES[op_id]
            elif mutating:
                unwrap = "detail"
            else:
                unwrap = None

            entries[op_id] = {
                "verb": verb.upper(),
                "path": path,
                "argOrder": arg_order,
                "pathParams": path_params,
                "queryParams": query_params,
                "bodyParams": body_params,
                "rawBody": raw_body,
                "unwrap": unwrap,
                "mutating": mutating,
            }

    if errors:
        raise GenerationError(
            "gen-api-client.py: uncovered /v1 operation(s):\n  " + "\n  ".join(errors)
        )

    return entries


def _build_alias_entries(base: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for alias_name, cfg in ALIASES.items():
        target = cfg["target"]
        if target not in base:
            errors.append(f"{alias_name}: target op {target!r} not found in base entries")
            continue
        target_entry = base[target]
        path = target_entry["path"]
        for name, value in cfg.get("fixed", {}).items():
            path = path.replace("{" + name + "}", value)

        arg_order = cfg["arg_order"]
        arg_set = set(arg_order)
        aliases[alias_name] = {
            "verb": target_entry["verb"],
            "path": path,
            "argOrder": arg_order,
            "pathParams": [p for p in target_entry["pathParams"] if p in arg_set],
            "queryParams": [p for p in target_entry["queryParams"] if p in arg_set],
            "bodyParams": [p for p in target_entry["bodyParams"] if p in arg_set],
            "rawBody": target_entry["rawBody"],
            "unwrap": target_entry["unwrap"],
            "mutating": target_entry["mutating"],
        }
    if errors:
        raise GenerationError("gen-api-client.py: bad ALIASES entries:\n  " + "\n  ".join(errors))
    return aliases


def build_api_map(spec: dict) -> dict[str, dict[str, Any]]:
    base = _build_base_entries(spec)
    aliases = _build_alias_entries(base)
    merged = {**base, **aliases}
    return dict(sorted(merged.items()))


def render_js(api_map: dict[str, dict[str, Any]]) -> str:
    body = json.dumps(api_map, indent=2, sort_keys=True)
    header = (
        "// AUTO-GENERATED — do not edit by hand.\n"
        "// Source of truth: docs/openapi-v1.json\n"
        "// Regenerate: python scripts/gen-api-client.py\n"
        "\n"
        "export const API_MAP = "
    )
    return header + body + ";\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec", default=str(DEFAULT_SPEC),
        help="Path to the openapi snapshot (default: docs/openapi-v1.json)",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="Output path for the generated map (default: js/api-map.js)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if the output file is missing or stale (does not write)",
    )
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"error: {spec_path} does not exist. Run scripts/gen-openapi.py first.", file=sys.stderr)
        return 1
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    try:
        api_map = build_api_map(spec)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rendered = render_js(api_map)
    out = Path(args.out)

    if args.check:
        if not out.exists():
            print(
                f"error: {out} does not exist. Run `python scripts/gen-api-client.py` and commit.",
                file=sys.stderr,
            )
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"error: {out} is stale. Run `python scripts/gen-api-client.py` and commit.",
                file=sys.stderr,
            )
            return 1
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
