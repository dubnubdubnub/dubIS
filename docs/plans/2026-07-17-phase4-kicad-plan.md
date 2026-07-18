# Phase 4 — KiCad HTTP Live Library (MVP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship the four `/v1/kicad/*` endpoints (root/categories/parts-by-category/
part-detail), the `server/auth.py` `Token`-scheme widening, the `data/kicad_mapping.json`
durable entity with a hand-seeded category set + eligibility filter + per-SKU overrides,
and the visibility gate — as ONE mergeable PR. No live `jlcpcb-catalog` network calls in
this plan (Full-scope, see design doc §4.2/§6) — the resolution-cache *shape* is built now
so Full doesn't redesign it, but the backfill runner that actually calls jlcpcb.com is a
separate, later PR.

**Architecture:** per `phase4-kicad-design-FINAL.md` (binding). Granularity is
individual SKU (canonical `part_id`), not `generic_parts` groups. Categories are a new
`data/kicad_mapping.json` taxonomy (hand-seeded for MVP), with `categorize.py` as the
mapping source for the seed, not a live JLCPCB lookup.

## Global Constraints

- Worktree: create a new worktree/branch off latest `main`,
  `claude/phase4-kicad-http-library` (confirm no unrelated work is checked out first,
  per the repo's worktree-per-task convention).
- TDD failing-first for every task: write the test, watch it fail for the right reason,
  then implement. Gates per task: focused `pytest` run; before each commit run
  `python -m pytest tests/python/ -q` and `ruff check .`; final task runs
  `bash scripts/verify.sh` in full (fixtures/code-map/manifest/layout-token staleness
  guards + ruff + pytest + eslint + tsc + vitest) with the full log captured and an
  explicit exit code check — never pipe through `tail` or otherwise truncate the gating
  output.
- Error contract everywhere new code touches HTTP: `{error, code, detail}`. No
  `pytest.skip`/`importorskip`/`mark.skip` anywhere. Prefer `AppLog.warn`/raising over
  silent catches (repo-wide policy).
- This feature is **server-only** — it does not touch `domain/schema.py`'s
  `INVENTORY_FIELDS` or any field the JS-facing inventory record sends to the frontend.
  Task 6 has an explicit assertion step confirming `gen-inventory-types.py --check` and
  `npx tsc --noEmit` stay green *without* being run as part of this feature's own
  changes (i.e., prove this feature didn't accidentally require a schema.py entry).
- Tests use the real-server harness (`start_live_server` / `python -m server`, same
  pattern as `tests/python/server/test_auth.py`), no HTTP mocks, per repo convention —
  except the LCSC→JLCPCB-category resolution path (Task 5), which is explicitly
  out-of-network for MVP and therefore has no live call to mock in the first place; its
  tests exercise only the `categorize.py`-fallback and hand-seed lookup paths.
- Auth `off` mode (today's default) must stay byte-identical: `/v1/kicad/*` unauthenticated
  behavior when `DUBIS_AUTH_MODE` is unset must match its `on`-mode-loopback behavior
  exactly (no accidental new gate).

---

### Task 1: `server/auth.py` — accept `Token` scheme alongside `Bearer`

**Files:** Modify `server/auth.py` (line ~162); Modify `tests/python/server/test_auth.py`
(add `Token`-scheme cases alongside the existing `Bearer` cases).

**Interfaces:**
```python
# server/auth.py, AuthMiddleware._resolve
if scheme.lower() in ("bearer", "token"):
    ...
```

**Tests (TDD — write first, confirm they fail against today's `== "bearer"` check):**
- `Authorization: Token <valid-token>` against any existing authed route (reuse
  `/v1/parts` or similar, whichever `test_auth.py` already uses for the `Bearer` case)
  resolves to the token's configured identity — same as `Bearer` does today.
- `Authorization: Bearer <valid-token>` still works unchanged (regression guard — this
  is the one line every other test in the file depends on).
- `Authorization: Token <invalid-token>` → 401, same shape as an invalid `Bearer` token.
- Case-insensitivity: `authorization: token <valid-token>` (lowercase header value)
  still resolves — `scheme.lower()` already handles this, assert it explicitly for the
  new branch.
- `off` mode: no change, already covered by existing suite — no new test needed, just
  confirm the existing off-mode tests still pass.

Commit `feat(server): accept Authorization: Token scheme alongside Bearer (kicad auth)`.

---

### Task 2: `data/kicad_mapping.json` entity — durable store + `load_into_db`

**Files:** Create `domain/kicad_mapping.py`, `tests/python/domain/test_kicad_mapping.py`;
Modify `domain/inventory.py` (call `kicad_mapping.load_into_db` in `rebuild()` alongside
`domain/generic_parts.py::load_into_db` and `domain/part_registry.py`'s load — same spot,
after parts are loaded so FK-style `known_parts` checks are meaningful), `cache_db.py`
(`create_schema`: new `kicad_categories`, `kicad_part_state` tables; bump
`SCHEMA_VERSION`).

**Interfaces** (mirror `domain/generic_parts.py`'s `_persist`/`load_into_db` pair and
`domain/part_registry.py`'s `load`/`save` shape — pick whichever fits; this entity is
closer to `part_registry.json` in that it's keyed by canonical `part_id` throughout, but
closer to `generic_parts.json` in that it has an idempotent DB-restore step, so model it
as `load_into_db(conn, data_dir)` + `_persist(conn, data_dir)`, JSON schema per design
doc §2.2):

```python
# domain/kicad_mapping.py
def load_into_db(conn: Any, data_dir: str) -> None: ...
def _persist(conn: Any, data_dir: str) -> None: ...
```
JSON shape: `{"version": 1, "categories": [...], "part_overrides": {...},
"part_category_cache": {...}}` — exact fields per design doc §2.2. Missing file →
empty structures, self-healing (matches `part_registry.py::load`'s missing-file
behavior). Unknown `version` → raise (matches `generic_parts.py::load_into_db`'s
`ValueError` on version mismatch — fail loudly per repo's "throw errors, don't
silently fail" policy).

**Tests (TDD):**
- Round-trip: write a `kicad_mapping.json` with one category + one part_override +
  one part_category_cache entry, `load_into_db`, query the resulting SQLite rows,
  assert they match.
- Missing file → empty tables, no crash (self-healing).
- Unsupported `version` → raises (not silently ignored).
- A `part_overrides` entry referencing a `part_id` not present in `parts` (SKU deleted
  from ledger since the override was set) → warn-logged, skipped in DB, **retained**
  in the JSON on next `_persist` (same "the part may return" contract as
  `generic_parts.py`'s member-retention logic) — write this as an explicit test, it is
  the easiest thing to regress by copying `generic_parts.py`'s pattern imprecisely.
- `SCHEMA_VERSION` bump: dropping and rebuilding `cache.db` restores identical
  `kicad_categories`/`kicad_part_state` rows from the JSON (proves SQLite really is a
  deletable derived view for this entity, per `docs/entity-store.md` rule 3).

Commit `feat(kicad): data/kicad_mapping.json durable entity + load_into_db`.

---

### Task 3: `/v1/kicad/*` routes — root, categories, parts-by-category, part-detail

**Files:** Create `server/routes/kicad.py`, `tests/python/server/test_kicad_routes.py`;
Modify `server/app.py` (mount `kicad.router`), `server/models.py` (new `KicadCategory`,
`KicadPartSummary`, `KicadPartDetail` Pydantic response models — all fields `str`-typed
per the protocol's string-encoding requirement).

**Interfaces:**
```python
# server/routes/kicad.py
router = APIRouter(prefix="/v1/kicad", tags=["kicad"])

@router.get("/", operation_id="kicad_root")
def kicad_root(request: Request) -> dict: ...

@router.get("/categories.json", operation_id="kicad_categories")
def kicad_categories(request: Request) -> list[dict]: ...

@router.get("/parts/category/{category_id}.json", operation_id="kicad_parts_by_category")
def kicad_parts_by_category(request: Request, category_id: str) -> list[dict]: ...

@router.get("/parts/{part_id}.json", operation_id="kicad_part_detail")
def kicad_part_detail(request: Request, part_id: str) -> dict: ...
```
No mutation, no `finish_mutation` call — this router is pure read, unlike
`generic_parts.py`. The facade methods these call into
(`InventoryApi`-level or a new small read-only helper module, e.g.
`domain/kicad_view.py`, that composes `kicad_mapping` + `categorize.py` + the parts
cache) apply the visibility gate from design doc §3 before returning anything —
gating logic lives in one place (`domain/kicad_view.py::visible_parts_by_category`,
`domain/kicad_view.py::resolve_part_detail`), not duplicated across route handlers.

**Tests (TDD — the exact-shape contract tests the design doc calls out):**
- `GET /v1/kicad/` → exactly `{"categories": "", "parts": ""}`.
- `GET /v1/kicad/categories.json` on a seeded fixture inventory → every leaf scalar in
  every element `isinstance(v, str)` (not spot-checked — iterate every key), category
  with zero visible members is absent from the list.
- `GET /v1/kicad/parts/category/{id}.json` → summary shape only (no `fields`, no
  `symbolIdStr` key at all), every scalar a string, invisible SKUs (per Task 4's gate)
  absent.
- `GET /v1/kicad/parts/{id}.json` on a visible SKU → full shape matches design doc §1.4
  exactly, including nested `fields.*.value`/`fields.*.visible` as strings
  (`"visible": "True"`/`"False"`, not booleans).
- `GET /v1/kicad/parts/{id}.json` on an invisible/unresolved SKU → 404
  `{error, code:"not_found", detail}` (matches `/v1`'s existing error contract).
- Fixed visible-field assertion: on the part-detail response, `Value`/`MPN`/`LCSC`/
  `Datasheet` have `"visible": "True"`; `footprint`/`Manufacturer` have
  `"visible": "False"`; `unit_price`/`ext_price`/`primary_vendor_id`/`po_history`/
  `qty`/`section` do not appear as keys anywhere in the response — assert their
  absence explicitly, not just that visible ones are present (this is the exact leak
  the design doc rules out).

Commit `feat(kicad): /v1/kicad/* read-only routes (root, categories, parts, part-detail)`.

---

### Task 4: Visibility gate + eligibility filter (default-exclude dev-boards, per-SKU override)

**Files:** Create `domain/kicad_view.py` (if not already created stub in Task 3 —
this task fleshes out the gating logic itself), `tests/python/domain/test_kicad_view.py`.

**Interfaces:**
```python
# domain/kicad_view.py
def is_visible(part_id: str, category: dict, override: dict | None) -> bool: ...
def resolve_symbol(part_id: str, category: dict, override: dict | None) -> str | None: ...
```
Gate logic exactly per design doc §3: category unresolved → invisible; no resolvable
`symbolIdStr` → invisible; `eligible_override is False` → invisible (wins outright);
`eligible_override is True` → visible (wins outright, even in the excluded bucket);
`eligible_override is None` and category is `"Development Boards, Kits, Programmers"`
(by `categorize_bucket`, matched against the literal `categorize.py` string, not a
magic re-derivation) → invisible; else visible.

**Tests (TDD — unit tests against the gating function directly, not just through HTTP,
per design doc §5):**
- Resolved category + resolved symbol + no override, non-dev-board category → visible.
- Resolved category + resolved symbol + no override, dev-board category → invisible
  (default-exclude).
- Dev-board category + `eligible_override: true` → visible (the ESP32/SoM case).
- Non-dev-board category + `eligible_override: false` → invisible (the
  mislabeled-tool case).
- No resolved category at all → invisible regardless of override (override can't force
  visibility for a SKU with nothing to categorize it — matches design doc §3 point 1
  taking precedence).
- Resolved category with `default_symbol: null` and no per-SKU `kicad_symbol` override
  → invisible even if eligible (no symbol to place — protocol-invalid otherwise).
- Resolved category with `default_symbol` set AND a per-SKU `kicad_symbol` override →
  the override wins (assert the actual returned string, not just visibility).

Commit `feat(kicad): visibility gate — eligibility default + per-SKU override`.

---

### Task 5: Category resolution — hand-seeded taxonomy + `categorize.py` fallback

**Files:** Create `data/kicad_mapping.json` (the actual seed data, committed, per design
doc §4.1's list: resistors→`Device:R`, ceramic caps→`Device:C`, electrolytic→`Device:CP`,
diodes→`Device:D`, inductors→`Device:L`, LEDs→standard LED symbol, each
`source: "categorize_fallback"` with `default_footprint_from_package: true`);
Create `tests/python/domain/test_kicad_category_resolution.py`; Modify
`domain/kicad_mapping.py` (add a `resolve_category_for_part(row, mapping) -> str | None`
helper: checks `part_category_cache` first, else runs `categorize.categorize(row)` and
matches against a `categorize_bucket`-sourced category entry, else `None`).

Note: this task does **not** implement the live `jlcpcb-catalog` HTTP lookup — that's
explicitly Full-scope per the design doc (§4.2/§6). `part_category_cache` in the JSON
schema exists as a shape Full will populate later; this task only exercises the
`categorize.py`-fallback path and confirms the cache-hit path works when an entry is
already present (e.g. pre-seeded by a future backfill run, or hand-entered).

**Tests (TDD):**
- A SKU whose `categorize.py` bucket matches a seeded `categorize_fallback` category
  (e.g. a resistor row) → resolves to that category's id.
- A SKU whose `categorize.py` bucket has no corresponding seeded category (e.g.
  `"ICs - Microcontrollers"`, not in the MVP seed) → resolves to `None` (unresolved,
  therefore invisible per Task 4 — not an error).
- A SKU with a `part_category_cache` entry already present → that cache entry wins,
  `categorize.py` is not even consulted (proves the cache-hit short-circuit).
- A SKU in the `"Development Boards, Kits, Programmers"` bucket resolves to that
  category id (resolution ≠ eligibility — Task 4 handles the exclusion, this task
  only proves resolution finds the bucket correctly).

Commit `feat(kicad): categorize.py-fallback category resolution + hand-seeded taxonomy`.

---

### Task 6: `.kicad_httplib` fixture + full-flow integration test + docs

**Files:** Create `tests/fixtures/dubis.kicad_httplib` (real config file, per design doc
§1.5's exact shape, `root_url` pointed at the test harness's loopback URL,
`token: "test-token"`), `tests/python/server/test_kicad_integration.py` (end-to-end:
start a live server with `DUBIS_AUTH_MODE=on` + `DUBIS_TOKENS=kicad-test:test-token`,
walk root → categories → parts-by-category → part-detail using the fixture's
`root_url`/`token`, assert the whole chain is internally consistent — every category id
returned by `categories.json` is queryable via `parts/category/{id}.json`, every part id
returned there is queryable via `parts/{id}.json`); Modify `CLAUDE.md` (new row in the
architecture table for `server/routes/kicad.py` + `domain/kicad_mapping.py` +
`domain/kicad_view.py`; a "KiCad live library" mention in the remote-deployment section
if root_url needs the same local-vs-remote distinction as other Phase 1c consumers).

**Tests:**
- Full chain integration test described above, both in `DUBIS_AUTH_MODE=off` (today's
  default, no token needed) and `on` (with the fixture's token, using the `Token`
  scheme from Task 1) — proves Tasks 1–5 compose correctly end to end, not just
  unit-by-unit.
- `gen-inventory-types.py --check` and `npx tsc --noEmit` pass **unchanged** — explicit
  assertion in the task report (not a new automated test, since there's nothing to
  assert programmatically beyond "still green") that this feature required no
  `domain/schema.py`/`js/inventory-record.d.ts` change, confirming the Global
  Constraints note.
- `bash scripts/verify.sh` full run, log captured, exit code checked explicitly.

Commit `feat(kicad): .kicad_httplib fixture + end-to-end integration test + docs`.

---

### Task 7: PR

**Files:** none beyond what's already committed.

- `bash scripts/push-pr.sh --title "feat(kicad): /v1/kicad/* HTTP live library (Phase 4 MVP)"`.
- Watch CI (`gh pr checks <number>`) to green; fix and repush on any failure per repo
  policy — do not abandon with red CI.

Commit: none (PR-only task; the branch's existing commits are squash-merged).
