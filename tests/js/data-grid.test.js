// @vitest-environment jsdom
/**
 * tests/js/data-grid.test.js — TDD unit tests for js/components/data-grid.js
 *
 * Covers: column/row render, rowActions onClick dispatch, inline onCellEdit
 * (Enter commits, Esc cancels), grouping headers + collapse, footerAggregates,
 * RovingGrid presence + refresh, destroy() teardown.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// AppLog spy — must be set up before DataGrid is imported so the mock is in place
const AppLogErrorSpy = vi.fn();
vi.mock('../../js/api.js', () => ({
  AppLog: {
    warn: vi.fn(),
    error: (...args) => AppLogErrorSpy(...args),
  },
}));

// ── Mocks ────────────────────────────────────────────────────────────────────

// RovingGrid depends on store.js (getShortcutPrefs) and has DOM side-effects;
// mock it to return a controllable spy object.
const rovingRefreshSpy = vi.fn();
const rovingDestroySpy = vi.fn();
const RovingGridMock = vi.fn(() => ({
  refresh: rovingRefreshSpy,
  destroy: rovingDestroySpy,
}));

vi.mock('../../js/a11y/roving-grid.js', () => ({
  RovingGrid: (...args) => RovingGridMock(...args),
}));

// scrollIntoView is not implemented in jsdom
Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
  value: vi.fn(),
  writable: true,
});

// ── Import under test ────────────────────────────────────────────────────────
import { DataGrid } from '../../js/components/data-grid.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeRoot() {
  const div = document.createElement('div');
  document.body.appendChild(div);
  return div;
}

function cleanup(root) {
  root.remove();
}

const SIMPLE_COLUMNS = [
  { key: 'name', label: 'Name' },
  { key: 'qty',  label: 'Qty',  align: 'right' },
];

const SAMPLE_DATA = [
  { name: 'Resistor 10k', qty: 50 },
  { name: 'Capacitor 100nF', qty: 20 },
];

// ── Column / Row Render ──────────────────────────────────────────────────────

describe('DataGrid — column and row render', () => {
  let root;
  afterEach(() => cleanup(root));

  it('renders a <table> with class data-grid', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    expect(root.querySelector('table.data-grid')).toBeTruthy();
  });

  it('renders <thead> with correct column labels', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const ths = Array.from(root.querySelectorAll('thead th'));
    expect(ths.map(th => th.textContent.trim())).toEqual(['Name', 'Qty']);
  });

  it('renders a <tbody> row per data item', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const rows = root.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(SAMPLE_DATA.length);
  });

  it('sets data-row-key on each row using rowKey()', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const rows = Array.from(root.querySelectorAll('tbody tr[data-row-key]'));
    const keys = rows.map(r => r.dataset.rowKey);
    expect(keys).toContain('Resistor 10k');
    expect(keys).toContain('Capacitor 100nF');
  });

  it('renders cell values in the correct columns', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const firstRow = root.querySelector('tbody tr');
    const cells = Array.from(firstRow.querySelectorAll('td'));
    expect(cells[0].textContent.trim()).toBe('Resistor 10k');
    expect(cells[1].textContent.trim()).toBe('50');
  });

  it('escapes HTML in cell values (XSS guard)', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render([{ name: '<script>xss</script>', qty: 1 }]);
    const firstCell = root.querySelector('tbody td');
    // Must NOT have a live <script> element
    expect(root.querySelector('script')).toBeNull();
    expect(firstCell.textContent).toBe('<script>xss</script>');
  });

  it('applies custom render() column', () => {
    root = makeRoot();
    const cols = [
      { key: 'name', label: 'Name', render: (item) => `[${item.name}]` },
    ];
    const grid = DataGrid(root, { columns: cols, rowKey: (item) => item.name });
    grid.render([{ name: 'Part A' }]);
    const cell = root.querySelector('tbody td');
    expect(cell.textContent).toContain('[Part A]');
  });

  it('applies getRowClass to <tr> elements', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      getRowClass: (item) => item.qty < 30 ? 'low-stock' : '',
    });
    grid.render(SAMPLE_DATA);
    const rows = Array.from(root.querySelectorAll('tbody tr'));
    const lowRow = rows.find(r => r.dataset.rowKey === 'Capacitor 100nF');
    expect(lowRow.classList.contains('low-stock')).toBe(true);
    const okRow = rows.find(r => r.dataset.rowKey === 'Resistor 10k');
    expect(okRow.classList.contains('low-stock')).toBe(false);
  });

  it('renders emptyMessage when data is empty', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      emptyMessage: 'No parts found',
    });
    grid.render([]);
    expect(root.textContent).toContain('No parts found');
  });

  it('refresh() re-renders current data', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    expect(root.querySelectorAll('tbody tr')).toHaveLength(2);
    // Mutate backing data then call refresh
    grid.render([SAMPLE_DATA[0]]);
    grid.refresh();
    // refresh re-renders with whatever getData() returns
    expect(root.querySelectorAll('tbody tr')).toHaveLength(1);
  });

  it('getData() returns the current data array', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    expect(grid.getData()).toBe(SAMPLE_DATA);
  });
});

// ── rowActions ────────────────────────────────────────────────────────────────

describe('DataGrid — rowActions', () => {
  let root;
  afterEach(() => cleanup(root));

  it('renders an actions column header when rowActions is provided', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rowActions: [{ key: 'del', label: '×', onClick: vi.fn() }],
    });
    grid.render(SAMPLE_DATA);
    const ths = Array.from(root.querySelectorAll('thead th'));
    // Extra header cell for actions column
    expect(ths.length).toBe(3);
  });

  it('calls onClick with the correct item when an action button is clicked', () => {
    root = makeRoot();
    const onClickMock = vi.fn();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rowActions: [{ key: 'del', label: '×', onClick: onClickMock }],
    });
    grid.render(SAMPLE_DATA);

    // Click the action button in the first row
    const btn = root.querySelector('[data-action-key="del"]');
    expect(btn).toBeTruthy();
    btn.click();

    expect(onClickMock).toHaveBeenCalledOnce();
    expect(onClickMock.mock.calls[0][0]).toEqual(SAMPLE_DATA[0]);
  });

  it('calls onClick with the correct item for second row', () => {
    root = makeRoot();
    const onClickMock = vi.fn();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rowActions: [{ key: 'del', label: '×', onClick: onClickMock }],
    });
    grid.render(SAMPLE_DATA);

    const btns = root.querySelectorAll('[data-action-key="del"]');
    btns[1].click();

    expect(onClickMock).toHaveBeenCalledOnce();
    expect(onClickMock.mock.calls[0][0]).toEqual(SAMPLE_DATA[1]);
  });

  it('respects when() predicate — hides button when false', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rowActions: [{ key: 'del', label: '×', when: (item) => item.qty > 30, onClick: vi.fn() }],
    });
    grid.render(SAMPLE_DATA);

    const btns = root.querySelectorAll('[data-action-key="del"]');
    // Capacitor has qty=20 so when() returns false; its button should not be rendered
    // Resistor has qty=50 so it should have a button
    expect(btns).toHaveLength(1);
    const parentRow = btns[0].closest('tr');
    expect(parentRow.dataset.rowKey).toBe('Resistor 10k');
  });
});

// ── onCellEdit ────────────────────────────────────────────────────────────────

describe('DataGrid — onCellEdit', () => {
  let root;
  afterEach(() => cleanup(root));

  function makeEditableGrid(onCellEdit) {
    root = makeRoot();
    const cols = [
      { key: 'name', label: 'Name', editable: true },
      { key: 'qty',  label: 'Qty' },
    ];
    const grid = DataGrid(root, {
      columns: cols,
      rowKey: (item) => item.name,
      onCellEdit,
    });
    grid.render(SAMPLE_DATA);
    return grid;
  }

  it('clicking an editable cell replaces content with an <input>', () => {
    makeEditableGrid(vi.fn());
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    expect(editableCell.querySelector('input')).toBeTruthy();
  });

  it('input is pre-populated with the current value', () => {
    makeEditableGrid(vi.fn());
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    const input = editableCell.querySelector('input');
    expect(input.value).toBe('Resistor 10k');
  });

  it('Enter key commits the edit and calls onCellEdit', async () => {
    const onCellEdit = vi.fn(async () => {});
    makeEditableGrid(onCellEdit);
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    const input = editableCell.querySelector('input');
    input.value = 'Updated Name';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    // Await any pending microtasks
    await new Promise(r => setTimeout(r, 0));
    expect(onCellEdit).toHaveBeenCalledWith(SAMPLE_DATA[0], 'name', 'Updated Name', 0);
  });

  it('Enter key restores display mode after commit', async () => {
    const onCellEdit = vi.fn(async () => {});
    makeEditableGrid(onCellEdit);
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    const input = editableCell.querySelector('input');
    input.value = 'Updated Name';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise(r => setTimeout(r, 0));
    // Input should be gone; display text should be visible
    expect(editableCell.querySelector('input')).toBeNull();
  });

  it('Escape key cancels the edit without calling onCellEdit', () => {
    const onCellEdit = vi.fn();
    makeEditableGrid(onCellEdit);
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    const input = editableCell.querySelector('input');
    input.value = 'Changed Value';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onCellEdit).not.toHaveBeenCalled();
    // Input should be gone
    expect(editableCell.querySelector('input')).toBeNull();
    // Original value preserved
    expect(editableCell.textContent.trim()).toBe('Resistor 10k');
  });

  it('onCellEdit rejection rolls back to original value and logs an error', async () => {
    AppLogErrorSpy.mockClear();
    const onCellEdit = vi.fn(() => Promise.reject(new Error('save failed')));
    makeEditableGrid(onCellEdit);
    const editableCell = root.querySelector('td[data-col-key="name"]');
    editableCell.click();
    const input = editableCell.querySelector('input');
    input.value = 'New Value';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise(r => setTimeout(r, 10));
    // Input should be gone
    expect(editableCell.querySelector('input')).toBeNull();
    // Cell must roll back to ORIGINAL value (not the new one)
    expect(editableCell.textContent.trim()).toBe('Resistor 10k');
    // Error must be surfaced via AppLog.error — not silently swallowed
    expect(AppLogErrorSpy).toHaveBeenCalledOnce();
    expect(AppLogErrorSpy.mock.calls[0][0]).toMatch(/save failed/);
  });
});

// ── Grouping ──────────────────────────────────────────────────────────────────

describe('DataGrid — grouping', () => {
  let root;
  afterEach(() => cleanup(root));

  const GROUPED_DATA = [
    { name: 'R1', qty: 10, type: 'Resistor' },
    { name: 'R2', qty: 5,  type: 'Resistor' },
    { name: 'C1', qty: 20, type: 'Capacitor' },
  ];

  const GROUPED_COLS = [
    { key: 'name', label: 'Name' },
    { key: 'qty',  label: 'Qty' },
  ];

  it('renders group header rows', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: GROUPED_COLS,
      rowKey: (item) => item.name,
      grouping: {
        by: (item) => item.type,
        header: (key) => `-- ${key} --`,
      },
    });
    grid.render(GROUPED_DATA);
    const headers = root.querySelectorAll('tr.group-header');
    expect(headers).toHaveLength(2);
  });

  it('group header contains the header text', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: GROUPED_COLS,
      rowKey: (item) => item.name,
      grouping: {
        by: (item) => item.type,
        header: (key) => `Group: ${key}`,
      },
    });
    grid.render(GROUPED_DATA);
    const headers = Array.from(root.querySelectorAll('tr.group-header'));
    const texts = headers.map(h => h.textContent.trim());
    expect(texts.some(t => t.includes('Resistor'))).toBe(true);
    expect(texts.some(t => t.includes('Capacitor'))).toBe(true);
  });

  it('collapsible grouping hides rows after clicking header', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: GROUPED_COLS,
      rowKey: (item) => item.name,
      grouping: {
        by: (item) => item.type,
        header: (key) => key,
        collapsible: true,
      },
    });
    grid.render(GROUPED_DATA);

    // Initially all data rows should be visible
    const dataRows = root.querySelectorAll('tbody tr:not(.group-header)');
    expect(dataRows).toHaveLength(3);

    // Click the first group header to collapse it
    const firstHeader = root.querySelector('tr.group-header');
    firstHeader.click();

    // Rows in that group should be hidden; only the Capacitor row (C1) remains visible.
    // Resistor group has 2 rows (R1, R2); collapsing it leaves exactly 1 visible row.
    const visibleRows = Array.from(root.querySelectorAll('tbody tr:not(.group-header)')).filter(
      r => r.style.display !== 'none'
    );
    expect(visibleRows.length).toBe(1);
  });

  it('collapsedByDefault renders groups collapsed', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: GROUPED_COLS,
      rowKey: (item) => item.name,
      grouping: {
        by: (item) => item.type,
        header: (key) => key,
        collapsible: true,
        collapsedByDefault: true,
      },
    });
    grid.render(GROUPED_DATA);

    const dataRows = Array.from(root.querySelectorAll('tbody tr:not(.group-header)'));
    const hiddenRows = dataRows.filter(r => r.style.display === 'none');
    expect(hiddenRows.length).toBe(3);
  });
});

// ── footerAggregates ──────────────────────────────────────────────────────────

describe('DataGrid — footerAggregates', () => {
  let root;
  afterEach(() => cleanup(root));

  it('renders a <tfoot> when footerAggregates is provided', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      footerAggregates: [
        { column: 'qty', render: (items) => String(items.reduce((s, i) => s + i.qty, 0)) },
      ],
    });
    grid.render(SAMPLE_DATA);
    expect(root.querySelector('tfoot')).toBeTruthy();
  });

  it('renders aggregate value under the correct column', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      footerAggregates: [
        { column: 'qty', render: (items) => String(items.reduce((s, i) => s + i.qty, 0)) },
      ],
    });
    grid.render(SAMPLE_DATA);
    const tfoot = root.querySelector('tfoot');
    const cells = Array.from(tfoot.querySelectorAll('td'));
    // Second column is 'qty'; total should be 50+20=70
    const qtyCell = cells.find(c => c.textContent.trim() === '70');
    expect(qtyCell).toBeTruthy();
  });
});

// ── RovingGrid integration ────────────────────────────────────────────────────

describe('DataGrid — RovingGrid integration', () => {
  let root;
  afterEach(() => cleanup(root));

  beforeEach(() => {
    RovingGridMock.mockClear();
    rovingRefreshSpy.mockClear();
    rovingDestroySpy.mockClear();
  });

  it('instantiates RovingGrid when rovingNav is not false', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    expect(RovingGridMock).toHaveBeenCalled();
  });

  it('does NOT instantiate RovingGrid when rovingNav is false', () => {
    root = makeRoot();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rovingNav: false,
    });
    grid.render(SAMPLE_DATA);
    expect(RovingGridMock).not.toHaveBeenCalled();
  });

  it('calls RovingGrid refresh() after each render()', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const callCount = rovingRefreshSpy.mock.calls.length;
    grid.render(SAMPLE_DATA);
    expect(rovingRefreshSpy.mock.calls.length).toBeGreaterThan(callCount);
  });

  it('passes rowKey attribute name to RovingGrid', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    const callArgs = RovingGridMock.mock.calls[0];
    expect(callArgs[1].rowKey).toBe('data-row-key');
  });
});

// ── destroy() ────────────────────────────────────────────────────────────────

describe('DataGrid — destroy()', () => {
  let root;
  afterEach(() => cleanup(root));

  beforeEach(() => {
    RovingGridMock.mockClear();
    rovingRefreshSpy.mockClear();
    rovingDestroySpy.mockClear();
  });

  it('calls RovingGrid destroy()', () => {
    root = makeRoot();
    const grid = DataGrid(root, { columns: SIMPLE_COLUMNS, rowKey: (item) => item.name });
    grid.render(SAMPLE_DATA);
    grid.destroy();
    expect(rovingDestroySpy).toHaveBeenCalledOnce();
  });

  it('detaches delegate click handlers after destroy — action buttons no longer fire', () => {
    root = makeRoot();
    const onClickMock = vi.fn();
    const grid = DataGrid(root, {
      columns: SIMPLE_COLUMNS,
      rowKey: (item) => item.name,
      rowActions: [{ key: 'del', label: '×', onClick: onClickMock }],
    });
    grid.render(SAMPLE_DATA);
    grid.destroy();

    const btn = root.querySelector('[data-action-key="del"]');
    if (btn) btn.click();
    expect(onClickMock).not.toHaveBeenCalled();
  });

  it('detaches cell edit click handler after destroy', () => {
    root = makeRoot();
    const cols = [{ key: 'name', label: 'Name', editable: true }];
    const grid = DataGrid(root, {
      columns: cols,
      rowKey: (item) => item.name,
      onCellEdit: vi.fn(),
    });
    grid.render(SAMPLE_DATA);
    grid.destroy();

    // Clicking an editable cell should NOT produce an input after destroy
    const editableCell = root.querySelector('td[data-col-key="name"]');
    if (editableCell) editableCell.click();
    expect(editableCell ? editableCell.querySelector('input') : null).toBeNull();
  });
});
