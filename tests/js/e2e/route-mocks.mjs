/**
 * route-mocks.mjs — /v1 HTTP fixture server for Playwright specs.
 *
 * Since Phase 1b Task 10, `js/api.js`'s `api()` is HTTP-only for every method
 * in `js/api-map.js` — there is no bridge fallback left to fall through to.
 * Every spec that renders the inventory grid (or calls any mapped method)
 * MUST use `installRouteMocks`, not `addMockSetup` alone, or its `/v1`
 * requests hit nothing and fail (no server, no bridge).
 *
 * Design:
 *   - `addMockSetup` is reused UNCHANGED as the shim layer: dialog/window
 *     methods (`open_file_dialog`, `save_file_dialog`, `load_file`,
 *     `set_bom_dirty`, `confirm_close`, `bench_mark`, `install_tesseract`,
 *     `start_digikey_login`, `open_source_file`) and the inventory-mirror
 *     endpoints (deliberately NOT on /v1 — see Task 4 brief) are the only
 *     methods actually still reachable through `window.pywebview.api` (the
 *     ~9-method ClientShell). Every OTHER method addMockSetup stubs is still
 *     installed on `window.pywebview.api` too, but is dead weight now — kept
 *     only so this file doesn't have to re-derive shim behavior.
 *   - `installRouteMocks` ADDITIONALLY installs `page.route('**\/v1/**')` to
 *     serve every mapped method over HTTP, keyed off the SAME `options`
 *     object addMockSetup takes (so a spec passes one options bag to both).
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

// Pre-computed column detections from Python (same fixture helpers.mjs's
// detect_columns bridge mock reads — keyed by comma-joined header string).
const _COLUMN_DETECTIONS = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'generated', 'column-detections.json'), 'utf8'),
);

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

/**
 * Mirrors inventory_ops.get_part_key's priority (LCSC C-prefixed > mpn >
 * digikey > pololu > mouser) — the real backend's STRICT single-key match,
 * used by update_part_fields (Task 7). Deliberately NOT used by
 * get_sourced_distributors, which mirrors the real backend's looser "any PN
 * column matches" scan (see that route's own inline logic below).
 */
