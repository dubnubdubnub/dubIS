// @vitest-environment jsdom
/* Verifies the live BOM-table click handler (inv-mutations.js's
   handleBomTableClick, wired by inv-bom-mode.js) routes a click on a
   label-mode selection checkbox to toggleSelection — instead of falling
   through to the action-button branches. */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Spy we can assert against; the real handler imports this symbol.
const toggleSpy = vi.fn();

vi.mock('../../js/label-selection.js', () => ({
  toggleSelection: (key) => toggleSpy(key),
}));

// Stub out the heavy collaborators the handler imports so the module loads
// in isolation. None of these run for a checkbox click anyway.
vi.mock('../../js/api.js', () => ({
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  api: vi.fn(),
}));
vi.mock('../../js/event-bus.js', () => ({
  EventBus: { emit: vi.fn(), on: vi.fn() },
  Events: {},
}));
vi.mock('../../js/ui-helpers.js', () => ({ showToast: vi.fn(), escHtml: (s) => s || '' }));
vi.mock('../../js/undo-redo.js', () => ({ UndoRedo: { save: vi.fn() } }));
vi.mock('../../js/store.js', () => ({
  store: { inventory: [], links: {} },
  snapshotLinks: vi.fn(),
}));
vi.mock('../../js/part-keys.js', () => ({ bomKey: vi.fn(), invPartKey: vi.fn() }));
vi.mock('../../js/inventory-modals.js', () => ({ openAdjustModal: vi.fn() }));
vi.mock('../../js/group-flyout/flyout-panel.js', () => ({ openFlyout: vi.fn() }));

import { handleBomTableClick } from '../../js/inventory/inv-mutations.js';

beforeEach(() => {
  toggleSpy.mockClear();
});

describe('handleBomTableClick — label-mode checkbox', () => {
  it('routes a real click on a label-select-checkbox to toggleSelection', () => {
    const tbody = document.createElement('tbody');
    tbody.innerHTML =
      '<tr data-part-key="PK1"><td class="btn-group">' +
      '<input type="checkbox" class="label-select-checkbox" data-key="C12345">' +
      '</td></tr>';
    document.body.appendChild(tbody);
    tbody.addEventListener('click', handleBomTableClick);

    const cb = tbody.querySelector('.label-select-checkbox');
    cb.click(); // real click → bubbles to delegated handler

    expect(toggleSpy).toHaveBeenCalledTimes(1);
    expect(toggleSpy).toHaveBeenCalledWith('C12345');

    tbody.remove();
  });

  it('does not treat a button click as a checkbox selection', () => {
    const tbody = document.createElement('tbody');
    tbody.innerHTML =
      '<tr data-part-key="PK1"><td class="btn-group">' +
      '<button class="confirm-btn">Confirm</button>' +
      '</td></tr>';
    document.body.appendChild(tbody);
    tbody.addEventListener('click', handleBomTableClick);

    tbody.querySelector('.confirm-btn').click();

    expect(toggleSpy).not.toHaveBeenCalled();
    tbody.remove();
  });
});
