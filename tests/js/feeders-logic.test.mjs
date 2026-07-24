// tests/js/feeders-logic.test.mjs
import { describe, it, expect } from 'vitest';
import {
  describeLoadedPart, searchParts, validateRegisterForm,
  validateLoadForm, validateSheetForm, formatTapeWidth, formatLoadedQty,
} from '../../js/feeders-logic.js';

const inventory = [
  { lcsc: 'C25794', mpn: 'CL05B104KB54PNC', description: '100nF 10% 16V X7R 0402 Cap', qty: 500 },
  { lcsc: '', mpn: '0402WGF1002TCE', description: '10k 1% 0402 Resistor', qty: 300 },
];

describe('describeLoadedPart', () => {
  it('returns null when nothing is loaded', () => {
    expect(describeLoadedPart(null, inventory)).toBeNull();
  });

  it('resolves a description when the part is still in inventory', () => {
    const result = describeLoadedPart({ part_key: 'C25794', qty: 100, tape_width_mm: 8 }, inventory);
    expect(result).toEqual({ part_key: 'C25794', description: '100nF 10% 16V X7R 0402 Cap', resolved: true });
  });

  it('marks unresolved when the part is no longer in inventory', () => {
    const result = describeLoadedPart({ part_key: 'C99999', qty: 10, tape_width_mm: null }, inventory);
    expect(result).toEqual({ part_key: 'C99999', description: '', resolved: false });
  });
});

describe('searchParts', () => {
  it('matches by part key, mpn, or description substring (case-insensitive)', () => {
    expect(searchParts(inventory, 'c25794').map(i => i.mpn)).toEqual(['CL05B104KB54PNC']);
    expect(searchParts(inventory, '100nF').map(i => i.lcsc)).toEqual(['C25794']);
    expect(searchParts(inventory, 'WGF1002').map(i => i.mpn)).toEqual(['0402WGF1002TCE']);
  });

  it('returns [] for an empty/blank term', () => {
    expect(searchParts(inventory, '')).toEqual([]);
    expect(searchParts(inventory, '   ')).toEqual([]);
  });

  it('caps results at the given limit', () => {
    const big = Array.from({ length: 20 }, (_, i) => ({ lcsc: `C${i}`, description: 'widget', mpn: '' }));
    expect(searchParts(big, 'widget', 5)).toHaveLength(5);
  });
});

describe('validateRegisterForm', () => {
  it('requires a numeric tag_id and a non-empty feeder_type', () => {
    expect(validateRegisterForm({ tag_id: '', feeder_type: '' })).toMatchObject({
      tag_id: expect.any(String), feeder_type: expect.any(String),
    });
    expect(validateRegisterForm({ tag_id: 'abc', feeder_type: '8mm' })).toMatchObject({
      tag_id: expect.any(String),
    });
  });

  it('passes for a valid tag id + feeder type', () => {
    expect(validateRegisterForm({ tag_id: '5', feeder_type: '8mm reel' })).toBeNull();
  });
});

describe('validateLoadForm', () => {
  it('requires a part_key', () => {
    expect(validateLoadForm({ part_key: '', qty: '10' })).toMatchObject({ part_key: expect.any(String) });
  });

  it('rejects negative, non-integer, or missing qty', () => {
    expect(validateLoadForm({ part_key: 'C1', qty: '-1' })).toMatchObject({ qty: expect.any(String) });
    expect(validateLoadForm({ part_key: 'C1', qty: '1.5' })).toMatchObject({ qty: expect.any(String) });
    expect(validateLoadForm({ part_key: 'C1', qty: '' })).toMatchObject({ qty: expect.any(String) });
  });

  it('allows a blank tape_width_mm (auto-derive) but rejects a non-positive one', () => {
    expect(validateLoadForm({ part_key: 'C1', qty: '10', tape_width_mm: '' })).toBeNull();
    expect(validateLoadForm({ part_key: 'C1', qty: '10', tape_width_mm: '0' })).toMatchObject({
      tape_width_mm: expect.any(String),
    });
  });

  it('passes for a fully valid form', () => {
    expect(validateLoadForm({ part_key: 'C25794', qty: '100', tape_width_mm: '8' })).toBeNull();
  });
});

describe('validateSheetForm', () => {
  it('requires a non-negative integer start and a count >= 1', () => {
    expect(validateSheetForm({ start: '-1', count: '10' })).toMatchObject({ start: expect.any(String) });
    expect(validateSheetForm({ start: '0', count: '0' })).toMatchObject({ count: expect.any(String) });
    expect(validateSheetForm({ start: '0', count: '24' })).toBeNull();
  });
});

describe('formatTapeWidth / formatLoadedQty', () => {
  it('shows an em-dash when nothing is loaded or tape width is unset', () => {
    expect(formatTapeWidth(null)).toBe('—');
    expect(formatTapeWidth({ tape_width_mm: null })).toBe('—');
    expect(formatLoadedQty(null)).toBe('—');
  });

  it('formats loaded values as plain strings', () => {
    expect(formatTapeWidth({ tape_width_mm: 8 })).toBe('8');
    expect(formatLoadedQty({ qty: 250 })).toBe('250');
  });
});
