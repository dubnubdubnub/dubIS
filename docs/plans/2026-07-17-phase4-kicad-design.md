# dubIS Phase 4 — KiCad HTTP Live Library — FINAL Design

Status: **APPROVED design, binding.** Supersedes `phase4-kicad-design-DRAFT.md` on every
point where the owner made a call. Grounded in `phase4-kicad-research.md` (protocol
authority), `phase4-uwrealitylabs-comparison.md` (taxonomy + eligibility precedent), and
the `platform-phase1c-remote` worktree's real interfaces (`server/auth.py`,
`server/app.py`, `server/routes/*.py`, `categorize.py`, `domain/generic_parts.py`,
`domain/part_registry.py`, `docs/entity-store.md`).

## Binding decisions (do not re-litigate)

1. **Granularity = individual SKU.** One KiCad part per dubIS distributor SKU
   (canonical `part_id` from `domain/part_registry.py`), **not** a `generic_parts`
   group. This reverses both prior drafts' group-level recommendation — the owner
   overrode it explicitly. A generic-parts group is *not* used as the KiCad part unit;
   each LCSC/MPN/DigiKey/Mouser/Pololu SKU that has ever been purchased gets its own
   chooser entry, keyed by its canonical `part_id`.
2. **Categories = JLCPCB catalog taxonomy**, resolved per-SKU via its LCSC part number
   (the scheme UWRealityLabs's generated library uses), with `categorize.py`'s existing
   `CATEGORY_RULES`/`SUBCATEGORY_RULES` taxonomy as the **fallback** for SKUs with no
   LCSC number (DigiKey-only, Mouser-only, Pololu-only parts). This is a **mapping
   layer** — dubIS does not adopt UWRL's taxonomy wholesale, does not touch
   `categorize.py`'s own rules, and does not change dubIS's shelf/UI categorization.
3. **Eligibility filter**: default-**exclude** the `categorize.py` bucket
   `"Development Boards, Kits, Programmers"`; everything else default-**include**.
   Plus a **per-part override bit**, persisted in a durable entity file following the
   `data/part_registry.json` precedent, so a solder-down module (ESP32/Pi
   Compute Module/radio SoM) can be force-included despite living in the excluded
   bucket, and a mislabeled tool can be force-excluded. Unmapped SKUs (no resolved
   category) and ineligible SKUs (excluded bucket, no override) are both **hidden**
   from the KiCad chooser — same visibility gate, two different reasons.
4. **Auth**: reuse `server/auth.py` bearer auth. Widen the scheme check at
   `server/auth.py:162` to accept KiCad's `Authorization: Token <token>` alongside
   `Bearer`. No other change to the resolution order.

---

## 1. Protocol surface — `/v1/kicad/*`

New router `server/routes/kicad.py`, mounted in `server/app.py` next to the other
`app.include_router(...)` calls (after line 45, alongside `preferences.router`) —
matches the existing router-per-resource style (`server/routes/generic_parts.py` is
the closest analog: `APIRouter(prefix="/v1", tags=[...])`, `request.app.state.api` for
the facade, Pydantic body models for anything non-GET). All four endpoints below are
**read-only GET**, so unlike `generic_parts.py` none of them call
`server/mutations.py:finish_mutation` — there is nothing to publish `inventory.updated`
for.

KiCad's `api_version` config field is a client-side path segment, not something the
server branches on. Fix it as the literal string `v1` in the `.kicad_httplib` file we
hand out and serve these endpoints directly under `/v1/kicad/` (avoids
`/v1/kicad/v1/...` double-versioning).

**String-encoding requirement (protocol-critical, per research §1.3):** every scalar in
every response body — ints, bools — must be JSON-encoded **as a string**
(`"id": "16"` not `16`). FastAPI's default Pydantic serialization will happily emit a
native `bool`/`int` if a response model's field type isn't explicitly `str` — this is
the single easiest thing to regress and gets its own contract test (Plan Task 3).
Define explicit Pydantic response models in `server/models.py` with `str`-typed
fields, built with explicit `str(...)`/`"True"/"False"` coercion in the route body —
do not rely on `orm_mode`/automatic casting.

All responses must be **HTTP 200** always; KiCad does not retry on non-200 and treats
it as a hard error. "Not found" for `/v1/kicad/parts/{id}.json` on an id that is
gated-invisible (§4) is therefore **404**, matching the rest of `/v1`'s error contract
(`{error, code:"not_found", detail}`) — the "always 200" rule in the spec is about
KiCad's *client* behavior on the endpoints it calls during normal browsing (root,
categories, parts-by-category), not license to return 200-with-garbage for a
genuinely bad id. Root/categories/parts-by-category endpoints never 404 (they always
return an array or the fixed root shape) — only part-detail can 404, and always will,
for a gated-invisible id.

