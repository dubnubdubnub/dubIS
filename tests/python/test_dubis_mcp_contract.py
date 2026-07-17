"""Contract guard for tools/dubis-mcp/server.py's USED_ROUTES (Task 3 of
docs/plans/2026-07-16-phase2-mcp-server-plan.md).

Parses the committed docs/openapi-v1.json snapshot and asserts every
(verb, path-template) tuple the MCP tools actually send exists in it, and
that every body field name the tools send is a real property of that
operation's request-body schema. This is a request-side pin: it does NOT
check response shapes (POST /v1/spec/extract, for one, returns its spec dict
unwrapped with no envelope — response-shape checking is out of scope here).

No live server involved — pure JSON parsing, so this module needs neither
the mcp_client fixture nor tools/dubis-mcp's sys.path insert (only
check_used_routes/USED_ROUTES from server.py, imported the same way
test_dubis_mcp_tools.py does).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = REPO_ROOT / "tools" / "dubis-mcp"
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi-v1.json"

# Insert only long enough to exec the module (it does a bare `import
# v1client`), then remove it again: leaving tools/dubis-mcp/ on sys.path
# would shadow the real top-level `server` package for every test module
# collected afterward in this session (tools/dubis-mcp/server.py is a
# same-named, non-package module) — test_dubis_mcp_tools.py hits this if
# collected after this file with the path left dangling.
sys.path.insert(0, str(MCP_DIR))
try:
    _spec = importlib.util.spec_from_file_location("dubis_mcp_server", str(MCP_DIR / "server.py"))
    dubis_mcp_server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(dubis_mcp_server)
finally:
    sys.path.remove(str(MCP_DIR))


@pytest.fixture(scope="module")
def openapi() -> dict:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_used_routes_all_exist_in_snapshot(openapi):
    # Must not raise: every route + body field the tools declare is real.
    dubis_mcp_server.check_used_routes(dubis_mcp_server.USED_ROUTES, openapi)


def test_used_routes_nonempty_and_covers_mutations():
    # Sanity: the table isn't accidentally empty, and both Task 3 mutation
    # routes are present (guards against silently deleting the declarations).
    assert dubis_mcp_server.USED_ROUTES
    declared = {(verb, path) for verb, path, _ in dubis_mcp_server.USED_ROUTES}
    assert ("post", "/v1/parts/{part_key}/adjust") in declared
    assert ("post", "/v1/bom/consume") in declared


def test_guard_fails_on_fabricated_route(openapi):
    # Negative proof the guard actually guards: a route that was never
    # registered on the real FastAPI app must not be silently accepted.
    fabricated = [*dubis_mcp_server.USED_ROUTES, ("post", "/v1/parts/{part_key}/self-destruct", ())]
    with pytest.raises(AssertionError, match="route not in OpenAPI snapshot"):
        dubis_mcp_server.check_used_routes(fabricated, openapi)


def test_guard_fails_on_fabricated_body_field(openapi):
    # Negative proof for the body-field half of the guard: a real route with
    # a body field that doesn't exist on its schema must not be accepted.
    fabricated = [*dubis_mcp_server.USED_ROUTES, ("post", "/v1/bom/consume", ("not_a_real_field",))]
    with pytest.raises(AssertionError, match="sends body field"):
        dubis_mcp_server.check_used_routes(fabricated, openapi)