function mockPartKey(item) {
  const lcsc = (item.lcsc || '').trim();
  if (lcsc && /^c/i.test(lcsc)) return lcsc;
  const mpn = (item.mpn || '').trim();
  if (mpn) return mpn;
  const dk = (item.digikey || '').trim();
  if (dk) return dk;
  const pol = (item.pololu || '').trim();
  if (pol) return pol;
  return (item.mouser || '').trim();
}

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
 * to the identical route). `handler(argMap, ctx)` returns the UNWRAPPED value
 * — for `mutation: true` routes, that's the payload that belongs under
 * `entry.unwrap` (always "detail" post-Task-10) in the real server's
 * `{"ok": true, "detail": ...}` envelope (see server/mutations.py's
 * `finish_mutation`); this file's dispatcher wraps it into that real envelope
 * shape rather than the bare `{[unwrap]: value}` used for ordinary
 * (non-finish_mutation) GET/lookup routes. Pass `{ mutation: true }` for any
 * route backed by a `finish_mutation(...)` call server-side
 * (server/routes/*.py) — none of these carry inventory data anymore (see the
 * dispatcher below).
 */
function route(method, handler, { mutation = false } = {}) {
  const entry = API_MAP[method];
  if (!entry) {
    throw new Error(`route-mocks.mjs: "${method}" is not in js/api-map.js — regenerate or fix the name`);
  }
  const { regex, names } = compilePath(entry.path);
  return {
    method, verb: entry.verb, regex, pathParamNames: names,
    unwrap: entry.unwrap, mutation, handler,
  };
}

// ── Per-method mock logic (mirrors addMockSetup's bridge mocks; Node-side, so
//    no addInitScript serialization boundary — options/inventory are plain
//    closures here). ──

const ROUTES = [
  route('load_preferences', (_a, ctx) => ctx.options.preferences || { thresholds: {} }),
  route('save_preferences', () => true),
  route('rebuild_inventory', (_a, ctx) => ctx.inventory),
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
  }, { mutation: true }),
  route('delete_vendor', (a) => ({ vendor_id: a.vendor_id }), { mutation: true }),
  route('merge_vendors', (a) => ({ src_id: a.src_id, dst_id: a.dst_id }), { mutation: true }),
  // Real detail shape per server/routes/inventory_mut.py's record_fetched_prices:
  // detail is {part_key, distributor}, mirroring the route's own `detail`
  // dict — never the raw inventory array. Callers (js/inventory/inv-modals.js,
  // js/part-preview.js) ignore the resolved value (`.catch(() => {})`), but
  // the mock must still shape it like the real envelope.
  route('record_fetched_prices', (a) => ({ part_key: a.part_key, distributor: a.distributor }), { mutation: true }),
  route('get_price_summary', (a, ctx) => (ctx.options.priceSummaries || {})[a.part_key] || {}),
  route('get_part_history', (a, ctx) => (ctx.options.partHistory || {})[a.part_key] || []),
  // Covers fetch_lcsc_product/fetch_digikey_product/fetch_pololu_product/fetch_mouser_product —
  // all alias this one route, `{name}` is the distributor.
  route('fetch_distributor_product', (a, ctx) =>
    (ctx.options.productMocks || {})[`${a.name}:${a.code}`] || null),
  route('get_po_source_preview', (a, ctx) => (ctx.options.poSourcePreview || {})[a.po_id] || null),
  route('list_purchase_orders', (_a, ctx) => ctx.options.purchaseOrders || []),
  // Cart header (Task B2): initCartHeader() calls loadCarts() at startup, so
  // every spec using installRouteMocks now issues this GET on load — default
  // to an empty carts list / no active cart unless a spec supplies its own
  // via options.carts. STATEFUL (Task B3): reads ctx.cartsState, a deep clone
  // seeded from options.carts at installRouteMocks() time, so a subsequent
  // list_carts (triggered by cart-store.js's loadCarts() after any cart
  // mutation) reflects e.g. an add_cart_item call earlier in the same test —
  // mirrors the pattern list_saved_searches/list_generic_parts already use
  // for their own stateful mocks below.
  route('list_carts', (_a, ctx) => ctx.cartsState),
  // add_cart_item (Task B3): mutates the matching cart in ctx.cartsState so
  // the next list_carts (loadCarts()) sees the new item — real shape per
  // server/routes/carts.py's add_cart_item: `detail` is the created item dict
  // (domain/api_cart.py's carts.add_item return), not the whole cart.
  route('add_cart_item', (a, ctx) => {
    const cart = ctx.cartsState.carts.find((c) => c.id === a.cart_id);
    if (!cart) throw new Error(`route-mocks.mjs: add_cart_item — no cart "${a.cart_id}" in mock cartsState (seed one via options.carts)`);
    if (!cart.items) cart.items = [];
    const item = {
      ref: `item-${cart.items.length + 1}`,
      part_id: a.part_id ?? null,
      raw: a.raw ?? null,
      qty: a.qty ?? 1,
      target_distributor: a.target_distributor ?? null,
      shortfall: a.shortfall ?? null,
    };
    cart.items.push(item);
    return item;
  }, { mutation: true }),
  // add_bom_missing_to_cart (Task B5): mirrors add_cart_item's stateful push
  // (one item per `missing` entry) so the next list_carts (loadCarts())
  // reflects every part the BOM panel's "Add missing to cart" button queued
  // — real shape per server/routes/carts.py: `detail` is the whole cart dict
  // (domain/api_cart.py's add_bom_missing_to_cart returns get_cart's shape),
  // not a single item like add_cart_item.
  route('add_bom_missing_to_cart', (a, ctx) => {
    const cart = ctx.cartsState.carts.find((c) => c.id === a.cart_id);
    if (!cart) throw new Error(`route-mocks.mjs: add_bom_missing_to_cart — no cart "${a.cart_id}" in mock cartsState (seed one via options.carts)`);
    if (!cart.items) cart.items = [];
    const missing = Array.isArray(a.missing) ? a.missing : JSON.parse(a.missing);
    missing.forEach((entry) => {
      cart.items.push({
        ref: `item-${cart.items.length + 1}`,
        part_id: entry.part_id ?? null,
        raw: entry.raw ?? null,
        qty: entry.qty ?? 1,
        target_distributor: entry.target_distributor ?? null,
        shortfall: entry.shortfall ?? null,
      });
    });
    return cart;
  }, { mutation: true }),
  route('get_po_with_items', (a, ctx) =>
    (ctx.options.poWithItems || {})[a.po_id] || { po_id: a.po_id, line_items: [] }),

  // ── import-panel.js (Task 5) ──────────────────────────────────────────────
  route('import_purchases', (a) => ({ count: (a.rows || []).length }), { mutation: true }),
  route('remove_last_purchases', (a) => ({ count: a.count }), { mutation: true }),
  route('detect_columns', (a) => {
    const headers = Array.isArray(a.headers) ? a.headers : JSON.parse(a.headers);
    return _COLUMN_DETECTIONS[headers.join(',')] || {};
  }),

  // ── mfg-direct-panel.js (Task 5) ──────────────────────────────────────────
  route('parse_source_file_b64', (_a, ctx) => ctx.options.mdtInvoiceParseResult || []),
  route('ocr_overlay_b64', async (a, ctx) => {
    if (ctx.options.ocrOverlayDelayMs) {
      await new Promise((r) => setTimeout(r, ctx.options.ocrOverlayDelayMs));
    }
    if (ctx.options.ocrEngineCheckThrows) throw new Error('bridge not ready (simulated)');
    if (!ctx.options.ocrOverlayResult) return null;
    return { ...ctx.options.ocrOverlayResult, template: a.template || ctx.options.ocrOverlayResult.template };
  }),
  route('match_part', () => ({ status: 'new' })),
  // Real envelope per server/routes/import_scan.py: {"available": <bool>}.
  // The client (js/api.js) unwraps "available" (see api-map.js), so call
  // sites still see the bare bool — this handler must return the bool, and
  // the dispatcher below wraps it under `match.unwrap` for non-mutation routes.
  route('ocr_engine_available', (_a, ctx) => {
    if (ctx.options.ocrEngineCheckThrows) throw new Error('bridge not ready (simulated)');
    return ctx.options.ocrEngineAvailable === undefined ? true : ctx.options.ocrEngineAvailable;
  }),
  route('start_scan_session', (a, ctx) => {
    const session = ctx.options.scanSession || {
      session_id: 'sess-test-1',
      template: a.template || 'generic',
      port: 7890,
      urls: ['http://192.168.1.50:7890/scan?s=sess-test-1'],
    };
    return { ...session, template: a.template || session.template };
  }),
  route('create_purchase_order_with_items', (_a, ctx) => ctx.inventory, { mutation: true }),
  route('update_purchase_order', (_a, ctx) => ctx.inventory, { mutation: true }),
  // delete_last_purchase_order's literal `/v1/purchase-orders/last` MUST be
  // registered before delete_purchase_order's `/v1/purchase-orders/{po_id}` —
  // ROUTES.find takes the first regex match, and {po_id}'s wildcard capture
  // matches the literal segment "last" too, so registering it first would
  // silently swallow every delete_last_purchase_order call under the wrong
  // route (still functionally correct — same mutation shape — but recorded
  // in __apiCalls under the wrong method name).
  route('delete_last_purchase_order', (_a, ctx) => ctx.options.deleteLastResult || ctx.inventory, { mutation: true }),
  route('delete_purchase_order', (_a, ctx) => ctx.inventory, { mutation: true }),

  // ── group-flyout (Task 5) ─────────────────────────────────────────────────
  // Stateful across a single installRouteMocks session: create_saved_search
  // pushes into this list so a subsequent list_saved_searches (including the
  // one the get_saved_search-dead-call fix now issues) sees it.
  route('list_saved_searches', (a, ctx) => ctx.savedSearches.filter((s) => s.generic_part_id === a.generic_part_id)),
  route('create_saved_search', (a, ctx) => {
    const tagState = Array.isArray(a.tag_state) ? a.tag_state : JSON.parse(a.tag_state || '[]');
    const record = {
      id: 'saved-' + (ctx.savedSearches.length + 1),
      generic_part_id: a.generic_part_id,
      name: a.name,
      tag_state: tagState,
      search_text: a.search_text || '',
      frozen_members: a.frozen_members || [],
    };
    ctx.savedSearches.push(record);
    return record;
  }, { mutation: true }),
  route('add_generic_member', (a, ctx) => (ctx.options.addMemberResult !== undefined
    ? ctx.options.addMemberResult : []), { mutation: true }),
  route('remove_generic_member', () => [], { mutation: true }),
  route('exclude_generic_member', (a) => ({ generic_part_id: a.generic_part_id, part_id: a.part_id }), { mutation: true }),
  // Task 8 census (saved-views.spec.mjs): mirrors server/routes/generic_parts.py's
  // delete_saved_search exactly — {"ok": true, "detail": {"search_id": ...}} — and
  // mutates the SAME stateful ctx.savedSearches array create_saved_search/
  // list_saved_searches above share, so a delete is reflected in the next
  // list_saved_searches call within the same test.
  route('delete_saved_search', (a, ctx) => {
    ctx.savedSearches = ctx.savedSearches.filter((s) => s.id !== a.search_id);
    return { search_id: a.search_id };
  }, { mutation: true }),

  // ── Incidental to group-flyout.spec.mjs's BOM auto-create-group flow (owned
  // by js/inventory/inv-mutations.js, ported properly in Task 7) — a flyout
  // opened via an unmatched value-only BOM row goes through
  // autoCreateGroupAndOpenFlyout(), which calls these before ever touching
  // flyout code. Stateful list_generic_parts/create_generic_part mirror
  // group-flyout.spec.mjs's old addGenericPartsMockPatch bridge patch so the
  // spec's assertions (flyout opens, shows the right group) still hold once
  // the whole page session is on HTTP transport.
  // Mirrors group-flyout.spec.mjs's old addGenericPartsMockPatch bridge
  // override exactly (a hardcoded hardcoded capacitor-shaped value, not
  // derived from the actual value string) — a pre-existing test shortcut,
  // not something introduced here. A real spec.value is required for
  // generateTags() (flyout-logic.js) to produce any tags at all.
  route('extract_spec_from_value', (a) => ({
    type: a.part_type, value: 1e-7, value_display: a.value_str, package: a.package_str,
  })),
  route('resolve_bom_spec', () => null),
  route('list_generic_parts', (_a, ctx) => ctx.genericParts),
  route('create_generic_part', (a, ctx) => {
    // Task 7 fix: inv-mutations.js now passes spec/strictness as raw dicts
    // (matching the real /v1 route's CreateGenericPartBody, which types both
    // as `dict`) — no more JSON.stringify double-encoding landmine. This
    // handler mirrors the real request body shape 1:1.
    const gp = {
      generic_part_id: 'new_' + a.part_type + '_' + Date.now(),
      name: a.name,
      part_type: a.part_type,
      spec: a.spec || {},
      strictness: a.strictness || {},
      source: 'auto',
      members: [],
    };
    ctx.genericParts.push(gp);
    return gp;
  }, { mutation: true }),

  // ── bom-panel.js / bom-events.js (Task 6) ─────────────────────────────────
  // Real detail shape per server/routes/inventory_mut.py's consume_bom/
  // remove_last_adjustments — a small dict, never the inventory array (Task
  // 10 removed the `?include=inventory` echo entirely). ctx.inventory itself
  // is left unmutated (no per-match decrement) — Task 10's sweep means call
  // sites no longer consume the mutation's own return value for rendering
  // anyway (they trigger `scheduleInventoryRefresh()` and re-read
  // `store.inventory`, which re-fetches this same unchanged fixture via
  // `rebuild_inventory`); computed decrement math is covered by the Python
  // domain tests (tests/python/test_inventory_api_adjustments.py) and the
  // live E2E suite (tests/js/e2e/live/bom-consume.spec.mjs).
  route('consume_bom', (a) => ({ bom_name: a.bom_name, board_qty: a.board_qty }), { mutation: true }),
  route('remove_last_adjustments', (a) => ({ count: a.count }), { mutation: true }),

  // ── inv-modals.js / inv-inline-edit.js (Task 7) ──────────────────────
  // Real detail shapes per server/routes/inventory_mut.py — small dicts, not
  // the inventory array (see the consume_bom comment above for why an
  // unchanged ctx.inventory is fine for adjust_part/update_part_price, which
  // don't mutate it here either).
  route('adjust_part', (a) => ({ part_key: a.part_key, adj_type: a.adj_type, quantity: a.quantity }), { mutation: true }),
  route('update_part_price', (a) => ({ part_key: a.part_key, unit_price: a.unit_price, ext_price: a.ext_price }), { mutation: true }),
  // update_part_fields DOES mutate the matching row in place (mirrors
  // addMockSetup's bridge mock) because inv-modals.js's applyFix()
  // re-fetches get_sourced_distributors right after and asserts the write
  // landed — an unchanged echo would make every distributor-PN-correction
  // spec see its own edit silently discarded. Its RETURN value is still the
  // small real `{part_key, fields}` detail dict, though — the mutation is a
  // side effect on `ctx.inventory` for later reads (get_sourced_distributors,
  // the post-mutation rebuild_inventory refetch), not part of the response.
  route('update_part_fields', (a, ctx) => {
    const part = ctx.inventory.find((p) => mockPartKey(p) === a.part_key);
    if (part) Object.assign(part, a.fields);
    return { part_key: a.part_key, fields: a.fields };
  }, { mutation: true }),
  route('delete_part', (a, ctx) => {
    ctx.inventory = ctx.inventory.filter((p) =>
      ![p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser].includes(a.part_key));
    return { part_key: a.part_key };
  }, { mutation: true }),
  route('get_last_po_quantity', (a, ctx) => {
    const m = ctx.options.lastPoQty || {};
    return (a.part_key in m) ? m[a.part_key] : null;
  }),
  route('has_purchase_history', (a, ctx) => {
    const m = ctx.options.hasPurchaseHistory || {};
    if (a.part_key in m) return m[a.part_key] === null ? undefined : m[a.part_key];
    const lp = ctx.options.lastPoQty || {};
    return typeof lp[a.part_key] === 'number';
  }),
  route('get_sourced_distributors', (a, ctx) => {
    const over = ctx.options.sourcedDistributors || {};
    if (a.part_key in over) return over[a.part_key];
    // Default: has-PN set derived from the matching inventory row — the
    // real backend's looser "any PN column matches" scan (deliberately NOT
    // mockPartKey's strict single-key match; see that helper's comment).
    const part = ctx.inventory.find((p) =>
      [p.lcsc, p.mpn, p.digikey, p.pololu, p.mouser].includes(a.part_key));
    if (!part) return [];
    const out = [];
    for (const d of ['lcsc', 'digikey', 'mouser', 'pololu']) {
      if ((part[d] || '').trim()) out.push({ distributor: d, part_number: part[d] });
    }
    return out;
  }),
  route('get_generic_group_names', (a, ctx) => (ctx.options.genericGroupNames || {})[a.part_key] || []),
  // detail is shaped {summary, inventory} per server/routes/inventory_mut.py:
  // `api.fetch_missing_descriptions()`'s own facade return already carries
  // both (it rebuilds after writing descriptions), and the route passes that
  // whole thing through as `detail` unchanged — never the bare summary dict,
  // and never a top-level `inventory` key (that affordance was removed
  // entirely in Task 10; see fetch-descriptions-command.js).
  route('fetch_missing_descriptions', (_a, ctx) => ({
    summary: ctx.options.fetchDescriptionsSummary || { updated: 0, failed: 0, skipped: 0 },
    inventory: ctx.inventory,
  }), { mutation: true }),
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

  const ctx = {
    // Deep-cloned (Task 7): update_part_fields/delete_part mutate matching
    // rows/filter the array in place (mirroring addMockSetup's bridge mocks,
    // which mutate a copy addInitScript already serialized independently
    // into the browser context per test). Without this clone, ctx.inventory
    // would alias the SAME array object a spec file's module-level
    // `INVENTORY` constant points to across every test in the file — one
    // test's write would silently corrupt every later test's fixture data.
    inventory: JSON.parse(JSON.stringify(inventory)),
    options,
    // Mutable per-session state for stateful mocks (grows via create_* calls).
    savedSearches: (options.savedSearches || []).slice(),
    genericParts: (options.genericParts || []).slice(),
    // Deep-cloned (Task B3): add_cart_item mutates the matching cart's items
    // in place, mirroring the `inventory` clone above's rationale — without
    // this clone, a spec's own `options.carts` object literal (or a shared
    // fixture) would be corrupted across tests/reused test-file scope.
    cartsState: JSON.parse(JSON.stringify(options.carts || { carts: [], active_cart_id: null })),
    // Canary counter (Finding 2): incremented once per intercepted /v1
    // request in the catch-all handler below, so specs can assert the HTTP
    // transport was actually exercised rather than silently falling back to
    // the bridge (which would make a bug in the HTTP path invisible to
    // otherwise-passing, transport-agnostic specs).
    httpHits: 0,
  };

  // Register the general '**/v1/**' catch-all BEFORE the specific
  // '/v1/health' and '/v1/events' routes below. Playwright matches routes in
  // REVERSE registration order (last-registered wins first) — registering the
  // narrower routes last is what lets them win over this catch-all for their
  // exact paths; registering them first (as this file originally did) let the
  // catch-all's 404-for-unknown-method fallback silently swallow /v1/health
  // itself — back when api.js still probed it before choosing a transport,
  // that made the probe always fail (pre-Task-10 history; api() is HTTP-only
  // now, so a swallowed /v1/health would instead just break app-init.js's
  // SSE-connect check, still worth avoiding).
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

    // Canary (Finding 2): count every mapped /v1 request actually
    // intercepted here, so specs can prove the HTTP transport (not the
    // bridge fallback) served the call — see assertHttpExercised below.
    ctx.httpHits += 1;

    const argMap = {};
    match.pathParamNames.forEach((name, i) => {
      argMap[name] = decodeURIComponent(match.regex.exec(pathname)[i + 1]);
    });

    const entry = API_MAP[match.method];

    // Query params (Task 6): api.js's buildUrl() puts non-path, non-body args
    // (e.g. remove_last_adjustments' `count`) on the querystring — decode them
    // into argMap too, coercing to a number when the raw text is numeric so
    // handlers/recorded __apiCalls args match the number the JS call site
    // originally passed (URLSearchParams only deals in strings).
    entry.queryParams.forEach((name) => {
      const raw = url.searchParams.get(name);
      if (raw === null) return;
      const num = Number(raw);
      argMap[name] = (raw !== '' && !Number.isNaN(num)) ? num : raw;
    });
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

    // A handler may throw to simulate a backend failure (e.g. ocrEngineCheckThrows) —
    // fulfill a non-ok response so api.js's real failure contract (catch →
    // AppLog.error + toast + undefined) engages, exactly like a thrown bridge
    // call does.
    let value;
    try {
      value = await match.handler(argMap, ctx);
    } catch (e) {
      await route.fulfill({ status: 500, json: { error: e.message } });
      return;
    }

    // Preserve the __apiCalls recorder contract with the SAME positional-args
    // shape a pywebview mock would have recorded.
    const args = entry.argOrder.map((name) => argMap[name]);
    try {
      await page.evaluate(({ method, args: recordedArgs }) => {
        window.__apiCalls = window.__apiCalls || {};
        (window.__apiCalls[method] = window.__apiCalls[method] || []).push(recordedArgs);
      }, recordCallScript(match.method, args));
    } catch {
      // A request in flight from a page the test has since navigated away
      // from (e.g. a mid-test reload) destroys the execution context before
      // this resolves — nothing left to record into, so it's safe to ignore.
    }

    // fetch_distributor_product's real route (server/routes/distributors.py)
    // returns 404 + {error, code: "product_not_found", detail: null} when the
    // underlying fetch returns None — never 200 + null. Mirror that shape
    // here so specs exercising the "no mock" path see the real contract
    // (fetchRow etc. must treat 404 as unavailable, not a valid null product).
    if (match.method === 'fetch_distributor_product' && value === null) {
      await route.fulfill({
        status: 404,
        json: {
          error: `Product not found: ${argMap.name}/${argMap.code}`,
          code: 'product_not_found',
          detail: null,
        },
      });
      return;
    }

    // `mutation: true` routes are backed by a real `finish_mutation(...)`
    // call server-side — mirror ITS envelope shape exactly:
    // `{"ok": true, "detail": ...}`, always, never an `inventory` key (Task
    // 10 removed the `?include=inventory` affordance entirely — mutation
    // responses never carry inventory data; the frontend's sole re-render
    // path is the SSE-driven `scheduleInventoryRefresh()`, see js/store.js).
    // Non-mutation routes (plain GETs / bespoke-shape lookups like
    // fetch_favicon's `{"path"}`) keep returning the bare `{[unwrap]: value}`
    // (or raw value) they always have.
    let body;
    if (match.mutation) {
      body = { ok: true, detail: value };
    } else {
      body = match.unwrap ? { [match.unwrap]: value } : value;
    }
    await route.fulfill({ json: body === undefined ? null : body });
  });

  // Registered AFTER the catch-all above so they win (Playwright tries
  // last-registered-first): the real narrow contract for the probe + SSE.
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

  // Canary accessor (Finding 2) — see assertHttpExercised.
  return {
    getHttpHits: () => ctx.httpHits,
  };
}

