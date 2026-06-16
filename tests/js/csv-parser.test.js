import { describe, it, expect } from 'vitest';
import {
  parseCSV,
  detectBOMColumns,
  extractLCSC,
  isDnp,
  extractPartIds,
  generateCSV,
  aggregateBomRows,
  processBOM,
} from '../../js/csv-parser.js';

describe('parseCSV', () => {
  it('parses simple comma-delimited CSV', () => {
    const result = parseCSV('a,b,c\n1,2,3\n');
    expect(result).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
  });

  it('handles quoted fields with commas', () => {
    const result = parseCSV('a,"b,c",d\n');
    expect(result).toEqual([['a', 'b,c', 'd']]);
  });

  it('handles escaped quotes inside quoted fields', () => {
    const result = parseCSV('"a""b",c\n');
    expect(result).toEqual([['a"b', 'c']]);
  });

  it('auto-detects tab delimiter', () => {
    const result = parseCSV('a\tb\tc\n1\t2\t3\n');
    expect(result).toEqual([['a', 'b', 'c'], ['1', '2', '3']]);
  });

  it('skips blank lines', () => {
    const result = parseCSV('a,b\n\n1,2\n');
    expect(result).toEqual([['a', 'b'], ['1', '2']]);
  });
});

describe('extractLCSC', () => {
  it('extracts LCSC part number from string', () => {
    expect(extractLCSC('C123456')).toBe('C123456');
  });

  it('extracts from surrounding text', () => {
    expect(extractLCSC('part C99999 here')).toBe('C99999');
  });

  it('returns null for non-LCSC strings', () => {
    expect(extractLCSC('ABC123')).toBeNull();
    expect(extractLCSC('')).toBeNull();
  });

  it('requires at least 4 digits', () => {
    expect(extractLCSC('C12')).toBeNull();
    expect(extractLCSC('C1234')).toBe('C1234');
  });
});

describe('isDnp', () => {
  it('recognizes common DNP values', () => {
    expect(isDnp('dnp')).toBe(true);
    expect(isDnp('DNP')).toBe(true);
    expect(isDnp('1')).toBe(true);
    expect(isDnp('yes')).toBe(true);
    expect(isDnp('true')).toBe(true);
    expect(isDnp('excluded from bom')).toBe(true);
  });

  it('rejects non-DNP values', () => {
    expect(isDnp('')).toBe(false);
    expect(isDnp('0')).toBe(false);
    expect(isDnp('no')).toBe(false);
    expect(isDnp(null)).toBe(false);
    expect(isDnp(undefined)).toBe(false);
  });
});

describe('detectBOMColumns', () => {
  it('detects standard JLCPCB BOM columns', () => {
    const headers = ['Designator', 'Quantity', 'LCSC Part Number', 'Description', 'Footprint'];
    const cols = detectBOMColumns(headers);
    expect(cols.ref).toBe(0);
    expect(cols.qty).toBe(1);
    expect(cols.lcsc).toBe(2);
    expect(cols.desc).toBe(3);
    expect(cols.footprint).toBe(4);
  });

  it('falls back to Value for MPN when no explicit MPN column', () => {
    const headers = ['Reference', 'Value', 'Quantity'];
    const cols = detectBOMColumns(headers);
    expect(cols.mpn).toBe(1); // falls back to Value column
  });

  it('does not misdetect Manufacturer Part Number as LCSC', () => {
    const headers = ['Designator*', 'Quantity*', 'Manufacturer Part Number*', 'Value', 'Procurement Type', 'Exclude from BOM'];
    const cols = detectBOMColumns(headers);
    expect(cols.lcsc).toBe(-1);
    expect(cols.mpn).toBe(2);
    expect(cols.ref).toBe(0);
    expect(cols.qty).toBe(1);
    expect(cols.value).toBe(3);
  });
});

