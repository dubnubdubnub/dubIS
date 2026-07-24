// tests/js/format-money.test.mjs
import { describe, it, expect } from 'vitest';
import { formatMoney } from '../../js/ui-helpers.js';

describe('formatMoney', () => {
  it('formats finite numbers as "$" + 2 decimals', () => {
    expect(formatMoney(1.5)).toBe('$1.50');
    expect(formatMoney(0)).toBe('$0.00');
    expect(formatMoney(1234.5)).toBe('$1234.50');
  });

  it('defaults to em-dash fallback for null/undefined/NaN', () => {
    expect(formatMoney(null)).toBe('—');
    expect(formatMoney(undefined)).toBe('—');
    expect(formatMoney(NaN)).toBe('—');
  });

  it('honors a custom fallback', () => {
    expect(formatMoney(null, { fallback: '' })).toBe('');
    expect(formatMoney(undefined, { fallback: 'n/a' })).toBe('n/a');
  });

  it('rounds like toFixed(2)', () => {
    expect(formatMoney(1.005)).toBe('$' + (1.005).toFixed(2));
    expect(formatMoney(2.999)).toBe('$3.00');
  });
});
