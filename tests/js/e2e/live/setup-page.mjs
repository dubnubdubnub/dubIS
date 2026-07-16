// tests/js/e2e/live/setup-page.mjs
/**
 * Per-page setup for live-backend E2E tests.
 * Reads the server URL from a file written by global-setup.mjs
 * (process.env doesn't propagate from globalSetup to worker processes),
 * resets backend state, and navigates to the app served by the real
 * `python -m server` instance.
 *
 * Unlike the deleted /api/{method} Proxy shim, live pages now hit /v1
 * directly: js/api.js's own HTTP transport (the same one the desktop app
 * uses) finds the real server because setupPage() navigates to that
 * server's own origin (it serves both the /v1 API and the static frontend,
 * via --static-dir — see server/__main__.py). The MOCKED allowlist below
 * stays as window.pywebview.api mocks for methods that were never ported to
 * /v1 (OS dialogs) or that must not hit real network/distributor endpoints
 * during tests.
 */

import { readFileSync } from 'node:fs';
import { SERVER_URL_FILE } from './global-setup.mjs';

/** Read the server URL written by globalSetup. Cached after first read. */
let _cachedUrl;
function getServerUrl() {
  if (!_cachedUrl) {
    try {
      _cachedUrl = readFileSync(SERVER_URL_FILE, 'utf8').trim();
    } catch {
      throw new Error('Server URL file not found — is globalSetup configured?');
    }
  }
  return _cachedUrl;
}

/**
 * Reset backend state — truncates this session's tagged adjustments and
 * rebuilds. See server/__main__.py's _mount_test_routes: lighter than the
 * old e2e-server.py's full-fixture recopy, since direct purchase_ledger.csv
 * writes (import/price/field edits/deletes) aren't undone by it. Call in
 * beforeEach; specs that mutate the ledger directly must use distinct part
 * keys per test.
 */
export async function resetServer() {
  const url = getServerUrl();
  const resp = await fetch(`${url}/v1/_test/reset`, { method: 'POST' });
  const body = await resp.json();
  if (!body.ok) throw new Error(`_test/reset failed: ${JSON.stringify(body)}`);
}

/**
 * Direct backend fetch for spec-side assertions/setup that need to read or
 * mutate server state without going through the UI. Specs used to reach
 * this via `window.pywebview.api.<method>()` under the old bridge (which
 * exposed every InventoryApi method) — the bridge has since shrunk to a
 * ~9-method client shell (Phase 1b Task 8), so that shortcut no longer
 * exists on `window.pywebview.api`. Hits `/v1` directly, same as the app's
 * own transport (js/api.js), just from Node instead of the page.
 */
export async function fetchApi(path, options) {
  const url = getServerUrl();
  const resp = await fetch(`${url}${path}`, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${path}: ${resp.status} ${text}`);
  }
  return resp.json();
}

/**
 * Distributor/pricing/network routes that must never hit the real internet
 * or a real DigiKey CDP session during tests. These methods are HTTP-mapped
 * in js/api-map.js (unlike the client-shell-only methods in MOCKED below),
 * so js/api.js's HTTP transport calls them directly over `/v1` and never
 * consults the injected pywebview Proxy — the only way to intercept them is
 * a Playwright route mock at the HTTP layer, same technique the
 * functional/quality Playwright projects use for `page.route('**\/v1/**')`
 * fixtures (see docs/plans/2026-07-16-phase1b-frontend-port-design.md,
 * decision 6).
 */
async function mockDistributorRoutes(page) {
  await page.route('**/v1/distributors/digikey/session', (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { logged_in: false } });
    }
    return route.fulfill({ json: { logged_in: false, configured: false } });
  });
  await page.route('**/v1/distributors/digikey/cookies/sync', (route) =>
    route.fulfill({ json: { logged_in: false } }));
  await page.route('**/v1/distributors/*/product/*', (route) =>
    route.fulfill({ json: null }));
  await page.route('**/v1/parts/*/prices', (route) =>
    route.fulfill({ json: null }));
  await page.route('**/v1/parts/*/fetched-prices', (route) =>
    route.fulfill({ json: { ok: true } }));
}

/**
 * Navigate to the live app and inject the client-shell mocks.
 * Methods in MOCKED return static values for the ~9-method client shell
 * (file dialogs, window/shell actions) — those never go over HTTP at all.
 * Distributor/pricing methods ARE HTTP-mapped, so they're intercepted via
 * mockDistributorRoutes() instead. Everything else goes over HTTP to the
 * real /v1 server, exactly like the desktop app's own transport (js/api.js).
 */
export async function setupPage(page) {
  const url = getServerUrl();

  await mockDistributorRoutes(page);

  await page.addInitScript(() => {
    // Client-shell-only methods (never HTTP-mapped — see js/api-map.js).
    // Distributor/pricing methods used to live here too, but they're all
    // HTTP-mapped now, so js/api.js's transport calls them over `/v1`
    // directly and never consults this Proxy; those are intercepted via
    // mockDistributorRoutes()'s page.route() fixtures instead.
    const MOCKED = {
      open_file_dialog:  () => null,
      save_file_dialog:  () => null,
      load_file:         () => null,
      confirm_close:     () => null,
      set_bom_dirty:     () => null,
      start_digikey_login: () => null,
    };

    window.pywebview = {
      api: new Proxy({}, {
        get(_target, method) {
          if (typeof method !== 'string') return undefined;
          if (method in MOCKED) return async (..._args) => MOCKED[method]();
          // Not in MOCKED and not routed to /v1 by js/api-map.js — there is
          // no live-server fallback anymore (the old /api/{method} Proxy is
          // gone). Fail loudly rather than silently returning undefined, so
          // a spec exercising an unmapped method surfaces as a clear error
          // instead of a confusing downstream assertion failure.
          return async () => {
            throw new Error(
              `pywebview.api.${method}: no MOCKED entry and no /v1 route — ` +
              'this method is not in the client shell and js/api-map.js ' +
              'does not cover it.',
            );
          };
        },
      }),
    };
  });

  // Navigate to the real server's own origin — js/api.js's httpAvailable()
  // probe does a relative fetch('/v1/health'), so the page must be served
  // from the same origin as the /v1 API for that probe to succeed.
  await page.goto(`${url}/index.html`);
}
