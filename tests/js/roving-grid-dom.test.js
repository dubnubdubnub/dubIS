// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// Mock store.js to avoid any fetch/init side-effects.
vi.mock('../../js/store.js', () => ({
  getShortcutPrefs: () => ({ vimNav: false }),
  store: {},
}));

import { RovingGrid } from '../../js/a11y/roving-grid.js';

// jsdom does not implement scrollIntoView; stub it globally so roving-grid.js
// can call target.scrollIntoView() without throwing.
Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
  value: vi.fn(),
  writable: true,
});

describe('RovingGrid innermost filtering', () => {
  let container;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
  });

  it('drops outer wrapper when child button also matches cellSelector', () => {
    // Build: container > row > column-div (matches) > button (also matches)
    container.innerHTML = `
      <div class="row">
        <div class="cell-col">
          <button class="cell-btn">Click</button>
        </div>
      </div>
    `;
    const cellSelector = '.cell-col,.cell-btn';
    const grid = RovingGrid(container, {
      rowSelector: '.row',
      cellSelector,
      rowKey: null,
    });

    // Only the inner button should be a grid cell (tabindex=0).
    // The outer .cell-col must be tabindex=-1 (excluded by innermost()).
    const outer = container.querySelector('.cell-col');
    const btn = container.querySelector('.cell-btn');

    expect(btn.tabIndex).toBe(0);
    expect(outer.tabIndex).toBe(-1);

    grid.destroy();
    container.remove();
  });

  it('keeps standalone cell when no child also matches', () => {
    container.innerHTML = `
      <div class="row">
        <div class="cell-col">text only</div>
        <div class="cell-col">text only</div>
      </div>
    `;
    const cellSelector = '.cell-col';
    const grid = RovingGrid(container, {
      rowSelector: '.row',
      cellSelector,
      rowKey: null,
    });

    const cells = container.querySelectorAll('.cell-col');
    // First cell gets tabindex=0 (rover), second gets tabindex=-1.
    expect(cells[0].tabIndex).toBe(0);
    expect(cells[1].tabIndex).toBe(-1);

    grid.destroy();
    container.remove();
  });

  it('ArrowRight navigates from outer td to next td (not into inner button)', () => {
    // Simulate a BOM comparison row: td > div.refs-scroll (not matched), td.btn-group excluded,
    // plain td cells that stand alone.
    container.innerHTML = `
      <table><tbody>
        <tr class="bom-row">
          <td class="refs-cell"><div class="refs-inner">R1</div></td>
          <td class="data-col">100R</td>
          <td class="btn-col"><button class="action-btn">Confirm</button></td>
        </tr>
      </tbody></table>
    `;
    // cellSelector: td:not(.btn-col) plus action buttons
    const cellSelector = 'td:not(.btn-col),.action-btn';
    const grid = RovingGrid(container, {
      rowSelector: 'tbody tr',
      cellSelector,
      rowKey: null,
    });

    // Row should have: [td.refs-cell, td.data-col, button.action-btn]
    // innermost: td.refs-cell has no matched child → kept
    //            td.data-col has no matched child → kept
    //            button.action-btn matched, td.btn-col not in selector → button kept
    const refsTd = container.querySelector('td.refs-cell');
    const dataTd = container.querySelector('td.data-col');

    // The rover starts on the first cell (refsTd). Trigger focusin to register it.
    refsTd.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    expect(refsTd.tabIndex).toBe(0);

    // Dispatch ArrowRight on the focused cell — must bubble up to container's keydown handler.
    const event = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true });
    refsTd.dispatchEvent(event);

    // After ArrowRight, rover should be on second cell (dataTd)
    expect(dataTd.tabIndex).toBe(0);
    expect(refsTd.tabIndex).toBe(-1);

    grid.destroy();
    container.remove();
  });

  it('single tab stop exists after grid init (exactly one tabindex=0)', () => {
    container.innerHTML = `
      <div class="row">
        <span class="cell">A</span>
        <span class="cell">B</span>
        <span class="cell">C</span>
      </div>
    `;
    const grid = RovingGrid(container, {
      rowSelector: '.row',
      cellSelector: '.cell',
      rowKey: null,
    });

    const cells = Array.from(container.querySelectorAll('.cell'));
    const zeros = cells.filter((c) => c.tabIndex === 0);
    const negones = cells.filter((c) => c.tabIndex === -1);

    expect(zeros).toHaveLength(1);
    expect(negones).toHaveLength(2);

    grid.destroy();
    container.remove();
  });
});
