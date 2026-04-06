import { describe, it, expect } from 'vitest';
import {
  TARGET_FIELDS,
  PART_ID_FIELDS,
  PO_TEMPLATES,
  classifyRow,
  countWarnings,
  transformImportRows,
  validateImportData,
} from '../../js/import/import-logic.js';

describe('TARGET_FIELDS', () => {
  it('contains expected core fields', () => {
    expect(TARGET_FIELDS).toContain('Skip');
    expect(TARGET_FIELDS).toContain('LCSC Part Number');
    expect(TARGET_FIELDS).toContain('Digikey Part Number');
    expect(TARGET_FIELDS).toContain('Pololu Part Number');
    expect(TARGET_FIELDS).toContain('Mouser Part Number');
    expect(TARGET_FIELDS).toContain('Manufacture Part Number');
    expect(TARGET_FIELDS).toContain('Quantity');
    expect(TARGET_FIELDS).toContain('Unit Price($)');
    expect(TARGET_FIELDS).toContain('Description');
  });

  it('has Skip as the first field', () => {
    expect(TARGET_FIELDS[0]).toBe('Skip');
  });
});

describe('PART_ID_FIELDS', () => {
  it('is a subset of TARGET_FIELDS', () => {
    PART_ID_FIELDS.forEach(field => {
      expect(TARGET_FIELDS).toContain(field);
    });
  });

  it('contains all distributor part number fields', () => {
    expect(PART_ID_FIELDS).toContain('LCSC Part Number');
    expect(PART_ID_FIELDS).toContain('Digikey Part Number');
    expect(PART_ID_FIELDS).toContain('Pololu Part Number');
    expect(PART_ID_FIELDS).toContain('Mouser Part Number');
    expect(PART_ID_FIELDS).toContain('Manufacture Part Number');
  });

  it('does not contain non-ID fields', () => {
    expect(PART_ID_FIELDS).not.toContain('Skip');
    expect(PART_ID_FIELDS).not.toContain('Quantity');
    expect(PART_ID_FIELDS).not.toContain('Description');
  });
});

describe('PO_TEMPLATES', () => {
  it('has expected template keys', () => {
    expect(Object.keys(PO_TEMPLATES)).toEqual(
      expect.arrayContaining(['generic', 'lcsc', 'digikey', 'pololu', 'mouser'])
    );
  });

  it('each template has a label and headers array', () => {
    for (const [key, tpl] of Object.entries(PO_TEMPLATES)) {
      expect(tpl).toHaveProperty('label');
      expect(tpl).toHaveProperty('headers');
      expect(Array.isArray(tpl.headers)).toBe(true);
      expect(tpl.headers.length).toBeGreaterThan(0);
    }
  });

  it('all template headers are valid TARGET_FIELDS (excluding Skip)', () => {
    for (const [key, tpl] of Object.entries(PO_TEMPLATES)) {
      tpl.headers.forEach(h => {
        expect(TARGET_FIELDS).toContain(h);
        expect(h).not.toBe('Skip');
      });
    }
  });

  it('each distributor template includes its own part number field', () => {
    expect(PO_TEMPLATES.lcsc.headers).toContain('LCSC Part Number');
    expect(PO_TEMPLATES.digikey.headers).toContain('Digikey Part Number');
    expect(PO_TEMPLATES.pololu.headers).toContain('Pololu Part Number');
    expect(PO_TEMPLATES.mouser.headers).toContain('Mouser Part Number');
  });
});

describe('classifyRow', () => {
  it('returns "subtotal" for rows containing subtotal text', () => {
    expect(classifyRow(['', 'Subtotal', '100'], {})).toBe('subtotal');
    expect(classifyRow(['total:', '', '50'], {})).toBe('subtotal');
  });

  it('returns "warn" when no part ID is present', () => {
    const mapping = { 0: 'Description', 1: 'Quantity' };
    expect(classifyRow(['resistor', '10'], mapping)).toBe('warn');
  });

  it('returns "ok" when a part ID and valid quantity are present', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    expect(classifyRow(['C12345', '10'], mapping)).toBe('ok');
  });

  it('returns "warn" when quantity is zero or negative', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    expect(classifyRow(['C12345', '0'], mapping)).toBe('warn');
    expect(classifyRow(['C12345', '-5'], mapping)).toBe('warn');
  });

  it('returns "ok" when part ID present and no quantity column mapped', () => {
    const mapping = { 0: 'Manufacture Part Number' };
    expect(classifyRow(['LM7805', ''], mapping)).toBe('ok');
  });
});

