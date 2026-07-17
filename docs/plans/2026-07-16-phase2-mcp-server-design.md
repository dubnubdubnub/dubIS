# Phase 2 — dubIS MCP Server — Design

**Date:** 2026-07-16
**Parent:** `docs/plans/2026-07-15-platform-architecture-design.md` (Phase 2). Builds on Phase 1a/1b (`/v1` API, PRs #359/#360).
**Standing decisions (owner):** stdio MCP server in `tools/` (devtools pattern: FastMCP), ~10 curated tools wrapping `/v1` — NOT 76 auto-generated ones — with schemas/contracts pinned to the OpenAPI snapshot.

## Purpose

Any Claude session (Claude Code, claude.ai with a connector, other agents) gets first-class dubIS access: "do I have a 100nF 0402 in stock?", "log that I used 3 of C2040", "what did I pay for this part?". This is the tooling-compounds phase — Phases 3/4 build and test against it.

## Architecture

```
Claude ──stdio──▶ tools/dubis-mcp/server.py (FastMCP "dubis")
                        │ httpx (sync)
                        ▼
                  /v1 HTTP API  ← desktop app's loopback server (preferred)
                                ← or python -m server (spawned fallback)
```

- **`tools/dubis-mcp/server.py`** — FastMCP server, same layout/conventions as `tools/dev-tools-mcp/`. Tools call `/v1` over HTTP via a small client module `tools/dubis-mcp/v1client.py`.
- **Server discovery (in order):**
  1. `DUBIS_URL` env var (explicit override, e.g. a tailnet server later).
  2. Port file `data/.v1_port` — **new in this phase:** `server/run.py`/`app.pyw` writes the bound port to `<data_dir>/.v1_port` on startup and removes it on clean shutdown; the MCP server reads it and health-checks (`GET /v1/health` must return `{ok: true}` — the JSON-validated probe lesson from 1b).
  3. **Spawned fallback:** if no live server is found, spawn `python -m server --data-dir <repo>/data --port 0` as a child process (READY:<port> stdout contract from Task 1b-9), own its lifecycle (terminate at exit). Single-writer stays safe: the spawn only happens when no other server is running, and the port file doubles as the liveness signal. If the port file exists but the health check fails (stale file after a crash), ignore it and proceed to spawn.
- **Source attribution:** every mutation the MCP server makes sends `source="mcp"` (the 1c identity work will enrich this; the field exists on adjust/consume today).

## Tool surface (curated, 10)

All read tools return compact JSON (trimmed fields, counts) — MCP results land in agent context; never dump the full 14-field inventory list when a filtered projection answers the question.

| Tool | Wraps | Notes |
|---|---|---|
| `dubis_status()` | GET /v1/health + /v1/meta | Which server (url/spawned), schema version, part count. |
| `search_parts(query="", section="", max_results=25)` | GET /v1/parts | Client-side filter over lcsc/mpn/description/manufacturer/package (case-insensitive substring); returns `{part_key, description, qty, section, package, unit_price}` projection + total match count. |
| `get_part(part_key)` | GET /v1/parts + /prices + /purchase-history + /groups + /history (last 5) | One aggregated detail card. 404 → clear error string. |
| `spec_search(part_type, value, package="")` | POST /v1/bom/resolve-spec (+ /v1/spec/extract when value is a string like "100nF") | Accepts numeric value or display string; returns the generic group + best in-stock member or "no match". |
| `low_stock(threshold=None)` | GET /v1/parts + /v1/preferences | Parts at/below threshold (per-section thresholds from prefs when threshold arg omitted). |
| `adjust_stock(part_key, adj_type, quantity, note="")` | POST /v1/parts/{k}/adjust | source="mcp". Returns new qty (from the refetched part). |
| `consume_bom(matches, board_qty=1, bom_name="mcp-bom")` | POST /v1/bom/consume | matches: `[{part_key, bom_qty}]` (the minimal match shape — verify against the body model and document in the tool description). source="mcp". |
| `price_summary(part_key)` | GET /v1/parts/{k}/prices + /last-po-quantity | Per-distributor latest/avg + last PO qty. |
| `part_history(part_key, limit=10)` | GET /v1/parts/{k}/history | Adjustment log projection. |
| `list_generic_parts(part_type="")` | GET /v1/generic-parts | Groups + member counts + best-stock member; optional type filter. |

Explicitly OUT (this phase): mutations beyond adjust/consume (vendors, POs, imports — desktop/UI concerns), distributor fetches (network-bound, credential-coupled), feeder tools (Phase 3), any write to generic parts.

## Contract guard

`tests/python/test_dubis_mcp.py` includes a **snapshot-pin test**: every `(verb, path)` the v1client uses must exist in `docs/openapi-v1.json` (parse the snapshot, assert each route+method the client declares is present, with params/body fields the client sends being a subset of the operation's schema). Server drift then fails CI here, mirroring the api-map guard philosophy. Tool behavior tests run against a real `python -m server --test-source mcp-test --rollback-on-exit` on a tmp data dir (the Task 9 harness) — no HTTP mocking; the live server IS the fixture.

## Dependencies

`mcp` (FastMCP) + `httpx` added to `requirements-dev.txt` (already dev-installed for the devtools server / TestClient; make them explicit). NOT runtime deps — the MCP server is developer/agent tooling like devtools; runtime `requirements.txt` unchanged.

## Docs

- `.mcp.json` is local/untracked — document the registration snippet in `tools/dubis-mcp/README.md` and CLAUDE.md's Agent Tooling section (add a third MCP server entry with a worked example: `search_parts("100nF")`).
- CLAUDE.md gains the port-file mention in the Data row (`data/.v1_port`, runtime).

## Risks

- **Two servers, one data dir:** the spawned fallback could race a desktop app started moments later. Mitigation: port file check happens at spawn time and the spawned server ALSO writes the port file (it goes through the same `server/run.py` path), so the later-started desktop app... would collide. 1c owns the data-dir lockfile; this phase documents the limitation in the README ("prefer running the desktop app or a standalone server; the spawn fallback is a convenience for headless use").
- **Windows child-process cleanup:** the spawn fallback must terminate its child on exit (atexit + best effort; the Task-9 lesson — atexit doesn't run on hard kill — is acceptable here since the child rolls back nothing and its port file goes stale-but-ignored thanks to the health check).
