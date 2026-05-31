# Live distributor-query test tier + cached-session helper

**Date:** 2026-05-31
**Status:** Design — awaiting review
**Scope:** Python test suite (`tests/python/`), distributor clients, fixture-capture script, CI config, dev docs.

## Background

A user noticed that running `pytest` opened a visible DigiKey login window. The cause was
`tests/python/test_distributor_api.py::test_start_login`, which calls the real
`start_digikey_login()` — on Windows that runs `subprocess.Popen([browser, --remote-debugging-port, .../MyDigiKey/Login])`,
a non-headless browser launch. (The production login flow *must* open a visible browser so the
user can authenticate; a background poll thread then scrapes the `dkuhint`/`cf_clearance` cookies
via Chrome DevTools Protocol — see `digikey_cdp.py`, `digikey_client.py:_poll_loop`.)

Rather than mock the launch away, the user wants the opposite: **more** tests that hit the real
endpoints with real latency, while (a) not opening a browser when a cached session still works,
and (b) keeping CI green and offline.

### What already exists (do not reinvent)

- **Cookie caching is implemented.** `DistributorManager` wires `DigikeyClient(cookies_file=data/digikey_cookies.json)`.
  `check_session()` loads saved cookies and returns `logged_in` instantly with no browser
  (`digikey_client.py:489-494`); `_set_logged_in()` persists them.
- **A "capture real responses → commit → replay offline in CI" pipeline exists.**
  `scripts/capture-distributor-fixtures.py` fetches **real** LCSC + DigiKey responses (DigiKey via the
  cached cookie header over plain HTTP + `__NEXT_DATA__` extraction — no webview) into the committed
  `tests/fixtures/generated/distributor-scrapes.json`. `tests/python/test_normalizers.py` replays those
  payloads through the real normalizers, fully offline. Its idiom: **no fixture file → no tests
  collected** (no `pytest.skip`), and staleness is a `warnings.warn`, not a failure.
- **No bidirectional cross-language mocking.** JS tests consume Python-generated JSON fixtures
  (`generate-test-fixtures.py`); the only JS-side hand-mock is the `window.pywebview.api` transport,
  not backend logic. Python tests mock only external services (`urllib`, CDP), never the frontend.

### The real coverage gap

The committed fixtures validate the **normalizers** against real data. Nothing automated exercises
the **live transport** of each client — whether the real endpoint still returns the expected shape
today, whether cookies have expired, whether a request is now blocked. And the DigiKey capture path
(HTTP+cookie) is **not** the production path (WebView2/CDP rendering through Cloudflare), so even that
real data doesn't cover the production scrape.

## Goals

1. A live, opt-in test tier that fetches from real distributor endpoints through the **actual clients**,
   catching upstream drift / blocking / cookie expiry — never collected by default `pytest` or CI.
2. Extend the existing capture→commit→replay coverage to **all four** distributors so CI validates
   every normalizer against real-captured data.
3. A cache-first session helper: reuse cached cookies, validate them cheaply (no browser), and open a
   browser **only** when the cache is stale. Shared by both the live tests and the app's startup.
4. Per-fetch latency recorded and reported (no threshold assertions yet).
5. Eliminate the everyday browser-pop annoyance without mocking the launch.

## Non-goals

- Threshold-based latency assertions (record-and-report only this round).
- App-wide latency instrumentation — flagged as a **separate follow-up** plan/agent.
- A bare-pytest DigiKey *product fetch*: it needs a WebView2 GUI event loop, infeasible in pytest.
  DigiKey's live coverage here is a **session smoke** (validate cached cookies) plus the HTTP-path
  capture; the production webview scrape remains covered by manual/app use.
- Running the live tier in GitHub CI (no logged-in session, no GUI, Cloudflare).

## Architecture — two complementary layers

