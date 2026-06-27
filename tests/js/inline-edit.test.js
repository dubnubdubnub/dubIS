// @vitest-environment jsdom
/**
 * tests/js/inline-edit.test.js
 *
 * Unit tests for js/inventory/inv-inline-edit.js.
 * Tests the core edit-state machine: enter edit, commit, cancel, guard.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock all dependencies before importing the module under test ───────────

vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: (s) => String(s || ''),
}));

vi.mock('../../js/undo-redo.js', () => ({
  UndoRedo: {
    save: vi.fn(),
    popLast: vi.fn(),
  },
}));

vi.mock('../../js/store.js', () => ({
  onInventoryUpdated: vi.fn(),
  store: {
    links: {
      get linkingMode() { return mockLinkingMode; },
    },
  },
  getThreshold: vi.fn(() => 50),
}));

vi.mock('../../js/inventory/inv-events.js', () => ({
  isFlyoutDragActive: () => mockFlyoutDragActive,
}));

vi.mock('../../js/part-keys.js', () => ({
  invPartKey: (item) => item.lcsc || item.mpn || 'UNKNOWN',
}));

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
}));

// ── Module-level mutable guard flags (see mock store.js above) ────────────

let mockLinkingMode = false;
let mockFlyoutDragActive = false;

// ── Lazy import after mocks are registered ─────────────────────────────────

let activateInlineEdit, cancelActiveInlineEdit;
let api, showToast, UndoRedo, onInventoryUpdated;

beforeEach(async () => {
  // Reset guard flags
  mockLinkingMode = false;
  mockFlyoutDragActive = false;

  // Dynamic import so Vitest uses the already-registered vi.mock() stubs
  const mod = await import('../../js/inventory/inv-inline-edit.js');
  activateInlineEdit    = mod.activateInlineEdit;
  cancelActiveInlineEdit = mod.cancelActiveInlineEdit;

  const apiMod = await import('../../js/api.js');
  api = apiMod.api;

  const uiMod = await import('../../js/ui-helpers.js');
  showToast = uiMod.showToast;

  const undoMod = await import('../../js/undo-redo.js');
  UndoRedo = undoMod.UndoRedo;

  const storeMod = await import('../../js/store.js');
  onInventoryUpdated = storeMod.onInventoryUpdated;

  // Clear call history on each test
  vi.clearAllMocks();

  // Default api mock: returns a truthy inventory array
  api.mockResolvedValue([{ lcsc: 'C1', qty: 5, unit_price: 1.0, ext_price: 5.0 }]);
});

// ── Helpers ────────────────────────────────────────────────────────────────

/** Build a minimal .inv-part-row DOM element with .part-qty and .part-unit-price cells. */
function buildRow(item) {
  const row = document.createElement('div');
  row.className = 'inv-part-row';

  const qtyCell = document.createElement('span');
  qtyCell.className = 'part-qty';
  qtyCell.textContent = String(item.qty);
  row.appendChild(qtyCell);

  const priceCell = document.createElement('span');
  priceCell.className = 'part-unit-price';
  priceCell.textContent = item.unit_price > 0 ? '$' + item.unit_price.toFixed(2) : '—';
  row.appendChild(priceCell);

  document.body.appendChild(row);
  return row;
}

/** Dispatch a dblclick event (bubbles, non-cancelable unless prevented). */
function dblclick(el) {
  el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
}

/** Fire a keydown event on an element. */
function keydown(el, key) {
  el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('activateInlineEdit — qty cell', () => {
  let item, row, qtyCell;

  beforeEach(() => {
    item = { lcsc: 'C42', mpn: 'RES-42', qty: 99, unit_price: 0.5, ext_price: 49.5 };
    row = buildRow(item);
    activateInlineEdit(row, item);
    qtyCell = row.querySelector('.part-qty');
  });

  afterEach(() => {
    row.remove();
  });

  it('dblclick on .part-qty replaces content with an input prefilled with current qty', () => {
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    expect(input).not.toBeNull();
    expect(input.type).toBe('number');
    expect(input.value).toBe('99');
  });

  it('input has aria-label "Edit quantity"', () => {
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    expect(input.getAttribute('aria-label')).toBe('Edit quantity');
  });

  it('Enter calls adjust_part with set + new qty and calls onInventoryUpdated', async () => {
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    input.value = '50';
    keydown(input, 'Enter');
    // Commit is async; wait for microtasks
    await new Promise(r => setTimeout(r, 0));

    expect(api).toHaveBeenCalledWith('adjust_part', 'set', 'C42', 50, 'inline-edit');
    expect(onInventoryUpdated).toHaveBeenCalledTimes(1);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('C42'));
  });

  it('Enter calls UndoRedo.save before api', async () => {
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    input.value = '75';

    let savedBeforeApi = false;
    api.mockImplementation(() => {
      savedBeforeApi = UndoRedo.save.mock.calls.length > 0;
      return Promise.resolve([{ lcsc: 'C42', qty: 75, unit_price: 0.5, ext_price: 37.5 }]);
    });

    keydown(input, 'Enter');
    await new Promise(r => setTimeout(r, 0));

    expect(savedBeforeApi).toBe(true);
    expect(UndoRedo.save).toHaveBeenCalledWith('adjust', expect.objectContaining({
      _undoType: 'adjust',
      adjType: 'set',
      qty: 75,
    }));
  });

  it('Esc restores original content (no api call)', async () => {
    const origText = qtyCell.textContent;
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    input.value = '1000';
    keydown(input, 'Escape');
    await new Promise(r => setTimeout(r, 0));

    expect(api).not.toHaveBeenCalledWith('adjust_part', expect.anything());
    expect(qtyCell.querySelector('input')).toBeNull();
    expect(qtyCell.textContent).toBe(origText);
  });

  it('api failure pops undo entry and restores cell', async () => {
    api.mockResolvedValueOnce(undefined); // falsy = failure
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    input.value = '5';
    keydown(input, 'Enter');
    await new Promise(r => setTimeout(r, 0));

    expect(UndoRedo.popLast).toHaveBeenCalledTimes(1);
    expect(qtyCell.querySelector('input')).toBeNull();
    expect(onInventoryUpdated).not.toHaveBeenCalled();
  });

  it('invalid (negative) qty shows toast and restores without calling api', async () => {
    dblclick(qtyCell);
    const input = qtyCell.querySelector('input');
    input.value = '-5';
    keydown(input, 'Enter');
    await new Promise(r => setTimeout(r, 0));

    expect(api).not.toHaveBeenCalledWith('adjust_part', expect.anything());
    expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid/i));
    expect(qtyCell.querySelector('input')).toBeNull();
  });

  it('single-click does NOT enter edit mode', () => {
    qtyCell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(qtyCell.querySelector('input')).toBeNull();
  });
});

