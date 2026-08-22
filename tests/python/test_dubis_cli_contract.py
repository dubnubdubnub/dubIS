"""Contract guard for the generated CLI surface.

Successor to the retired tests/python/test_dubis_mcp_contract.py, which pinned
a hand-maintained USED_ROUTES table against the committed OpenAPI snapshot.
The CLI's table is generated from that snapshot instead, so the guard widens
from "the ten routes the tools happen to call" to "every command the generator
emits", and gains a staleness check — the failure that let the MCP README
claim for months that the Phase 1c lockfile did not exist.

No live server: pure JSON/table inspection.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi-v1.json"
CLI_DIR = REPO_ROOT / "tools" / "dubis-cli"
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "dubis-cli" / "SKILL.md"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen_cli = _load("gen_cli", REPO_ROOT / "scripts" / "gen-cli.py")
commands_module = _load("dubis_cli_commands", CLI_DIR / "commands.py")
COMMANDS = commands_module.COMMANDS


@pytest.fixture(scope="module")
def openapi() -> dict:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


# ── the generated artifacts match the spec ───────────────────────────────────


def test_commands_table_is_not_stale(openapi):
    """Equivalent to `python scripts/gen-cli.py --check` for the table."""
    assert gen_cli.build_commands(openapi) == COMMANDS


def test_skill_file_is_not_stale(openapi):
    rendered = gen_cli.render_skill(gen_cli.build_commands(openapi))
    assert SKILL_PATH.read_text(encoding="utf-8") == rendered


def test_generator_is_deterministic(openapi):
    assert gen_cli.build_commands(openapi) == gen_cli.build_commands(openapi)


# ── every command names a real route ─────────────────────────────────────────


def test_every_command_path_exists_in_the_snapshot(openapi):
    paths = openapi["paths"]
    for name, cmd in COMMANDS.items():
        assert cmd["path"] in paths, f"{name}: {cmd['path']} not in OpenAPI snapshot"
        verb = cmd["httpVerb"].lower()
        assert verb in paths[cmd["path"]], f"{name}: {verb.upper()} not on {cmd['path']}"


def test_every_body_param_is_a_real_schema_property(openapi):
    """The MCP guard's request-side pin, widened to every generated command."""
    schemas = openapi.get("components", {}).get("schemas", {})
    for name, cmd in COMMANDS.items():
        if not cmd["bodyParams"]:
            continue
        operation = openapi["paths"][cmd["path"]][cmd["httpVerb"].lower()]
        body = operation.get("requestBody")
        assert body is not None, f"{name} sends a body but the route takes none"
        schema = body["content"]["application/json"]["schema"]
        ref = schema.get("$ref")
        if ref:
            schema = schemas[ref.rsplit("/", 1)[-1]]
        allowed = set(schema.get("properties", {}).keys())
        extra = set(cmd["bodyParams"]) - allowed
        assert not extra, f"{name} sends body field(s) {sorted(extra)} not in its schema"


def test_every_path_param_appears_in_the_path_template():
    for name, cmd in COMMANDS.items():
        for param in cmd["pathParams"]:
            assert "{" + param + "}" in cmd["path"], f"{name}: {param} not in path"


def test_the_guard_actually_guards(openapi):
    """A fabricated command must be rejected — otherwise the checks above
    could be vacuously passing."""
    poisoned = dict(COMMANDS)
    poisoned["fake resource"] = {
        **COMMANDS["parts adjust"],
        "path": "/v1/not-a-real-route",
    }
    paths = openapi["paths"]
    with pytest.raises(AssertionError):
        for name, cmd in poisoned.items():
            assert cmd["path"] in paths, f"{name}: {cmd['path']} not in OpenAPI snapshot"


# ── properties the runtime depends on ────────────────────────────────────────


def test_writes_is_derived_from_the_http_verb():
    """`writes` must NOT be the api-map's `mutating`, which means "the frontend
    must refresh inventory" — add_cart_item is a POST with mutating:false, and
    treating it as read-only would let --dry-run pass a real write through."""
    for name, cmd in COMMANDS.items():
        expected = cmd["httpVerb"] in {"POST", "PUT", "PATCH", "DELETE"}
        assert cmd["writes"] is expected, name


def test_add_cart_item_is_a_write():
    # The specific case that motivated not reusing `mutating`.
    assert COMMANDS["carts add-item"]["writes"] is True


def test_no_command_name_collisions():
    names = [f"{c['resource']} {c['verb']}" for c in COMMANDS.values()]
    assert len(names) == len(set(names))


def test_command_keys_match_their_resource_and_verb():
    for name, cmd in COMMANDS.items():
        assert name == f"{cmd['resource']} {cmd['verb']}"


def test_every_param_has_a_type_the_cli_can_coerce():
    known = {"string", "integer", "number", "boolean", "array", "object"}
    for name, cmd in COMMANDS.items():
        for param, spec in cmd["params"].items():
            assert spec["type"] in known, f"{name}.{param}: {spec['type']}"


def test_legacy_openpnp_shim_is_not_exposed():
    """/api/* duplicates /v1 routes for the machine that speaks the old paths;
    exposing it would give two spellings of the same operation."""
    for name, cmd in COMMANDS.items():
        assert not cmd["path"].startswith("/api/"), name


def test_colliding_verbs_fail_generation(openapi):
    """The generator must refuse an ambiguous table rather than silently
    picking a winner — same discipline gen-api-client.py applies to ARG_ORDER."""
    original = dict(gen_cli.RESOURCE_ALIASES)
    try:
        # Fold two distinct resources onto one name so their verbs can clash.
        gen_cli.RESOURCE_ALIASES["generic-parts"] = "parts"
        with pytest.raises(gen_cli.GenerationError):
            gen_cli.build_commands(openapi)
    finally:
        gen_cli.RESOURCE_ALIASES.clear()
        gen_cli.RESOURCE_ALIASES.update(original)
