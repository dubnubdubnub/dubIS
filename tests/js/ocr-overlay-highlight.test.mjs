// tests/js/ocr-overlay-highlight.test.mjs
import { describe, it, expect } from 'vitest';
import { rowHighlightBoxes, backendLabel } from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-highlight.js';

const page = { width: 100, height: 100, words: [
  { text: 'C12345', x: 10, y: 10, w: 30, h: 8 },
  { text: '100', x: 50, y: 10, w: 12, h: 8 },
  { text: 'NOISE', x: 0, y: 90, w: 20, h: 8 },
] };

describe('rowHighlightBoxes', () => {
  it('uses the row bbox when present (VLM)', () => {
    const row = { _backend: 'vlm', bbox: [5, 6, 40, 12], distributor_pn: 'C12345' };
    expect(rowHighlightBoxes(row, page)).toEqual([{ x: 5, y: 6, w: 40, h: 12 }]);
  });

  it('falls back to fuzzy token match when bbox is null', () => {
    const row = { _backend: 'flat', bbox: null, distributor_pn: 'C12345', quantity: 100 };
    const boxes = rowHighlightBoxes(row, page);
    expect(boxes).toContainEqual({ x: 10, y: 10, w: 30, h: 8 });
    expect(boxes).toContainEqual({ x: 50, y: 10, w: 12, h: 8 });
    expect(boxes).not.toContainEqual({ x: 0, y: 90, w: 20, h: 8 });
  });

  it('returns empty array when nothing matches', () => {
    const row = { _backend: 'flat', bbox: null, mpn: 'ZZZ' };
    expect(rowHighlightBoxes(row, page)).toEqual([]);
  });

  it('short numeric field value does not match a longer numeric token', () => {
    const p = { width: 100, height: 100, words: [
      { text: '100', x: 5, y: 5, w: 10, h: 8 },   // a different number
      { text: 'C12345', x: 20, y: 5, w: 30, h: 8 },
    ] };
    const row = { _backend: 'flat', bbox: null, quantity: 10, distributor_pn: 'C12345' };
    const boxes = rowHighlightBoxes(row, p);
    expect(boxes).toContainEqual({ x: 20, y: 5, w: 30, h: 8 });   // real PN matches
    expect(boxes).not.toContainEqual({ x: 5, y: 5, w: 10, h: 8 }); // qty 10 does NOT match 100
  });
});

describe('backendLabel', () => {
  it('maps known backends', () => {
    expect(backendLabel('vlm')).toBe('VLM');
    expect(backendLabel('grid')).toBe('OCR grid');
    expect(backendLabel('flat')).toBe('OCR');
    expect(backendLabel('???')).toBe('');
  });
});
