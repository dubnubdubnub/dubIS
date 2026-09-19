# Multi-server dubIS: the local server becomes a hub

**Date:** 2026-09-19
**Branch:** `claude/multi-server-tabs`
**Status:** design

## What the user asked for

1. Connect to several dubIS servers and load their part inventories.
2. A quick switcher — tabs at the top — to move between servers instantly.
3. A merged view across servers, reached from those same tabs.
4. No client restart to change servers.

## The decision that makes all four cheap

**The desktop app always runs its local `/v1` server, always serves the window from
it, and never navigates away from that origin. Other dubIS servers become *data
sources* that the local server fetches on the client's behalf.**

Today the opposite is true: `app.pyw:198` resolves one URL at launch, and in remote
mode it skips booting a local server entirely (`app.pyw:258`) and points the webview
at the remote origin. That single decision is what forces a restart to switch, and
it is also what makes a merged view impossible in the browser.

### Why not fetch the other servers from JS

The obvious design — teach `js/api.js` a per-call base URL and fan out from the page —
runs into four walls, all of them load-bearing:

- **CORS.** Only `/v1/health` carries `Access-Control-Allow-Origin` (`server/routes/meta.py:20`),
  and `tests/python/server/test_health_cors.py:69` exists specifically to fail if that
  spreads to `/v1/parts`, `/v1/meta` or `/v1/preferences`.
- **Preflight.** A bearer token makes every cross-origin call preflighted, and
  `AuthMiddleware.dispatch` (`server/auth.py:192`) checks only `request.url.path`,
  never the method — so an `OPTIONS` arrives unauthenticated and gets 401, and would
  otherwise get 405 since no route declares OPTIONS.
- **SSE.** `EventSource` cannot set an `Authorization` header, and the session cookie is
  `samesite="lax"` (`server/auth.py:251`), so it is not sent on cross-site streams.
  Cross-origin push would need a new `?token=` surface.
- **Mixed content.** A page served over `https` from the tailnet can never reach
  `http://127.0.0.1` — `js/servers-logic.js:243` already documents this as unprobeable.

Fetching from the hub sidesteps every one of them. The browser stays same-origin by
construction: no CORS, no preflight, no cookie rules, no mixed content, and the SSE
connection opened at `js/app-init.js:133` never has to be torn down. Verified today:
`curl https://dubis-server.miku-parore.ts.net/v1/parts` returns 200 from this machine
with no token, because the tailscale operator proxy supplies the identity — so the hub
can read the deployed server **with no change to the deployed image**.

It also fixes a wart the current remote mode has: preferences are read from whichever
server is active (`js/store.js:311`), so the server roster itself moves when you switch.
With a hub, preferences are always local, and the roster is stable client config.

## Architecture

```
webview window  ──same-origin──►  local dubIS server (the HUB)
 (always http://127.0.0.1:port)     │
                                    ├─ active = local   → handled in-process (today's path)
                                    ├─ active = remote  → proxied with that source's token
                                    └─ active = merged  → fan-out + merge across sources
                                                            │
                                            ┌───────────────┼───────────────┐
                                            ▼               ▼               ▼
                                     bench (local)    shop (tailnet)   any dubIS /v1
```

### New server modules

| Unit | Responsibility |
|------|----------------|
| `server/sources.py` | The source registry: id/name/url/token/enabled, read from preferences, plus which source is *active* (`local`, a source id, or `merged`). Holds an `httpx.AsyncClient` per remote source. |
| `server/routes/sources.py` | `GET /v1/sources` (roster + live reachability), `PUT /v1/sources/active` (switch — **no restart**), `POST/PATCH/DELETE /v1/sources` (roster CRUD, superseding the JS-side roster writes). |
| `server/proxy.py` | Forward one `/v1` request to a source and stream the response back, carrying the source's bearer token. Local-only allowlist: `/v1/health`, `/v1/sources`, `/v1/preferences`, `/v1/import/parse` (already loopback-only), `/v1/events`. |
| `domain/federation.py` | **Pure** merge: given `{source_id: [records]}`, produce merged records. No I/O, unit-tested. |
| `server/fanout.py` | Async fan-out to enabled sources with per-source timeout and partial failure (a source that is down degrades the view, never fails it). |

