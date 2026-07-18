# KiCad live library (Phase 4)

dubIS exposes a read-only [KiCad HTTP Library](https://docs.kicad.org/master/en/eeschema/eeschema_advanced.html#http-libraries)
at `/v1/kicad/*` — every distributor SKU dubIS has ever purchased, with a
live symbol/footprint/MPN/LCSC/Value annotation, individually placeable in
KiCad's Symbol Chooser. Design doc:
`docs/plans/2026-07-17-phase4-kicad-design.md`.

## Pointing KiCad at it

1. Copy `tests/fixtures/dubis.kicad_httplib` (or write your own — same
   shape) and edit `source.root_url` to your dubIS instance's `/v1/kicad`
   endpoint:
   - Local desktop app: `http://127.0.0.1:<port>/v1/kicad` (the port dubIS
     bound at startup — see `data/.v1_port`).
   - Remote/Phase 1c deployment: `https://dubis.<tailnet>/v1/kicad` (same
     local-vs-remote distinction as every other Phase 1c consumer —
     `docs/deploy-runbook.md`).
2. Set `source.token` to a value from `DUBIS_TOKENS` (only needed when the
   server runs with `DUBIS_AUTH_MODE=on`; a loopback-only local instance in
   `off` mode needs no token at all). Issue one named token per KiCad-using
   human, e.g. `DUBIS_TOKENS=kicad-isaac:<random>,...`.
3. In KiCad: **Preferences → Manage Symbol Libraries** (and/or **Manage
   Footprint Libraries**) → **Add Library** → **HTTP Library** → browse to
   your `.kicad_httplib` file.

KiCad authenticates with `Authorization: Token <token>` (the DRF
`TokenAuthentication` convention its HTTP library client uses) —
`server/auth.py` accepts this scheme identically to `Bearer` (see
`server/auth.py`'s module docstring). No other auth path (loopback trust,
session cookie, tailnet header) changes for this router; `/v1/kicad/*` goes
through the exact same `AuthMiddleware` gate as every other `/v1` route —
it is not exempt, and it is not a separate auth mechanism.

## What's exposed

Four read-only endpoints (`server/routes/kicad.py`, gating logic in
`domain/kicad_view.py`):

| Endpoint | Shape |
|---|---|
| `GET /v1/kicad/` | Connection check: `{"categories": "", "parts": ""}` |
| `GET /v1/kicad/categories.json` | Categories with >= 1 visible member |
| `GET /v1/kicad/parts/category/{id}.json` | Visible SKUs in that category (summary: id/name/description/keywords/footprint_filters) |
| `GET /v1/kicad/parts/{id}.json` | Full detail for one SKU (symbol, fixed field set, footprint filters) — 404 if the id is unknown or gated-invisible |

Every scalar in every response is JSON-encoded **as a string** (`"id":
"16"`, not `16`) — required by the protocol, enforced by explicit
`str`-typed Pydantic response models in `server/models.py`
(`KicadRootResponse`, `KicadCategory`, `KicadPartSummary`,
`KicadPartDetail`).

Part detail's visible field set is fixed for v1: `Value`, `MPN`, `LCSC`,
`Datasheet`. `footprint` and `Manufacturer` are present but hidden.
`unit_price`, `ext_price`, `primary_vendor_id`, `po_history`, `qty`, and
`section` are **never exposed** — no price or purchase history reaches a
schematic.

## Eligibility and category-override model

Every SKU has three independent visibility gates, all of which must pass
(`domain/kicad_view.py::is_visible`):

1. **Resolves to a category.** Resolution order: an explicit per-SKU
   `category_id` override in `data/kicad_mapping.json` wins outright; else
   a memoized LCSC→JLCPCB-taxonomy lookup (wired, not yet exercised against
   live network calls — Full-scope, see below); else `categorize.py`'s
   existing shelf-taxonomy bucket, matched against a `categorize_fallback`
   category entry's `categorize_bucket` field. No match → unresolved →
   invisible.
2. **Resolves to a symbol.** An explicit per-SKU `kicad_symbol` override
   wins; else the resolved category's `default_symbol`. No symbol →
   invisible (KiCad's protocol requires every chooser entry to have one).
3. **Passes eligibility.** The `categorize.py` bucket
   `"Development Boards, Kits, Programmers"` is default-**excluded**;
   every other category is default-**included**. A per-SKU
   `eligible_override` tri-state (`true`/`false`/`null`) wins outright in
   either direction over the category default — `true` force-includes a
   solder-down module (ESP32, Pi Compute Module, radio SoM) that would
   otherwise be lumped in with real dev boards; `false` force-excludes a
   mislabeled tool.

An unmapped SKU and an ineligible SKU look identical to KiCad: both are
simply absent from `categories.json`/`parts/category/{id}.json`, and
`parts/{id}.json` 404s for their id — the correct posture (`docs/plans/
2026-07-17-phase4-kicad-design.md` §3).

## Symbol/footprint mapping — `data/kicad_mapping.json`

The durable entity backing all of the above (entity-store convention:
`docs/entity-store.md`), keyed by canonical `part_id`:

- **`categories`** — the resolved KiCad-facing taxonomy. Each entry carries
  a `default_symbol`/`default_footprint_from_package`/`default_reference`
  cascade (InvenTree-style: a standard `Device:R`/`Device:C`/etc. symbol
  covers most SKUs at zero per-part cost) and either `jlcpcb_catalog_name`
  (`source: "jlcpcb"`, not yet populated — see Deferred) or
  `categorize_bucket` (`source: "categorize_fallback"`, the literal
  `categorize.py` bucket string it maps from).
- **`part_overrides`** — user-curated per-SKU state that must survive cache
  deletion: `category_id`, `kicad_symbol`, `kicad_footprint`,
  `kicad_datasheet` overrides, and the `eligible_override` tri-state.
  Hand-edit this file today (no UI yet — same bootstrap posture
  `data/generic_parts.json` had before its UI existed).
- **`part_category_cache`** — a memoized LCSC→JLCPCB-category lookup
  result. Safe to delete; self-heals on the next backfill run.

`domain/kicad_mapping.py::load_into_db` restores this file into two
derived SQLite tables (`kicad_categories`, `kicad_part_state`) on every
rebuild — deletable, rebuildable from the JSON, same as every other
cache table.

## Deferred (not yet built)

- **Live JLCPCB auto-categorization.** MVP ships with a hand-seeded
  category list (`data/kicad_mapping.json`'s `categorize_fallback` rows);
  the LCSC→JLCPCB-taxonomy lookup (`part_category_cache`, a
  `jlcpcb_category.py` client, a backfill runner) is wired at the read path
  but never populated by a live network call yet. Blocked on pinning down
  the exact JLCPCB response field carrying the human-readable category
  name (design doc §6, item 1).
- **Symbol/footprint UI.** `part_overrides` is hand-edited JSON today; a
  dubIS panel for authoring it (ideally with autocomplete against installed
  `.kicad_sym`/`.pretty` libraries) is Full-scope.
- **`GET /v1/kicad/httplib-config`** convenience endpoint / desktop "download
  `.kicad_httplib`" affordance — today you copy `tests/fixtures/
  dubis.kicad_httplib` by hand and edit `root_url`/`token`.
- Multi-footprint support, per-token category visibility, datasheet URL
  persistence at the SKU level — see design doc §6 for the full list.