### 1.1 Root — connection check

`GET /v1/kicad/`
```json
{ "categories": "", "parts": "" }
```
Keys only matter (liveness/shape check when the user adds/tests the library in KiCad).

### 1.2 Categories

`GET /v1/kicad/categories.json`
```json
[
  {"id": "16", "name": "Passives/Capacitors/Ceramic", "description": "Ceramic capacitors"},
  {"id": "42", "name": "Development Boards, Kits, Programmers", "description": "..."}
]
```
- Sourced from `data/kicad_mapping.json`'s `categories` array (§2.2) — one row per
  resolved JLCPCB-taxonomy label (or `categorize.py` fallback bucket) that currently
  has **at least one eligible, visible SKU**. Categories with zero visible members are
  omitted, not returned empty — keeps the chooser tree free of dead branches.
- `/`-delimited `name` renders as a nested tree in KiCad's Symbol Chooser (matches both
  JLCPCB's own taxonomy shape, e.g. `"Active Parts/Clock and Timer ICs"`, and
  UWRealityLabs's flat-folder convention once relabeled with `/`).
- Note: `"Development Boards, Kits, Programmers"` **can** appear here if — and only
  if — at least one SKU in it has the per-part eligibility override bit set (§4). The
  category itself is never blanket-excluded; only its *default* member eligibility is.

### 1.3 Parts by category

`GET /v1/kicad/parts/category/{id}.json`
```json
[
  {
    "id": "C15850",
    "name": "CL10B104KB8NNNC",
    "description": "100nF ±10% 16V X7R 0603 MLCC",
    "keywords": "capacitor ceramic 100nf 0603 x7r",
    "footprint_filters": ["C_*", "Capacitor_SMD:C_0603*"]
  }
]
```
- `id` = the SKU's canonical `part_id` (from `domain/part_registry.py`) — stable
  across distributor-PN aliasing/enrichment, same identity used everywhere else in
  dubIS.
- Summary shape only — no `fields`, no `symbolIdStr`. Filtered to SKUs in that
  category that are **visible** per the gating rule (§4) — invisible SKUs never
  appear here.

### 1.4 Part detail

`GET /v1/kicad/parts/{id}.json`, fetched lazily only when the user selects/places a
specific part — a real DB read here is not a chooser-scale perf concern.
```json
{
  "id": "C15850",
  "name": "CL10B104KB8NNNC",
  "symbolIdStr": "Device:C",
  "description": "100nF ±10% 16V X7R 0603 MLCC",
  "keywords": "capacitor ceramic 100nf 0603 x7r",
  "exclude_from_bom": "False",
  "exclude_from_board": "False",
  "exclude_from_sim": "False",
  "footprint_filters": ["C_*", "Capacitor_SMD:C_0603*"],
  "fields": {
    "footprint": {"value": "Capacitor_SMD:C_0603_1608Metric", "visible": "False"},
    "datasheet": {"value": "https://item.szlcsc.com/...", "visible": "False"},
    "Value":        {"value": "100nF", "visible": "True"},
    "MPN":          {"value": "CL10B104KB8NNNC", "visible": "True"},
    "LCSC":         {"value": "C15850", "visible": "True"},
    "Manufacturer": {"value": "Samsung Electro-Mechanics", "visible": "False"}
  }
}
```
- `id`/`name`/`symbolIdStr` as above. `symbolIdStr` resolution order: per-SKU override
  in `kicad_mapping.json` → the SKU's resolved category's `default_symbol` → invisible
  (§4) if neither exists.
