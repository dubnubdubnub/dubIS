import { describe, it, expect } from 'vitest';
import { canonicalizeUrl, emptyLineItem, validateLineItems, formatMatchBadge }
  from '../../js/import/mfg-direct/mfg-direct-logic.js';

describe('canonicalizeUrl', () => {
  it('adds https scheme', () => {
    expect(canonicalizeUrl('Example.com')).toBe('https://example.com');
  });
  it('strips trailing slash', () => {
    expect(canonicalizeUrl('https://example.com/')).toBe('https://example.com');
  });
});

describe('validateLineItems', () => {
  it('flags empty mpn', () => {
    const items = [{ ...emptyLineItem(), quantity: 5 }];
    expect(validateLineItems(items).length).toBeGreaterThan(0);
  });
  it('flags zero qty', () => {
    const items = [{ ...emptyLineItem(), mpn: 'X' }];
    expect(validateLineItems(items).length).toBeGreaterThan(0);
  });
  it('passes valid', () => {
    const items = [{ ...emptyLineItem(), mpn: 'X', quantity: 5 }];
    expect(validateLineItems(items)).toEqual([]);
  });
});

describe('formatMatchBadge', () => {
  it('definite', () => {
    expect(formatMatchBadge({ status: 'definite' }).cls).toBe('match-definite');
  });
  it('possible includes top candidate mpn', () => {
    const b = formatMatchBadge({ status: 'possible', candidates: [{ mpn: 'TMR2615' }] });
    expect(b.label).toContain('TMR2615');
  });
});
