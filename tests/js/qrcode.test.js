import { describe, it, expect } from 'vitest';
import { qrModules } from '../../js/vendor/qrcode.js';

describe('qrModules', () => {
  it('produces a square boolean matrix sized 4n+17', () => {
    const m = qrModules('http://192.168.1.5:8770/scan?s=abc123');
    expect(Array.isArray(m)).toBe(true);
    const size = m.length;
    expect((size - 17) % 4).toBe(0);
    m.forEach(row => {
      expect(row).toHaveLength(size);
      row.forEach(cell => expect(typeof cell).toBe('boolean'));
    });
  });

  it('has the three finder patterns (dark corners)', () => {
    const m = qrModules('hello');
    // Finder patterns are dark at their centers (3,3), (size-4,3), (3,size-4)
    const size = m.length;
    expect(m[3][3]).toBe(true);
    expect(m[size - 4][3]).toBe(true);
    expect(m[3][size - 4]).toBe(true);
  });

  it('grows the type number for longer payloads', () => {
    const small = qrModules('a');
    const big = qrModules('x'.repeat(200));
    expect(big.length).toBeGreaterThan(small.length);
  });

  it('throws on data too long for supported types', () => {
    expect(() => qrModules('x'.repeat(5000))).toThrow();
  });
});
