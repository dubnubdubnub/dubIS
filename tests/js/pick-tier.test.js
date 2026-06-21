import { describe, it, expect, vi } from 'vitest';

// Mock side-effecting imports so the pure pickTier export can be loaded in isolation.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  Modal: vi.fn(() => ({ open: vi.fn(), close: vi.fn(), el: { classList: { contains: () => true } } })),
  linkPriceInputs: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));
vi.mock('../../js/undo-redo.js', () => ({
  UndoRedo: { register: vi.fn(), save: vi.fn(), popLast: vi.fn(), _undo: [] },
}));
vi.mock('../../js/store.js', () => ({ onInventoryUpdated: vi.fn() }));

import { pickTier } from '../../js/inventory-modals.js';

describe('pickTier', () => {
  const tiers = [{ qty: 1, price: 0.50 }, { qty: 10, price: 0.30 }, { qty: 100, price: 0.10 }];

  it('chooses the largest qty tier <= target', () => {
    expect(pickTier(tiers, 50)).toEqual({ qty: 10, price: 0.30 });
  });

  it('picks the tier on an exact-match target', () => {
    expect(pickTier(tiers, 100)).toEqual({ qty: 100, price: 0.10 });
    expect(pickTier(tiers, 10)).toEqual({ qty: 10, price: 0.30 });
  });

  it('falls back to the lowest tier when target is below the smallest qty', () => {
    expect(pickTier(tiers, 0.5)).toEqual({ qty: 1, price: 0.50 });
  });

  it('falls back to the lowest tier when target is null/0/undefined', () => {
    expect(pickTier(tiers, null)).toEqual({ qty: 1, price: 0.50 });
    expect(pickTier(tiers, 0)).toEqual({ qty: 1, price: 0.50 });
    expect(pickTier(tiers, undefined)).toEqual({ qty: 1, price: 0.50 });
  });

  it('returns null for empty or invalid prices', () => {
    expect(pickTier([], 50)).toBeNull();
    expect(pickTier(null, 50)).toBeNull();
    expect(pickTier(undefined, 50)).toBeNull();
    expect(pickTier('not-an-array', 50)).toBeNull();
  });

  it('handles unsorted input correctly', () => {
    const unsorted = [{ qty: 100, price: 0.10 }, { qty: 1, price: 0.50 }, { qty: 10, price: 0.30 }];
    expect(pickTier(unsorted, 50)).toEqual({ qty: 10, price: 0.30 });
    expect(pickTier(unsorted, null)).toEqual({ qty: 1, price: 0.50 });
  });
});
