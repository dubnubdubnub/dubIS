import { describe, it, expect } from 'vitest';
import { stockValueColor, vendorIconSrc } from '../../js/ui-helpers.js';

describe('vendorIconSrc', () => {
  it('returns empty string for empty/missing path', () => {
    expect(vendorIconSrc('')).toBe('');
    expect(vendorIconSrc(null)).toBe('');
    expect(vendorIconSrc(undefined)).toBe('');
  });

  it('prefixes a bare data-relative filename with data/', () => {
    expect(vendorIconSrc('lcsc-icon.ico')).toBe('data/lcsc-icon.ico');
  });

  it('converts Windows backslashes and prefixes data/', () => {
    expect(vendorIconSrc('sources\\favicons\\abc.png')).toBe('data/sources/favicons/abc.png');
  });

  it('leaves an already-prefixed data/ path untouched', () => {
    expect(vendorIconSrc('data/sources/favicons/abc.png')).toBe('data/sources/favicons/abc.png');
  });

  it('passes through http(s), data, blob and file URIs', () => {
    expect(vendorIconSrc('https://x.com/f.ico')).toBe('https://x.com/f.ico');
    expect(vendorIconSrc('data:image/png;base64,AAAA')).toBe('data:image/png;base64,AAAA');
    expect(vendorIconSrc('blob:abc')).toBe('blob:abc');
  });

  it('passes through absolute filesystem paths', () => {
    expect(vendorIconSrc('C:\\Users\\x\\f.png')).toBe('C:/Users/x/f.png');
    expect(vendorIconSrc('/var/data/f.png')).toBe('/var/data/f.png');
  });
});

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
