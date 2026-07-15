# dubIS Platform Architecture — Program Design

**Date:** 2026-07-15
**Status:** Approved (program-level). Each phase gets its own spec + implementation plan.
**Scope:** Evolve dubIS from a desktop inventory app into the hub of the PCB design→assembly workflow — component selection (KiCad live library), BOM/purchasing, and OpenPnP board/feeder setup — while the out-of-the-box experience remains a simple desktop app.

## Decisions (settled with owner)

1. **Primary API consumers:** Claude agents (MCP), OpenPnP (HTTP), humans (desktop UI). KiCad integrates via a *live library* (KiCad 8+ HTTP-library protocol), not a plugin.
2. **Server-authoritative when deployed:** the optional standalone server (docker/standalone) owns its data dir; all clients — including the desktop app — talk to it over HTTP. OOTB, the desktop spawns the same server on loopback. Single writer by construction.
3. **Full client/server split in v1:** the pywebview JS bridge is removed. The webview becomes a plain browser pointed at the local server. One code path for local and remote.
4. **API shape:** one versioned service API (`/v1`) that the desktop itself dogfoods. Resources where natural (`/v1/parts`, `/v1/generic-parts`, `/v1/prices`), commands where natural (`/v1/bom/consume`, `/v1/parts/{id}/adjust`), plus `/v1/events` (WebSocket/SSE) replacing `evaluate_js` pushes. Not strict-REST dogma; UI-local concerns (file dialogs, close-confirm) leave the API and live in a ~3-method client shell.
5. **Radical long-term-health bias:** prefer deleting dual paths over maintaining compatibility shims.

## What does NOT change

- **CSVs stay the source of truth** (git-diffable, human-editable, atomic-write layer proven). SQLite stays a deletable materialized view — and the fixes below make that guarantee actually true.
- **Vanilla JS, no build step** frontend.
- **`domain/` as the logic layer**; guard architecture (`verify.sh`, staleness regeneration, frozen-surface contract testing) — repointed, not removed.

## Architectural fixes motivating the design (from 2026-07-15 assessment)

- **CRITICAL — "cache.db is deletable" is currently false:** manual generic parts / member adds / exclusions live only in SQLite (`domain/generic_parts.py`); rebuild only regenerates `source='auto'`; `SCHEMA_VERSION` bump drops the tables. `events/part_events.csv` is written but never replayed (decoy event-sourcing). Saved-searches (`saved_searches.json` + `load_into_db`) is the correct persistence template.
- **HIGH — unstable part identity:** `get_part_key()` derives identity (LCSC > MPN > DigiKey > Pololu > Mouser) at read time; enriching a part with a higher-precedence PN silently changes its identity, orphaning adjustments, price observations, group memberships, and any future feeder assignment. Pricing already warn-and-drops on key mismatch.
- **HIGH-latent — no cross-process locking:** `RLock` is in-process only; multi-process read-modify-write on `purchase_ledger.csv` is last-writer-wins. Server-authoritative mode dissolves this; a data-dir lock guards the transition.
- **Three ad-hoc HTTP surfaces** (`pnp_server` :7890 unauth write, mirror :7893 read-only, pywebview bridge) with duplicated part-resolution → consolidate into one server.
- **Full-inventory return on every mutation** and frequent full rebuilds → mutations return their result; clients refresh via `/v1/events`.

## Target architecture

```
                      ┌───────────────────────────────────────┐
                      │             dubis-server               │
                      │   (one process, owns the data dir)     │
 KiCad HTTP library ─▶│  /v1/parts  /v1/generic-parts          │
 Claude MCP server  ─▶│  /v1/adjustments  /v1/bom/*            │
 OpenPnP Jython     ─▶│  /v1/pnp/* (consume, part-map, feeders)│
 Desktop frontend   ─▶│  /v1/kicad/*   /v1/events (WS/SSE)     │
 (webview=browser)    │                                         │
                      │  domain/ (unchanged) + CSV SoT          │
                      │  + SQLite cache (truly deletable)       │
                      └───────────────────────────────────────┘
```

- **OOTB desktop:** `app.pyw` spawns dubis-server on a loopback port, opens pywebview at `http://127.0.0.1:<port>`. UX identical to today.
- **Server deployment:** same binary as docker image / standalone on the tailnet. Auth reuses the mirror model: loopback trusted; remote requires tailnet identity allowlist and/or token. Authenticated identity is stamped into every mutation's `source` field (`mcp:claude`, `tailnet:user@…`, `openpnp`).
- **Consolidation:** `pnp_server.py` routes become `/v1/pnp/*` (+ scan pages served by the same server); mirror read endpoints become `/v1/parts`; the standalone mirror daemon is retired or kept only as the "readable while dubIS is down" fallback until server deployment replaces it.

## Cross-cutting cleanups (approved)

1. **One schema, all artifacts:** `domain/schema.py` SSOT extends to generate the OpenAPI spec, MCP tool schemas, and API contract-test fixtures alongside `js/inventory-record.d.ts`. Four descriptions of the wire format collapse into one generated set.
2. **Uniform entity-store convention:** durable file + `load_into_db` + schema entry + `/v1` resource. All entities (vendors, saved searches, POs, generic parts, and new: part registry, BOMs/boards, feeders, part-map) converge on it. "Add an entity" becomes a mechanical template.
3. **One frontend state discipline:** server events → store → signals as the only propagation path; EventBus retired or scoped to intra-UI interactions; dead `PREFS_CHANGED` enum entry removed. Done during the `/v1` port when call sites are already being touched.
4. **Preferences split:** server-owned settings stay server-side; per-client UI state (panel widths, last BOM path) moves client-side (localStorage/per-client namespace).
5. **Test-story simplification:** `dubis_headless.py` deleted — the server *is* headless; Playwright runs against the real HTTP path. `--test-source`/`--rollback-on-exit` survive as server flags.
6. **Write attribution:** auth middleware stamps identity into every mutation (`source`), giving a permanent who-did-what audit trail.