- **Visible field set is fixed for v1, not configurable**: `Value`, `MPN`, `LCSC`,
  `Datasheet` visible; `footprint`, `Manufacturer`, and any other custom field hidden.
  `unit_price`, `ext_price`, `primary_vendor_id`, `po_history`, `qty`, `section` are
  **never exposed** — this is the explicit non-goal from the binding decisions and
  research §3: no price or PO history on the schematic. (`qty`/stock as an
  informational `Stock` field, floated in the DRAFT, is cut from scope entirely — not
  even hidden-but-present — to keep the fixed field set minimal and matching exactly
  what the owner asked to see: Value, Footprint, MPN, LCSC, Datasheet.)

### 1.5 Auth — reuse `server/auth.py`, one-line scheme widening

`server/auth.py:162` today:
```python
if scheme.lower() == "bearer":
```
KiCad's actual client (per research §1.4, DRF `TokenAuthentication` convention) sends
`Authorization: Token <token>`, not `Bearer`. Widen to:
```python
if scheme.lower() in ("bearer", "token"):
```
That is the entire change. Everything downstream is unaffected:
- `AuthMiddleware._resolve`'s full order (loopback → bearer/token → cookie → tailnet
  header → 401) is unchanged; only the scheme string accepted in step 2 widens.
- `lookup_token`'s constant-time comparison, `stamp_source`'s identity-suffixing, and
  `require_loopback` are untouched — `/v1/kicad/*` is a pure-read surface and never
  calls `require_loopback` or mutates, so identity-stamping doesn't even apply to it.
