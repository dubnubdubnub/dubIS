import { describe, it, expect } from 'vitest';
import { nextScope, sortPartsBy } from '../../js/inventory/inv-sort-group.js';

describe('nextScope', () => {
  it('cycles subsection → section → global → null at groupLevel=0', () => {
    expect(nextScope(0, null)).toBe('subsection');
    expect(nextScope(0, 'subsection')).toBe('section');
    expect(nextScope(0, 'section')).toBe('global');
    expect(nextScope(0, 'global')).toBe(null);
  });

  it('cycles section → global → null at groupLevel=1', () => {
    expect(nextScope(1, null)).toBe('section');
    expect(nextScope(1, 'section')).toBe('global');
    expect(nextScope(1, 'global')).toBe(null);
  });

  it('cycles global → null at groupLevel=2', () => {
    expect(nextScope(2, null)).toBe('global');
    expect(nextScope(2, 'global')).toBe(null);
  });

  it('coerces invalid current scope back to first scope of the level', () => {
    expect(nextScope(1, 'subsection')).toBe('section');
    expect(nextScope(2, 'subsection')).toBe('global');
    expect(nextScope(2, 'section')).toBe('global');
  });
});

const SAMPLE = [
  { mpn: 'BBB', qty: 5,  unit_price: 0.10, description: 'Beta',  lcsc: 'C2' },
  { mpn: 'AAA', qty: 20, unit_price: 0.50, description: 'Alpha', lcsc: 'C1' },
  { mpn: 'CCC', qty: 1,  unit_price: 5.00, description: 'Gamma', lcsc: 'C3' },
];

describe('sortPartsBy', () => {
  it('returns input unchanged when column is null', () => {
    expect(sortPartsBy(SAMPLE, null)).toEqual(SAMPLE);
  });

  it('sorts mpn ascending (A→Z)', () => {
    const out = sortPartsBy(SAMPLE, 'mpn');
    expect(out.map(p => p.mpn)).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('sorts description ascending', () => {
    const out = sortPartsBy(SAMPLE, 'description');
    expect(out.map(p => p.description)).toEqual(['Alpha', 'Beta', 'Gamma']);
  });

  it('sorts qty descending', () => {
    const out = sortPartsBy(SAMPLE, 'qty');
    expect(out.map(p => p.qty)).toEqual([20, 5, 1]);
  });

  it('sorts unit_price descending', () => {
    const out = sortPartsBy(SAMPLE, 'unit_price');
    expect(out.map(p => p.unit_price)).toEqual([5.00, 0.50, 0.10]);
  });

  it('sorts total value (qty * unit_price) descending', () => {
    const out = sortPartsBy(SAMPLE, 'value');
    // values: 5*0.10=0.5, 20*0.50=10, 1*5=5
    expect(out.map(p => p.mpn)).toEqual(['AAA', 'CCC', 'BBB']);
  });

  it('does not mutate the input array', () => {
    const copy = SAMPLE.slice();
    sortPartsBy(SAMPLE, 'qty');
    expect(SAMPLE).toEqual(copy);
  });

  it('treats missing numeric fields as 0 and sorts last in desc', () => {
    const parts = [
      { mpn: 'A', qty: 5 },
      { mpn: 'B' },
      { mpn: 'C', qty: 10 },
    ];
    const out = sortPartsBy(parts, 'qty');
    expect(out.map(p => p.mpn)).toEqual(['C', 'A', 'B']);
  });

  it('treats missing/empty strings as last in asc', () => {
    const parts = [
      { mpn: '' },
      { mpn: 'B' },
      { mpn: 'A' },
    ];
    const out = sortPartsBy(parts, 'mpn');
    expect(out.map(p => p.mpn)).toEqual(['A', 'B', '']);
  });
});
