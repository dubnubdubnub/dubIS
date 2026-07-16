// @vitest-environment jsdom
//
// Table-driven coverage of js/api.js's HTTP transport: one representative
// operation per class from js/api-map.js (GET path-param, POST body, DELETE
// query, scalar unwrap, distributor alias, mutating unwrap:"detail"),
// plus the failure contract and the bridge path for methods not in
// API_MAP. Task 10 flip: HTTP is unconditional for mapped methods — no
// probe, no bridge fallback, no `?include=inventory`.
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

describe('api() HTTP transport', () => {
  beforeEach(() => {
    AppLog.clear();
    vi.mocked(showToast).mockClear();
    delete window.pywebview;
  });

  it('GET with a path param builds the URL and unwraps nothing', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ unit_price: 1.5, ext_price: 3 }));
    const result = await api('get_price_summary', 'PART-1');
    const [url] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/parts/PART-1/prices');
    expect(result).toEqual({ unit_price: 1.5, ext_price: 3 });
  });

  it('POST sends a JSON body assembled from bodyParams in order', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ ok: true, detail: { id: 'gp-1' } }));
    await api('create_generic_part', 'Resistor 10k', 'resistor', { ohms: 10000 }, 'loose');
    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/generic-parts');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      name: 'Resistor 10k',
      part_type: 'resistor',
      spec: { ohms: 10000 },
      strictness: 'loose',
    });
  });

  it('DELETE with a query param encodes it in the URL (no body, no ?include)', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ ok: true, detail: { count: 3 } }));
    const result = await api('remove_last_purchases', 3);
    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/purchases/last?count=3');
    expect(init.method).toBe('DELETE');
    expect(init.body).toBeUndefined();
    expect(result).toEqual({ count: 3 });
  });

  it('unwraps a scalar envelope (has_purchase_history)', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ has_purchase_history: true }));
    const result = await api('has_purchase_history', 'PART-1');
    expect(result).toBe(true);
  });

  it('unwraps a scalar envelope (ocr_engine_available -> "available")', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ available: false }));
    const result = await api('ocr_engine_available');
    expect(result).toBe(false);
  });

  it('distributor alias fixes the `name` path segment and derives from `code`', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ mpn: 'C12345' }));
    await api('fetch_lcsc_product', 'C12345');
    const [url] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/distributors/lcsc/product/C12345');
  });

  it('mutating op unwraps "detail" to the facade payload — no ?include=inventory', async () => {
    // adjust_part (server/routes/inventory_mut.py): Task 10 removed the
    // `?include=inventory` echo entirely — mutation responses are always
    // `{"ok": true, "detail": <facade detail>}`. Frontend refresh is
    // SSE-driven (js/store.js's scheduleInventoryRefresh), not carried in
    // the mutation response.
    global.fetch = vi.fn(async () => jsonResponse({ ok: true, detail: { part_key: 'PART-1', adj_type: 'add', quantity: 5 } }));
    const result = await api('adjust_part', 'add', 'PART-1', 5, 'note', 'test');
    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/parts/PART-1/adjust');
    expect(JSON.parse(init.body)).toEqual({
      adj_type: 'add', note: 'note', quantity: 5, source: 'test',
    });
    expect(result).toEqual({ part_key: 'PART-1', adj_type: 'add', quantity: 5 });
  });

  it('CFG mutation (update_vendor) also unwraps "detail" to the facade payload', async () => {
    // js/vendors-modal.js reads vendor fields (`v.name`) straight off the
    // api() return, so the client must unwrap "detail", not the envelope.
    global.fetch = vi.fn(async () => jsonResponse({ ok: true, detail: { id: 'v1', name: 'Acme', url: 'acme.com' } }));
    const result = await api('update_vendor', 'v1', 'Acme', 'acme.com');
    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/v1/vendors');
    expect(init.method).toBe('PUT');
    expect(result).toEqual({ id: 'v1', name: 'Acme', url: 'acme.com' });
  });

  it('non-ok response parses {error} and hits the failure contract: undefined + AppLog + toast', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ error: 'part not found' }, false, 404));
    const result = await api('get_price_summary', 'MISSING');
    expect(result).toBeUndefined();
    expect(AppLog._entries.some(
      e => e.level === 'error' && e.msg.includes('part not found'),
    )).toBe(true);
    expect(showToast).toHaveBeenCalledWith('Error: part not found');
  });
});

describe('api() bridge path for methods not in API_MAP', () => {
  beforeEach(() => {
    AppLog.clear();
    vi.mocked(showToast).mockClear();
  });

  it('a method not in API_MAP always goes straight to the bridge (no HTTP attempt)', async () => {
    global.fetch = vi.fn(async () => { throw new Error('should not be called'); });
    const bridged = vi.fn().mockResolvedValue('ok');
    window.pywebview = { api: { open_file_dialog: bridged } };

    const result = await api('open_file_dialog', 'Select', null);

    expect(bridged).toHaveBeenCalledWith('Select', null);
    expect(result).toBe('ok');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('a mapped method never falls back to the bridge, even if fetch fails', async () => {
    global.fetch = vi.fn(async () => { throw new Error('network error'); });
    const bridged = vi.fn().mockResolvedValue({ unit_price: 1 });
    window.pywebview = { api: { get_price_summary: bridged } };

    const result = await api('get_price_summary', 'PART-1');

    expect(bridged).not.toHaveBeenCalled();
    expect(result).toBeUndefined();
    expect(showToast).toHaveBeenCalledWith('Error: network error');
  });
});
