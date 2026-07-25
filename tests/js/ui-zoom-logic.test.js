import { describe, it, expect } from 'vitest';
import {
  ZOOM_STEPS, clampToStep, stepZoom, zoomIndex, zoomFromIndex,
  zoomPercent, normalizePersistedZoom, viewportFor, scaleRect,
} from '../../js/ui-zoom-logic.js';

describe('ZOOM_STEPS', () => {
  it('is the agreed ladder, ascending, containing exactly one 100% rung', () => {
    expect(ZOOM_STEPS).toEqual([0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2]);
    expect([...ZOOM_STEPS].sort((a, b) => a - b)).toEqual(ZOOM_STEPS);
    expect(ZOOM_STEPS.filter(z => z === 1)).toHaveLength(1);
  });
});

describe('stepZoom', () => {
  it('walks exactly one rung in each direction', () => {
    expect(stepZoom(1, 1)).toBe(1.1);
    expect(stepZoom(1, -1)).toBe(0.9);
    expect(stepZoom(0.67, 1)).toBe(0.75);
    expect(stepZoom(1.75, 1)).toBe(2);
  });

  it('clamps at both ends without wrapping', () => {
    expect(stepZoom(2, 1)).toBe(2);
    expect(stepZoom(0.5, -1)).toBe(0.5);
  });

  it('snaps an off-ladder value onto the ladder before stepping', () => {
    expect(stepZoom(0.83, 1)).toBe(0.9);   // 0.83 snaps to 0.8, then up
    expect(stepZoom(0.83, -1)).toBe(0.75); // 0.83 snaps to 0.8, then down
  });
});

describe('clampToStep', () => {
  it('returns the nearest ladder value', () => {
    expect(clampToStep(0.81)).toBe(0.8);
    expect(clampToStep(1.4)).toBe(1.5);
    expect(clampToStep(0.1)).toBe(0.5);
    expect(clampToStep(99)).toBe(2);
  });

  it('is exact for values already on the ladder', () => {
    for (const z of ZOOM_STEPS) expect(clampToStep(z)).toBe(z);
  });

  it('throws on a non-finite argument — a programming error, not user data', () => {
    expect(() => clampToStep(NaN)).toThrow(TypeError);
    expect(() => clampToStep(Infinity)).toThrow(TypeError);
    // @ts-expect-error deliberate misuse
    expect(() => clampToStep('big')).toThrow(TypeError);
    // @ts-expect-error deliberate misuse
    expect(() => clampToStep(undefined)).toThrow(TypeError);
  });
});

describe('zoomIndex / zoomFromIndex', () => {
  it('round-trips every ladder value', () => {
    for (const z of ZOOM_STEPS) expect(zoomFromIndex(zoomIndex(z))).toBe(z);
  });

  it('round-trips every index', () => {
    for (let i = 0; i < ZOOM_STEPS.length; i++) expect(zoomIndex(zoomFromIndex(i))).toBe(i);
  });

  it('clamps an out-of-range index rather than returning undefined', () => {
    expect(zoomFromIndex(-5)).toBe(0.5);
    expect(zoomFromIndex(500)).toBe(2);
  });

  it('puts 100% in the middle of the ladder', () => {
    expect(zoomIndex(1)).toBe(5);
  });
});

describe('zoomPercent', () => {
  it('renders integer percentages', () => {
    expect(zoomPercent(1)).toBe(100);
    expect(zoomPercent(0.67)).toBe(67);
    expect(zoomPercent(1.25)).toBe(125);
    expect(zoomPercent(0.5)).toBe(50);
    expect(zoomPercent(2)).toBe(200);
  });
});

describe('normalizePersistedZoom', () => {
  it('accepts a valid stored value', () => {
    expect(normalizePersistedZoom(0.8)).toBe(0.8);
    expect(normalizePersistedZoom(2)).toBe(2);
  });

  it('falls back to 100% for missing or unusable data instead of throwing', () => {
    for (const bad of [undefined, null, 'abc', '', {}, [], true, NaN, Infinity, 0, -1]) {
      expect(normalizePersistedZoom(bad)).toBe(1);
    }
  });

  it('snaps an out-of-range or off-ladder stored number onto the ladder', () => {
    expect(normalizePersistedZoom(99)).toBe(2);
    expect(normalizePersistedZoom(0.01)).toBe(0.5);
    expect(normalizePersistedZoom(1.4)).toBe(1.5);
  });

  it('accepts a numeric string, since hand-edited JSON can quote numbers', () => {
    expect(normalizePersistedZoom('0.8')).toBe(0.8);
  });
});

describe('viewportFor', () => {
  it('divides window dimensions by the zoom factor', () => {
    expect(viewportFor(1600, 900, 1)).toEqual({ w: 1600, h: 900 });
    expect(viewportFor(1600, 900, 0.8)).toEqual({ w: 2000, h: 1125 });
    expect(viewportFor(1600, 900, 2)).toEqual({ w: 800, h: 450 });
  });

  it('treats a zero or non-finite zoom as 1 rather than dividing by zero', () => {
    expect(viewportFor(1600, 900, 0)).toEqual({ w: 1600, h: 900 });
    expect(viewportFor(1600, 900, NaN)).toEqual({ w: 1600, h: 900 });
    // @ts-expect-error deliberate misuse
    expect(viewportFor(1600, 900, undefined)).toEqual({ w: 1600, h: 900 });
  });

  it('zooming out yields a larger usable viewport — the point of the feature', () => {
    const at100 = viewportFor(1280, 800, 1);
    const at80 = viewportFor(1280, 800, 0.8);
    expect(at80.w).toBeGreaterThan(at100.w);
    expect(at80.h).toBeGreaterThan(at100.h);
  });
});

describe('scaleRect', () => {
  const rect = { left: 100, top: 50, right: 300, bottom: 90, width: 200, height: 40 };

  it('scales every edge', () => {
    expect(scaleRect(rect, 2)).toEqual({
      left: 200, top: 100, right: 600, bottom: 180, width: 400, height: 80,
    });
  });

  it('converts a post-zoom rect back to authored px with 1/zoom', () => {
    // A trigger authored at left:600 reads back as 300 at zoom 0.5 (verified
    // against Chromium); scaling by 1/0.5 must recover 600.
    expect(scaleRect({ ...rect, left: 300 }, 1 / 0.5).left).toBe(600);
    expect(scaleRect({ ...rect, left: 1200 }, 1 / 2).left).toBe(600);
  });

  it('is the identity at factor 1, so zoom-unaware callers are unaffected', () => {
    expect(scaleRect(rect, 1)).toEqual(rect);
  });

  it('round-trips through a factor and its inverse', () => {
    expect(scaleRect(scaleRect(rect, 0.8), 1 / 0.8)).toEqual(rect);
  });

  it('falls back to identity for a nonsensical factor rather than producing NaN', () => {
    expect(scaleRect(rect, 0)).toEqual(rect);
    expect(scaleRect(rect, NaN)).toEqual(rect);
  });

  it('returns a plain object, not a live DOMRect', () => {
    const out = scaleRect(rect, 2);
    expect(Object.keys(out).sort())
      .toEqual(['bottom', 'height', 'left', 'right', 'top', 'width']);
  });
});
