import { describe, it, expect } from 'vitest';
import { extractBomValue } from '../../js/matching.js';

describe('extractBomValue — resistor plain-number inference', () => {
  it('parses "620" as 620 ohms when refs indicate a resistor', () => {
    expect(extractBomValue({ refs: 'R2, R5', value: '620', desc: '' })).toBe(620);
  });

  it('parses "0.012" as 0.012 ohms for R-prefixed refs (shunt resistor case)', () => {
    expect(extractBomValue({ refs: 'R28, R29', value: '0.012', desc: '' })).toBe(0.012);
  });

  it('parses plain integers like "100", "200", "400"', () => {
    expect(extractBomValue({ refs: 'R15', value: '100', desc: '' })).toBe(100);
    expect(extractBomValue({ refs: 'R14', value: '200', desc: '' })).toBe(200);
    expect(extractBomValue({ refs: 'R3', value: '400', desc: '' })).toBe(400);
  });

  it('still parses EE-style values like 35k7 and 4k75', () => {
    expect(extractBomValue({ refs: 'R1', value: '35k7', desc: '' })).toBeCloseTo(35700);
    expect(extractBomValue({ refs: 'R9', value: '4k75', desc: '' })).toBeCloseTo(4750);
    expect(extractBomValue({ refs: 'R11', value: '12k1', desc: '' })).toBeCloseTo(12100);
    expect(extractBomValue({ refs: 'R10', value: '1k', desc: '' })).toBe(1000);
  });

  it('does NOT infer plain numbers for capacitor refs', () => {
    expect(extractBomValue({ refs: 'C1', value: '100', desc: '' })).toBeNull();
  });

  it('does NOT infer plain numbers for inductor refs', () => {
    expect(extractBomValue({ refs: 'L1', value: '100', desc: '' })).toBeNull();
  });

  it('does NOT infer plain numbers when refs indicate non-RCL parts', () => {
    expect(extractBomValue({ refs: 'U1', value: '100', desc: '' })).toBeNull();
    expect(extractBomValue({ refs: 'Q1', value: '100', desc: '' })).toBeNull();
    expect(extractBomValue({ refs: 'D1', value: '100', desc: '' })).toBeNull();
  });

  it('prefers existing parsing paths before falling back to plain-number inference', () => {
    expect(extractBomValue({ refs: 'R1', value: '1k', desc: '' })).toBe(1000);
  });

  it('returns null for non-numeric values regardless of refs', () => {
    expect(extractBomValue({ refs: 'R1', value: 'BZT52C5V6', desc: '' })).toBeNull();
    expect(extractBomValue({ refs: 'R1', value: 'abc', desc: '' })).toBeNull();
  });
});
