// tests/js/e2e/live/setup-page.mjs
/**
 * Per-page setup for live-backend E2E tests.
 * Reads the server URL from a file written by global-setup.mjs
 * (process.env doesn't propagate from globalSetup to worker processes),
 * resets backend state, and injects the pywebview bridge.
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
 * Reset backend state — re-copies fixtures, rebuilds inventory.
 * Call in beforeEach to ensure test isolation.
 */
export async function resetServer() {
  const url = getServerUrl();
  const resp = await fetch(`${url}/api/_reset`, { method: 'POST' });
  const body = await resp.json();
  if (!body.ok) throw new Error(`_reset failed: ${body.error}`);
}

/**
 * Inject pywebview bridge that proxies API calls to the live server.
 * Methods in MOCKED return static values (file dialogs, distributor fetches).
 */
export async function setupPage(page) {
  const url = getServerUrl();

  await page.addInitScript((serverUrl) => {
    const MOCKED = {
      open_file_dialog:         () => null,
      save_file_dialog:         () => null,
      load_file:                () => null,
      confirm_close:            () => null,
      set_bom_dirty:            () => null,

      fetch_lcsc_product:       () => null,
      fetch_digikey_product:    () => null,
      fetch_pololu_product:     () => null,
      fetch_mouser_product:     () => null,

      get_price_summary:        () => null,
      record_fetched_prices:    () => null,

      check_digikey_session:    () => ({ logged_in: false }),
      get_digikey_login_status: () => ({ logged_in: false }),
      start_digikey_login:      () => null,
      sync_digikey_cookies:     () => ({ logged_in: false }),
      logout_digikey:           () => null,
    };

    window.pywebview = {
      api: new Proxy({}, {
        get(_target, method) {
          if (typeof method !== 'string') return undefined;
          if (method in MOCKED) return async (..._args) => MOCKED[method]();
          return async (...args) => {
            const resp = await fetch(`${serverUrl}/api/${method}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ args }),
            });
            const body = await resp.json();
            if (!body.ok) throw new Error(`${method}: ${body.error}`);
            return body.result;
          };
        },
      }),
    };
  }, url);
}
