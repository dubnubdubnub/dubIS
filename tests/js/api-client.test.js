// @vitest-environment jsdom
//
// Table-driven coverage of js/api.js's HTTP transport: one representative
// operation per class from js/api-map.js (GET path-param, POST body, DELETE
// query, scalar unwrap, distributor alias, mutation include=inventory),
// plus the failure contract and the HTTP-probe → bridge fallback path.
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import { AppLog, api } from '../../js/api.js';
import { showToast } from '../../js/ui-helpers.js';

function jsonResponse(body, ok = true, status = 200) {
  return {
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: async () => body,
  };
}

describe('api() HTTP transport (probe succeeds)', () => {
  beforeEach(() => {
    AppLog.clear();
    vi.mocked(showToast).mockClear();
    delete window.pywebview;
    // The probe result is memoized at module scope; force it healthy for
    // every call in this block by having every fetch (including the /v1/health
    // probe itself) resolve ok.
    global.fetch = vi.fn(async () => jsonResponse({ ok: true }));
  });

  it('GET with a path param builds the URL and unwraps nothing', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ unit_price: 1.5, ext_price: 3 });
    });
    const result = await api('get_price_summary', 'PART-1');
    const calls = global.fetch.mock.calls.map(([url]) => url);
    expect(calls).toContain('/v1/parts/PART-1/prices');
    expect(result).toEqual({ unit_price: 1.5, ext_price: 3 });
  });

  it('POST sends a JSON body assembled from bodyParams in order', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ id: 'gp-1' });
    });
    await api('create_generic_part', 'Resistor 10k', 'resistor', { ohms: 10000 }, 'loose');
    const [, init] = global.fetch.mock.calls.find(([url]) => url === '/v1/generic-parts');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      name: 'Resistor 10k',
      part_type: 'resistor',
      spec: { ohms: 10000 },
      strictness: 'loose',
    });
  });

  it('DELETE with a query param encodes it in the URL (no body)', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ inventory: [{ part_key: 'X' }] });
    });
    const result = await api('remove_last_purchases', 3);
    const [url, init] = global.fetch.mock.calls.find(([u]) => u !== '/v1/health');
    expect(url).toBe('/v1/purchases/last?count=3&include=inventory');
    expect(init.method).toBe('DELETE');
    expect(init.body).toBeUndefined();
    expect(result).toEqual([{ part_key: 'X' }]);
  });

  it('unwraps a scalar envelope (has_purchase_history)', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ has_purchase_history: true });
    });
    const result = await api('has_purchase_history', 'PART-1');
    expect(result).toBe(true);
  });

  it('unwraps a scalar envelope (ocr_engine_available -> "available")', async () => {
    // Real server envelope (server/routes/import_scan.py) is {"available": bool} —
    // the client must unwrap it back to a bare bool for call sites.
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ available: false });
    });
    const result = await api('ocr_engine_available');
    expect(result).toBe(false);
  });

  it('distributor alias fixes the `name` path segment and derives from `code`', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ mpn: 'C12345' });
    });
    await api('fetch_lcsc_product', 'C12345');
    const calls = global.fetch.mock.calls.map(([url]) => url);
    expect(calls).toContain('/v1/distributors/lcsc/product/C12345');
  });

  it('CFG mutation (no ?include support) unwraps "detail" to the facade payload', async () => {
    // update_vendor's route (server/routes/vendors_pos.py) has no `include`
    // query param — it's not "mutating" in the ?include=inventory sense —
    // but its response is still the real finish_mutation envelope
    // `{"ok": true, "detail": <vendor>}`. js/vendors-modal.js reads vendor
    // fields (`v.name`) straight off the api() return, so the client must
    // unwrap "detail", not receive the envelope itself.
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ ok: true, detail: { id: 'v1', name: 'Acme', url: 'acme.com' } });
    });
    const result = await api('update_vendor', 'v1', 'Acme', 'acme.com');
    const [url, init] = global.fetch.mock.calls.find(([u]) => u !== '/v1/health');
    expect(url).toBe('/v1/vendors');
    expect(init.method).toBe('PUT');
    expect(result).toEqual({ id: 'v1', name: 'Acme', url: 'acme.com' });
  });

  it('mutating ops auto-append ?include=inventory and unwrap it', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ ok: true, detail: {}, inventory: [{ part_key: 'A' }] });
    });
    const result = await api('adjust_part', 'add', 'PART-1', 5, 'note', 'test');
    const [url, init] = global.fetch.mock.calls.find(([u]) => u !== '/v1/health');
    expect(url).toBe('/v1/parts/PART-1/adjust?include=inventory');
    expect(JSON.parse(init.body)).toEqual({
      adj_type: 'add', note: 'note', quantity: 5, source: 'test',
    });
    expect(result).toEqual([{ part_key: 'A' }]);
  });

  it('non-ok response parses {error} and hits the failure contract: undefined + AppLog + toast', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/v1/health') return jsonResponse({ ok: true });
      return jsonResponse({ error: 'part not found' }, false, 404);
    });
    const result = await api('get_price_summary', 'MISSING');
    expect(result).toBeUndefined();
    expect(AppLog._entries.some(
      e => e.level === 'error' && e.msg.includes('part not found'),
    )).toBe(true);
    expect(showToast).toHaveBeenCalledWith('Error: part not found');
  });
});

describe('api() falls back to the pywebview bridge when the HTTP probe fails', () => {
  beforeEach(() => {
    AppLog.clear();
    vi.mocked(showToast).mockClear();
  });

  it('mapped method still goes to the bridge if /v1/health is unreachable', async () => {
    vi.resetModules();
    global.fetch = vi.fn(async () => { throw new Error('network error'); });
    const bridged = vi.fn().mockResolvedValue({ unit_price: 1 });
    window.pywebview = { api: { get_price_summary: bridged } };

    const { api: freshApi } = await import('../../js/api.js');
    const result = await freshApi('get_price_summary', 'PART-1');

    expect(bridged).toHaveBeenCalledWith('PART-1');
    expect(result).toEqual({ unit_price: 1 });
  });

  it('window.__DUBIS_HTTP__ === false forces the bridge without probing', async () => {
    vi.resetModules();
    global.fetch = vi.fn(async () => jsonResponse({ ok: true }));
    window.__DUBIS_HTTP__ = false;
    const bridged = vi.fn().mockResolvedValue([{ part_key: 'A' }]);
    window.pywebview = { api: { rebuild_inventory: bridged } };

    const { api: freshApi } = await import('../../js/api.js');
    const result = await freshApi('rebuild_inventory');

    expect(bridged).toHaveBeenCalled();
    expect(result).toEqual([{ part_key: 'A' }]);
    // fetch was never even attempted for the mapped call (only possibly for /v1/health,
    // which the __DUBIS_HTTP__ guard should also skip).
    expect(global.fetch).not.toHaveBeenCalled();
    delete window.__DUBIS_HTTP__;
  });

  it('a method not in API_MAP always goes straight to the bridge (no probe)', async () => {
    vi.resetModules();
    global.fetch = vi.fn(async () => { throw new Error('should not be called'); });
    const bridged = vi.fn().mockResolvedValue('ok');
    window.pywebview = { api: { open_file_dialog: bridged } };

    const { api: freshApi } = await import('../../js/api.js');
    const result = await freshApi('open_file_dialog', 'Select', null);

    expect(bridged).toHaveBeenCalledWith('Select', null);
    expect(result).toBe('ok');
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
