import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import {
  classifyBomRow,
  countBomWarnings,
  computeRows,
  buildStatusMap,
  buildLinkableKeys,
  prepareConsumption,
  computePriceInfo,
} from '../../js/bom/bom-logic.js';

const baseCols = { lcsc: 0, mpn: 1, qty: 2, ref: 3, desc: -1, value: -1, footprint: -1, dnp: -1 };

describe('classifyBomRow', () => {
  it('returns "ok" for row with valid LCSC', () => {
    expect(classifyBomRow(['C123456', '', '1', 'R1'], baseCols)).toBe('ok');
  });

  it('returns "ok" for row with MPN only', () => {
    expect(classifyBomRow(['', 'STM32F405', '5', 'U1'], baseCols)).toBe('ok');
  });

  it('returns "warn" for row with no LCSC or MPN', () => {
    expect(classifyBomRow(['', '', '1', 'X1'], baseCols)).toBe('warn');
  });

  it('returns "subtotal" for row containing subtotal', () => {
    expect(classifyBomRow(['', '', 'Subtotal', ''], baseCols)).toBe('subtotal');
  });

  it('returns "subtotal" for row containing total:', () => {
    expect(classifyBomRow(['', '', 'Total:', '100'], baseCols)).toBe('subtotal');
  });

  it('returns "dnp" for DNP row when DNP column is set', () => {
    const cols = { ...baseCols, dnp: 4 };
    expect(classifyBomRow(['C123', '', '1', 'R1', 'DNP'], cols)).toBe('dnp');
  });

  it('returns "dnp" for DNP row even without part IDs', () => {
    const cols = { ...baseCols, dnp: 4 };
    expect(classifyBomRow(['', '', '1', 'R1', 'DNP'], cols)).toBe('dnp');
  });

  it('returns "warn" for row with invalid qty content', () => {
    expect(classifyBomRow(['C123', '', 'abc', 'R1'], baseCols)).toBe('warn');
  });

  it('returns "ok" for row with empty qty (defaults to 1)', () => {
    expect(classifyBomRow(['C123', '', '', 'R1'], baseCols)).toBe('ok');
  });

  it('returns "warn" for row with zero qty', () => {
    expect(classifyBomRow(['C123', '', '0', 'R1'], baseCols)).toBe('warn');
  });
});

describe('countBomWarnings', () => {
  it('counts warn and subtotal rows', () => {
    const rows = [
      ['C123', '', '1', 'R1'],     // ok
      ['', '', '1', 'X1'],          // warn (no IDs)
      ['', '', 'Subtotal', ''],     // subtotal
      ['C456', '', '2', 'C1'],     // ok
    ];
    expect(countBomWarnings(rows, baseCols)).toBe(2);
  });

  it('returns 0 for all-ok rows', () => {
    const rows = [
      ['C123', '', '1', 'R1'],
      ['C456', '', '2', 'C1'],
    ];
    expect(countBomWarnings(rows, baseCols)).toBe(0);
  });

  it('returns 0 for empty rows', () => {
    expect(countBomWarnings([], baseCols)).toBe(0);
  });
});