### Merge semantics (decided with the user)

One row per part key, `qty` summed across sources, with a per-source breakdown that the
UI can expand:

```json
{ "lcsc": "C1000", "qty": 1250,
  "sources": [ {"id": "bench", "name": "bench", "qty": 850},
               {"id": "shop",  "name": "shop",  "qty": 400} ] }
```

- Part identity reuses the frontend's existing rule so the two halves agree:
  `invPartKey` (`js/part-keys.js:167`) — `lcsc` when it starts with C, else
  `mpn || digikey || pololu || mouser`. Ported to `domain/federation.py` and pinned by a
  test that feeds both implementations the same fixture.
- Conflicting scalar metadata (description, package, manufacturer): first non-empty
  value wins, in source order, and a `conflicts` list names any field where sources
  disagreed, so the UI can mark it rather than silently pick.
- Money fields (`ext_price`) sum; `unit_price` becomes the quantity-weighted mean.
- A single-source row still gets a one-entry `sources` array — the UI never branches.

### Writes in merged mode (decided with the user)

A row knows its owner, so writes route to it. Transport: an optional
`X-Dubis-Source: <source id>` request header, honored by the proxy layer. This is chosen
precisely because `js/api.js` maps args **positionally** onto `entry.argOrder`
(`js/api.js:81`) across 141 call sites — a header set by a new `apiOn(sourceId, method, ...)`
wrapper touches only the handful of mutation sites that need routing, and leaves the
other 130-odd untouched. A summed row with two sources prompts for the target.

### Push updates

The hub owns the only SSE stream the browser sees. For each enabled remote source it
runs a background subscriber on that source's `/v1/events` and re-publishes what it
hears into the local bus (`server/events.py`), tagged with the source id. Switching the
active source publishes `inventory.updated` so the existing debounce in
`js/store.js:522` re-fetches — the switch needs no new frontend machinery.

## Boot changes

- `app.pyw` always starts the local server and always navigates to
  `splash.html?port=<port>`. The `?base=` branch (`app.pyw:261`) goes away, and with it
  the second-origin navigation race stays permanently out of reach.
- `resolve_remote_base_url` keeps its precedence rules but its result now seeds the
  hub's *active source* instead of deciding whether to boot. `DUBIS_URL` and
  `server_url` keep working and keep meaning "where my data comes from", which is what
  `tools/dubis-cli` already assumes.
- `restart_app` stays on the client shell (the frozen surface in
  `tests/python/test_api_surface.py:63` is unchanged), but nothing in the server picker
  calls it any more.

## Frontend

- `js/server-tabs.js` + `css/components/server-tabs.css`: the tab strip. Tabs are
  Local, each roster entry, and All. Click = `PUT /v1/sources/active` then the existing
  refresh path. A reachability dot per tab, now fed by the hub's probe (the hub is what
  does the fetching, so "reachable from the hub" is finally the honest question).
- **Header trap**: `.header` is `flex-wrap: wrap` and a new row costs ~28px, which
  `tests/js/e2e/resize-visibility.spec.mjs` catches by shoving the Import button off an
  800x600 viewport. The tab strip therefore gets its own row *below* the header rather
  than inside it, and follows the `max-width: 1199px` precedent in
  `css/components/ui-zoom.css` for narrow widths.
- Inventory gains a source badge/column and an expandable per-source breakdown on
  summed rows. New record fields go through `domain/schema.py` →
  `scripts/gen-inventory-types.py` → `js/inventory-record.d.ts`, per CLAUDE.md.
- Row identity: `rowMap` (`js/inventory/inv-state.js:16`) and `tr.dataset.partKey`
  (`js/inventory/inv-html-builders.js:182`) stay keyed by part key, which the merge
  keeps unique by construction. Only the write path needs the source id.

## What this deliberately does not do

- No change to the deployed server image is required for any of the above.
- No cross-origin `/v1` surface; `test_health_cors.py`'s spread guard stays green as
  written.