describe('activateInlineEdit — unit-price cell', () => {
  let item, row, priceCell;

  beforeEach(() => {
    item = { lcsc: 'C99', mpn: 'CAP-99', qty: 10, unit_price: 2.5, ext_price: 25.0 };
    row = buildRow(item);
    activateInlineEdit(row, item);
    priceCell = row.querySelector('.part-unit-price');
  });

  afterEach(() => {
    row.remove();
  });

  it('dblclick on .part-unit-price replaces content with input prefilled with raw price', () => {
    dblclick(priceCell);
    const input = priceCell.querySelector('input');
    expect(input).not.toBeNull();
    expect(input.value).toBe('2.5');
  });

  it('input has aria-label "Edit unit price"', () => {
    dblclick(priceCell);
    const input = priceCell.querySelector('input');
    expect(input.getAttribute('aria-label')).toBe('Edit unit price');
  });

  it('Enter calls update_part_price with new unit price and calls onInventoryUpdated', async () => {
    dblclick(priceCell);
    const input = priceCell.querySelector('input');
    input.value = '3.75';
    keydown(input, 'Enter');
    await new Promise(r => setTimeout(r, 0));

    expect(api).toHaveBeenCalledWith('update_part_price', 'C99', 3.75, null);
    expect(onInventoryUpdated).toHaveBeenCalledTimes(1);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('C99'));
  });

  it('Esc restores original content (no api call)', async () => {
    const origText = priceCell.textContent;
    dblclick(priceCell);
    keydown(priceCell.querySelector('input'), 'Escape');
    await new Promise(r => setTimeout(r, 0));

    expect(api).not.toHaveBeenCalledWith('update_part_price', expect.anything());
    expect(priceCell.textContent).toBe(origText);
  });

  it('UndoRedo.save called with price type before api', async () => {
    dblclick(priceCell);
    const input = priceCell.querySelector('input');
    input.value = '1.23';
    keydown(input, 'Enter');
    await new Promise(r => setTimeout(r, 0));

    expect(UndoRedo.save).toHaveBeenCalledWith('price', expect.objectContaining({
      _undoType: 'price',
      oldUp: 2.5,
      newUp: 1.23,
    }));
  });
});

describe('guard: link mode and flyout drag', () => {
  let item, row, qtyCell;

  beforeEach(() => {
    item = { lcsc: 'C7', mpn: '', qty: 5, unit_price: 1.0, ext_price: 5.0 };
    row = buildRow(item);
    activateInlineEdit(row, item);
    qtyCell = row.querySelector('.part-qty');
  });

  afterEach(() => {
    row.remove();
    mockLinkingMode = false;
    mockFlyoutDragActive = false;
  });

  it('dblclick while link mode is active does NOT enter edit', () => {
    mockLinkingMode = true;
    dblclick(qtyCell);
    expect(qtyCell.querySelector('input')).toBeNull();
  });

  it('dblclick while flyout drag is active does NOT enter edit', () => {
    mockFlyoutDragActive = true;
    dblclick(qtyCell);
    expect(qtyCell.querySelector('input')).toBeNull();
  });
});

describe('cancelActiveInlineEdit', () => {
  let item, row, qtyCell;

  beforeEach(() => {
    item = { lcsc: 'C55', mpn: '', qty: 20, unit_price: 0.1, ext_price: 2.0 };
    row = buildRow(item);
    activateInlineEdit(row, item);
    qtyCell = row.querySelector('.part-qty');
  });

  afterEach(() => {
    row.remove();
  });

  it('cancels an active edit and restores original content', () => {
    const origText = qtyCell.textContent;
    dblclick(qtyCell);
    expect(qtyCell.querySelector('input')).not.toBeNull();

    cancelActiveInlineEdit();
    expect(qtyCell.querySelector('input')).toBeNull();
    expect(qtyCell.textContent).toBe(origText);
  });

  it('calling cancelActiveInlineEdit when no edit is active is a no-op', () => {
    expect(() => cancelActiveInlineEdit()).not.toThrow();
  });
});
