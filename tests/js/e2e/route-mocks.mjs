/**
 * route-mocks.mjs — /v1 HTTP fixture server for Playwright specs.
 *
 * Replaces (for the panels ported so far) the `window.pywebview.api` mocks in
 * helpers.mjs's `addMockSetup` with `page.route('**\/v1/**')` interception, so
 * js/api.js's HTTP transport is exercised instead of the bridge fallback.
 *
 * Design:
 *   - `addMockSetup` is reused UNCHANGED as the shim layer: dialog/window
 *     methods (`open_file_dialog`, `save_file_dialog`, `load_file`,
 *     `set_bom_dirty`, `confirm_close`, `bench_mark`, `install_tesseract`,
 *     `start_digikey_login`, `open_source_file`) and the inventory-mirror
 *     endpoints (deliberately NOT on /v1 — see Task 4 brief) stay served by
 *     `window.pywebview.api`, exactly as before. Every OTHER method addMockSetup
 *     stubs is still installed on `window.pywebview.api` too, but becomes dead
 *     weight once HTTP wins the probe — harmless, and keeps this file from
 *     having to re-derive shim behavior.
 *   - `installRouteMocks` ADDITIONALLY installs `page.route('**\/v1/**')` to
 *     serve every method THIS WAVE's panels call over HTTP, keyed off the SAME
 *     `options` object addMockSetup takes (so a spec passes one options bag to
 *     both). The `/v1/health` route is installed ONLY here — unported specs
 *     never get it, so their probe fails and they keep falling back to the
 *     bridge (see js/api.js's `probeHttp`).
 *   - Route→method mapping is read from js/api-map.js so path templates never
 *     drift from the generated client; this file supplies the MOCK DATA/LOGIC
 *     per method (paralleling, not reimplementing, addMockSetup's bridge mocks).
 *
 * Grows per wave: add a `route(...)` entry (+ handler) for each newly-ported
 * panel's backend calls.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { addMockSetup } from './helpers.mjs';

// js/api-map.js is a plain .js file with ESM `export const` syntax but no
// "type": "module" in package.json to disambiguate it. Plain `node` (and
// vitest's Vite-based loader) auto-detect and reparse it as ESM fine, but
// Playwright Test's own module loader does not, so `import { API_MAP } from
// '../../../js/api-map.js'` throws "does not provide an export named
// 'API_MAP'" under `playwright test` even though it works standalone. Read +
// parse the object literal instead — sidesteps the loader ambiguity entirely
// and needs no changes to the generated file's format.
const __dirname = dirname(fileURLToPath(import.meta.url));
const _apiMapSrc = readFileSync(join(__dirname, '..', '..', '..', 'js', 'api-map.js'), 'utf8');
const _apiMapMatch = _apiMapSrc.match(/export const API_MAP = (\{[\s\S]*\})\s*;?\s*$/);
if (!_apiMapMatch) {
  throw new Error('route-mocks.mjs: could not parse js/api-map.js — has its export format changed?');
}
const API_MAP = JSON.parse(_apiMapMatch[1]);

/** Turn a `/v1/foo/{bar}/baz` template into a matcher + param-name list. */
function compilePath(template) {
  const names = [];
  const pattern = template.replace(/\{([^}]+)\}/g, (_m, name) => {
    names.push(name);
    return '([^/?]+)';
  });
  return { regex: new RegExp('^' + pattern + '$'), names };
}

/**
 * Build one router entry from a canonical API_MAP method name (pick any alias
 * that shares the verb+path+unwrap — e.g. `get_digikey_session` covers
 * `check_digikey_session`/`get_digikey_login_status` too, since they resolve
 * to the identical route). `handler(argMap, ctx)` returns the UNWRAPPED value;
 * this file's dispatcher applies `entry.unwrap` the same way js/api.js's
 * `callHttp` expects the server to.
 */
function route(method, handler) {
  const entry = API_MAP[method];
  if (!entry) {
    throw new Error(`route-mocks.mjs: "${method}" is not in js/api-map.js — regenerate or fix the name`);
  }
  const { regex, names } = compilePath(entry.path);
  return { method, verb: entry.verb, regex, pathParamNames: names, unwrap: entry.unwrap, handler };
}

// ── Per-method mock logic (mirrors addMockSetup's bridge mocks; Node-side, so
//    no addInitScript serialization boundary — options/inventory are plain
//    closures here). ──