- The remote-served browser client (someone opening the tailnet URL directly) sees
  exactly what it sees today: that server, alone. Hub behavior is available there too,
  but only if peer sources are configured on it.

## Addendum — frontend facts that shaped the above

Added after reading the inventory/header/test surface.

### Provenance is a badge, not a column

A new inventory *column* has to be registered in five hand-maintained lists that must
agree — header HTML (`js/inventory/inv-html-builders.js:505`), row HTML (`:148`), the
resize registry `INV_RESIZE_COLS` (`:447`), a matching pair of CSS width rules
(`css/panels/inventory.css:75` and `:259`) and the sort config
(`js/inventory/inv-sort-group.js:41`) — plus two e2e specs that hard-code the
header-class ↔ row-selector pairs (`tests/js/e2e/inv-col-alignment.spec.mjs:20`,
`inv-col-header.spec.mjs`).

A **badge in the existing group cell**, beside `.inv-section-chip`
(`inv-html-builders.js:124`), is the established in-row provenance pattern and skips
three of those five. Summed rows get an expander that reveals the per-source
breakdown. A `server` filter chip follows the `distributor`/`section` precedent in
`js/inventory/filter-chips-fields.js:22`, and server name joins the search haystack in
`inventory-logic.js:47`.

### Row identity is the real collision surface

`invPartKey` (`js/part-keys.js:67`) returns the same key for `C1000` on two servers, and
~15 call sites key off it: `row.dataset.partId` (`inv-row-build.js:32`), roving focus
(`js/a11y/keyboard-nav.js:88`), label selection (`js/label-selection.js`), near-miss
badges, import markers, the group flyout, feeder lookup, price fetch, and the
`{bomKey, invPartKey}` pairs persisted for manual links and confirmed matches
(`js/store.js:228`).

The merge keeps one row per part key, so the *display* surface stays collision-free by
construction. Only the write path needs the source id, which is why writes carry
`X-Dubis-Source` rather than a new key format — a key format change would touch every
site above and would break the persisted link pairs.

One place already does the right thing and is worth preserving as the model: linking
mode compares by **object identity** (`inv-row-build.js:38`), not by key.

### Guards this feature must satisfy

`scripts/verify.sh` runs four staleness guards that this change can trip:

- **manifests** — a new module in `js/inventory/` must be listed in `js/inventory/_README.md`.
- **layout-tokens** — no bare `px` for width/height/padding/gap in new CSS; use tokens in
  `css/tokens.css`. The ignore file is keyed by line number, so `python scripts/regen-layout-ignore.py`
  after any drift.
- **claude-md** — every backticked repo path in `CLAUDE.md` must exist, so document new
  files only once they are written.
- **inventory-types** — a new record field means `domain/schema.py` →
  `scripts/gen-inventory-types.py` → `js/inventory-record.d.ts`, and note that
  `cache_db.query_inventory:701` is still hand-written and not derived from the schema.

Also: `js/store.js:670` applies a preference whitelist on **both** read and write, so a
new preference key that is not added there looks saved and comes back as the default,
silently, in both directions.

### Header height is the sharp edge

`.header` is `flex-wrap: wrap`; a new control costs a whole ~28px row at narrow widths
and pushes the panels down far enough to shove the Import button off an 800×600
viewport, which `tests/js/e2e/resize-visibility.spec.mjs` asserts against and which
CLAUDE.md:126 forbids weakening. The tab strip therefore lives in its own row below the
header, hides below `max-width: 1199px` exactly as `css/components/ui-zoom.css:11` does,
and scrolls horizontally rather than wrapping when there are many servers. Below that
width the Preferences server picker remains the way to switch, so no capability is lost.

### Testing a second server

The `live` e2e project spawns exactly one backend: `tests/js/e2e/live/global-setup.mjs:71`
runs `python -m server --data-dir <tmp> --port 0`, waits for `READY:<port>`, and writes
the URL to a single fixed path (`SERVER_URL_FILE`, `:17`). Nothing there is parameterized
over N servers, so a genuine two-server e2e means a second spawn and a second URL file.
The mocked `functional` project needs none of that — `tests/js/e2e/server-picker.spec.mjs:26`
already fakes three distinct origins with `page.route`.
