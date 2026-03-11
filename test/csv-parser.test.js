import { describe, it, expect } from 'vitest';
import { loadGlobals } from './helpers/load-globals.js';

const g = loadGlobals();

describe('parseCSV', () => {
  it('parses simple comma-delimited CSV', () => {
    const result = g.parseCSV('a,b,c\n1,2,3\n');
    expect(result).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
  });

  it('handles quoted fields with commas', () => {
    const result = g.parseCSV('a,"b,c",d\n');
    expect(result).toEqual([['a', 'b,c', 'd']]);
  });

  it('handles escaped quotes inside quoted fields', () => {
    const result = g.parseCSV('"a""b",c\n');
    expect(result).toEqual([['a"b', 'c']]);
  });

  it('auto-detects tab delimiter', () => {
    const result = g.parseCSV('a\tb\tc\n1\t2\t3\n');
    expect(result).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
  });

  it('skips blank lines', () => {
    const result = g.parseCSV('a,b\n\n1,2\n');
    expect(result).toEqual([['a', 'b'], ['1', '2']]);
  });
});

describe('extractLCSC', () => {
  it('extracts LCSC part number from string', () => {
    expect(g.extractLCSC('C123456')).toBe('C123456');
  });

  it('extracts from surrounding text', () => {
    expect(g.extractLCSC('part C99999 here')).toBe('C99999');
  });

  it('returns null for non-LCSC strings', () => {
    expect(g.extractLCSC('ABC123')).toBeNull();
    expect(g.extractLCSC('')).toBeNull();
  });

  it('requires at least 4 digits', () => {
    expect(g.extractLCSC('C12')).toBeNull();
    expect(g.extractLCSC('C1234')).toBe('C1234');
  });
});

describe('isDnp', () => {
  it('recognizes common DNP values', () => {
    expect(g.isDnp('dnp')).toBe(true);
    expect(g.isDnp('DNP')).toBe(true);
    expect(g.isDnp('1')).toBe(true);
    expect(g.isDnp('yes')).toBe(true);
    expect(g.isDnp('true')).toBe(true);
    expect(g.isDnp('excluded from bom')).toBe(true);
  });

  it('rejects non-DNP values', () => {
    expect(g.isDnp('')).toBe(false);
    expect(g.isDnp('0')).toBe(false);
    expect(g.isDnp('no')).toBe(false);
    expect(g.isDnp(null)).toBe(false);
    expect(g.isDnp(undefined)).toBe(false);
  });
});

describe('detectBOMColumns', () => {
  it('detects standard JLCPCB BOM columns', () => {
    const headers = ['Designator', 'Quantity', 'LCSC Part Number', 'Description', 'Footprint'];
    const cols = g.detectBOMColumns(headers);
    expect(cols.ref).toBe(0);
    expect(cols.qty).toBe(1);
    expect(cols.lcsc).toBe(2);
    expect(cols.desc).toBe(3);
    expect(cols.footprint).toBe(4);
  });

  it('falls back to Value for MPN when no explicit MPN column', () => {
    const headers = ['Reference', 'Value', 'Quantity'];
    const cols = g.detectBOMColumns(headers);
    expect(cols.mpn).toBe(1); // falls back to Value column
  });
});

describe('extractPartIds', () => {
  it('extracts LCSC and MPN from row', () => {
    const cols = { lcsc: 0, mpn: 1, qty: 2, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
    const result = g.extractPartIds(['C123456', 'STM32F405', '10'], cols);
    expect(result.lcsc).toBe('C123456');
    expect(result.mpn).toBe('STM32F405');
  });

  it('extracts LCSC from MPN field if no dedicated LCSC column', () => {
    const cols = { lcsc: -1, mpn: 0, qty: 1, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
    const result = g.extractPartIds(['C99999', '5'], cols);
    expect(result.lcsc).toBe('C99999');
  });
});

describe('generateCSV', () => {
  it('generates RFC 4180 CSV', () => {
    const csv = g.generateCSV(['a', 'b'], [['1', '2'], ['3', '4']]);
    expect(csv).toBe('a,b\r\n1,2\r\n3,4\r\n');
  });

  it('quotes fields containing commas', () => {
    const csv = g.generateCSV(['a'], [['hello, world']]);
    expect(csv).toBe('a\r\n"hello, world"\r\n');
  });
});

describe('aggregateBomRows', () => {
  it('aggregates duplicate parts by LCSC', () => {
    const headers = ['LCSC', 'Qty', 'Designator'];
    const cols = { lcsc: 0, mpn: -1, qty: 1, ref: 2, desc: -1, value: -1, footprint: -1, dnp: -1 };
    const rows = [
      ['C123456', '2', 'R1, R2'],
      ['C123456', '3', 'R3, R4, R5'],
    ];
    const { aggregated, warnings } = g.aggregateBomRows(rows, headers, cols);
    expect(aggregated.size).toBe(1);
    const part = aggregated.get('C123456');
    expect(part.qty).toBe(5);
    expect(part.refs).toBe('R1, R2, R3, R4, R5');
  });
});

describe('processBOM', () => {
  it('returns null for too-short input', () => {
    expect(g.processBOM('just one line')).toBeNull();
  });

  it('processes a minimal BOM', () => {
    const csv = 'LCSC Part Number,Quantity,Designator\nC123456,2,R1 R2\n';
    const result = g.processBOM(csv, 'test.csv');
    expect(result).not.toBeNull();
    expect(result.aggregated.size).toBe(1);
  });
});
