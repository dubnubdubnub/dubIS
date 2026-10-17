// @ts-check
/* bom-spec-lookup.test.mjs — js/bom/bom-spec-lookup.js's contract: which rows
 * are asked about, what is cached for how long, the concurrency bound, and
 * that failures are reported (once per run) rather than swallowed. */
import { describe, it, expect, vi } from 'vitest';
import {
  createSpecLookup, codesNeedingSpec, specFromProduct, isNotFoundError, RETRY_MS,
} from '../../js/bom/bom-spec-lookup.js';

const PRODUCT = {
  productCode: 'C11702', mpn: '0402WGF1001TCE', manufacturer: 'UNI-ROYAL',
  description: '1kΩ ±1% 62.5mW 0402 Thick Film Resistor', package: '0402',
  category: 'Resistors', subcategory: 'Chip Resistor - Surface Mount',
  attributes: [{ name: 'Resistance', value: '1kΩ' }],
  prices: [{ qty: 100, price: 0.001 }], stock: 123456, imageUrl: 'x',
};

const notFound = () => Object.assign(new Error('Product not found'), { status: 404, code: 'product_not_found' });

function logger() {
  return { info: vi.fn(), warn: vi.fn() };
}

describe('codesNeedingSpec', () => {
  const row = (matchType, lcsc, extra = {}) => ({ matchType, bom: { lcsc, dnp: false, ...extra } });

  it('asks about missing/value/fuzzy/generic rows, never identity matches', () => {
    const codes = codesNeedingSpec([
      row('none', 'C1'), row('value', 'C2'), row('fuzzy', 'C3'), row('generic', 'C4'),
      row('lcsc', 'C5'), row('mpn', 'C6'), row('manual', 'C7'), row('confirmed', 'C8'),
    ]);
    expect(codes).toEqual(['C1', 'C2', 'C3', 'C4']);
  });

  it('skips DNP rows, malformed codes, and duplicates', () => {
    expect(codesNeedingSpec([
      row('none', 'C1', { dnp: true }), row('none', ''), row('none', 'LCSC?'),
      row('none', 'c9'), row('value', 'C9'),
    ])).toEqual(['C9']);
  });

  it('tolerates no results', () => {
    expect(codesNeedingSpec(null)).toEqual([]);
  });
});

describe('specFromProduct', () => {
  it('keeps what matching reads and drops prices/stock', () => {
    const s = specFromProduct('C11702', PRODUCT);
    expect(s).toEqual({
      source: 'lcsc', code: 'C11702', mpn: '0402WGF1001TCE', manufacturer: 'UNI-ROYAL',
      description: PRODUCT.description, package: '0402', category: 'Resistors',
      subcategory: 'Chip Resistor - Surface Mount', attributes: [{ name: 'Resistance', value: '1kΩ' }],
    });
  });
});

describe('isNotFoundError', () => {
  it('reads the envelope code or a 404', () => {
    expect(isNotFoundError(notFound())).toBe(true);
    expect(isNotFoundError(Object.assign(new Error('x'), { status: 404 }))).toBe(true);
    expect(isNotFoundError(Object.assign(new Error('x'), { status: 502, code: 'distributor_timeout' }))).toBe(false);
    expect(isNotFoundError(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('createSpecLookup', () => {
  it('requires a fetcher', () => {
    // @ts-expect-error deliberately wrong
    expect(() => createSpecLookup({})).toThrow(/fetchProduct/);
  });

  it('applies found specs to the matching entries only, leaving the row\'s own columns alone', async () => {
    const lookup = createSpecLookup({ fetchProduct: async () => PRODUCT });
    await lookup.lookup(['C11702']);
    const agg = new Map([
      ['C11702', { lcsc: 'C11702', value: '0402WGF1001TCE', desc: '' }],
      ['C1', { lcsc: 'C1', value: 'x', desc: '' }],
    ]);
    lookup.apply(agg);
    expect(agg.get('C11702').spec.attributes[0].value).toBe('1kΩ');
    expect(agg.get('C11702').value).toBe('0402WGF1001TCE');
    expect(agg.get('C11702').desc).toBe('');
    expect('spec' in agg.get('C1')).toBe(false);
  });

  it('caches found and not-found answers for the session', async () => {
    const fetchProduct = vi.fn(async (code) => {
      if (code === 'C1') return PRODUCT;
      throw notFound();
    });
    const lookup = createSpecLookup({ fetchProduct });
    const first = await lookup.lookup(['C1', 'C2']);
    expect(first.found).toEqual(['C1']);
    expect(first.notFound).toEqual(['C2']);
    const again = await lookup.lookup(['C1', 'C2']);
    expect(again.asked).toEqual([]);
    expect(fetchProduct).toHaveBeenCalledTimes(2);
  });

  it('treats a null answer as not found', async () => {
    const lookup = createSpecLookup({ fetchProduct: async () => null });
    expect((await lookup.lookup(['C1'])).notFound).toEqual(['C1']);
  });

  it('a failure is a warning, retried only after RETRY_MS', async () => {
    let t = 0;
    const log = logger();
    const fetchProduct = vi.fn(async () => { throw new TypeError('Failed to fetch'); });
    const lookup = createSpecLookup({ fetchProduct, log, now: () => t });
    const r = await lookup.lookup(['C1', 'C2']);
    expect(r.failed.map((f) => f.code)).toEqual(['C1', 'C2']);
    // One summary warning for the run, naming the codes and the cause.
    expect(log.warn).toHaveBeenCalledTimes(1);
    expect(log.warn.mock.calls[0][0]).toMatch(/2 of 2.*C1, C2.*Failed to fetch/);
    t = RETRY_MS - 1;
    expect((await lookup.lookup(['C1'])).asked).toEqual([]);
    t = RETRY_MS;
    expect((await lookup.lookup(['C1'])).asked).toEqual(['C1']);
    expect(fetchProduct).toHaveBeenCalledTimes(3);
  });

  it('a not-found code is logged as info, not as a warning', async () => {
    const log = logger();
    const lookup = createSpecLookup({ fetchProduct: async () => { throw notFound(); }, log });
    await lookup.lookup(['C408339']);
    expect(log.warn).not.toHaveBeenCalled();
    expect(log.info.mock.calls[0][0]).toMatch(/C408339/);
  });

  it('never runs more than `concurrency` fetches at once', async () => {
    let live = 0;
    let peak = 0;
    const fetchProduct = async () => {
      live++; peak = Math.max(peak, live);
      await new Promise((r) => setTimeout(r, 1));
      live--;
      return PRODUCT;
    };
    const lookup = createSpecLookup({ fetchProduct, concurrency: 3 });
    const codes = Array.from({ length: 10 }, (_, i) => 'C' + (i + 1));
    const r = await lookup.lookup(codes);
    expect(r.found).toHaveLength(10);
    expect(peak).toBe(3);
  });

  it('shares an in-flight fetch between overlapping runs', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const fetchProduct = vi.fn(async () => { await gate; return PRODUCT; });
    const lookup = createSpecLookup({ fetchProduct });
    const a = lookup.lookup(['C1']);
    const b = lookup.lookup(['C1']);
    release();
    const [ra, rb] = await Promise.all([a, b]);
    expect(fetchProduct).toHaveBeenCalledTimes(1);
    expect(ra.found).toEqual(['C1']);
    expect(rb.found).toEqual(['C1']);
  });
});
