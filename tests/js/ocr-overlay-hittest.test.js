import { describe, it, expect } from 'vitest';
import {
  normalizeRect, rectsIntersect, tokensInRect,
} from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-hittest.js';

describe('normalizeRect', () => {
  it('orders corners regardless of drag direction', () => {
    expect(normalizeRect({ x: 10, y: 20 }, { x: 4, y: 6 }))
      .toEqual({ left: 4, top: 6, right: 10, bottom: 20 });
    expect(normalizeRect({ x: 1, y: 2 }, { x: 3, y: 4 }))
      .toEqual({ left: 1, top: 2, right: 3, bottom: 4 });
  });
});

describe('rectsIntersect', () => {
  const a = { left: 0, top: 0, right: 10, bottom: 10 };
  it('detects overlap', () => {
    expect(rectsIntersect(a, { left: 5, top: 5, right: 15, bottom: 15 })).toBe(true);
  });
  it('detects edge-touch as overlap', () => {
    expect(rectsIntersect(a, { left: 10, top: 0, right: 20, bottom: 10 })).toBe(true);
  });
  it('rejects disjoint rectangles', () => {
    expect(rectsIntersect(a, { left: 11, top: 0, right: 20, bottom: 10 })).toBe(false);
    expect(rectsIntersect(a, { left: 0, top: 11, right: 10, bottom: 20 })).toBe(false);
  });
});

describe('tokensInRect', () => {
  const boxes = [
    { id: '0:w:0', left: 0, top: 0, right: 5, bottom: 5 },
    { id: '0:w:1', left: 20, top: 20, right: 30, bottom: 30 },
    { id: '0:w:2', left: 3, top: 3, right: 8, bottom: 8 },
  ];
  it('returns ids of intersecting tokens only', () => {
    const sel = { left: 0, top: 0, right: 6, bottom: 6 };
    expect(tokensInRect(sel, boxes)).toEqual(['0:w:0', '0:w:2']);
  });
  it('returns empty when nothing intersects', () => {
    const sel = { left: 50, top: 50, right: 60, bottom: 60 };
    expect(tokensInRect(sel, boxes)).toEqual([]);
  });
});
