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
      "unwrap": "inventory",
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

``mutating`` is true exactly when the operation exposes an ``include`` query
parameter (i.e. it goes through ``server.mutations.finish_mutation``);
``js/api.js`` uses this flag to auto-append ``?include=inventory``, and the
same condition drives the default ``unwrap: "inventory"``.

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
    "add_generic_member": ["generic_part_id", "part_id"],
    "adjust_part": ["adj_type", "part_key", "quantity", "note", "source"],
    "consume_bom": ["matches", "board_qty", "bom_name", "note", "source"],
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

# Scalar-envelope unwraps for operations that don't go through
# finish_mutation's ?include=inventory convention. `list_parts` is the one
# GET whose *own* body IS the inventory envelope (unwrap "inventory") even
# though it has no `include` query param / isn't "mutating".
#
# CFG (config-mutation) class: these mutate config/generic-parts/saved-search
# state, not inventory rows. Their routes call finish_mutation with
# `include=None` (no `?include=inventory` support) or, for
# fetch_missing_descriptions/record_fetched_prices, DO accept `include` but
# put the client-relevant payload in `detail` regardless (see
# server/routes/*.py). Call sites (js/vendors-modal.js, js/inventory/
# inv-mutations.js, js/group-flyout/flyout-drag.js, js/group-flyout/
# flyout-events.js, js/inventory/fetch-descriptions-command.js) consume the
# facade return directly (e.g. `v.name`, `result.generic_part_id`,
# `result.id`), so these must unwrap "detail" — the envelope's `detail` field
# carries exactly that facade return — never "inventory".
UNWRAP_OVERRIDES: dict[str, str] = {
    "list_parts": "inventory",
    "get_generic_group_names": "groups",
    "get_last_po_quantity": "quantity",
    "has_purchase_history": "has_purchase_history",
    "extract_spec": "spec",
    "resolve_bom_spec": "match",
    "fetch_favicon": "path",
    # CFG class — see comment above.
    "update_vendor": "detail",
    "create_generic_part": "detail",
    "update_generic_part": "detail",
    "add_generic_member": "detail",
    "remove_generic_member": "detail",
    "exclude_generic_member": "detail",
    "set_preferred_member": "detail",
    "create_saved_search": "detail",
    "delete_saved_search": "detail",
    "record_fetched_prices": "detail",
    "fetch_missing_descriptions": "detail",
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
            query_params = [
                p["name"] for p in params if p["in"] == "query" and p["name"] != "include"
            ]
            mutating = any(p["name"] == "include" for p in params)
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
                # Checked before the `mutating` default so CFG-class mutating
                # ops (fetch_missing_descriptions, record_fetched_prices) can
                # override "inventory" with "detail" — see UNWRAP_OVERRIDES.
                unwrap = UNWRAP_OVERRIDES[op_id]
            elif mutating:
                unwrap = "inventory"
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
