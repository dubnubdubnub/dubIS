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

  it('grows the type number for longer payloads (within version 9)', () => {
    // Both payloads stay <= type 9 (8-bit char count), so this only certifies
    // that a bigger payload picks a bigger (still valid) version.
    const small = qrModules('a');
    const big = qrModules('x'.repeat(100));
    expect(big.length).toBeGreaterThan(small.length);
  });

  it('encodes a version >= 10 payload as a structurally valid matrix', () => {
    // ~200 bytes forces type 10, which requires a 16-bit byte-mode char count.
    // Before the fix this produced a non-decodable QR (8-bit count written for a
    // version-10 code). Assert the matrix is structurally valid: square, 4n+17,
    // boolean, type 10 (size 57), with intact finder patterns.
    const m = qrModules('x'.repeat(200));
    const size = m.length;
    expect((size - 17) % 4).toBe(0);
    const typeNumber = (size - 17) / 4;
    expect(typeNumber).toBe(10); // version 10 → 16-bit char count path
    m.forEach(row => {
      expect(row).toHaveLength(size);
      row.forEach(cell => expect(typeof cell).toBe('boolean'));
    });
    // Finder pattern centers remain dark.
    expect(m[3][3]).toBe(true);
    expect(m[size - 4][3]).toBe(true);
    expect(m[3][size - 4]).toBe(true);
  });

  it('throws on data too long for supported types', () => {
    expect(() => qrModules('x'.repeat(5000))).toThrow();
  });
});