```
                         REAL Python backend / clients
                                     │
        ┌────────────────────────────┴───────────────────────────┐
        │ Layer A (CI, offline)                Layer B (local, opt-in) │
        │ capture-distributor-fixtures.py      pytest -m live           │
        │   → distributor-scrapes.json         real fetch via clients   │
        │     (committed)                      + latency record         │
        │   → test_normalizers.py replays      + DigiKey session smoke  │
        │     all 4 normalizers offline                                  │
        └──────────────────────────────────────────────────────────────┘
                    ▲                                   │
                    └── live tier flags drift ──────────┘
                        → re-run capture → commit fresh fixtures
```

The two layers reinforce each other: Layer A is the fast offline correctness gate; Layer B detects
when Layer A's fixtures have drifted from reality and is how they get refreshed.

## Components

### 1. The `live` marker + default deselection

In `pyproject.toml [tool.pytest.ini_options]`:

```toml
markers = ["live: hits real distributor endpoints / opens a browser; deselected by default"]
addopts = ["-m", "not live"]
```

- Plain `pytest` and CI (`pytest tests/python/ -v`, ci.yml:276) inherit `addopts` → live tests
  deselected (not skipped, not collected). Satisfies the no-skip policy.
- `pytest -m live` overrides `addopts` (command-line `-m` wins) → runs the tier.
- Marker name `live` is local to the Python suite; it is distinct from the Playwright `--project live`
  (a real-backend E2E project) — no interaction.

### 2. Layer A — extend capture + replay to Mouser & Pololu

- `scripts/capture-distributor-fixtures.py`: add `capture_mouser()` (real API via the configured key in
  `data/mouser_credentials.json`) and `capture_pololu()` (real HTTP product page), writing under new
  `mouser` / `pololu` keys in `distributor-scrapes.json`. Mirror the existing rate-limit/backoff and
  `--mouser-only` / `--pololu-only` flags; skip a distributor cleanly (recorded `_auth` error) if its
  credential is absent, exactly as `capture_digikey` already does.
- `tests/python/test_normalizers.py`: add `TestMouserNormalizer` / `TestPololuNormalizer` parametrized
  over the captured parts, asserting the real normalizers produce valid output. Reuse the module-level
  "no fixtures → no tests" guard.

### 3. Layer B — `tests/python/test_distributor_live.py`

All tests `@pytest.mark.live`. A `live_data` fixture copies the canonical `data/digikey_cookies.json`
and `data/mouser_credentials.json` into `tmp_path`, then builds `DistributorManager(tmp_path, ...)`.
Rationale: reuses the cached login every run, and confines any session-invalidation cleanup
(`_invalidate_session(delete_cookies_file=True)`) to the **copy**, never deleting the real file.

Tests:

| Test | Auth needed | Transport | Asserts |
|------|-------------|-----------|---------|
| `test_lcsc_live` | none | real `urllib` JSON API | known C-number → mpn, price tiers, stock |
| `test_pololu_live` | none | real `urllib` HTML | known SKU → fields |
| `test_mouser_live` | API key | real Mouser API | known MPN → fields |
| `test_digikey_session_live` | cached cookies | HTTP probe (no webview) | cached session validates as live |

If a required credential is missing when running `-m live`, the test **fails with an actionable
message** (e.g. "no DigiKey session — log in via the app first"). This is an honest, visible failure of
an explicitly-requested live run, not a hidden skip.

### 4. Latency recording (record & report only)

A small timing helper wraps each live fetch, captures elapsed seconds, prints a per-distributor line,
and appends to a **gitignored** `tests/python/.live_latencies.json` (rolling history). No thresholds.
This provides a baseline now; thresholds can be added later without reworking the tests.

### 5. Shared cache-first session helper (tests **and** app)

New methods on `DigikeyClient`:

- `validate_session_http(cookies) -> bool` — lightweight, **no-webview** check: HTTP GET a DigiKey page
  with the cookie header; return True iff it returns product/account content (not a `/login` redirect,
  not 403). Factors the capture script's `fetch_digikey_http` approach into the client.
