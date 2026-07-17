# Phase 2 — dubIS MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tools/dubis-mcp/` — a FastMCP stdio server exposing 10 curated inventory tools over the `/v1` API, with server discovery (env → port file → spawned fallback), live-server tests, and an OpenAPI-snapshot contract guard.

**Architecture:** per `docs/plans/2026-07-16-phase2-mcp-server-design.md` (binding: tool table, discovery order, compact-projection rule, out-of-scope list).

**Tech Stack:** FastMCP (`mcp` package, devtools pattern), httpx sync client, pytest against a real `python -m server`.

## Global Constraints

- Follow `tools/dev-tools-mcp/server.py` conventions (FastMCP module layout, docstring style, `@mcp.tool()`).
- Read tools return COMPACT projections (design doc table), never raw full records; every mutation sends `source="mcp"`.
- Health validation = `GET /v1/health` body `{ok: true}` (JSON-checked, not status-only — the 1b probe lesson).
- Tests use the real server harness (`python -m server --test-source ... --rollback-on-exit`, READY:<port> contract) — no HTTP mocks. No pytest.skip.
- Gates per task: `python -m pytest tests/python/test_dubis_mcp*.py -q` focused; before commit `python -m pytest tests/python/ -q`, `ruff check .`; full `bash scripts/verify.sh` in the final task. Full-log + explicit exit codes; never tail-gate.
- Worktree D:/gehub/dubIS/.claude/worktrees/platform-phase2-mcp, branch `claude/platform-phase2-mcp-server`. TDD failing-first.

---

### Task 1: Port file + v1client (discovery/spawn) + server skeleton with dubis_status

**Files:** Modify `server/run.py` (write/remove `<data_dir>/.v1_port`), `server/__main__.py` (same for standalone — check where the data dir is known), `app.pyw` only if run.py can't own it alone; Create `tools/dubis-mcp/v1client.py`, `tools/dubis-mcp/server.py` (FastMCP "dubis", `dubis_status` tool only), `tests/python/test_dubis_mcp_client.py`.

**Interfaces:**
- `server/run.py::start_server` gains `data_dir: str | None = None`; when given, after `server.started` write the bound port to `<data_dir>/.v1_port` (plain int text, atomic write); `stop_server` removes it (best-effort). `app.pyw` and `__main__.py` pass their data dir.
- `v1client.py`: `class V1Client` with `base_url`, `.get(path, **params)`, `.post(path, json)`, raising `V1Error(message, status)` on non-2xx (parse `{error}` body); module function `connect(repo_root) -> V1Client` implementing the discovery order (env DUBIS_URL → port file+healthcheck → spawn `python -m server --data-dir <repo>/data --port 0`, parse READY:<port>, register atexit terminate). Spawned child handle kept module-global for cleanup.
- `server.py`: FastMCP instance; `dubis_status()` → `{server: url, discovered_via: env|port_file|spawned, schema_version, part_count}` (health+meta+len(parts)).
- Tests: port-file write/remove roundtrip via `start_live_server` fixture pattern (reuse `tests/python/server/conftest.py::start_live_server`); discovery precedence (env wins; stale port file with dead port → ignored); V1Client error mapping (404 → V1Error with server's message). Spawn path: one test that spawns against a tmp data dir and gets a healthy client (mark it with a generous timeout).

TDD → implement → gates → commit `feat(mcp): dubis-mcp skeleton — /v1 discovery (env/port-file/spawn) + status tool`.

### Task 2: Read tools (search_parts, get_part, spec_search, low_stock, price_summary, part_history, list_generic_parts)

**Files:** Modify `tools/dubis-mcp/server.py`; Create `tests/python/test_dubis_mcp_tools.py`.

Per the design table exactly: compact projections, max_results capping with total-count reporting, get_part aggregation (parts+prices+purchase-history+groups+history[:5]), spec_search accepting numeric OR display-string values (route through POST /v1/spec/extract first when non-numeric), low_stock per-section thresholds from GET /v1/preferences when arg omitted. Tests run against ONE session-scoped real-server fixture (seeded tmp data dir with 3-4 parts incl. a 100nF 0402 so spec_search has a real hit — reuse `tests/python/helpers.py` seeding); assert projections' exact key sets (drift guard) and behaviors (search matches on description AND mpn; low_stock threshold logic; spec_search hit + miss).

Commit `feat(mcp): read tools — search/get/spec/low-stock/prices/history/generic-parts`.

### Task 3: Mutation tools (adjust_stock, consume_bom) + OpenAPI contract guard

**Files:** Modify `tools/dubis-mcp/server.py`, `v1client.py` (if needed); Create `tests/python/test_dubis_mcp_contract.py`.

- `adjust_stock`: POST /v1/parts/{k}/adjust with `source="mcp"`; returns `{part_key, new_qty}` (refetch or parse detail). `consume_bom`: verify the real body model in `server/routes/inventory_mut.py` for the minimal match dict shape; tool docstring documents it; source="mcp". Tests: adjust roundtrip (qty changes on the live server, adjustments.csv row has source=mcp — read via part_history), consume roundtrip, error surfaces (bad part_key → V1Error message passthrough into the MCP error).
- Contract guard: parse `docs/openapi-v1.json`; walk a declared table in v1client/server of every (verb, path-template) used; assert each exists in the snapshot and that request-body field names the tools send are ⊆ the operation's schema properties. Must FAIL on a fabricated route (prove in-test with a negative assertion helper, not by editing prod code).

Commit `feat(mcp): mutation tools + OpenAPI-snapshot contract guard`.

### Task 4: Docs + verify + PR

**Files:** Create `tools/dubis-mcp/README.md` (registration snippet for .mcp.json, tool list, discovery order, spawn-fallback caveat re: 1c lockfile); Modify `CLAUDE.md` (Agent Tooling third server + worked example; Data row gains `data/.v1_port`); regen code-map.

- `bash scripts/verify.sh` full PASS (claude-md guard must accept the new README path references).
- `bash scripts/push-pr.sh --title "feat(mcp): dubis MCP server — 10 curated inventory tools over /v1 (Phase 2)"` + body per the design doc; watch CI (full-log + explicit exits) to green. Controller merges.