describe('extractPartIds', () => {
  it('extracts LCSC and MPN from row', () => {
    const cols = { lcsc: 0, mpn: 1, qty: 2, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
    const result = extractPartIds(['C123456', 'STM32F405', '10'], cols);
    expect(result.lcsc).toBe('C123456');
    expect(result.mpn).toBe('STM32F405');
  });

  it('extracts LCSC from MPN field if no dedicated LCSC column', () => {
    const cols = { lcsc: -1, mpn: 0, qty: 1, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
    const result = extractPartIds(['C99999', '5'], cols);
    expect(result.lcsc).toBe('C99999');
  });
});

describe('generateCSV', () => {
  it('generates RFC 4180 CSV', () => {
    const csv = generateCSV(['a', 'b'], [['1', '2'], ['3', '4']]);
    expect(csv).toBe('a,b\r\n1,2\r\n3,4\r\n');
  });

  it('quotes fields containing commas', () => {
    const csv = generateCSV(['a'], [['hello, world']]);
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
    const { aggregated, warnings } = aggregateBomRows(rows, headers, cols);
    expect(aggregated.size).toBe(1);
    const part = aggregated.get('C123456');
    expect(part.qty).toBe(5);
    expect(part.refs).toBe('R1, R2, R3, R4, R5');
  });

  it('derives qty from the designator count when there is no Qty column', () => {
    // Grouped KiCad BOM: one row per part, quantity implied by the designators.
    const headers = ['Value', 'Reference', 'Footprint', 'LCSC', 'DNP', 'Exclude from BOM'];
    const cols = detectBOMColumns(headers);
    expect(cols.qty).toBe(-1);
    expect(cols.ref).toBe(1);
    const rows = [
      ['0402WGF5101TCE', 'R2,R3,R4,R5,R7,R8,R10,R11', 'Resistor_SMD:R_0402_1005Metric', 'C25905', '', ''],
      ['WS2812B-V5/W', 'D1,D2,D3,D4', 'LED:WS2812B', 'C2874885', '', ''],
      ['WSD4066DN', 'U1', 'DFN-8', 'C377861', '', ''],
    ];
    const { aggregated } = aggregateBomRows(rows, headers, cols);
    expect(aggregated.get('C25905').qty).toBe(8);
    expect(aggregated.get('C2874885').qty).toBe(4);
    expect(aggregated.get('C377861').qty).toBe(1);
  });

  it('sums designator counts across rows that share a part (no Qty column)', () => {
    const headers = ['Value', 'Reference', 'LCSC'];
    const cols = detectBOMColumns(headers);
    expect(cols.qty).toBe(-1);
    const rows = [
      ['10k', 'R1,R2', 'C123456'],
      ['10k', 'R3 R4 R5', 'C123456'], // whitespace-separated designators
    ];
    const { aggregated } = aggregateBomRows(rows, headers, cols);
    expect(aggregated.get('C123456').qty).toBe(5);
  });

  it('falls back to qty 1 when there is neither a Qty nor a Reference column', () => {
    const headers = ['Value', 'LCSC'];
    const cols = detectBOMColumns(headers);
    expect(cols.qty).toBe(-1);
    expect(cols.ref).toBe(-1);
    const { aggregated } = aggregateBomRows([['10k', 'C123456']], headers, cols);
    expect(aggregated.get('C123456').qty).toBe(1);
  });
});

describe('processBOM', () => {
  it('returns null for too-short input', () => {
    expect(processBOM('just one line')).toBeNull();
  });

  it('processes a minimal BOM', () => {
    const csv = 'LCSC Part Number,Quantity,Designator\nC123456,2,R1 R2\n';
    const result = processBOM(csv, 'test.csv');
    expect(result).not.toBeNull();
    expect(result.aggregated.size).toBe(1);
  });

  it('preserves designators from NextPCB/KiCad format with asterisk headers', () => {
    const csv = [
      'Designator*,Quantity*,Manufacturer Part Number*,Value,Procurement Type,Exclude from BOM,Exclude from Board',
      '"C1,C2,C4,C5",4,CL05A475MP5NRNC,4u7,,,',
      '"R1,R2,R3",3,0402WGF3300TCE,330R,,,',
    ].join('\n');
    const result = processBOM(csv, 'test-nextpcb.csv');
    expect(result).not.toBeNull();
    expect(result.cols.ref).toBe(0);
    expect(result.aggregated.size).toBe(2);
    const parts = [...result.aggregated.values()];
    const cap = parts.find(p => p.mpn === 'CL05A475MP5NRNC');
    expect(cap).toBeDefined();
    expect(cap.refs).toBe('C1,C2,C4,C5');
    expect(cap.qty).toBe(4);
    const res = parts.find(p => p.mpn === '0402WGF3300TCE');
    expect(res).toBeDefined();
    expect(res.refs).toBe('R1,R2,R3');
    expect(res.qty).toBe(3);
  });

  it('aggregates refs when same MPN appears in multiple rows', () => {
    const csv = [
      'Reference,Quantity,MPN,Value',
      'R1,1,RC0402FR-0710KL,10k',
      'R2,1,RC0402FR-0710KL,10k',
      'R3,1,RC0402FR-0710KL,10k',
    ].join('\n');
    const result = processBOM(csv, 'test-split.csv');
    expect(result).not.toBeNull();
    const parts = [...result.aggregated.values()];
    expect(parts.length).toBe(1);
    expect(parts[0].refs).toBe('R1, R2, R3');
    expect(parts[0].qty).toBe(3);
  });
});
