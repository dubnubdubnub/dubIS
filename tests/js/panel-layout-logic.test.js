import { describe, it, expect } from 'vitest';
import {
  PANEL_MIN_WIDTHS, flexFor, canDrag, rebaseWeights, clampDrag,
} from '../../js/panel-layout-logic.js';

/** CSS Flexbox §9.7 in miniature: what each grow item gets from `free` px when
 *  every basis is 0 and no min-width binds. Factors summing below 1 hand out only
 *  that fraction — the dead-strip rule. */
function distribute(factors, free) {
  const sum = factors.reduce((a, b) => a + b, 0);
  const share = sum < 1 ? free * sum : free;
  return factors.map(f => (sum === 0 ? 0 : share * f / sum));
}

describe('PANEL_MIN_WIDTHS', () => {
  it('is [import, inventory, bom] in authored px, matching css/panels/', () => {
    expect(PANEL_MIN_WIDTHS).toEqual([240, 300, 380]);
  });
});

describe('flexFor', () => {
  it('encodes a weight as a px-valued grow factor on a zero basis', () => {
    expect(flexFor(422)).toBe('422 1 0px');
  });

  it('clears to the stylesheet for an unrecorded panel', () => {
    expect(flexFor(null)).toBe('');
  });

  it('refuses a fractional weight — the value that brings the dead strip back', () => {
    // The old encoding: three fractions summing to 1.
    expect(() => flexFor(0.22)).toThrow(RangeError);
    expect(() => flexFor(0)).toThrow(RangeError);
    expect(() => flexFor(NaN)).toThrow(RangeError);
    expect(() => flexFor(Infinity)).toThrow(RangeError);
  });

  it('px weights fill the row for every subset of visible panels; fractions do not', () => {
    const px = [422, 678, 810];
    const fractions = px.map(w => w / (422 + 678 + 810));
    const free = 1910;
    for (const visible of [[0, 1, 2], [1, 2], [0, 1], [1]]) {
      const pick = (ws) => visible.map(i => ws[i]);
      const fill = distribute(pick(px), free).reduce((a, b) => a + b, 0);
      expect(fill).toBeCloseTo(free, 6);
    }
    // The reported case: hide import and the fraction encoding leaves ~22% empty.
    const survivors = distribute([fractions[1], fractions[2]], free);
    expect(free - survivors[0] - survivors[1]).toBeCloseTo(422, 6);
  });
});

describe('canDrag', () => {
  it('drags when both neighbours are visible', () => {
    expect(canDrag(0, [false, false, false])).toBe(true);
    expect(canDrag(1, [false, false, false])).toBe(true);
  });

  it('does not drag a divider beside a collapsed panel', () => {
    expect(canDrag(0, [true, false, false])).toBe(false);
    expect(canDrag(1, [false, false, true])).toBe(false);
  });

  it('a collapse on the far side does not disable the other divider', () => {
    expect(canDrag(1, [true, false, false])).toBe(true);
    expect(canDrag(0, [false, false, true])).toBe(true);
  });
});

describe('rebaseWeights', () => {
  it('visible panels take their measured widths', () => {
    expect(rebaseWeights([null, null, null], [300, 700, 600], [false, false, false]))
      .toEqual([300, 700, 600]);
  });

  it('never records a collapsed panel at its measured 0px', () => {
    expect(rebaseWeights([422, 678, 810], [0, 870, 1040], [true, false, false])[0])
      .not.toBe(0);
  });

  it('a collapsed panel with no weight stays on the stylesheet', () => {
    expect(rebaseWeights([null, null, null], [0, 900, 1010], [true, false, false]))
      .toEqual([null, 900, 1010]);
  });

  it('rescales a collapsed weight so reopening gives back its width', () => {
    // Dragged at 1910px of free space to [422, 678, 810]; hiding import grows the
    // survivors to [870, 1040]. Rebased into that row, import must keep the share
    // that makes it 422px again once it reopens with the others unchanged.
    const w = rebaseWeights([422, 678, 810], [0, 870.33, 1039.67], [true, false, false]);
    const reopened = distribute(/** @type {number[]} */ (w), 1910);
    expect(reopened[0]).toBeCloseTo(422, 6);
  });

  it('only visible panels that have a weight set the scale', () => {
    // bom is visible but never recorded (still on the stylesheet), so it cannot
    // say how far the row moved; inventory alone does.
    const w = rebaseWeights([500, 1000, null], [0, 1500, 600], [true, false, false]);
    expect(w).toEqual([750, 1500, 600]);
  });

  it('throws on misaligned inputs rather than guessing', () => {
    expect(() => rebaseWeights([1, 2], [1, 2, 3], [false, false, false])).toThrow(RangeError);
  });
});

describe('clampDrag', () => {
  it('moves the divider by dx and conserves the pair', () => {
    expect(clampDrag(400, 600, 50, 240, 300)).toEqual({ left: 450, right: 550 });
    expect(clampDrag(400, 600, -50, 240, 300)).toEqual({ left: 350, right: 650 });
  });

  it('holds the left side at its minimum', () => {
    expect(clampDrag(400, 600, -500, 240, 300)).toEqual({ left: 240, right: 760 });
  });

  it('holds the right side at its minimum', () => {
    expect(clampDrag(400, 600, 500, 240, 300)).toEqual({ left: 700, right: 300 });
  });

  it('changes nothing when the pair is already narrower than both minimums', () => {
    // A 1200px window at 200% zoom: no split is legal, so do not invent one.
    expect(clampDrag(250, 300, 80, 240, 380)).toEqual({ left: 250, right: 300 });
  });

  it('throws on non-finite input', () => {
    expect(() => clampDrag(400, 600, NaN, 240, 300)).toThrow(RangeError);
  });
});