- `/v1/kicad/*` is **not** added to `EXEMPT_PATHS` — it goes through the same `on`-mode
  gate as every other `/v1` route. In `off` mode (today's default) it's open exactly
  like the rest of `/v1`, matching the desktop-local threat model.
- Issue one named token per KiCad-using human via the existing `DUBIS_TOKENS` env var:
  `DUBIS_TOKENS=kicad-isaac:<random>,mcp-ci:<random2>` — no new token concept.

The `.kicad_httplib` file handed to the user:
```json
{
  "meta": {"version": 1.0},
  "name": "dubIS Inventory",
  "description": "Live KiCad library backed by dubIS on-hand parts",
  "source": {
    "type": "REST_API",
    "api_version": "v1",
    "root_url": "https://dubis.<tailnet>/v1/kicad",
    "token": "<DUBIS_TOKENS value>",
    "timeout_parts_seconds": 60,
    "timeout_categories_seconds": 600
  }
}
```
Generating this (with the right `root_url` for local vs. Phase-1c remote-deploy mode)
is a nice-to-have `GET /v1/kicad/httplib-config` convenience endpoint — not required
for MVP correctness, deferred to Full (§6).

---

## 2. Data model

### 2.1 What KiCad wants vs. what dubIS has

`domain/schema.py`'s `INVENTORY_FIELDS` gives each SKU: `section`, `lcsc`, `mpn`,
`digikey`, `pololu`, `mouser`, `manufacturer`, `package`, `description`, `qty`,
`unit_price`, `ext_price`, `primary_vendor_id`, `po_history`. None of these are
KiCad-shaped: no symbol, no footprint, no schematic-grouping category, no persisted
datasheet URL. Per binding decision 1, the KiCad-part unit is the SKU itself
(`domain/part_registry.py` canonical `part_id`), not a `generic_parts` group — this
sidesteps the group/SKU granularity question the prior drafts raised as open (draft
§6.2 / research §5.6.2) since the owner has now decided it.

### 2.2 New durable entity: `data/kicad_mapping.json`

Follows `docs/entity-store.md`'s pattern exactly — same shape of contract as
`data/part_registry.json` (canonical-id-keyed, additive, self-healing on delete) and
`data/generic_parts.json` (`load_into_db`/`_persist` pair, `version` field, warn-skip
on stale references):

```json
{
  "version": 1,
  "categories": [
    {
      "id": "1",
      "name": "Passives/Capacitors/Ceramic",
      "source": "jlcpcb",
      "jlcpcb_catalog_name": "Multilayer Ceramic Capacitors MLCC - SMD/SMT",
      "default_symbol": "Device:C",
      "default_footprint_from_package": true,
      "default_reference": "C"
    },
    {
      "id": "2",
      "name": "Development Boards, Kits, Programmers",
      "source": "categorize_fallback",
      "categorize_bucket": "Development Boards, Kits, Programmers",
      "default_symbol": null,
      "default_footprint_from_package": false,
      "default_reference": null
    }
  ],
  "part_overrides": {
    "C15850": {
      "category_id": "1",
      "kicad_symbol": null,
      "kicad_footprint": null,
      "kicad_datasheet": null,
      "eligible_override": null
    },
    "STLINKV3MINIE": {
      "category_id": "2",
      "eligible_override": null
    },
    "ESP32-WROOM-32E-N4": {
      "category_id": "2",
      "eligible_override": true
    }
  },
  "part_category_cache": {
    "C15850": {
      "lcsc": "C15850",
      "jlcpcb_catalog_name": "Multilayer Ceramic Capacitors MLCC - SMD/SMT",
      "resolved_category_id": "1",
      "resolved_via": "jlcpcb",
      "resolved_at": "2026-07-17T04:00:00Z"
    }
  }
}
```

Keyed by canonical `part_id` throughout, matching `part_registry.json`'s keying
convention exactly (binding decision 1). Four top-level sections:

- **`categories`**: the resolved KiCad-facing taxonomy. Each entry has either
  `source: "jlcpcb"` (carries `jlcpcb_catalog_name`, the literal JLCPCB catalog label
  the taxonomy came from) or `source: "categorize_fallback"` (carries
  `categorize_bucket`, the literal `categorize.py` `CATEGORY_RULES` category string it
  maps from). `default_symbol` / `default_reference` are InvenTree-cascade-style
  category defaults (standard `Device:*` symbols cover the bulk of BOMs at zero
  per-SKU cost); `null` means "no category default, every SKU in this category needs
  a per-part override to become visible" — this is the correct default for
  `"Development Boards, Kits, Programmers"`, where visibility is opt-in per SKU, not
  per category.
- **`part_overrides`**: per-SKU state a human has explicitly set — symbol/footprint/
  datasheet override (falls back to the resolved category's default when `null`), and
  `eligible_override` (tri-state: `true` forces visible regardless of category
  default, `false` forces invisible regardless, `null` defers to the category
  default — this is the mechanism for binding decision 3's ESP32/mislabeled-tool
  cases).
- **`part_category_cache`**: the **memoized result** of the LCSC→JLCPCB-category
  lookup (§2.3) — a cache, not a durable override; it is safe to delete and it
  self-heals (re-resolves) on next backfill run, unlike `part_overrides` which is
  user-curated state that must survive.
- `version: 1`, same convention as every other entity file.

`load_into_db(conn, data_dir)` in a new `domain/kicad_mapping.py`, called from
`domain/inventory.py:rebuild()` alongside `domain/generic_parts.py::load_into_db` and
`domain/part_registry.py`'s load. Restores into two new SQLite tables in
`cache_db.create_schema` (`kicad_categories`, `kicad_part_state` — both derived,
droppable on `SCHEMA_VERSION` bump, restored from the JSON on next rebuild). No
`domain/schema.py` entry — this data never reaches the JS-facing inventory record
(`/v1/kicad/*` is a server-only surface with its own Pydantic response models), so
`gen-inventory-types.py`/`tsc` are untouched by this feature (confirmed against the
DRAFT's §"Fixture regen" note, still holds now that granularity changed to SKU-level).

### 2.3 LCSC → JLCPCB-taxonomy resolution, and WHERE it happens

**Mechanism.** For a SKU with a non-empty `lcsc` field (an LCSC C-number), resolve its
JLCPCB catalog category the way `jlcpcb-catalog`'s underlying public endpoint does —
this environment's `jlcpcb-catalog` skill wraps
`POST https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`
(keyword/C-number search, no auth). dubIS's *server* cannot invoke the Claude Skill
tool itself (skills are agent-side, not backend-callable) — it needs its own small
HTTP client (`jlcpcb_category.py`, new) hitting the **same public endpoint** the skill
uses, querying by the SKU's exact LCSC C-number and reading the catalog-category label
off the result. **Open item, flagged in §6**: the exact response field carrying the
human-readable catalog name (the thing UWRealityLabs's folder names like
`Basic_Capacitors_Resistors__C` were harvested from) was not verified live in this
design pass — `componentBrandEn`/`componentModelEn`/`componentLibraryType` are
confirmed fields per research §4, but the category-name field itself needs a live
query to pin down before Full-scope auto-categorization ships at volume. This does
**not** block MVP (§5) because MVP ships with a hand-seeded category list and does not
require the live lookup to be correct at scale yet.
- SKUs with **no** LCSC number (DigiKey-only/Mouser-only/Pololu-only) skip this lookup
  entirely and resolve via `categorize.py`'s `categorize()` fallback (binding decision
  2) — reusing the exact function already used for shelf-taxonomy, output relabeled
  1:1 into a `categorize_fallback`-sourced KiCad category (e.g. `"Passives -
  Capacitors"` → a `data/kicad_mapping.json` category entry with
  `categorize_bucket: "Passives - Capacitors"`).

**Where it happens: build-time/backfill, not on the request path.** The `jlcpcb-catalog`
lookup is network-bound (a live HTTP call to jlcpcb.com per SKU) and rate-limit-shaped
for interactive/agent use, not for serving KiCad's `timeout_categories_seconds`/
`timeout_parts_seconds`-bounded chooser requests synchronously. Doing the lookup
inline inside `GET /v1/kicad/categories.json` or `.../parts/category/{id}.json` would:
(a) make those endpoints' latency proportional to inventory size × network RTT to
jlcpcb.com, wildly exceeding KiCad's client-side timeouts on any inventory of
non-trivial size; (b) hit jlcpcb.com repeatedly for SKUs that never change category.
Instead:
- Resolution runs as a **backfill step**, one new SKU (canonical `part_id` not yet in
  `part_category_cache`) at a time, triggered either (a) opportunistically during
  `rebuild_inventory()` for a small bounded batch of unresolved SKUs per rebuild (so
  normal usage self-heals over a few app launches without a dedicated maintenance
  step), or (b) via an explicit `scripts/backfill_kicad_categories.py` for bulk
  first-run seeding of an existing large ledger. Recommend **(a) with a small
  per-rebuild cap** (e.g. 20 unresolved SKUs per rebuild) as the default, with (b)
  available for an operator who wants to force a full backfill immediately rather than
  wait several app launches.
- `/v1/kicad/*` route handlers **only ever read `part_category_cache`** (and
  `categorize.py`'s fallback, which is already synchronous/local) — never make a live
  jlcpcb.com call on a request thread. A SKU not yet resolved is simply invisible
  (§4) until the next backfill batch resolves it — same "unmapped is hidden, not
  broken" posture as an unresolvable SKU.
- Network failure during backfill (jlcpcb.com unreachable, rate-limited, or the SKU's
  LCSC number returns no match) → immediate fallback to `categorize.py`'s bucket for
  that SKU, logged via `AppLog.warn` (per CLAUDE.md's error policy — never silently
  drop), retried on a later backfill pass rather than blocking.

### 2.4 Field mapping table

| dubIS source | KiCad target |
|---|---|
| `part_id` (canonical) | `id` |
| `mpn` (or `lcsc`/`digikey`/etc. if `mpn` empty — same precedence as `domain/part_registry.py::derive_key`) | `name` |
| `description` | `description`, `keywords` (tokenized) |
| `mpn` | custom field `MPN` (visible) |
| `lcsc` | custom field `LCSC` (visible) |
| `digikey` / `mouser` / `pololu` | custom fields `DigiKey` / `Mouser` / `Pololu` (hidden) |
| `manufacturer` | custom field `Manufacturer` (hidden) |
| parsed spec value (`spec_extractor.py`, when the SKU also belongs to a `generic_parts` group — informational reuse only, does not change the SKU-level KiCad-part identity) | `Value` (visible) |
| `package` (when `default_footprint_from_package`) or per-SKU `kicad_footprint` override | `fields.footprint` (hidden) |
| per-SKU `kicad_datasheet` override, else best-effort captured distributor product-page URL | `fields.datasheet` (visible) |
| resolved category's `default_symbol`, overridden by per-SKU `kicad_symbol` | `symbolIdStr` |
| `unit_price`, `ext_price`, `primary_vendor_id`, `po_history`, `qty`, `section` | **never exposed** |

---

## 3. Visibility/gating rule (cross-cutting, stated once)

A SKU is **visible** to every `/v1/kicad/*` endpoint iff **all** of the following hold:

1. It resolves to a category (via `part_category_cache` + jlcpcb match, or the
   `categorize.py` fallback) — an unresolved SKU is invisible.
2. It resolves to a non-empty `symbolIdStr` — per-SKU `kicad_symbol` override, else the
   resolved category's `default_symbol`. No symbol → invisible, matching Part-DB's
   precedent (research §2.4) and preventing the chooser from ever showing a
   `symbolIdStr`-less part (protocol-invalid).
3. Eligibility passes: `eligible_override` is `true` (force-include, wins outright),
   or (`eligible_override` is `null` **and** the resolved category is not
   `"Development Boards, Kits, Programmers"`). `eligible_override: false` always wins
   as a force-exclude regardless of category.

Any failure on 1–3 → the SKU is absent from `categories.json`'s member count and
`parts/category/{id}.json`, and `parts/{id}.json` for that id returns 404. This is one
gate serving two purposes (no symbol/footprint mapped, vs. deliberately ineligible
category) — both produce identical invisibility, which is the correct user-facing
behavior (an unmapped tool and an excluded tool should look the same: absent).

---

## 4. MVP vs. Full

### 4.1 MVP (ships as one PR)

- All four `/v1/kicad/*` endpoints, read-only, exact shapes in §1.
- `server/auth.py:162` scheme widening (§1.5).
- `data/kicad_mapping.json` entity + `domain/kicad_mapping.py::load_into_db` +
  `cache_db.create_schema` tables, per §2.2 — but populated with a **small
  hand-authored category seed** at MVP time, not live `jlcpcb-catalog` calls:
  resistors → `Device:R`, ceramic caps → `Device:C`, electrolytic → `Device:CP`,
  diodes → `Device:D`, inductors → `Device:L` (all standard-library symbols, `source:
  "categorize_fallback"` mapping straight off `categorize.py`'s existing bucket names
  for these — since these are exactly the categories `categorize.py` already buckets
  cleanly, the JLCPCB-taxonomy lookup for MVP is a deferred enhancement, not a
  blocker, per §2.3's "does not block MVP" note).
  `default_footprint_from_package: true` on all of them.
- Eligibility filter: `"Development Boards, Kits, Programmers"` default-excluded,
  everything else default-included, `eligible_override` field present and
  respected — even though MVP won't have a UI to set it yet (hand-edit
  `kicad_mapping.json`, same bootstrap posture `data/generic_parts.json` had before
  any UI existed for it).
- Fixed visible-field set (§1.4/§2.4): `Value`, `MPN`, `LCSC`, `Datasheet`.
- No live `jlcpcb-catalog` network calls — `part_category_cache`/backfill machinery
  (§2.3) ships as dead code paths behind the hand-seed for MVP, wired but not yet
  exercised against real network calls; OR (equally acceptable, simpler): defer the
  backfill script itself to Full and ship MVP with `categories` entries covering only
  what the hand-seed needs. **Recommend the simpler path** — don't build the backfill
  runner until Full needs it; §2.3's design is specified now so Full doesn't have to
  redesign the cache shape, but the runner script is Full-scope (Plan Task 6 makes
  this split explicit).
- No datasheet persistence beyond best-effort capture where already available; empty
  is acceptable (protocol treats it as optional).
- No write support (matches KiCad's own read-only protocol ceiling anyway).

Ships value the day it merges: every resistor/cap/diode/inductor/LED SKU dubIS has
ever bought becomes individually placeable in KiCad by category, with live MPN/LCSC/
Value annotations, zero manual per-SKU curation — plus the override mechanism already
live for the one SKU-level exception a user hits day one (a solder-down module).

### 4.2 Full (later)

- The `jlcpcb_category.py` client + backfill runner (§2.3) actually exercised against
  live `jlcpcb.com` traffic, replacing/extending the hand-seed with real per-SKU
  JLCPCB-taxonomy resolution at volume — contingent on pinning down the exact
  category-name response field (§2.3's flagged open item).
- A dubIS UI panel for authoring `kicad_symbol`/`kicad_footprint`/`eligible_override`
  per SKU (replacing hand-edited JSON), ideally with autocomplete against the user's
  installed `.kicad_sym`/`.pretty` libraries.
- Multi-footprint support (KiCad 9.0.5+) — expose known alternate packages as a
  footprint list rather than one string.
- `GET /v1/kicad/httplib-config` convenience endpoint / desktop-UI "download
  .kicad_httplib" affordance.
- jlcpcb-catalog **sourcing enrichment** (distinct from categorization): surfacing
  "can I actually buy more of this" annotations, kept as a separate dubIS-side
  sourcing feature, not mixed into the KiCad chooser response (avoids conflating
  "what's in the bin" with "what I could buy" inside one KiCad category tree).
- Possibly write support if/when KiCad's protocol grows a write path.

---

## 5. Testing approach (detailed in the Plan doc's per-task test lists)

- **Contract tests**: every scalar in every response is a JSON string
  (`isinstance(v, str)` on every leaf, not spot checks) — the single easiest thing to
  regress per research §1.3.
- **Real `.kicad_httplib` fixture**: committed under `tests/fixtures/`, used both as a
  manual-smoke artifact for loading into a real KiCad install and to pin the
  config-generation shape.
- **Auth tests**: reuse the Phase 1c harness (`tests/python/server/test_auth.py`
  pattern), parametrized over `Bearer` (existing, must keep working) and `Token`
  (new), plus the negative/`off`-mode cases.
- **Eligibility-filter tests**: category-default exclude, `eligible_override: true`
  force-include, `eligible_override: false` force-exclude, unresolved-category
  invisibility — as direct unit tests against the gating function (§3), not just
  through the HTTP layer.
- **LCSC→category resolution + fallback tests**: a SKU with an LCSC number resolves
  via the (mocked, in MVP-scope tests — no live network in CI) jlcpcb lookup path;
  a SKU without one resolves via `categorize.py`; a SKU whose jlcpcb lookup fails
  falls back to `categorize.py` and logs a warning.

---

## 6. Deferred / open questions (do NOT block MVP)

1. **Exact JLCPCB response field for the human-readable catalog-category name** — the
   research and comparison docs confirm `componentCode`/`componentLibraryType`/
   `componentBrandEn`/`componentModelEn`/`stockCount`/`dataManualUrl` as live fields on
   the public search endpoint, but the specific field carrying a UWRL-style category
   label (`"Multilayer Ceramic Capacitors MLCC - SMD/SMT"`) was not pinned down against
   a live query in this design pass. Needed before Full's backfill runner ships;
   MVP's hand-seed sidesteps it entirely.
2. **Cached vs. live JLCPCB category lookup, long-term** — §2.3 recommends
   cache-forever-until-manually-invalidated (a JLCPCB catalog placement for a given
   LCSC part essentially never changes), but there's no invalidation/refresh policy
   designed yet beyond "delete the cache section of `kicad_mapping.json` and let
   backfill re-run" (self-healing, matching the entity-store convention, but not yet
   exercised).
3. **Symbol/footprint authoring workflow, long-term** — typing `Library:Symbol`
   strings into JSON (or a future form UI, Part-DB-style, ideally with autocomplete
   against installed KiCad libraries) vs. dubIS eventually parsing installed
   `.kicad_sym`/`.pretty` libraries directly (shelling to `kicad-cli` or reading files)
   for a real picker. Flagged Full-only; doesn't affect the override-field shapes
   already committed to in `kicad_mapping.json`.
4. **Per-token category visibility** — Phase 1c tokens are all-or-nothing per identity
   (whole-`/v1` access, hence whole-`/v1/kicad` access too). Not needed for a single
   household deployment; would matter if multiple people share one dubIS instance and
   want different KiCad chooser scopes. Not designed here.
5. **Datasheet URL persistence at the SKU level** — currently transient
   (distributor-client scrapes, discarded). MVP tolerates an empty `Datasheet` field;
   Full should decide whether to persist the URL onto the SKU record generally (useful
   beyond KiCad) or keep it KiCad-mapping-scoped (`kicad_datasheet` override only).
