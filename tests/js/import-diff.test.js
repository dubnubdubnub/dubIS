import { describe, it, expect } from 'vitest';
import { computeImportDiff } from '../../js/import/import-diff.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build a minimal invRow with full field names (as transformImportRows produces). */
function row({ lcsc = '', mpn = '', digikey = '', pololu = '', mouser = '', qty = '10', price = '0.01' } = {}) {
  return {
    'LCSC Part Number': lcsc,
    'Manufacture Part Number': mpn,
    'Digikey Part Number': digikey,
    'Pololu Part Number': pololu,
    'Mouser Part Number': mouser,
    'Quantity': String(qty),
    'Unit Price($)': price,
  };
}

/** Build a minimal inventory item (short field names). */
function item({ lcsc = '', mpn = '', digikey = '', pololu = '', mouser = '', qty = 0 } = {}) {
  return { lcsc, mpn, digikey, pololu, mouser, qty };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('computeImportDiff — empty inventory', () => {
  it('returns insert for every row when inventory is empty', () => {
    const rows = [row({ lcsc: 'C100', qty: '5' }), row({ mpn: 'ABC123', qty: '10' })];
    const diff = computeImportDiff(rows, []);
    expect(diff).toHaveLength(2);
    expect(diff[0].status).toBe('insert');
    expect(diff[1].status).toBe('insert');
  });

  it('sets currentQty=0 and resultingQty=addQty for inserts', () => {
    const diff = computeImportDiff([row({ lcsc: 'C200', qty: '7' })], []);
    expect(diff[0].currentQty).toBe(0);
    expect(diff[0].addQty).toBe(7);
    expect(diff[0].resultingQty).toBe(7);
  });
});

describe('computeImportDiff — update detection', () => {
  it('detects update when part key matches existing inventory', () => {
    const diff = computeImportDiff(
      [row({ lcsc: 'C429942', qty: '10' })],
      [item({ lcsc: 'C429942', qty: 30 })],
    );
    expect(diff[0].status).toBe('update');
    expect(diff[0].currentQty).toBe(30);
    expect(diff[0].addQty).toBe(10);
    expect(diff[0].resultingQty).toBe(40);
    expect(diff[0].matchedItem).toBeTruthy();
  });

  it('matches by MPN when no LCSC', () => {
    const diff = computeImportDiff(
      [row({ mpn: 'SOMEMPN', qty: '5' })],
      [item({ mpn: 'SOMEMPN', qty: 20 })],
    );
    expect(diff[0].status).toBe('update');
    expect(diff[0].resultingQty).toBe(25);
  });

  it('matches by DigiKey part number', () => {
    const diff = computeImportDiff(
      [row({ digikey: 'DK-12345', qty: '3' })],
      [item({ digikey: 'DK-12345', qty: 10 })],
    );
    expect(diff[0].status).toBe('update');
    expect(diff[0].resultingQty).toBe(13);
  });

  it('matches by Pololu part number', () => {
    const diff = computeImportDiff(
      [row({ pololu: 'POLOLU-99', qty: '2' })],
      [item({ pololu: 'POLOLU-99', qty: 5 })],
    );
    expect(diff[0].status).toBe('update');
    expect(diff[0].resultingQty).toBe(7);
  });

  it('matches by Mouser part number', () => {
    const diff = computeImportDiff(
      [row({ mouser: 'M-ABC', qty: '1' })],
      [item({ mouser: 'M-ABC', qty: 4 })],
    );
    expect(diff[0].status).toBe('update');
    expect(diff[0].resultingQty).toBe(5);
  });

  it('LCSC takes priority over MPN when both present in invRow', () => {
    // Inventory has MPN match only; LCSC in invRow should key off LCSC
    const diff = computeImportDiff(
      [row({ lcsc: 'C999', mpn: 'MYMATCH', qty: '2' })],
      [
        item({ lcsc: 'C999', qty: 10 }),
        item({ mpn: 'MYMATCH', qty: 50 }),
      ],
    );
    expect(diff).toHaveLength(1);
    expect(diff[0].matchedItem.qty).toBe(10); // matched C999, not MYMATCH
    expect(diff[0].resultingQty).toBe(12);
  });
});

describe('computeImportDiff — skip rows', () => {
  it('skips rows with no part identifier', () => {
    const diff = computeImportDiff([row({ qty: '5' })], []);
    expect(diff[0].status).toBe('skip');
    expect(diff[0].skipReason).toMatch(/no part identifier/i);
    expect(diff[0].partKey).toBe('');
  });

  it('skips rows with qty = 0', () => {
    const diff = computeImportDiff([row({ lcsc: 'C100', qty: '0' })], []);
    expect(diff[0].status).toBe('skip');
    expect(diff[0].skipReason).toMatch(/qty/i);
  });

  it('skips rows with negative qty', () => {
    const diff = computeImportDiff([row({ lcsc: 'C100', qty: '-5' })], []);
    expect(diff[0].status).toBe('skip');
  });

  it('skips rows with non-numeric qty', () => {
    const diff = computeImportDiff([row({ mpn: 'ABC', qty: 'bad' })], []);
    expect(diff[0].status).toBe('skip');
  });

  it('preserves the original row object on skip entries', () => {
    const r = row({ qty: '0', lcsc: 'C1' });
    const diff = computeImportDiff([r], []);
    expect(diff[0].row).toBe(r);
  });
});

describe('computeImportDiff — qty summing (duplicate keys)', () => {
  it('sums qty for duplicate keys in same import batch', () => {
    const diff = computeImportDiff(
      [
        row({ lcsc: 'C500', qty: '10' }),
        row({ lcsc: 'C500', qty: '20' }),
      ],
      [],
    );
    // Should produce a single entry, not two
    expect(diff).toHaveLength(1);
    expect(diff[0].addQty).toBe(30);
    expect(diff[0].resultingQty).toBe(30);
    expect(diff[0].status).toBe('insert');
  });

  it('sums qty for duplicate update keys against existing inventory', () => {
    const diff = computeImportDiff(
      [
        row({ mpn: 'PART-A', qty: '5' }),
        row({ mpn: 'PART-A', qty: '3' }),
      ],
      [item({ mpn: 'PART-A', qty: 100 })],
    );
    expect(diff).toHaveLength(1);
    expect(diff[0].status).toBe('update');
    expect(diff[0].currentQty).toBe(100);
    expect(diff[0].addQty).toBe(8);
    expect(diff[0].resultingQty).toBe(108);
  });
});

describe('computeImportDiff — mixed batch', () => {
  it('produces correct mix of insert / update / skip in one batch', () => {
    const invRows = [
      row({ lcsc: 'C001', qty: '10' }),   // insert (not in inventory)
      row({ lcsc: 'C002', qty: '5' }),    // update (exists)
      row({ qty: '99' }),                  // skip (no key)
    ];
    const inventory = [item({ lcsc: 'C002', qty: 20 })];
    const diff = computeImportDiff(invRows, inventory);
    expect(diff).toHaveLength(3);
    expect(diff.find(d => d.partKey === 'C001').status).toBe('insert');
    expect(diff.find(d => d.partKey === 'C002').status).toBe('update');
    expect(diff.find(d => d.status === 'skip')).toBeTruthy();
  });

  it('exposes partKey on all non-skip entries', () => {
    const diff = computeImportDiff([row({ lcsc: 'C123', qty: '1' })], []);
    expect(diff[0].partKey).toBe('C123');
  });
});