- `ensure_session(interactive: bool = False) -> bool` — orchestrator:
  1. load cached cookies; if present and `validate_session_http` passes → mark logged in, return True
     (**no browser**);
  2. else if `interactive` → `start_login()` (visible browser), poll `sync_cookies()` until logged in or
     timeout, persist, return result;
  3. else → return False (caller fails loudly).

Consumers:
- `test_digikey_session_live` calls `ensure_session(interactive=True)` so a stale cache opens a browser
  for re-login, but a warm cache stays silent.
- App startup (`check_session`) gains the cheap `validate_session_http` gate so it reports an
  **honest** logged-in state immediately, instead of only discovering expiry on the first failed fetch.

**Caveat (documented, not solved):** `cf_clearance` is fingerprint-bound, so a passing HTTP probe is a
strong signal but not a guarantee the WebView2 fetch will succeed. The HTTP probe is the right *cheap
cache gate*; the real webview fetch remains the source of truth on an actual product lookup.

### 6. The two browser-opening unit tests

`test_start_login` and `test_check_session`: **revert the earlier `subprocess` mock** (per the
"don't mock it" preference) and mark both `@pytest.mark.live`. Result: everyday `pytest` no longer
opens any browser (deselected); `pytest -m live` exercises the real launch + CDP cookie poll.
Tradeoff: CI loses the trivial "returns a dict" coverage of these two — acceptable, since they are
login plumbing (no product dict to commit; cookies are secret/expiring) and the live tier covers them
for real.

## Cadence — when the live tier runs

Decision: **auto on distributor-code changes + a scheduled drift check.**

- **Auto-on-change (documented in CLAUDE.md):** after touching distributor clients, normalizers, or the
  capture script — and before merging that work — run `pytest -m live`; if it flags drift, re-run the
  capture script and commit refreshed fixtures. Documented so any Claude instance follows it.
- **Scheduled drift check (`/schedule`):** a periodic (e.g. weekly) run that re-captures fixtures and
  reports upstream changes. **Constraint:** a remote scheduled agent has no local DigiKey cookies and no
  GUI, so it covers the HTTP distributors (LCSC, Pololu, Mouser-with-key) and the offline replay; DigiKey
  webview drift still requires a local live run. The schedule setup is the final, optional step after the
  code lands.

## File-by-file change list

| File | Change |
|------|--------|
| `pyproject.toml` | add `live` marker + `addopts = ["-m", "not live"]` |
| `scripts/capture-distributor-fixtures.py` | add Mouser + Pololu capture, `--mouser-only`/`--pololu-only` |
| `tests/python/test_normalizers.py` | add Mouser + Pololu normalizer replay tests |
| `tests/python/test_distributor_live.py` | **new** — Layer B live tier + latency helper + `live_data` fixture |
| `digikey_client.py` | add `validate_session_http()` + `ensure_session()`; wire `check_session()` to the cheap probe |
| `tests/python/test_distributor_api.py` | revert `subprocess` mock on the two tests; mark them `@pytest.mark.live` |
| `.gitignore` | ignore `tests/python/.live_latencies.json` |
| `CLAUDE.md` | document the auto-on-change live-tier trigger |
| `docs/ci-reference.md` | note the `live` marker tier and how to run it |

## Verification

- `pytest tests/python/ -v` (default): no browser opens, live tests not collected, suite green.
- `pytest -m live` locally with a warm cached session: real fetches succeed, **no browser window**,
  latency lines printed.
- `pytest -m live` with cookies deleted: `test_digikey_session_live` opens a browser, polls, re-logs in,
  then passes (interactive path).
- Re-run `capture-distributor-fixtures.py`; `test_normalizers.py` validates all four distributors offline.
- Confirm CI (ci.yml:276) still runs only `-m "not live"` and stays green.

## Risks / open caveats

- HTTP probe vs webview fingerprint divergence (documented above).
- Live tier depends on network + a logged-in local session; it is intentionally non-hermetic and
  local-only. That is the point of the tier, not a defect.
- Scheduled remote drift check cannot cover DigiKey's webview path.
