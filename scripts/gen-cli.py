#!/usr/bin/env python3
"""Generate the dubIS CLI command table from docs/openapi-v1.json.

Usage:
    python scripts/gen-cli.py            # write the generated artifacts
    python scripts/gen-cli.py --check    # exit 1 if any is stale

Reuses ``scripts/gen-api-client.py``'s ``build_api_map()`` — the same
transport-neutral IR that drives ``js/api-map.js`` — so the CLI inherits its
``ARG_ORDER``/``UNWRAP_OVERRIDES`` curation instead of re-deriving it, and any
new /v1 route shows up in both clients at once.

Emits:
  tools/dubis-cli/commands.py        the command table (COMMANDS)
  .claude/skills/dubis-cli/SKILL.md  on-demand agent docs

``dubis schema --json`` serializes COMMANDS at runtime rather than reading a
third generated file — one less artifact to keep in sync.

Two things this adds on top of the IR:

``writes``
    True for POST/PUT/PATCH/DELETE. This is deliberately NOT the IR's
    ``mutating`` flag, which means "the frontend must run finish_mutation and
    refresh inventory" — a different question. ``add_cart_item`` is a POST
    with ``mutating: false``; treating that as read-only would let --dry-run
    silently pass a real write straight through to the server.

``resource``/``verb``
    Derived from the path and operationId, then checked for collisions. A
    collision fails generation and demands an explicit VERB_OVERRIDES entry,
    matching gen-api-client.py's "fail loud, seed by hand" discipline for
    ARG_ORDER rather than silently picking a winner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pprint
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "docs" / "openapi-v1.json"
COMMANDS_OUT = REPO_ROOT / "tools" / "dubis-cli" / "commands.py"
SKILL_OUT = REPO_ROOT / ".claude" / "skills" / "dubis-cli" / "SKILL.md"

# Legacy OpenPnP compatibility shim (/api/consume, /api/health, /api/parts).
# It duplicates /v1 routes for a machine that speaks the old paths; exposing it
# in the CLI would offer two spellings of the same operation.
LEGACY_PREFIX = "/api/"

# Operations whose derived <resource> <verb> would collide or read badly.
# Keyed by operationId -> (resource, verb).
VERB_OVERRIDES: dict[str, tuple[str, str]] = {}

# Path segment -> CLI resource name, where the segment is a poor command noun.
RESOURCE_ALIASES: dict[str, str] = {
    "generic-parts": "generic",
    "purchase-orders": "po",
    "saved-searches": "saved-search",
}

WRITE_VERBS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Prepositions that only made sense while the resource token was still in the
# operationId; dropped from the tail of a derived verb.
_TRAILING_CONNECTORS = frozenset({"to", "from", "for", "in", "of", "with", "on"})


class GenerationError(Exception):
    """Raised when the spec cannot be turned into an unambiguous command table."""


def _load_gen_api_client():
    """Import gen-api-client.py as a module.

    The filename has hyphens, so it is not importable as a module path; load it
    by file location instead. The module guards its own main() behind
    __name__ == "__main__", so executing it here has no side effects.
    """
    path = Path(__file__).resolve().parent / "gen-api-client.py"
    spec = importlib.util.spec_from_file_location("_gen_api_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resource_of(path: str) -> tuple[str, str]:
    """(path segment, CLI resource noun) for a /v1 path.

    Both are returned because the verb derivation needs the ORIGINAL segment
    to know which tokens are redundant: under the alias ``po``, nothing in
    ``list_purchase_orders`` looks like the resource, and the verb would come
    out as ``list-purchase-orders`` instead of ``list``.
    """
    parts = [p for p in path.split("/") if p]
    segment = parts[1] if len(parts) > 1 else parts[0]
    return segment, RESOURCE_ALIASES.get(segment, segment)


def _singular_forms(resource: str) -> set[str]:
    """Tokens that name the resource itself, and so carry no information in a
    verb derived from an operationId (``adjust_part`` under ``parts``)."""
    forms = {resource}
    for token in resource.split("-"):
        forms.add(token)
        if token.endswith("s"):
            forms.add(token[:-1])
    if resource.endswith("s"):
        forms.add(resource[:-1])
    return forms


def _derive_verb(op_id: str, segment: str, resource: str) -> str:
    """Strip resource-naming tokens out of an operationId to get a verb.

    ``adjust_part`` under ``parts`` -> ``adjust``; ``add_cart_item`` under
    ``carts`` -> ``add-item``. Noise is drawn from both the path segment and
    the (possibly aliased) resource name, so ``list_purchase_orders`` under
    ``purchase-orders``/``po`` -> ``list``. If nothing survives the strip
    (``get_cart`` under ``carts``), the leading token is kept.
    """
    noise = _singular_forms(segment) | _singular_forms(resource)
    tokens = op_id.split("_")
    kept = [t for t in tokens if t not in noise]
    # A dangling preposition is left behind when the object it pointed at was
    # the resource itself (add_bom_missing_to_cart -> "add-bom-missing-to").
    while kept and kept[-1] in _TRAILING_CONNECTORS:
        kept.pop()
    if not kept:
        kept = [tokens[0]]
    return "-".join(kept)


def _param_types(spec: dict, verb: str, path: str) -> dict[str, dict[str, Any]]:
    """Per-parameter {type, required} for one operation.

    Covers path/query parameters (from the operation's ``parameters``) and
    JSON body properties (from its requestBody schema, resolving one level of
    $ref, which is all this snapshot uses). Types drive argparse coercion —
    without them every flag would arrive as a string and ``--qty 50`` would
    post ``"50"``.
    """
    operation = spec["paths"][path][verb.lower()]
    schemas = spec.get("components", {}).get("schemas", {})
    out: dict[str, dict[str, Any]] = {}

    for param in operation.get("parameters", []):
        # Via _schema_type, not a bare .get("type"): optional query params are
        # rendered as anyOf[X, null] and would otherwise all type as strings
        # (reel_ceiling is anyOf[number, null], and `--reel-ceiling 5` would
        # go over the wire quoted).
        out[param["name"]] = {
            "type": _schema_type(param.get("schema") or {}),
            "required": bool(param.get("required", False)),
        }

    body = operation.get("requestBody")
    if body:
        schema = body["content"]["application/json"]["schema"]
        ref = schema.get("$ref")
        if ref:
            schema = schemas[ref.rsplit("/", 1)[-1]]
        required = set(schema.get("required", []))
        for name, prop in schema.get("properties", {}).items():
            out[name] = {
                "type": _schema_type(prop),
                "required": name in required,
            }
    return out


def _schema_type(prop: dict) -> str:
    """Collapse an openapi property schema to one of the CLI's argparse types."""
    if "type" in prop:
        return prop["type"]
    # anyOf/oneOf (usually `X | null`): take the first non-null branch.
    for branch in prop.get("anyOf", []) or prop.get("oneOf", []):
        if branch.get("type") and branch["type"] != "null":
            return branch["type"]
    return "string"


def build_commands(spec: dict) -> dict[str, dict[str, Any]]:
    """Transform the api-map IR into a CLI command table keyed 'resource verb'."""
    gen_api_client = _load_gen_api_client()
    api_map = gen_api_client.build_api_map(spec)
    # gen-api-client's ALIASES exist to preserve the old pywebview bridge's
    # method names for js/api.js, and they bake fixed path params into the
    # path (e.g. {name} -> "digikey"), which then matches nothing in the spec.
    # The CLI derives its own names from the real routes, so aliases are pure
    # duplication here — skipped rather than special-cased.
    js_aliases = set(gen_api_client.ALIASES)

    commands: dict[str, dict[str, Any]] = {}
    collisions: dict[str, list[str]] = {}

    for op_id, entry in api_map.items():
        path = entry["path"]
        if path.startswith(LEGACY_PREFIX) or op_id in js_aliases:
            continue
        if op_id in VERB_OVERRIDES:
            resource, verb = VERB_OVERRIDES[op_id]
        else:
            segment, resource = _resource_of(path)
            verb = _derive_verb(op_id, segment, resource)

        name = f"{resource} {verb}"
        if name in commands:
            collisions.setdefault(name, [commands[name]["operationId"]]).append(op_id)
            continue

        types = _param_types(spec, entry["verb"], path)
        commands[name] = {
            "operationId": op_id,
            "resource": resource,
            "verb": verb,
            "httpVerb": entry["verb"],
            "path": path,
            "pathParams": entry["pathParams"],
            "queryParams": entry["queryParams"],
            "bodyParams": entry["bodyParams"],
            "rawBody": entry["rawBody"],
            "unwrap": entry["unwrap"],
            # See the module docstring: verb-derived, NOT the IR's `mutating`.
            "writes": entry["verb"] in WRITE_VERBS,
            "params": types,
        }

    if collisions:
        lines = [
            f"{name!r} <- {', '.join(op_ids)}"
            for name, op_ids in sorted(collisions.items())
        ]
        raise GenerationError(
            "gen-cli.py: colliding command names; add VERB_OVERRIDES entries for:\n  "
            + "\n  ".join(lines)
        )
    return dict(sorted(commands.items()))


def render_commands(commands: dict[str, dict[str, Any]]) -> str:
    # pprint, not json.dumps: this is a Python module, and JSON's true/false/
    # null are not Python literals. sort_dicts keeps the output deterministic
    # so --check compares equal across runs.
    body = pprint.pformat(commands, indent=1, width=100, sort_dicts=True)
    return (
        '"""AUTO-GENERATED — do not edit by hand.\n\n'
        "Source of truth: docs/openapi-v1.json\n"
        "Regenerate: python scripts/gen-cli.py\n"
        '"""\n\n'
        "COMMANDS = " + body + "\n"
    )


def render_skill(commands: dict[str, dict[str, Any]]) -> str:
    by_resource: dict[str, list[str]] = {}
    for name, cmd in commands.items():
        by_resource.setdefault(cmd["resource"], []).append(name)

    lines = [
        "---",
        "name: dubis-cli",
        "description: >-",
        "  Drive the dubIS parts inventory from the command line — search parts,",
        "  adjust stock, consume BOMs, plan carts. Use when the task involves dubIS",
        "  inventory data or the /v1 API.",
        "---",
        "",
        "<!-- AUTO-GENERATED by scripts/gen-cli.py — do not edit by hand. -->",
        "",
        "# dubis CLI",
        "",
        "Every command needs a running `/v1` server. Start one with `dubis serve`;",
        "without one, commands exit 4 and say so. `DUBIS_URL` overrides discovery.",
        "",
        "## Conventions",
        "",
        "- `dubis <resource> <verb> [path-args] [--flags]` — path params are",
        "  positional, everything else is a flag.",
        "- `--json` for machine-readable output on any command.",
        "- `--dry-run` on writing commands prints the request without sending it.",
        "- `--source NAME` tags mutations (default `cli`), so",
        "  `dubis adjustments rollback NAME` can undo a whole session.",
        "- Exit codes: 2 bad usage, 3 server error, 4 no server found.",
        "",
        "## Full surface",
        "",
        "`dubis schema --json` dumps every command with its params. "
        f"{len(commands)} commands:",
        "",
    ]
    for resource in sorted(by_resource):
        lines.append(f"- **{resource}**: " + ", ".join(
            sorted(n.split(" ", 1)[1] for n in by_resource[resource])
        ))
    lines.append("")
    return "\n".join(lines)


def _write_or_check(path: Path, rendered: str, check: bool) -> int:
    if check:
        if not path.exists():
            print(
                f"error: {path.relative_to(REPO_ROOT)} does not exist. "
                "Run `python scripts/gen-cli.py` and commit.",
                file=sys.stderr,
            )
            return 1
        if path.read_text(encoding="utf-8") != rendered:
            print(
                f"error: {path.relative_to(REPO_ROOT)} is stale. "
                "Run `python scripts/gen-cli.py` and commit.",
                file=sys.stderr,
            )
            return 1
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if any generated artifact is missing or stale (does not write)",
    )
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"error: {spec_path} does not exist. Run scripts/gen-openapi.py first.", file=sys.stderr)
        return 1
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    try:
        commands = build_commands(spec)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rc = _write_or_check(COMMANDS_OUT, render_commands(commands), args.check)
    rc |= _write_or_check(SKILL_OUT, render_skill(commands), args.check)
    return rc


if __name__ == "__main__":
    sys.exit(main())