describe('computeRows', () => {
  it('returns null when results is null', () => {
    expect(computeRows(null, 1, {})).toBeNull();
  });

  it('sets status "ok" when inventory has enough qty', () => {
    const results = [
      { bom: { qty: 5, dnp: false }, inv: { qty: 10 }, matchType: 'lcsc', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('ok');
    expect(rows[0].effectiveQty).toBe(5);
  });

  it('sets status "short" when inventory is insufficient', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'lcsc', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('short');
  });

  it('sets status "missing" when no inventory match', () => {
    const results = [
      { bom: { qty: 1, dnp: false }, inv: null, matchType: 'none', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('missing');
  });

  it('sets status "dnp" when bom.dnp is true', () => {
    const results = [
      { bom: { qty: 1, dnp: true }, inv: { qty: 10 }, matchType: 'lcsc', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('dnp');
  });

  it('sets status "possible" for value matches', () => {
    const results = [
      { bom: { qty: 1, dnp: false }, inv: { qty: 10 }, matchType: 'value', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('possible');
  });

  it('sets status "possible" for fuzzy matches', () => {
    const results = [
      { bom: { qty: 1, dnp: false }, inv: { qty: 10 }, matchType: 'fuzzy', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('possible');
  });

  it('applies multiplier to effectiveQty', () => {
    const results = [
      { bom: { qty: 5, dnp: false }, inv: { qty: 50 }, matchType: 'lcsc', alts: [] },
    ];
    const rows = computeRows(results, 3, {});
    expect(rows[0].effectiveQty).toBe(15);
    expect(rows[0].effectiveStatus).toBe('ok');
  });

  it('multiplier causes short status when inventory insufficient', () => {
    const results = [
      { bom: { qty: 5, dnp: false }, inv: { qty: 10 }, matchType: 'lcsc', alts: [] },
    ];
    const rows = computeRows(results, 3, {});
    expect(rows[0].effectiveStatus).toBe('short');
    expect(rows[0].effectiveQty).toBe(15);
  });

  it('sets status "manual" for manual matches with enough qty', () => {
    const results = [
      { bom: { qty: 1, dnp: false }, inv: { qty: 10 }, matchType: 'manual', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('manual');
  });

  it('sets status "manual-short" for manual matches with insufficient qty', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'manual', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('manual-short');
  });

  it('sets status "confirmed" for confirmed matches with enough qty', () => {
    const results = [
      { bom: { qty: 1, dnp: false }, inv: { qty: 10 }, matchType: 'confirmed', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('confirmed');
  });

  it('sets status "confirmed-short" for confirmed matches with insufficient qty', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'confirmed', alts: [] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('confirmed-short');
  });

  it('computes altQty and combinedQty', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'lcsc', alts: [{ qty: 3 }, { qty: 2 }] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].altQty).toBe(5);
    expect(rows[0].combinedQty).toBe(10);
  });

  it('sets coveredByAlts when alts cover the shortage', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'lcsc', alts: [{ qty: 5 }] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].effectiveStatus).toBe('short');
    expect(rows[0].coveredByAlts).toBe(true);
  });

  it('does not set coveredByAlts when alts are insufficient', () => {
    const results = [
      { bom: { qty: 10, dnp: false }, inv: { qty: 5 }, matchType: 'lcsc', alts: [{ qty: 2 }] },
    ];
    const rows = computeRows(results, 1, {});
    expect(rows[0].coveredByAlts).toBe(false);
  });
});

describe('buildStatusMap', () => {
  it('builds map from bomAggKey to effectiveStatus', () => {
    const rows = [
      { bom: { lcsc: 'C123', mpn: '', dnp: false }, effectiveStatus: 'ok' },
      { bom: { lcsc: '', mpn: 'STM32', dnp: false }, effectiveStatus: 'missing' },
      { bom: { lcsc: 'C456', mpn: '', dnp: true }, effectiveStatus: 'dnp' },
    ];
    const map = buildStatusMap(rows);
    expect(map['C123']).toBe('ok');
    expect(map['STM32']).toBe('missing');
    expect(map['C456:DNP']).toBe('dnp');
  });

  it('returns empty map for empty rows', () => {
    expect(buildStatusMap([])).toEqual({});
  });

  it('skips rows with no key', () => {
    const rows = [
      { bom: { lcsc: '', mpn: '', dnp: false }, effectiveStatus: 'warn' },
    ];
    const map = buildStatusMap(rows);
    expect(Object.keys(map)).toHaveLength(0);
  });
});

describe('buildLinkableKeys', () => {
  const rows = [
    { bom: { lcsc: 'C100', mpn: '', dnp: false }, effectiveStatus: 'missing' },
    { bom: { lcsc: 'C200', mpn: '', dnp: false }, effectiveStatus: 'ok' },
    { bom: { lcsc: 'C300', mpn: '', dnp: false }, effectiveStatus: 'possible' },
    { bom: { lcsc: 'C400', mpn: '', dnp: false }, effectiveStatus: 'short' },
    { bom: { lcsc: 'C500', mpn: '', dnp: false }, effectiveStatus: 'manual-short' },
    { bom: { lcsc: 'C600', mpn: '', dnp: false }, effectiveStatus: 'confirmed-short' },
  ];

  it('returns linkable keys when linking mode is active', () => {
    const keys = buildLinkableKeys(rows, true);
    expect(keys.has('C100')).toBe(true);
    expect(keys.has('C200')).toBe(false);
    expect(keys.has('C300')).toBe(true);
    expect(keys.has('C400')).toBe(true);
    expect(keys.has('C500')).toBe(true);
    expect(keys.has('C600')).toBe(true);
  });

  it('returns empty set when linking mode is false', () => {
    const keys = buildLinkableKeys(rows, false);
    expect(keys.size).toBe(0);
  });
});

describe('prepareConsumption', () => {
  it('extracts matched parts (not value or fuzzy)', () => {
    const results = [
      { inv: { lcsc: 'C123', mpn: '', digikey: '' }, matchType: 'lcsc', bom: { qty: 5 } },
      { inv: { lcsc: 'C456', mpn: '', digikey: '' }, matchType: 'mpn', bom: { qty: 3 } },
      { inv: { lcsc: 'C789', mpn: '', digikey: '' }, matchType: 'value', bom: { qty: 2 } },
      { inv: { lcsc: '', mpn: 'ABC', digikey: '' }, matchType: 'fuzzy', bom: { qty: 1 } },
      { inv: null, matchType: 'none', bom: { qty: 4 } },
      { inv: { lcsc: 'C111', mpn: '', digikey: '' }, matchType: 'manual', bom: { qty: 7 } },
      { inv: { lcsc: 'C222', mpn: '', digikey: '' }, matchType: 'confirmed', bom: { qty: 8 } },
    ];
    const { matches, matchesJson } = prepareConsumption(results);
    expect(matches).toHaveLength(4);
    expect(matches[0]).toEqual({ part_key: 'C123', bom_qty: 5 });
    expect(matches[1]).toEqual({ part_key: 'C456', bom_qty: 3 });
    expect(matches[2]).toEqual({ part_key: 'C111', bom_qty: 7 });
    expect(matches[3]).toEqual({ part_key: 'C222', bom_qty: 8 });
    expect(JSON.parse(matchesJson)).toEqual(matches);
  });

  it('returns empty matches for no inventory hits', () => {
    const results = [
      { inv: null, matchType: 'none', bom: { qty: 1 } },
    ];
    const { matches } = prepareConsumption(results);
    expect(matches).toHaveLength(0);
  });

  it('skips parts with no invPartKey', () => {
    const results = [
      { inv: { lcsc: '', mpn: '', digikey: '' }, matchType: 'lcsc', bom: { qty: 1 } },
    ];
    const { matches } = prepareConsumption(results);
    expect(matches).toHaveLength(0);
  });
});

describe('computePriceInfo', () => {
  it('computes price per board and total price', () => {
    const rows = [
      { bom: { qty: 10 }, inv: { unit_price: 0.5 } },
      { bom: { qty: 5 }, inv: { unit_price: 1.0 } },
    ];
    const { pricePerBoard, totalPrice } = computePriceInfo(rows, 3);
    expect(pricePerBoard).toBeCloseTo(10.0);  // 10*0.5 + 5*1.0
    expect(totalPrice).toBeCloseTo(30.0);     // 10.0 * 3
  });

  it('skips rows without inventory', () => {
    const rows = [
      { bom: { qty: 10 }, inv: null },
      { bom: { qty: 5 }, inv: { unit_price: 2.0 } },
    ];
    const { pricePerBoard, totalPrice } = computePriceInfo(rows, 1);
    expect(pricePerBoard).toBeCloseTo(10.0);
    expect(totalPrice).toBeCloseTo(10.0);
  });

  it('skips rows with zero unit_price', () => {
    const rows = [
      { bom: { qty: 10 }, inv: { unit_price: 0 } },
      { bom: { qty: 5 }, inv: { unit_price: 1.0 } },
    ];
    const { pricePerBoard } = computePriceInfo(rows, 1);
    expect(pricePerBoard).toBeCloseTo(5.0);
  });

  it('returns zero for empty rows', () => {
    const { pricePerBoard, totalPrice } = computePriceInfo([], 1);
    expect(pricePerBoard).toBe(0);
    expect(totalPrice).toBe(0);
  });
});
