import { describe, it, expect, vi } from 'vitest';

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

import { rowPrice, cheapestRow } from '../../js/inventory-modals.js';

describe('rowPrice', () => {
  const tiers = [{ qty: 1, price: 1.00 }, { qty: 100, price: 0.50 }];

  it('returns unit + extended price for the chosen tier', () => {
    expect(rowPrice(tiers, 100)).toEqual({ tier: { qty: 100, price: 0.50 }, unitPrice: 0.50, extPrice: 50 });
  });

  it('uses the low tier below the smallest break, ext = unit * qty', () => {
    expect(rowPrice(tiers, 1)).toEqual({ tier: { qty: 1, price: 1.00 }, unitPrice: 1.00, extPrice: 1.00 });
  });

  it('returns nulls when there are no prices', () => {
    expect(rowPrice([], 10)).toEqual({ tier: null, unitPrice: null, extPrice: null });
    expect(rowPrice(null, 10)).toEqual({ tier: null, unitPrice: null, extPrice: null });
  });
});

describe('cheapestRow', () => {
  it('returns the index of the lowest unit price', () => {
    expect(cheapestRow([{ unitPrice: 5 }, { unitPrice: 2 }, { unitPrice: 9 }])).toBe(1);
  });

  it('breaks ties toward the lowest index', () => {
    expect(cheapestRow([{ unitPrice: 2 }, { unitPrice: 2 }])).toBe(0);
  });

  it('ignores rows without a finite price', () => {
    expect(cheapestRow([{ unitPrice: null }, { unitPrice: 3 }, { unitPrice: undefined }])).toBe(1);
  });

  it('returns -1 when no row has a price', () => {
    expect(cheapestRow([{ unitPrice: null }, {}])).toBe(-1);
    expect(cheapestRow([])).toBe(-1);
  });
});