Riders (agent hygiene, ship with Phase 0): machine-check `CLAUDE.md` (referenced paths exist, counts match); fix `gen-code-map.py`/`event_trace` comment-parsing false positive (comments are stripped before EventBus scanning); remove dead `PREFS_CHANGED`; update drifted CLAUDE.md facts (`price_ops.py`/`price_history.py`/root `generic_parts.py`/`css/styles.css` no longer exist; 76 methods; ~85 JS modules).

## Roadmap (each phase = its own spec → plan → implementation)

### Phase 0 — Foundations (first; unblocks everything)
- **Part registry:** durable store mapping `part_uid` → alias set (all known distributor PNs/MPN). `get_part_key()` becomes a registry lookup; enrichment adds aliases instead of changing identity. Migration assigns uids to all existing parts (existing derived key becomes the first alias); no CSV rewrites required.
- **Entity-store template:** documented convention; generic parts migrated onto it (durable file + `load_into_db`), fixing the data-loss bug; `part_events.csv` explicitly demoted to append-only audit trail (documented as non-replayed).
- **Riders** above.
- Deliverable: no user-visible change; ground is stable.

### Phase 1 — Platform split (staged internally)
- **1a:** transport-neutral service extraction (remove `webview.*` reach-ins and JSON-string coercions from facades); `/v1` defined and generated from schema SSOT (OpenAPI + d.ts + contract tests); `/v1/events` push channel.
- **1b:** local server mode (`app.pyw` spawns server, webview = browser); frontend ported to `/v1` panel-by-panel (frozen-surface test is the burn-down list); EventBus→signals; prefs split; `dubis_headless.py` deleted; `pnp_server` absorbed as `/v1/pnp/*`.
- **1c:** remote deployment — docker image; auth middleware (loopback / tailnet allowlist / token) + identity stamping; desktop can point at a remote URL; data-dir lock file guards against two processes opening one data dir; mirror retired or demoted to fallback.
- Deliverable: same desktop OOTB; optional tailnet server.

### Phase 2 — MCP server (small; early because tooling compounds)
- Thin MCP wrapper over `/v1`, tool schemas generated from the SSOT: query inventory, spec search, adjust, consume BOM, price lookup; feeders later.
- Deliverable: any Claude session can query stock or log consumption.

### Phase 3 — PnP/feeder layer (revives `docs/plans/2026-04-06-phase3-feeder-tracking.md`, adapted)
- Boards/BOMs become first-class entities (entity-store template) — board setup reconciles against a durable BOM.
- Feeder entity referencing part **uids**: reels, feeder↔part assignment, `/v1/pnp/feeders` CRUD, reconciliation with the OpenPnP Jython bridge's live feeder state, part-map curation tooling (fills the currently-empty `pnp_part_map.json`), feeder-level consume attribution.
- Deliverable: "set up this board" — dubIS says which reels to load where; OpenPnP reads assignments; consumption decrements the right reel.

### Phase 4 — Design-side loop (parallelizable with Phase 3 after Phase 2)
- Spec extraction beyond passives (incremental, driven by real BOM contents).
- `/v1/kicad/*` implementing KiCad's HTTP-library protocol — live inventory in the symbol chooser with stock/price/LCSC fields.
- Parametric sourcing (jlcpcb-catalog integration first, distributor search later) closing the design↔selection loop.
- Deliverable: in-stock parts visible inside KiCad; gaps route to purchasing.

## Error handling, testing, migration strategy

- **Error policy:** unchanged — throw, don't swallow. HTTP layer maps `dubis_errors` hierarchy to status codes; `/v1` errors are structured `{error, code, detail}`. The JS client helper must surface failures (fixing the current `api()` swallow-and-return-`undefined` behavior); a lint rule flags dead `.catch()` patterns during the port.
- **Contract testing:** `test_api_surface.py`'s role transfers to a generated `/v1` contract test; during Phase 1b it doubles as the migration burn-down list (each bridge method is ported-or-deleted, never silently dropped).
- **Migration safety:** every phase lands behind the existing guard suite (`verify.sh` all-green); Phase 0 registry migration is idempotent and reversible (registry file is additive; deleting it falls back to derived keys until Phase 1 makes it mandatory). Phase 1b ports panel-by-panel with both paths alive only within the phase — dual paths are deleted before the phase closes.
- **E2E:** Playwright drives the real server over HTTP in all modes; PnP E2E (`tests/pnp-e2e/`) repoints at `/v1/pnp/*`; realistic-interaction policy unchanged.

## Risks

- **Phase 1 is large.** Mitigated by internal staging (1a/1b/1c), the burn-down contract test, and multi-worktree parallel dispatch per panel.
- **WebSocket/SSE in pywebview (WebView2):** verify early in 1a that the chosen push transport works inside WebView2; SSE is the fallback (plain HTTP, no upgrade dance).
- **Part-registry key migration:** alias collisions (same PN appearing under two uids) must hard-fail at migration time, not warn-and-drop — consistent with the throw-don't-swallow policy.
- **Scan/OCR phone flow** rides on the server port; keep its session model intact when absorbing `pnp_server`.