describe('countWarnings', () => {
  it('counts rows with warn or subtotal classification', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    const rows = [
      ['C12345', '10'],   // ok
      ['', '5'],           // warn (no part ID)
      ['Subtotal', '100'], // subtotal
      ['C67890', '3'],     // ok
    ];
    expect(countWarnings(rows, mapping)).toBe(2);
  });

  it('returns 0 when all rows are ok', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    const rows = [
      ['C12345', '10'],
      ['C67890', '3'],
    ];
    expect(countWarnings(rows, mapping)).toBe(0);
  });
});

describe('transformImportRows', () => {
  it('produces correct output with basic mapping', () => {
    const mapping = {
      0: 'LCSC Part Number',
      1: 'Quantity',
      2: 'Description',
    };
    const rows = [
      ['C12345', '10', '100nF capacitor'],
      ['C67890', '5', '10k resistor'],
    ];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({
      'LCSC Part Number': 'C12345',
      'Quantity': '10',
      'Description': '100nF capacitor',
    });
    expect(result[1]).toEqual({
      'LCSC Part Number': 'C67890',
      'Quantity': '5',
      'Description': '10k resistor',
    });
  });

  it('cleans up quantity values (removes commas, quotes)', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    const rows = [['C12345', '1,000']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]['Quantity']).toBe('1000');
  });

  it('sets invalid quantity to "0"', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Quantity' };
    const rows = [['C12345', 'abc']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]['Quantity']).toBe('0');
  });

  it('cleans up price values', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Unit Price($)' };
    const rows = [['C12345', '$1,234.56']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]['Unit Price($)']).toBe('1234.56');
  });

  it('sets invalid price to empty string', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Unit Price($)' };
    const rows = [['C12345', 'N/A']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]['Unit Price($)']).toBe('');
  });

  it('skips columns mapped to "Skip"', () => {
    const mapping = { 0: 'Skip', 1: 'LCSC Part Number', 2: 'Quantity' };
    const rows = [['ignore-me', 'C12345', '10']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]).not.toHaveProperty('Skip');
    expect(result[0]).toEqual({
      'LCSC Part Number': 'C12345',
      'Quantity': '10',
    });
  });

  it('handles extended price cleanup', () => {
    const mapping = { 0: 'LCSC Part Number', 1: 'Ext.Price($)' };
    const rows = [['C12345', '$99.99']];
    const result = transformImportRows(rows, mapping, TARGET_FIELDS);
    expect(result[0]['Ext.Price($)']).toBe('99.99');
  });

  it('returns empty array for empty input', () => {
    const result = transformImportRows([], {}, TARGET_FIELDS);
    expect(result).toEqual([]);
  });
});

describe('validateImportData', () => {
  it('returns true when at least one row has a part ID', () => {
    const rows = [
      { 'LCSC Part Number': 'C12345', 'Quantity': '10' },
      { 'Description': 'just a note' },
    ];
    expect(validateImportData(rows, PART_ID_FIELDS)).toBe(true);
  });

  it('returns false when no rows have a part ID', () => {
    const rows = [
      { 'Description': 'resistor', 'Quantity': '10' },
      { 'Description': 'capacitor', 'Quantity': '5' },
    ];
    expect(validateImportData(rows, PART_ID_FIELDS)).toBe(false);
  });

  it('returns false for empty rows array', () => {
    expect(validateImportData([], PART_ID_FIELDS)).toBe(false);
  });

  it('returns false for null/undefined input', () => {
    expect(validateImportData(null, PART_ID_FIELDS)).toBe(false);
    expect(validateImportData(undefined, PART_ID_FIELDS)).toBe(false);
  });

  it('returns true with Digikey part number', () => {
    const rows = [{ 'Digikey Part Number': 'LM7805CT-ND' }];
    expect(validateImportData(rows, PART_ID_FIELDS)).toBe(true);
  });

  it('returns true with Manufacture Part Number', () => {
    const rows = [{ 'Manufacture Part Number': 'LM7805' }];
    expect(validateImportData(rows, PART_ID_FIELDS)).toBe(true);
  });

  it('ignores whitespace-only values', () => {
    const rows = [{ 'LCSC Part Number': '   ', 'Digikey Part Number': '  ' }];
    expect(validateImportData(rows, PART_ID_FIELDS)).toBe(false);
  });
});
