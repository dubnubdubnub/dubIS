import { describe, it, expect } from 'vitest';
import { isColorNear, channelDominant } from './e2e/visual/color.mjs';

describe('visual color predicates', () => {
  it('isColorNear matches within tolerance and rejects outside', () => {
    expect(isColorNear([100, 100, 100], [105, 98, 100], 6)).toBe(true);
    expect(isColorNear([100, 100, 100], [120, 100, 100], 6)).toBe(false);
    expect(isColorNear(null, [0, 0, 0], 6)).toBe(false);
  });

  it('channelDominant detects a bluish AA stroke but not the dark background', () => {
    expect(channelDominant([38, 55, 90], 2, 28, 60)).toBe(true);
    expect(channelDominant([13, 17, 23], 2, 28, 60)).toBe(false);
    expect(channelDominant(null, 2, 28, 60)).toBe(false);
  });
});
