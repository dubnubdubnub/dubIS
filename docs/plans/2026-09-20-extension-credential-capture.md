# Browser-extension credential capture, and the JLCPCB parts library

**Date:** 2026-09-20
**Branch:** `claude/extension-credential-capture`
**Status:** design

## What the user asked for

1. Get the JLCPCB private parts library (parts stored in JLC's warehouse) into dubIS.
2. Support more than one JLC account.
3. Login UX: click *Sign in* in dubIS, a browser tab opens, and the session lands in
   dubIS automatically.
4. **Saved-password autofill must work.** Stated as non-negotiable — preferable to
   have users quit their browser than to lose it.
5. Rebuild DigiKey and Mouser login the same way, if it fits.
6. Don't ship a wide-open backdoor.

## The decision

**A narrowly-scoped MV3 browser extension, running in the user's own profile, pushes
session cookies to dubIS over an authenticated, user-initiated handshake.**

Requirement 4 forces this. Everything else was eliminated by evidence, below.

### Why every other mechanism is dead

**Page JS cannot read the cookie.** JLC's session cookie is
`JLCPCB_SESSION_ID=<uuid>; Domain=jlcpcb.com; Secure; SameSite=None; HttpOnly`
(observed live). HttpOnly plus cross-origin means a dubIS page at
`http://127.0.0.1:<port>` can never see it, and there is no OAuth callback to redirect
a token to. A bookmarklet fails for the same reason.

**CDP is incompatible with autofill, by explicit design.** Since Chrome 136,
`--remote-debugging-port` is *silently ignored* against the default user-data
directory — the port never binds, with no error
([Chrome for Developers](https://developer.chrome.com/blog/remote-debugging-port)).
The stated mechanism is that a non-default data directory uses a *different encryption
key*, which is anti-infostealer hardening. That also kills the obvious workaround:
a copied profile pointed at a non-default `--user-data-dir` cannot decrypt the saved
passwords, so autofill dies exactly where requirement 4 needs it. Quitting the browser
does not help. This machine runs Chrome 153.

**This already broke DigiKey.** `digikey_session.start_login` (`digikey_session.py:370`)
launches `[exe, f"--remote-debugging-port={port}", url]`, and `--user-data-dir` appears
nowhere in the repo. On any Chromium ≥136 the poll loop (40 × 3s) times out and the UI
sits on *"Browser opened — waiting for login…"* forever. The `ConnectionRefusedError`
branch then advises "close the browser", which cannot help. **DigiKey login is
currently broken on modern Chrome**; this plan is its fix, not a refinement.

**A dedicated Chromium profile loses autofill.** Rejected by requirement 4.

### Why the extension is a good fit and not just the last one standing

It runs *inside* the real profile, so autofill, SSO (JLC offers Google and Apple), and
existing sessions all work because nothing is being simulated. It reads HttpOnly
cookies via `chrome.cookies`, which is the one capability page JS lacks. And it can
re-post a fresh cookie whenever the user is signed in anyway, so dubIS's copy
self-heals and *Sign in* becomes a button pressed once.

## Threat model, and the rules that follow

The danger in a cookie-reading extension is not the cookies. It is **reachability**:
anything on the internet that can ask it to act turns it into a confused deputy. The
rules below are load-bearing, not hygiene.

1. **Enumerated hosts. Never `<all_urls>`.** `host_permissions` lists exactly
   `*://*.jlcpcb.com/*` (phase 1) and later `*://*.digikey.com/*`. The `cookies`
   permission is scoped by host permissions, so Chrome itself enforces the boundary.
   No `tabs`, no `scripting`, no `webRequest`, no `downloads`.
2. **Push-only, gesture-initiated.** No `externally_connectable` — no web page can
   message the extension, ever. It acts on a user click, never on a request.
3. **A pairing token pins the destination and proves intent.** dubIS mints a
   single-use, short-TTL nonce when the user clicks *Sign in*. The extension must
   present it. Without this, the extension could be pointed at an attacker's
   "dubIS", and a send could fire without a user starting one.
4. **Write-only on the dubIS side.** No route ever returns a stored credential.
   Status answers account number, label and item count. `mouser_client.get_api_key`'s
   `{"configured": bool}` surface (`mouser_client.py:241`) is the precedent.
5. **Local-only route.** The receive route goes in `proxy.LOCAL_ONLY_PATHS`
   (`server/proxy.py:77`) and calls `auth.require_loopback` (`server/auth.py:296`),
   so a hub never forwards credentials to a remote source.
6. **Filter at the source.** The extension sends only the named session cookies for
   one domain, never the jar. DigiKey's current code harvests everything and filters
   in the caller (`digikey_session.py:111-181`); invert that.
7. **Visible and revocable.** A preferences panel lists every stored session —
   account, captured-at, last-validated — each with Revoke.
8. **`0600` at rest.** `digikey_cookies.json` and `mouser_credentials.json` are both
   plaintext with default mode bits today. Fix while here.
9. **Watch the permission diff.** The realistic failure mode is not v1; it is an
   extension that grows `<all_urls>` in a routine change later. A test asserts the
   manifest's permission set exactly (see Guards).

## Architecture

```
dubIS preferences                          user's real Chrome
  "Sign in to JLC"  ──1. POST /pairing──►  (autofill, SSO, saved sessions)
   shows pairing code   {nonce, ttl}
        │                                   2. user opens JLC, signs in normally
        │                                   3. user pastes the code into the
        │                                      extension popup, clicks Send
        ▲                                   4. extension polls the library API
        │                                      until code != 460
        │ 5. POST /v1/distributors/jlcpcb/session
        │    {nonce, account, cookies}   (loopback + nonce)
        │ 6. server re-validates, stores, SSE push
```

**The nonce is pasted, not carried in a URL.** The original design had dubIS open
`passport.jlcpcb.com/#/login?dubis_nonce=…` and the service worker read it from the
tab — but seeing a tab's URL requires the `tabs` permission or a content script, and
rules 1 and 2 forbid both. Paste-into-popup is the strictly smaller attack surface:
the popup click *is* the gesture, and the extension needs no visibility into browsing
at all. Adopted over the original.

**Nonce TTL must cover the whole human flow**, not just the handshake: display →
paste → sign in → up to ~2 minutes of polling → POST. It is consumed at step 5, which
is minutes after step 1. **10 minutes**, single-use. (A 60s TTL was specified in an
earlier draft of this plan and is wrong — it expires mid-login every time.)

### Validation is an API call, not a cookie check

An anonymous request to the JLC API **already mints a fresh `JLCPCB_SESSION_ID`**
(verified: `curl` with no cookies returns `{"code":460,...}` and a `set-cookie`).
So cookie-presence is not evidence of login — a poll that waits for the cookie to
appear succeeds instantly and harvests an anonymous session, 100% of the time.

Both the extension's poll and the server's accept-time check must call
`GET /api/overseas-smt-component-order-platform/v1/overseasSmtComponentOrder/myLibrary/getCustomerComponentStock?pageNum=1&pageSize=1&keyWord=`
and require `code != 460`. This is the same class of mistake as DigiKey's
`"dkuhint" in cookie_names` heuristic (`digikey_session.py:74-81`), but here the naive
version never works.

### Three-state session validation

Follow `digikey_session.validate_session_http` (`:208-260`): `code 460` → expired;
`code 200` with data → valid; network error, 403, 5xx → **inconclusive, keep the
session**. Never invalidate on a probe that could not run. `check_session` must return
a truthful dict, never raise — the frontend calls distributor session routes on every
startup, and that has already produced a 500 twice (CLAUDE.md Traps).

### Credential store

`data/jlc_sessions.json`, mode `0600`:

```json
{
  "12625901A": {
    "label": "impossible_hardware",
    "cookies": [{"name": "JLCPCB_SESSION_ID", "value": "...", "domain": ".jlcpcb.com"}],
    "added_at": "2026-09-20T02:04:59Z",
    "last_ok": "2026-09-20T02:05:03Z"
  }
}
```

Keyed by JLC account number, which the validation call returns (`customerCode` on every
row). That makes multi-account bookkeeping automatic: the extension posts whatever is
signed in, and the server files it by the account it actually resolved to — the user
never has to declare which account they just used.

## Why the fetch path is easy for JLC and hard for DigiKey

**JLC: no bot wall.** Plain `curl`, default `curl/8.x` UA, no cookies, reaches the API
and gets a clean application-level `{"code":460}`. Akamai is in front (`ak_p`,
`akamai-grn` headers) but is not challenging this endpoint. So once the cookie is held,
a plain `urllib`/`httpx` request works — **no browser, no CDP, no WebView2.** This is
what makes JLC the right first target.

**DigiKey: `cf_clearance` is fingerprint-bound** to the browser that earned it (TLS/JA3
plus User-Agent). That is why the existing design replays cookies *into a WebView2
window* (`digikey_session.inject_cookies_to_window:421-471`) rather than doing plain
HTTP. So the extension cleanly fixes DigiKey *acquisition*, but *fetching* still needs
a real browser. Phase 2 decides between keeping that split and letting the extension
fetch too — see Open decisions.

## Mouser is out of scope, deliberately

Mouser is an **API key**, not a session: `{"api_key": "..."}` with status
`{"configured": bool}` (`mouser_client.py:226-266`). A durable credential pasted once,
no expiry, nothing to harvest. An extension adds nothing. The only Mouser pain is the
*keyless* path, where `mouser_client` renders the product page through `browser_page`
and parses `table.pricing-table` — a fetch problem, not an auth problem, and it is
already solved in-cluster by `DUBIS_CDP_URL`.

The one change Mouser gets here is rule 8: write its credential file `0600`.

## Reach: which browser runs this, and which dubIS it can reach

Two questions this design keeps provoking. Answered here with evidence so
neither gets re-litigated from scratch.

### The cluster browser must not get this extension

`DUBIS_CDP_URL` points `browser_page` at the shared Chrome in the cluster's
`browser` namespace (`deploy/deployment.yaml`). Three independent reasons the
extension does not belong there:

1. **Nothing in the JLC path touches a browser.** `jlc_session.fetch_page`
   (`jlc_session.py:174`) is plain `urllib`, because JLC puts no bot wall on the
   library endpoint — the fact that made JLC the right first target, above.
   `browser_page` is imported by `mouser_client.py` and two `scripts/`, never by
   `jlc_session.py` or `jlcpcb_client.py`. Installing the extension in that
   browser would not place it on any code path dubIS runs.
2. **There is nobody there to click it.** Rule 2 makes the extension push-only
   and gesture-initiated: a human signs in to JLC in *their own* profile — that
   is the entire point, requirement 4 — and pastes a pairing code into the popup.
   A shared automation browser has no JLC login, no saved passwords, no user.
3. **It would be an exfiltration vector.** CDP is unauthenticated, which is
   precisely why Isaac declined labelling `arc-runners` `browser-client=enabled`
   (`docs/ci-reference.md`): anything that reaches port 9222 can drive that
   browser. This extension's safety argument is *reachability* — no
   `externally_connectable`, no content scripts, one human gesture — and an open
   CDP port is a general-purpose reachability bypass for every extension in the
   profile, at a fixed published ID since `manifest.json` pins `key`. It would
   turn a shared page-renderer into a cookie-reading service for anything in the
   cluster that can open a socket to it.

**No**, therefore — in phase 2 as well. The cluster browser exists to render
pages the server cannot fetch; JLC needs no rendering.

### A remote dubis-server cannot be paired — verified, and deliberate

Rule 5 in practice, both halves confirmed against the code:

- `POST /v1/distributors/jlcpcb/pairing` and `POST /v1/distributors/jlcpcb/session`
  are in `proxy.LOCAL_ONLY_PATHS` (`server/proxy.py:90-91`), so a hub serves them
  itself and never forwards them to a source.
- Both call `auth.require_loopback` (`server/routes/distributors.py:145,162`).
  The deployed server runs `DUBIS_AUTH_MODE=on` (`docs/deploy-runbook.md`), so a
  tailnet caller resolves to a token name or a tailnet login, neither of which is
  `local`, and gets 403 `loopback_only`. Pinned by
  `tests/python/server/test_jlcpcb_routes.py::test_credential_intake_refuses_a_remote_caller`.

So the extension on a laptop can never hand a session to the tailnet server. That
is the intended reading of rule 5 and not an oversight.

**One caveat worth knowing:** `require_loopback` is a *no-op* when auth is off
(`server/auth.py:296-315` — no middleware means no `request.state.identity`, and
"everything is loopback by definition"). An `off`-mode server bound to anything
but loopback therefore accepts a pushed credential from any peer that can reach
it. That is the same trust model `/v1/import/parse` has always had, and the
network boundary is the real guard; it is stated here because the phase-1 prose
asserts the gate unconditionally. Pinned as behaviour by
`test_credential_intake_is_gated_only_in_auth_on_mode`.

### Decision: remote JLC credentials are out of scope

The hub architecture makes this cost far less than it first appears. The desktop
app always boots its own local `/v1` hub and treats remote servers as *sources*
(CLAUDE.md, "Remote deployment"), so the machine holding the JLC cookie is the
machine the user is sitting at, and `fetch_jlc_library` runs there over plain
`urllib`. Nothing about reading a JLC library needs the credential to exist on
the cluster.

Three things are genuinely lost, and only the first is likely to be noticed:

1. **Pairing writes local; status reads the active source.** `/sessions` and
   `/library` are deliberately *not* local-only
   (`test_the_read_only_jlc_paths_are_not_local_only`) and the preferences panel
   calls them through plain `api()` (`js/jlc-sessions.js`), which stamps the
   active tab's `X-Dubis-Source`. Pair while a remote tab is in front and the
   write lands on your hub while the panel keeps reading the remote's empty store
   — "No JLC account paired", forever. **Pair from a Local tab.** Making the
   panel pin itself to `local` would fix the split-brain and forfeit the
   asymmetry's whole point (a future remote that holds its own credential); it is
   a phase-2 call, not a phase-1 patch.
2. `dubis jlc library` against the deployed server from another machine answers
   401 `distributor_auth`.
3. A browser pointed straight at the tailnet server, with no local hub in the
   picture, cannot pair at all and shows an empty JLC panel.

### If that is revisited, the cost is two guarded edits, not one

Weighed, so a later phase starts from here rather than from zero:

- **A second CORS exception plus bearer auth on the receive route.** Cheapest on
  paper and the only option that serves loss 3. But it costs *two* deliberate
  widenings, not one: `Access-Control-Allow-Origin` for the extension's origin
  (`tests/python/server/test_health_cors.py` exists to fail when that spreads
  beyond `/v1/health`) **and** a host permission in the manifest
  (`tests/python/test_extension_manifest.py` pins the set exactly, and forbids
  `optional_host_permissions`). It also means a live JLC cookie crossing a
  network, which is the thing rule 5 exists to prevent — so it needs the receive
  route to require a bearer token *and* keep the nonce, never one or the other.
- **The Unix-socket transport over `ssh -L`** (`server/uds.py`). Unforgeable and
  secret-free, and it is how a shared Linux box already works — but a UDS peer is
  *not* `local` (CLAUDE.md, trap (f)), so `require_loopback` refuses it today.
  Admitting it would mean deciding that a named peer may push a credential, which
  is a broader change than it looks.
- **A CLI paste path** (`dubis jlc paste-session`) over the already-authenticated
  `/v1` channel. Smallest honest option for losses 2 and 3, since the transport's
  auth already exists and nothing new becomes cross-origin readable. Costs the
  user a manual cookie copy, which requirement 4 was trying to avoid — but only
  for the remote case, which is rare.
- **Out of scope** — what phase 1 does, and the recommendation until someone
  actually wants loss 2 or 3.

### Open blocker: the extension has no host permission for *any* dubIS

Found while answering the above, unresolved, and it affects the **local** flow
too:

`host_permissions` is `*://*.jlcpcb.com/*` and nothing else
(`extension/jlc-bridge/manifest.json`), while `pushToDubis`
(`extension/jlc-bridge/background.js`) POSTs `Content-Type: application/json` to
the configured dubIS origin. Chrome's rule for an MV3 service worker is that a
fetch to a host *outside* `host_permissions` is an ordinary cross-origin request
([Cross-origin network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)),
so that POST needs a preflight — and `/v1` answers one with `405` and no
`Access-Control-*` header at all (verified against `create_app`; only
`/v1/health` carries CORS, by design). If that reading is right the push never
reaches dubIS, on loopback as much as on the tailnet.

Two ways out, both deliberate manifest edits that must update
`tests/python/test_extension_manifest.py` in the same change: a fixed loopback
host permission (`http://127.0.0.1/*`, `http://localhost/*` — wide enough to
cover the ephemeral port, narrow enough to stay off the public internet), or
`optional_host_permissions` granted at runtime, which that guard currently
forbids on purpose. **Confirm it live first** — load the unpacked extension,
press Send, and read the service-worker console — because the fix is a widening
of the one boundary this whole design rests on and should not be made on a
documentation reading alone.

While there: `DEFAULT_BASE_URL` is `http://127.0.0.1:7897`
(`extension/jlc-bridge/config.js`), which matches nothing. `dubis serve` defaults
to `7891` (`server/__main__.py`) and the desktop app picks an **ephemeral** port
per launch (`app.pyw`'s `_free_port`, written to `data/.v1_port`), so the options
page would need re-typing after every launch. A stable, documented port for the
desktop hub — or having the panel show the current one next to the pairing code —
is part of whatever fixes the above.

## Phases

### Phase 1 — JLC auth loop + library read (this branch)

Ends at: *the user clicks Sign in, logs in with autofill, and dubIS can list their JLC
warehouse parts.* Deliberately stops short of merging into inventory.

- **1a. Extension** — `extension/jlc-bridge/`: MV3 manifest, service worker, options
  page (dubIS base URL), popup (status + manual re-send). Push-only, nonce-bearing,
  two host permissions.
- **1b. Server** — `jlc_session.py` (store, validate, three-state), `jlcpcb_client.py`
  (paginated library fetch → dubIS-shaped records), routes on
  `server/routes/distributors.py`, models, `DistributorFacade` wiring, frozen-surface
  test updates.
- **1c. Frontend + CLI** — preferences panel (sign in, session list, revoke),
  regenerate `js/api-map.js` / `tools/dubis-cli`, a curated `dubis jlc library`
  command.

### Phase 2 — DigiKey acquisition via the same extension

Add `*://*.digikey.com/*`, reuse the handshake, retire the dead CDP launch path. The
fetch question stays open (below).

### Phase 3 — inventory integration

Blocked on a decision (below). Not scoped here.

## Decisions (settled with Isaac, 2026-09-20)

**The fact that drives 1 and 2:** JLC's own docs state that private-library parts are
*exclusively for PCBA orders and will not ship separately*. They can never be
hand-soldered. So JLC stock is not "the same parts in another drawer" — it is a
different class of asset, and `domain/federation.py`'s qty rule ("850 on the bench plus
400 in the shop is 1250 parts") is simply false for it.

1. **JLC enters inventory as a read-only source carrying a `fungible: false` flag.**
   Merged `qty` sums only fungible sources; the `sources` breakdown carries every
   source including non-fungible ones, so the inventory row reads as bench stock with
   a "+1010 at JLC" badge — the *provenance-is-a-badge-not-a-column* pattern already
   established in `docs/plans/2026-09-19-multi-server-hub-design.md`.

   *Accepted cost:* `domain/federation.py` currently guarantees that entries in
   `sources` sum to the row's `qty`, and that invariant is property-tested. It becomes
   "*fungible* entries sum to `qty`". Deliberate weakening of a pure module's
   invariant — update the property test with a comment naming this decision.

2. **No JLC-assembly planning mode. JLC stock is display-only and never enters
   planning.** Isaac plans assembly orders in JLC's own BOM tool.

   This makes `cart_plan.requirement` (`domain/cart_plan.py:36-56`) correct as written:
   it reads the *local* `stock` table via `domain/api_cart.py:128`, so non-fungible
   source stock is structurally excluded. **Add a permanent regression test asserting
   JLC stock does not reduce a bench requirement**, so nobody later "helpfully" wires
   merged qty into `_on_hand`. That test is a guard forever, not a phase-3 placeholder.

   Consequence: the planner never needs per-source stock queries, so the non-fungible
   flag stays purely a merge/display concern. Phase 3 is smaller than originally scoped.

3. **Phase 2 harvests cookies only; DigiKey keeps its WebView2 fetch.** Rule 2
   (push-only, gesture-initiated) is the single property that makes this extension
   safe, and a fetch proxy requires an inbound command channel from dubIS — even a
   poll-for-work design spends it semantically. DigiKey's *fetch* works where people
   use it; only its *login* is broken, and that is what phase 2 repairs.

   Revisit only if DigiKey pricing in the container stops being an acceptable gap
   (today it is a documented deliberate one). Note the second-order cost that decided
   this: extension-fetch would make the extension load-bearing for core pricing, not
   just onboarding — "no extension, no DigiKey prices" is a far bigger commitment than
   "no extension, paste a cookie".

4. **Unpacked dev-mode now; Chrome Web Store *unlisted* before anyone but Isaac
   installs it.** Auto-update is a security property for a credential-handling
   extension, and Developer Mode nags make unpacked unpleasant for non-developers.

   **Add a `key` field to `manifest.json` from the start** so the extension ID is
   stable across the move — otherwise the ID changes on migration and anything pinning
   it breaks.

## What this deliberately does not do

- **No scripted login.** Chrome's saved-password autofill cannot be driven
  programmatically (verified: the password field exposes a native dropdown that page
  automation cannot operate), and JLC offers Google/Apple SSO where there are no
  credentials to hold. dubIS must never attempt to log in on the user's behalf.
- **No cookie read-back.** No route returns a stored credential, ever.
- **No generic fetch proxy in phase 1.** The extension has no "fetch this URL" message.
- **No Mouser login.** There isn't one.
- **No inventory merge.** Phase 3, blocked on decision 1.

## Guards this feature must satisfy

- `tests/python/test_api_surface.py` and `tests/python/server/test_v1_surface.py`
  freeze the method/route surface — additions are deliberate edits, not drift.
- A new test asserts the extension manifest's `permissions` and `host_permissions`
  match an expected set **exactly**, so a later widening fails CI (rule 9).
- A test asserts the receive route is in `proxy.LOCAL_ONLY_PATHS` and that
  `tests/python/server/test_health_cors.py`'s "only `/v1/health` carries CORS"
  invariant still holds — the extension reaching a *remote* dubIS is phase 2+ and
  needs its own deliberate, tested exception.
- A test asserts no route response schema contains a cookie or key value.
- Credential files are created `0600`; a test asserts the mode.
- `bash scripts/verify.sh` green before PR.

## Appendix — observed facts

Gathered live on 2026-09-20 against account `12625901A` (`impossible_hardware`).

- Library endpoint: `GET /api/overseas-smt-component-order-platform/v1/overseasSmtComponentOrder/myLibrary/getCustomerComponentStock?pageNum=N&pageSize=100&keyWord=`; `pageSize` caps at 100 (`{"code":200,"msg":"pageSize cannot exceed 100"}`).
- Envelope: `{code, msg, data:{total, list, pages, ...}}` (PageHelper-style).
- Row fields: `lcscComponentId, minImage, minImageAccessId, description, customerCode, componentCode, componentModel, privateStockCount, overseasStockCount, postStockCount, idleStockCount, customerPresaleStockKeyId, componentType, componentSpecification, componentBrand, componentStatus, buyPrivateFlag, stockStatus, urlSuffix, rohsFlag, overseasComponentCatalogBizKey`.
- Field mapping: `componentCode`→`lcsc` (dubIS's primary key), `componentModel`→`mpn`,
  `componentBrand`→`manufacturer`, `componentSpecification`→`package`,
  `description`→`description`, `componentType`→`section`, `privateStockCount`→`qty`.
- **No price field.** `unit_price`/`ext_price` must come from the public LCSC catalog
  (`lcsc_client.py` already speaks it) or Parts Order History.
- Live state: 191 items / 170,627 units; `overseasStockCount`, `postStockCount`,
  `idleStockCount` all zero across every row. Global Sourcing / Consign / Free Parts
  all zero.
- Login page: `https://passport.jlcpcb.com/#/login` — JLCONE SSO spanning JLCPCB,
  JLC3DP, JLCCNC, JLCMC, JLCDFM, JLCCAN, EasyEDA and **LCSC**. "Remember me" defaults
  unchecked and is orthogonal to API access (it governs browser cookie persistence,
  not authentication).
- JLC supports native sub-accounts — a Super Administrator may create up to 50 Member
  Accounts. Unverified whether a Member sees the admin's My Parts Lib; if it does, an
  org may need only one credential.
