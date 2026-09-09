// @vitest-environment jsdom
/**
 * Unit tests for js/hover-affordance.js — the pure half of the fix for
 * "moving the cursor to the popup makes it disappear".
 *
 * The bug was a hide path that could only ask `mouseout`/`relatedTarget` where
 * the pointer went. In the few px of gap between a trigger and its popup the
 * answer is "neither" — some unrelated row — so the popup was torn down before
 * the pointer arrived. `inHoverCorridor` is the question the hide path asks
 * instead, and these tests pin its geometry: every rect is authored px (see the
 * root-zoom seam in js/ui-zoom.js), so the maths here is zoom-independent.
 */
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));

const mod = await import('../../js/hover-affordance.js');
const { padBox, boxContains, bridgeBox, inHoverCorridor, copyText } = mod;

/** A trigger cell and a popup anchored 6px below it, the app's real geometry. */
const TRIGGER = { left: 100, top: 200, right: 180, bottom: 213 };
const POP_BELOW = { left: 100, top: 219, right: 520, bottom: 299 };
/** The same popup flipped above the trigger (near the bottom of the window). */
const POP_ABOVE = { left: 100, top: 114, right: 520, bottom: 194 };

describe('padBox', () => {
  it('grows every edge and does not mutate its input', () => {
    const src = { left: 10, top: 20, right: 30, bottom: 40 };
    expect(padBox(src, 5)).toEqual({ left: 5, top: 15, right: 35, bottom: 45 });
    expect(src).toEqual({ left: 10, top: 20, right: 30, bottom: 40 });
  });
});

describe('boxContains', () => {
  it('is inclusive of the edges', () => {
    expect(boxContains(TRIGGER, 100, 200)).toBe(true);
    expect(boxContains(TRIGGER, 180, 213)).toBe(true);
  });
  it('rejects points outside and a null box', () => {
    expect(boxContains(TRIGGER, 99, 200)).toBe(false);
    expect(boxContains(null, 100, 200)).toBe(false);
  });
});

describe('bridgeBox', () => {
  it('spans the gap under the trigger when the popup is below', () => {
    expect(bridgeBox(TRIGGER, POP_BELOW)).toEqual({
      left: 100, top: 213, right: 520, bottom: 219,
    });
  });

  it('spans the gap above the trigger when the popup is flipped above', () => {
    expect(bridgeBox(TRIGGER, POP_ABOVE)).toEqual({
      left: 100, top: 194, right: 520, bottom: 200,
    });
  });

  it('widens to the union of both boxes so a diagonal approach stays inside', () => {
    // Popup clamped to the left of a right-edge trigger: the band must cover
    // both, or a pointer cutting the corner falls out of the corridor.
    const rightTrigger = { left: 900, top: 200, right: 980, bottom: 213 };
    const clampedPop = { left: 620, top: 219, right: 1040, bottom: 299 };
    expect(bridgeBox(rightTrigger, clampedPop)).toEqual({
      left: 620, top: 213, right: 1040, bottom: 219,
    });
  });

  it('is null when the boxes overlap vertically (no gap to cross)', () => {
    expect(bridgeBox(TRIGGER, { left: 100, top: 205, right: 520, bottom: 300 })).toBeNull();
  });

  it('is null when the popup is flush against the trigger', () => {
    expect(bridgeBox(TRIGGER, { left: 100, top: 213, right: 520, bottom: 293 })).toBeNull();
  });

  it('is null for a missing box', () => {
    expect(bridgeBox(TRIGGER, null)).toBeNull();
    expect(bridgeBox(null, POP_BELOW)).toBeNull();
  });
});

describe('inHoverCorridor', () => {
  it('holds while the pointer is on the trigger', () => {
    expect(inHoverCorridor(140, 206, TRIGGER, POP_BELOW)).toBe(true);
  });

  it('holds in the gap — the case mouseout/relatedTarget cannot answer', () => {
    expect(inHoverCorridor(140, 216, TRIGGER, POP_BELOW)).toBe(true);
  });

  it('holds once the pointer is on the popup', () => {
    expect(inHoverCorridor(300, 260, TRIGGER, POP_BELOW)).toBe(true);
  });

  it('holds across every step of a straight trigger→popup traversal', () => {
    for (let y = 206; y <= 260; y += 1) {
      expect(inHoverCorridor(140, y, TRIGGER, POP_BELOW),
        `pointer at y=${y} should still be inside the corridor`).toBe(true);
    }
  });

  it('holds for a diagonal traversal toward the popup Copy button', () => {
    // Trigger at (140, 206) → the Copy button at the popup's right edge.
    const steps = 20;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const x = 140 + t * (490 - 140);
      const y = 206 + t * (255 - 206);
      expect(inHoverCorridor(x, y, TRIGGER, POP_BELOW),
        `diagonal step ${i} at (${x.toFixed(1)}, ${y.toFixed(1)})`).toBe(true);
    }
  });

  it('holds for the flipped-above layout too', () => {
    for (let y = 150; y <= 206; y += 1) {
      expect(inHoverCorridor(140, y, TRIGGER, POP_ABOVE),
        `pointer at y=${y} should be inside the flipped corridor`).toBe(true);
    }
  });

  it('releases when the pointer leaves for somewhere unrelated', () => {
    expect(inHoverCorridor(140, 400, TRIGGER, POP_BELOW)).toBe(false);   // below the popup
    expect(inHoverCorridor(140, 150, TRIGGER, POP_BELOW)).toBe(false);   // above the trigger
    expect(inHoverCorridor(700, 260, TRIGGER, POP_BELOW)).toBe(false);   // right of both
  });

  it('releases when the pointer is beside the trigger but level with it', () => {
    // Same row, a different cell: the corridor must not swallow the whole row.
    expect(inHoverCorridor(400, 206, TRIGGER, POP_BELOW)).toBe(false);
  });

  it('is false before the first pointer sample (NaN) rather than throwing', () => {
    expect(inHoverCorridor(NaN, NaN, TRIGGER, POP_BELOW)).toBe(false);
  });

  it('is false when either box is missing', () => {
    expect(inHoverCorridor(140, 216, null, POP_BELOW)).toBe(false);
    expect(inHoverCorridor(140, 216, TRIGGER, null)).toBe(false);
  });

  it('honours a custom pad', () => {
    // 40px below the popup: outside with the default pad, inside with pad 50.
    expect(inHoverCorridor(300, 339, TRIGGER, POP_BELOW)).toBe(false);
    expect(inHoverCorridor(300, 339, TRIGGER, POP_BELOW, 50)).toBe(true);
  });
});

describe('copyText', () => {
  it('uses navigator.clipboard when available', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    expect(await copyText('C429942')).toBe(true);
    expect(writeText).toHaveBeenCalledWith('C429942');
    vi.unstubAllGlobals();
  });

  it('refuses empty input without touching the clipboard', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    expect(await copyText('')).toBe(false);
    expect(await copyText(null)).toBe(false);
    expect(await copyText(undefined)).toBe(false);
    expect(writeText).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('falls back to execCommand when the Clipboard API rejects', async () => {
    const writeText = vi.fn(async () => { throw new Error('denied'); });
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const execCommand = vi.fn(() => true);
    // @ts-ignore — jsdom does not implement execCommand
    document.execCommand = execCommand;
    expect(await copyText('fallback')).toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
    vi.unstubAllGlobals();
  });
});
