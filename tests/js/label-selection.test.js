import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mocks ───────────────────────────────────────────────────────────────────
const showToast = vi.fn();
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToast(...a),
  escHtml: vi.fn(s => s || ''),
  Modal: vi.fn(),
}));

const api = vi.fn().mockResolvedValue(undefined);
vi.mock('../../js/api.js', () => ({
  api: (...a) => api(...a),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));

// Mutable mock store backing arrays
let mockInventory = [];
let mockBomResults = null;
vi.mock('../../js/store.js', () => ({
  store: {
    get inventory() { return mockInventory; },
    get bomResults() { return mockBomResults; },
  },
}));

// invPartKey is the same key helper the renderers use; mirror its real logic.
vi.mock('../../js/part-keys.js', () => ({
  invPartKey: (item) => {
    const lcsc = item.lcsc || '';
    if (lcsc && /^C/i.test(lcsc)) return lcsc;
    return item.mpn || item.digikey || item.pololu || item.mouser || '';
  },
}));

import {
  isLabelMode, enterLabelMode, exitLabelMode,
  select, deselect, toggleSelection, isSelected, clearSelection, selectedCount,
  selectPo, getSelectedItems,
  getTape, setTape,
  setPreviewHandler, createLabels,
} from '../../js/label-selection.js';
import { EventBus, Events } from '../../js/event-bus.js';

beforeEach(() => {
  // Reset state between tests
  if (isLabelMode()) exitLabelMode();
  clearSelection();
  setTape('6mm');
  setPreviewHandler(() => {});
  mockInventory = [];
  mockBomResults = null;
  showToast.mockClear();
  api.mockClear();
  EventBus._listeners = {};
});

describe('label mode', () => {
  it('starts disabled', () => {
    expect(isLabelMode()).toBe(false);
  });

  it('enterLabelMode emits LABEL_MODE(true)', () => {
    const spy = vi.fn();
    EventBus.on(Events.LABEL_MODE, spy);
    enterLabelMode();
    expect(isLabelMode()).toBe(true);
    expect(spy).toHaveBeenCalledWith(true);
  });

  it('exitLabelMode emits LABEL_MODE(false)', () => {
    enterLabelMode();
    const spy = vi.fn();
    EventBus.on(Events.LABEL_MODE, spy);
    exitLabelMode();
    expect(isLabelMode()).toBe(false);
    expect(spy).toHaveBeenCalledWith(false);
  });

  it('entering then exiting clears the selection', () => {
    enterLabelMode();
    select('C1');
    select('C2');
    expect(selectedCount()).toBe(2);
    exitLabelMode();
    expect(selectedCount()).toBe(0);
  });

  it('enter is idempotent (no duplicate emit)', () => {
    enterLabelMode();
    const spy = vi.fn();
    EventBus.on(Events.LABEL_MODE, spy);
    enterLabelMode();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('selection', () => {
  it('select / isSelected / selectedCount', () => {
    select('C1');
    expect(isSelected('C1')).toBe(true);
    expect(selectedCount()).toBe(1);
  });

  it('select ignores duplicates', () => {
    select('C1');
    select('C1');
    expect(selectedCount()).toBe(1);
  });

  it('deselect removes a key', () => {
    select('C1');
    deselect('C1');
    expect(isSelected('C1')).toBe(false);
    expect(selectedCount()).toBe(0);
  });

  it('toggleSelection adds then removes', () => {
    toggleSelection('C1');
    expect(isSelected('C1')).toBe(true);
    toggleSelection('C1');
    expect(isSelected('C1')).toBe(false);
  });

  it('clearSelection empties the set', () => {
    select('C1');
    select('C2');
    clearSelection();
    expect(selectedCount()).toBe(0);
  });

  it('emits LABEL_SELECTION_CHANGED with the new count', () => {
    const spy = vi.fn();
    EventBus.on(Events.LABEL_SELECTION_CHANGED, spy);
    select('C1');
    expect(spy).toHaveBeenLastCalledWith(1);
    select('C2');
    expect(spy).toHaveBeenLastCalledWith(2);
  });
});

describe('selectPo', () => {
  it('adds every inventory item whose po_history includes the PO id', () => {
    mockInventory = [
      { lcsc: 'C1', po_history: ['po_a', 'po_b'] },
      { lcsc: 'C2', po_history: ['po_b'] },
      { mpn: 'M3', po_history: ['po_a'] },
    ];
    const added = selectPo('po_a');
    expect(added).toBe(2);
    expect(isSelected('C1')).toBe(true);
    expect(isSelected('M3')).toBe(true);
    expect(isSelected('C2')).toBe(false);
  });
});

describe('getSelectedItems', () => {
  it('resolves keys to full inventory items', () => {
    const c1 = { lcsc: 'C1', description: 'one' };
    const c2 = { lcsc: 'C2', description: 'two' };
    mockInventory = [c1, c2, { lcsc: 'C3' }];
    select('C1');
    select('C2');
    const items = getSelectedItems();
    expect(items).toHaveLength(2);
    expect(items).toContain(c1);
    expect(items).toContain(c2);
  });

  it('resolves keys from matched BOM rows when not in inventory', () => {
    const inv = { mpn: 'BOMPART', description: 'bom inv' };
    mockInventory = [];
    mockBomResults = [{ bom: { mpn: 'BOMPART' }, inv }];
    select('BOMPART');
    const items = getSelectedItems();
    expect(items).toEqual([inv]);
  });

  it('does not duplicate an item present in both inventory and BOM', () => {
    const item = { lcsc: 'C1' };
    mockInventory = [item];
    mockBomResults = [{ bom: { lcsc: 'C1' }, inv: item }];
    select('C1');
    expect(getSelectedItems()).toHaveLength(1);
  });
});

describe('tape width', () => {
  it('defaults to 6mm', () => {
    expect(getTape()).toBe('6mm');
  });

  it('setTape updates the value', () => {
    setTape('12mm');
    expect(getTape()).toBe('12mm');
  });

  it('setTape rejects invalid widths', () => {
    expect(() => setTape('9mm')).toThrow();
  });
});

describe('preview handler', () => {
  it('createLabels invokes handler with selected items + tape', () => {
    const handler = vi.fn();
    setPreviewHandler(handler);
    const c1 = { lcsc: 'C1' };
    mockInventory = [c1];
    select('C1');
    setTape('12mm');
    createLabels();
    expect(handler).toHaveBeenCalledWith([c1], '12mm');
  });

  it('createLabels toasts and skips handler when nothing selected', () => {
    const handler = vi.fn();
    setPreviewHandler(handler);
    createLabels();
    expect(handler).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalled();
  });

  it('setPreviewHandler rejects non-functions', () => {
    expect(() => setPreviewHandler(null)).toThrow();
  });
});
