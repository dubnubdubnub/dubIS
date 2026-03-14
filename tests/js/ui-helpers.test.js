import { describe, it, expect } from 'vitest';
import { stockValueColor } from '../../js/ui-helpers.js';

describe('stockValueColor', () => {
  it('returns green when threshold is 0', () => {
    expect(stockValueColor(0, 0)).toBe('#3fb950');
    expect(stockValueColor(100, 0)).toBe('#3fb950');
  });

  it('returns green when threshold is negative', () => {
    expect(stockValueColor(5, -10)).toBe('#3fb950');
  });

  it('returns red-ish at ratio 0 (stock = 0)', () => {
    const color = stockValueColor(0, 100);
    // Should be the first stop: rgb(248,81,73) = #f85149
    expect(color).toBe('rgb(248,81,73)');
  });

  it('returns green at ratio 1 (stock = threshold)', () => {
    const color = stockValueColor(100, 100);
    // Should be the last stop: rgb(63,185,80) = #3fb950
    expect(color).toBe('rgb(63,185,80)');
  });

  it('clamps ratio above 1 to green', () => {
    const color = stockValueColor(200, 100);
    expect(color).toBe('rgb(63,185,80)');
  });

  it('interpolates between stops at midpoints', () => {
    // ratio = 0.5 → t = 1.5 → between stop[1] (orange) and stop[2] (yellow)
    const color = stockValueColor(50, 100);
    // Should be somewhere between orange and yellow
    expect(color).toMatch(/^rgb\(\d+,\d+,\d+\)$/);
  });

  it('returns orange-ish at ratio ~0.33', () => {
    // ratio = 1/3 → t = 1.0 → exactly at stop[1] (orange: 240,136,62)
    const color = stockValueColor(100, 300);
    expect(color).toBe('rgb(240,136,62)');
  });
});
