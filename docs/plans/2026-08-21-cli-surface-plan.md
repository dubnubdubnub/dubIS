# Plan: replace `tools/dubis-mcp` with a generated CLI

**Date:** 2026-08-21
**Goal:** agents drive dubIS inventory through a CLI covering the full `/v1`
surface, generated from `docs/openapi-v1.json`. `tools/dubis-mcp` is removed.

## Why generated, not hand-written

`scripts/gen-api-client.py` already solves the hard part: `build_api_map(spec)`
(line 405) returns a transport-neutral IR — `{verb, path, argOrder, pathParams,
queryParams, bodyParams, rawBody, unwrap, mutating}` per operation — and
`render_js()` (line 412) is the only JS-specific code. A second emitter reading
the same IR gets all 81 routes for the cost of one renderer, and inherits the
existing `ARG_ORDER` / `UNWRAP_OVERRIDES` curation.

The MCP README going stale about the Phase 1c lockfile is the failure this
avoids: every agent-facing artifact below is generated and `--check`-guarded.

## What must survive the removal

`tools/dubis-mcp` is not just transport. Three things carry over or are lost:

1. **Compact projections** (`server.py::_compact_part`) — 6 fields, not the
   full 14-field record. Context economy.
2. **The add/remove precheck** (`server.py::adjust_stock`) — `/v1`'s domain
   layer silently no-ops `add`/`remove` on an unknown key and returns
   `{"new_qty": null}`. The precheck raises instead.
3. **Source tagging** — mutations tag `source`, making
   `DELETE /v1/adjustments/by-source/{source}` a working undo.

Also worth keeping: `server.py::check_used_routes` (line 565), the route-drift
guard proving every route the client uses exists in the OpenAPI snapshot with
valid body fields. In the generated world it becomes stronger — it can assert
over the whole emitted command table rather than a hand-maintained list.

## Proposed shape

```
dubis <resource> <verb> [args] [--json] [--source X] [--dry-run]

dubis serve                       # start the /v1 server (everything else needs one)
dubis parts search 100nF
dubis parts adjust C1234 --type add --qty 50 --note "reel"
dubis carts plan 3
dubis schema --json
```

Generated resource-verb commands for all 81 routes, plus a short alias table
for the ten hot-path commands that were MCP tools (`dubis search`,
`dubis low-stock`, ...) so the common case stays terse.

## Phases

Build first, remove second — no window where neither surface works.

### 1. Shared client core
Move `tools/dubis-mcp/v1client.py` → `tools/dubis_client/v1client.py`, keeping
auth, `V1Error`, and the first two discovery steps (`DUBIS_URL` env →
health-checked `<data_dir>/.v1_port`).

**Drop the spawn fallback** — delete `_spawn_server`, `shutdown_spawned`,
`_spawned_process`, and the `atexit` registration. `connect()` raises a typed
`NoServerFoundError` instead of spawning.

Rationale: the fallback's cost model assumed MCP's one-long-lived-process-per-
session shape, where the spawn amortizes across the whole session. A CLI is one
process per *invocation*, so `connect()` runs on every command. Measured floor
for spawn→ready→teardown is ~0.57s (interpreter + imports + uvicorn boot +
teardown), before any inventory build. Worse than the latency: every spawn
takes the data-dir lock, so two concurrently-invoked commands — routine for an
agent issuing parallel tool calls — make the second fail with
`DataDirLockedError` against a server the first one just started. Phase 1c's
lockfile closed the data-corruption race; it does not close this one, which is
created by the short process lifetime. Orphan risk compounds it: `atexit` does
not run on hard kill, so a Ctrl-C'd command can leave a lock-holding server
behind and break the next desktop launch with no visible cause.

Also port `_compact_part`, `_derive_part_key`, `_matches_part`, `_find_part`,
and the adjust precheck into `tools/dubis_client/curate.py`.

### 2. Generator
New `scripts/gen-cli.py`, importing `build_api_map` from `gen-api-client.py`.
Emits, with a `--check` mode for each:

| Output | Purpose |
|---|---|
| `tools/dubis-cli/commands.py` | generated command table (the IR, as Python) |
| `tools/dubis-cli/SCHEMA.json` | machine-readable full surface for `dubis schema --json` |
| `.claude/skills/dubis-cli/SKILL.md` | on-demand agent docs |

Refactor `gen-api-client.py` so `build_api_map` is importable without side
effects; leave `render_js` and its output byte-identical (guarded by the
existing `api-client` step in verify.sh).

### 3. CLI runtime
`tools/dubis-cli/dubis.py` — argparse dispatch over the generated table.

- `--json` everywhere; compact projection default, `--full` to opt out.
- `--source` defaulting to `cli`.
- `--dry-run` on every `mutating: true` command: print the request, don't send.
- argparse `choices=` so bad subcommands list the valid ones on stderr.
- Distinct exit codes: `2` bad usage, `3` server error, `4` no server found.
- Add a `dubis` console script to `pyproject.toml` (none exist today).

**`dubis serve`** — hand-written (not generated; it starts a server rather than
calling a route), a thin wrapper over `python -m server` passing through
`--data-dir` / `--host` / `--port`. This replaces the dropped spawn fallback:
lifecycle becomes explicit and visible instead of implicit and per-command.

