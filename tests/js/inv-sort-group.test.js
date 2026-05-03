import { describe, it, expect } from 'vitest';
import { nextScope } from '../../js/inventory/inv-sort-group.js';

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