/**
 * Task 8 census (inv-col-header.spec.mjs): stateful round-trip for
 * GET/PUT /v1/preferences over sessionStorage, mirroring helpers.mjs's
 * pre-HTTP `addPersistentPrefsMock` bridge patch (which trapped
 * `window.pywebview.api.load_preferences`/`save_preferences`) — that bridge
 * trap is now dead code since the client-shell bridge no longer carries
 * `load_preferences`/`save_preferences` at all (they moved to /v1). Must be
 * called AFTER installRouteMocks so this page.route registration wins
 * (Playwright matches last-registered-first) over installRouteMocks' generic
 * '**\/v1/**' catch-all, which only serves ctx.options.preferences statically
 * (no write-back).
 *
 * @param {import('@playwright/test').Page} page
 */
export async function addPersistentPrefsRouteMock(page) {
  // Matches the literal sessionStorage key inv-col-header.spec.mjs's own
  // "Group state persists across reload" test polls via waitForFunction —
  // that inline check is spec-owned (pre-existing, part of the test's own
  // assertion of the mock's flush timing) and deliberately left untouched;
  // this mock must write under the SAME key or the test hangs waiting for a
  // key that's never written.
  const STORAGE_KEY = '__test_prefs_inv_view';
  await page.route('**/v1/preferences', async (route) => {
    const request = route.request();
    if (request.method() === 'GET') {
      const stored = await page.evaluate((k) => window.sessionStorage.getItem(k), STORAGE_KEY);
      const prefs = stored ? JSON.parse(stored) : { thresholds: {} };
      await route.fulfill({ json: prefs });
      return;
    }
    if (request.method() === 'PUT') {
      // save_preferences is a rawBody route (js/api-map.js) — the request body
      // IS the JSON-stringified prefs object, no wrapper to unwrap.
      const body = request.postData() || '{}';
      await page.evaluate(({ k, v }) => window.sessionStorage.setItem(k, v), { k: STORAGE_KEY, v: body });
      await route.fulfill({ json: true });
      return;
    }
    await route.continue();
  });
}

/**
 * Canary (Finding 2): throws if zero /v1 requests were intercepted by the
 * route mocks installed above. A wave-1/wave-2 spec that passes ONLY because
 * every call silently fell back to `window.pywebview.api` (the bridge) would
 * still pass its own assertions — this catches that failure mode. Call at
 * the END of at least one test per ported spec file, after the page has
 * done its real work, so the count reflects genuine traffic.
 *
 * @param {{getHttpHits: () => number}} state Return value of installRouteMocks.
 */
export async function assertHttpExercised(state) {
  const hits = state.getHttpHits();
  if (hits === 0) {
    throw new Error(
      'assertHttpExercised: 0 /v1 requests were intercepted — the spec likely fell back to '
      + 'the window.pywebview bridge instead of exercising the HTTP transport.',
    );
  }
}
