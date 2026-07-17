"""MCP dubis server — curated inventory tools over the /v1 API.

FastMCP stdio server, same layout/conventions as tools/dev-tools-mcp/server.py.
Tools call /v1 over HTTP via v1client.py's V1Client rather than talking to
InventoryApi directly — this process never touches the CSVs/SQLite cache
itself; the /v1 server (desktop app, or a spawned standalone instance) is the
single writer.

Task 1 ships only dubis_status(); read/mutation tools land in later tasks
(see docs/plans/2026-07-16-phase2-mcp-server-plan.md).
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from v1client import V1Client, connect

mcp = FastMCP("dubis")

# tools/dubis-mcp/server.py -> repo root is two levels up.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_client: V1Client | None = None


def _get_client() -> V1Client:
    """Lazily discover and cache the /v1 client for this process's lifetime.

    Lazy (not at import time) so importing this module — e.g. from tests —
    never triggers discovery/spawn as a side effect.
    """
    global _client
    if _client is None:
        _client = connect(str(REPO_ROOT))
    return _client


@mcp.tool()
def dubis_status() -> dict:
    """Report which /v1 server this MCP session is talking to.

    Returns:
        {server, discovered_via, schema_version, part_count} — discovered_via
        is one of "env", "port_file", "spawned" (see v1client.connect()'s
        discovery order).
    """
    client = _get_client()
    client.get("/v1/health")
    meta = client.get("/v1/meta")
    parts = client.get("/v1/parts")
    part_count = len(parts.get("inventory", [])) if isinstance(parts, dict) else len(parts)
    return {
        "server": client.base_url,
        "discovered_via": client.discovered_via,
        "schema_version": meta.get("schema_version"),
        "part_count": part_count,
    }


if __name__ == "__main__":
    mcp.run()