const ROUTES = [
  route('load_preferences', (_a, ctx) => ctx.options.preferences || { thresholds: {} }),
  route('save_preferences', () => true),
  route('rebuild_inventory', (_a, ctx) => ctx.inventory),
  route('list_generic_parts', () => []),
  route('get_warnings', () => ({
    migration: { inferred_count: 0, unknown_count: 0 },
    duplicates: [],
    inferred_only: 0,
  })),
  route('get_digikey_session', (_a, ctx) => ctx.options.digikeyStatus || { logged_in: false }),
  route('validate_digikey_session', () => null),
  route('sync_digikey_cookies', (_a, ctx) => ctx.options.digikeyStatus || { logged_in: false }),
  route('logout_digikey', () => null),
  route('get_mouser_api_key_status', (_a, ctx) => ctx.options.mouserKeyStatus || { configured: false }),
  route('set_mouser_api_key', () => null),
  route('clear_mouser_api_key', () => null),
  route('list_vendors', (_a, ctx) => ctx.options.mfgDirectVendors || [
    { id: 'v_unknown', name: 'Unknown', type: 'unknown', icon: '❓', url: '', favicon_path: '' },
    { id: 'v_self', name: 'Self', type: 'self', icon: '⚙️', url: '', favicon_path: '' },
    { id: 'v_salvage', name: 'Salvage', type: 'salvage', icon: '♻️', url: '', favicon_path: '' },
  ]),
  route('update_vendor', (a, ctx) => {
    const vendors = ctx.options.mfgDirectVendors;
    if (vendors) {
      const existing = vendors.find(v =>
        (v.url && a.url && v.url === a.url) || (v.name && a.name && v.name === a.name));
      if (existing) return existing;
    }
    return {
      id: a.vendor_id || `v_${(a.name || '').toLowerCase().replace(/\s+/g, '_')}_test`,
      name: a.name || '', url: a.url || '', type: a.url ? 'real' : 'inferred',
      favicon_path: '', icon: '',
    };
  }),
  route('delete_vendor', (_a, ctx) => ctx.inventory),
  route('merge_vendors', (_a, ctx) => ctx.inventory),
  route('record_fetched_prices', (_a, ctx) => ctx.inventory),
  route('get_price_summary', (a, ctx) => (ctx.options.priceSummaries || {})[a.part_key] || {}),
  route('get_part_history', (a, ctx) => (ctx.options.partHistory || {})[a.part_key] || []),
  // Covers fetch_lcsc_product/fetch_digikey_product/fetch_pololu_product/fetch_mouser_product —
  // all alias this one route, `{name}` is the distributor.
  route('fetch_distributor_product', (a, ctx) =>
    (ctx.options.productMocks || {})[`${a.name}:${a.code}`] || null),
  route('get_po_source_preview', (a, ctx) => (ctx.options.poSourcePreview || {})[a.po_id] || null),
  route('list_purchase_orders', (_a, ctx) => ctx.options.purchaseOrders || []),
  route('get_po_with_items', (a, ctx) =>
    (ctx.options.poWithItems || {})[a.po_id] || { po_id: a.po_id, line_items: [] }),
];

/** Record a call the same shape a pywebview mock would: [positional args...]. */
function recordCallScript(method, args) {
  return { method, args };
}

/**
 * Install `page.route('**\/v1/**')` fixture handlers for this wave's panels,
 * on top of the (unchanged) `addMockSetup` shim/bridge mocks.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object[]} inventory
 * @param {object} [options] Same option keys as addMockSetup, PLUS this
 *   file's own: digikeyStatus, mouserKeyStatus, priceSummaries, poSourcePreview,
 *   poWithItems (keyed by po_id), preferences.
 */
export async function installRouteMocks(page, inventory, options = {}) {
  await addMockSetup(page, inventory, options);

  const ctx = { inventory, options };

  await page.route('**/v1/health', async (route) => {
    await route.fulfill({ json: { ok: true } });
  });

  await page.route('**/v1/events', async (route) => {
    // Immediately-closing 200 text/event-stream — EventSource will retry with
    // native backoff, which is fine for test-length runs (see Task 4 brief).
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: ': hello\n\n',
    });
  });

  await page.route('**/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const verb = request.method();

    const match = ROUTES.find((r) => r.verb === verb && r.regex.test(pathname));
    if (!match) {
      await route.fulfill({ status: 404, json: { error: `route-mocks.mjs: no mock for ${verb} ${pathname}` } });
      return;
    }

    const argMap = {};
    match.pathParamNames.forEach((name, i) => {
      argMap[name] = decodeURIComponent(match.regex.exec(pathname)[i + 1]);
    });

    const entry = API_MAP[match.method];
    if (entry.bodyParams.length || entry.rawBody) {
      const postData = request.postData();
      if (postData) {
        try {
          const body = JSON.parse(postData);
          if (entry.rawBody) {
            Object.assign(argMap, { [entry.argOrder[0]]: body });
          } else if (body && typeof body === 'object') {
            Object.assign(argMap, body);
          }
        } catch {
          // Non-JSON body on a route with no bodyParams (e.g. GET) — ignore.
        }
      }
    }

    const value = await match.handler(argMap, ctx);

    // Preserve the __apiCalls recorder contract with the SAME positional-args
    // shape a pywebview mock would have recorded.
    const args = entry.argOrder.map((name) => argMap[name]);
    await page.evaluate(({ name, args: recordedArgs }) => {
      window.__apiCalls = window.__apiCalls || {};
      (window.__apiCalls[name] = window.__apiCalls[name] || []).push(recordedArgs);
    }, recordCallScript(match.method, args));

    const body = match.unwrap ? { [match.unwrap]: value } : value;
    await route.fulfill({ json: body === undefined ? null : body });
  });
}