`NoServerFoundError` maps to exit 4 with an actionable message naming the fix:

```
no /v1 server found. start one with `dubis serve`, or set DUBIS_URL
```

Headless callers (CI, cron) start one `dubis serve` for the run and let every
subsequent command discover it via the port file — one line in the job, and
honest about who owns the writer.

### 4. Discovery surface
- `dubis schema --json` — one call, whole surface.
- `.claude/skills/dubis-cli/SKILL.md` — generated; `.claude/skills/` does not
  exist yet, this creates it.
- ~12 lines in `CLAUDE.md` for the hot path only, replacing the existing
  dubis-MCP block in the Agent Tooling section.

### 5. Tests
Port the three MCP suites (804 lines total) to CLI equivalents:

| From | To | Note |
|---|---|---|
| `test_dubis_mcp_client.py` (259) | `test_dubis_client.py` | discovery/auth — drop the spawn-fallback cases, add `NoServerFoundError` |
| `test_dubis_mcp_tools.py` (468) | `test_dubis_cli_commands.py` | per-command behaviour, incl. the adjust precheck |
| `test_dubis_mcp_contract.py` (77) | `test_dubis_cli_contract.py` | route-drift guard, widened to the generated table |

Add: generated-artifact staleness tests, `--dry-run` sends nothing, exit-code
mapping.

### 6. Removal
Delete `tools/dubis-mcp/` and the three old test files. Update the seven
referencing files — all but two are one-line comments:

- `CLAUDE.md` — Agent Tooling section (**required**: `scripts/check-claude-md.py`
  fails on backticked paths that no longer exist, so removal and doc update
  must land in the same commit)
- `docs/entity-store.md:70`, `docs/deploy-runbook.md:283` — prose
- `app.pyw:254`, `server/__main__.py:138`, `server/routes/parts_read.py:81`,
  `tests/python/server/test_predicates_routes.py:4`,
  `tests/python/domain/test_pricing.py:1051` — comments naming the file
- `docs/code-map.md` — regenerate via `scripts/gen-code-map.py`
- `.mcp.json.example` — drop the `dubis` entry

Leave `docs/plans/2026-07-16-phase2-mcp-server-{design,plan}.md` as historical
record.

**User action, not scriptable:** `.mcp.json` is local and untracked — the
`dubis` MCP entry must be removed by hand.

### 7. Wiring
Add to `scripts/verify.sh` after step 4d, matching the existing pattern:

```bash
# 4e. cli
run_step "cli" "$PY" scripts/gen-cli.py --check
```

## Decisions

All three open questions are settled; no blockers remain.

1. **Alias table — include it.** Ten terse hot-path aliases (`dubis search`,
   `dubis low-stock`, ...) over the generated `dubis <resource> <verb>` table,
   matching the names of today's MCP tools. Cheap, and it's the surface agents
   hit most.
2. **Spawn fallback — dropped**, replaced by explicit `dubis serve`. See
   phases 1 and 3 for the reasoning and the measurement behind it.
3. **Two packages** — `tools/dubis_client` (HTTP + curation) and
   `tools/dubis-cli` (argparse surface), so the client stays reusable.

## Not in scope

Changing `/v1` itself. Every route already exists; this is a new client only.

## Outcome (2026-08-22)

Shipped in PR #424 across four commits. 87 commands generated; 61 tests
replacing the MCP suites; all 14 `verify.sh` gates green.

Deviations from the plan above, and why:

- **No `console_script`.** `pyproject.toml` has no `[project]` table, so
  getting `dubis` onto PATH would mean making the repo an installable package
  and reckoning with `dubIS.spec` (PyInstaller). Shipped `scripts/dubis`, a
  shim, with a symlink line in its header.
- **Two generated artifacts, not three.** `SCHEMA.json` was dropped — `dubis
  schema --json` serializes the in-memory table instead, so there is one less
  file to keep in sync.
- **The hot-path "alias table" was not built.** Decision 1 assumed the ten MCP
  tools were aliases for single routes. They are not: `get_part` aggregates
  five calls, `search_parts` filters client-side, `low_stock` reads a
  preferences threshold. They are a *curated command family* that has to be
  hand-written against `dubis_client.curate`, not generated. The generated
  surface covers every route and the curation hooks preserve the safety
  properties, so nothing is lost in capability — but `dubis search 100nF` does
  not exist yet, and the terse hot path is still worth adding.
- **`.claude/skills/` is now tracked** (`.gitignore` narrowed to `.claude/*`
  with a `!.claude/skills/` negation). A generated artifact that CI must
  staleness-check cannot be untracked. This makes the directory shared team
  state rather than local config.
- **`.mcp.json.example` needed no edit** — it never carried a dubis entry. The
  local untracked `.mcp.json` still does and must be edited by hand.

Two bugs the build surfaced, both recorded in commit messages: the api-map's
`mutating` flag does not mean "writes" (it means "the frontend must refresh
inventory"), and optional query params are `anyOf[X, null]` so a bare
`schema.type` read mistypes them as strings.
