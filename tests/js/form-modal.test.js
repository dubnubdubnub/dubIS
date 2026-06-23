// @vitest-environment jsdom
/**
 * form-modal.test.js — TDD unit tests for js/components/form-modal.js
 *
 * Tests focus on the pure-logic parts that don't need a real pywebview bridge:
 *   - Field value extraction from DOM inputs
 *   - validate() gating (errors block confirm, inline messages render)
 *   - onConfirm-failure rollback (UndoRedo.popLast called when api returns falsy)
 *   - onInput linked-field behaviour (unit↔ext price math)
 *   - open(ctx) populates fields from onPopulate
 *   - successToast called with correct args
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../js/a11y/focus-trap.js', () => ({
  trap: vi.fn(),
  release: vi.fn(),
}));

const showToastMock = vi.fn();
const ModalMock = vi.fn((id, opts = {}) => {
  // Build a minimal Modal: open/close toggle .hidden; wire cancelId if given.
  const el = document.getElementById(id);
  if (!el) throw new Error(`ModalMock: #${id} not found`);
  const open  = () => { el.classList.remove('hidden'); };
  const close = () => { el.classList.add('hidden'); if (opts.onClose) opts.onClose(); };
  if (opts.cancelId) {
    const c = document.getElementById(opts.cancelId);
    if (c) c.addEventListener('click', close);
  }
  return { el, open, close };
});

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToastMock(...a),
  Modal: (...a) => ModalMock(...a),
  escHtml: (s) => (s == null ? '' : String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')),
  linkPriceInputs: vi.fn(),
}));

vi.mock('../../js/undo-redo.js', () => ({
  UndoRedo: {
    save: vi.fn(),
    popLast: vi.fn(),
    register: vi.fn(),
  },
}));

// ── Import under test ────────────────────────────────────────────────────────

import { defineFormModal } from '../../js/components/form-modal.js';
import { UndoRedo as UndoRedoMock } from '../../js/undo-redo.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildBaseDOM() {
  document.body.innerHTML = '<div id="toast"></div>';
}

function getModal(id) {
  return document.getElementById(id);
}

function isHidden(el) {
  return el.classList.contains('hidden');
}

function confirmBtn(id) {
  return document.querySelector(`#${id} .form-modal-confirm`);
}

function cancelBtn(id) {
  return document.querySelector(`#${id} .form-modal-cancel`);
}

function getInput(id, key) {
  return document.querySelector(`#${id} [data-field="${key}"]`);
}

function getError(id, key) {
  return document.querySelector(`#${id} [data-field-error="${key}"]`);
}

function clickConfirm(id) {
  confirmBtn(id).click();
}

// ── Tests ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  buildBaseDOM();
  showToastMock.mockClear();
  ModalMock.mockClear();
  UndoRedoMock.save.mockClear();
  UndoRedoMock.popLast.mockClear();
  UndoRedoMock.register.mockClear();
});

describe('defineFormModal — DOM creation', () => {
  it('creates the modal overlay in document.body when it does not exist', () => {
    defineFormModal('fm-create-test', {
      title: 'Test Modal',
      fields: [{ key: 'name', label: 'Name', type: 'text' }],
      onPopulate: () => ({ name: '' }),
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    const el = document.getElementById('fm-create-test');
    expect(el).not.toBeNull();
    expect(el.classList.contains('modal-overlay')).toBe(true);
    expect(document.body.contains(el)).toBe(true);
  });

  it('does not duplicate the overlay if called again with the same id', () => {
    const spec = {
      title: 'Once',
      fields: [{ key: 'x', label: 'X', type: 'text' }],
      onPopulate: () => ({ x: '' }),
      onConfirm: vi.fn().mockResolvedValue(true),
    };
    defineFormModal('fm-dedup-test', spec);
    defineFormModal('fm-dedup-test', spec);

    const all = document.querySelectorAll('#fm-dedup-test');
    expect(all.length).toBe(1);
  });

  it('renders one input per field (text)', () => {
    defineFormModal('fm-fields-test', {
      title: 'Fields',
      fields: [
        { key: 'alpha', label: 'Alpha', type: 'text' },
        { key: 'beta', label: 'Beta', type: 'number' },
      ],
      onPopulate: () => ({ alpha: '', beta: '' }),
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    expect(getInput('fm-fields-test', 'alpha')).not.toBeNull();
    expect(getInput('fm-fields-test', 'beta')).not.toBeNull();
  });

  it('renders a select element for type="select" fields', () => {
    defineFormModal('fm-select-test', {
      title: 'Select',
      fields: [{
        key: 'mode',
        label: 'Mode',
        type: 'select',
        options: [{ value: 'a', label: 'Option A' }, { value: 'b', label: 'Option B' }],
      }],
      onPopulate: () => ({ mode: 'a' }),
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    const sel = document.querySelector('#fm-select-test [data-field="mode"]');
    expect(sel).not.toBeNull();
    expect(sel.tagName).toBe('SELECT');
  });
});

describe('defineFormModal — open() and field population', () => {
  it('open() fills fields from onPopulate(ctx)', () => {
    const fm = defineFormModal('fm-populate-test', {
      title: 'Populate',
      fields: [
        { key: 'unit', label: 'Unit Price', type: 'number' },
        { key: 'ext', label: 'Ext Price', type: 'number' },
      ],
      onPopulate: (ctx) => ({ unit: ctx.unit_price, ext: ctx.ext_price }),
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({ unit_price: 1.5, ext_price: 3.0 });

    expect(getInput('fm-populate-test', 'unit').value).toBe('1.5');
    expect(getInput('fm-populate-test', 'ext').value).toBe('3');
  });

  it('open() removes the hidden class', () => {
    const fm = defineFormModal('fm-open-test', {
      title: 'Open',
      fields: [{ key: 'x', label: 'X', type: 'text' }],
      onPopulate: () => ({ x: 'hello' }),
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    const el = getModal('fm-open-test');
    expect(isHidden(el)).toBe(true); // starts hidden

    fm.open({});
    expect(isHidden(el)).toBe(false);
  });

  it('open() clears previous error messages', () => {
    const onConfirmFail = vi.fn().mockResolvedValue(null);
    const fm = defineFormModal('fm-clear-errors-test', {
      title: 'Errors',
      fields: [{ key: 'price', label: 'Price', type: 'number' }],
      onPopulate: () => ({ price: '' }),
      validate: (values) => {
        if (!values.price) return { price: 'Price is required' };
        return null;
      },
      onConfirm: onConfirmFail,
    });

    fm.open({});
    // Trigger confirm to produce validation errors
    clickConfirm('fm-clear-errors-test');

    const errEl = getError('fm-clear-errors-test', 'price');
    expect(errEl).not.toBeNull();
    expect(errEl.textContent).toBe('Price is required');

    // Re-open: errors should be cleared
    fm.open({});
    const errEl2 = getError('fm-clear-errors-test', 'price');
    expect(errEl2 === null || errEl2.textContent === '').toBe(true);
  });
});

describe('defineFormModal — value extraction', () => {
  it('gathers all field values as an object keyed by field.key', async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);
    const fm = defineFormModal('fm-values-test', {
      title: 'Values',
      fields: [
        { key: 'unit', label: 'Unit', type: 'number' },
        { key: 'note', label: 'Note', type: 'text' },
      ],
      onPopulate: () => ({ unit: '', note: '' }),
      onConfirm,
    });

    fm.open({});
    getInput('fm-values-test', 'unit').value = '2.5';
    getInput('fm-values-test', 'note').value = 'hello';

    clickConfirm('fm-values-test');
    // Wait for the async confirm to settle
    await new Promise(r => setTimeout(r, 10));

    expect(onConfirm).toHaveBeenCalledWith(
      { unit: '2.5', note: 'hello' },
      expect.anything(),
    );
  });
});

describe('defineFormModal — validate() gating', () => {
  it('does not call onConfirm when validate returns errors', async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);
    const fm = defineFormModal('fm-validate-test', {
      title: 'Validate',
      fields: [{ key: 'price', label: 'Price', type: 'number' }],
      onPopulate: () => ({ price: '' }),
      validate: (values) => {
        if (!values.price || values.price === '') return { price: 'Required' };
        return null;
      },
      onConfirm,
    });

    fm.open({});
    clickConfirm('fm-validate-test');
    await new Promise(r => setTimeout(r, 10));

    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('renders inline error messages next to failed fields', async () => {
    const fm = defineFormModal('fm-inline-errors-test', {
      title: 'Inline Errors',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: () => ({ unit: '' }),
      validate: (values) => {
        if (!values.unit) return { unit: 'Enter a value' };
        return null;
      },
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({});
    clickConfirm('fm-inline-errors-test');
    await new Promise(r => setTimeout(r, 10));

    const errEl = getError('fm-inline-errors-test', 'unit');
    expect(errEl).not.toBeNull();
    expect(errEl.textContent).toContain('Enter a value');
  });

  it('does NOT close when validate returns errors', async () => {
    const fm = defineFormModal('fm-noclose-test', {
      title: 'No Close',
      fields: [{ key: 'v', label: 'V', type: 'text' }],
      onPopulate: () => ({ v: '' }),
      validate: (values) => values.v === '' ? { v: 'Required' } : null,
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({});
    expect(isHidden(getModal('fm-noclose-test'))).toBe(false);

    clickConfirm('fm-noclose-test');
    await new Promise(r => setTimeout(r, 10));

    expect(isHidden(getModal('fm-noclose-test'))).toBe(false);
  });

  it('calls onConfirm and closes when validate passes', async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);
    const fm = defineFormModal('fm-validate-pass-test', {
      title: 'Valid',
      fields: [{ key: 'v', label: 'V', type: 'text' }],
      onPopulate: () => ({ v: '' }),
      validate: (values) => values.v === '' ? { v: 'Required' } : null,
      onConfirm,
    });

    fm.open({});
    getInput('fm-validate-pass-test', 'v').value = 'ok';
    clickConfirm('fm-validate-pass-test');
    await new Promise(r => setTimeout(r, 50));

    expect(onConfirm).toHaveBeenCalled();
    expect(isHidden(getModal('fm-validate-pass-test'))).toBe(true);
  });
});

describe('defineFormModal — onConfirm failure rollback', () => {
  it('calls UndoRedo.popLast when onConfirm returns falsy (api failure)', async () => {
    const onConfirm = vi.fn().mockResolvedValue(null); // api failure
    const fm = defineFormModal('fm-rollback-test', {
      title: 'Rollback',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: () => ({ unit: '1.0' }),
      undo: {
        type: 'price',
        snapshot: (ctx) => ({ partKey: ctx.pk, old: 0 }),
        restore: vi.fn(),
      },
      onConfirm,
    });

    fm.open({ pk: 'C123' });
    getInput('fm-rollback-test', 'unit').value = '2.0';
    clickConfirm('fm-rollback-test');
    await new Promise(r => setTimeout(r, 50));

    expect(UndoRedoMock.save).toHaveBeenCalled();
    expect(UndoRedoMock.popLast).toHaveBeenCalled();
  });

  it('does NOT call UndoRedo.popLast when onConfirm returns truthy', async () => {
    const onConfirm = vi.fn().mockResolvedValue([{ qty: 5 }]);
    const fm = defineFormModal('fm-no-rollback-test', {
      title: 'No Rollback',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: () => ({ unit: '1.0' }),
      undo: {
        type: 'price',
        snapshot: (ctx) => ({ partKey: ctx.pk, old: 0 }),
        restore: vi.fn(),
      },
      onConfirm,
    });

    fm.open({ pk: 'C456' });
    getInput('fm-no-rollback-test', 'unit').value = '3.0';
    clickConfirm('fm-no-rollback-test');
    await new Promise(r => setTimeout(r, 50));

    expect(UndoRedoMock.popLast).not.toHaveBeenCalled();
  });

  it('keeps modal open when onConfirm fails', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const fm = defineFormModal('fm-keepopen-test', {
      title: 'Keep Open',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: () => ({ unit: '1.0' }),
      onConfirm,
    });

    fm.open({});
    getInput('fm-keepopen-test', 'unit').value = '2.0';
    clickConfirm('fm-keepopen-test');
    await new Promise(r => setTimeout(r, 50));

    expect(isHidden(getModal('fm-keepopen-test'))).toBe(false);
  });
});

describe('defineFormModal — successToast', () => {
  it('shows the successToast when provided and onConfirm succeeds', async () => {
    const fresh = [{ qty: 10 }];
    const onConfirm = vi.fn().mockResolvedValue(fresh);
    const fm = defineFormModal('fm-toast-test', {
      title: 'Toast',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: (ctx) => ({ unit: ctx.unit_price }),
      onConfirm,
      successToast: (values, ctx, result) => `Updated ${ctx.pk}`,
    });

    fm.open({ pk: 'C789', unit_price: '1.0' });
    getInput('fm-toast-test', 'unit').value = '5.0';
    clickConfirm('fm-toast-test');
    await new Promise(r => setTimeout(r, 50));

    expect(showToastMock).toHaveBeenCalledWith('Updated C789');
  });

  it('shows a default toast when successToast is not provided', async () => {
    const onConfirm = vi.fn().mockResolvedValue([{ qty: 1 }]);
    const fm = defineFormModal('fm-default-toast-test', {
      title: 'Default Toast',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: () => ({ unit: '1' }),
      onConfirm,
      confirmLabel: 'Save',
    });

    fm.open({});
    getInput('fm-default-toast-test', 'unit').value = '2';
    clickConfirm('fm-default-toast-test');
    await new Promise(r => setTimeout(r, 50));

    expect(showToastMock).toHaveBeenCalled();
  });
});

describe('defineFormModal — onInput linked fields (unit/ext price math)', () => {
  it('onInput is called with changedKey and setValue when a field changes', () => {
    const onInput = vi.fn();
    const fm = defineFormModal('fm-oninput-test', {
      title: 'OnInput',
      fields: [
        { key: 'unit', label: 'Unit', type: 'number' },
        { key: 'ext', label: 'Ext', type: 'number' },
      ],
      onPopulate: () => ({ unit: '', ext: '' }),
      onInput,
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({});
    const unitInput = getInput('fm-oninput-test', 'unit');
    unitInput.value = '2.0';
    unitInput.dispatchEvent(new Event('input', { bubbles: true }));

    expect(onInput).toHaveBeenCalledWith(
      'unit',
      expect.objectContaining({ unit: '2.0' }),
      expect.any(Function),
    );
  });

  it('setValue(key, v) updates the field value without re-rendering', () => {
    const fm = defineFormModal('fm-setvalue-test', {
      title: 'SetValue',
      fields: [
        { key: 'unit', label: 'Unit', type: 'number' },
        { key: 'ext', label: 'Ext', type: 'number' },
      ],
      onPopulate: (ctx) => ({ unit: String(ctx.qty), ext: '' }),
      onInput: (key, values, setValue) => {
        // Simulate unit→ext price linkage with qty=5
        if (key === 'unit') {
          const up = parseFloat(values.unit);
          if (!isNaN(up)) setValue('ext', (up * 5).toFixed(2));
        }
        if (key === 'ext') {
          const ep = parseFloat(values.ext);
          if (!isNaN(ep)) setValue('unit', (ep / 5).toFixed(4));
        }
      },
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({ qty: 5 });

    const unitInput = getInput('fm-setvalue-test', 'unit');
    const extInput  = getInput('fm-setvalue-test', 'ext');

    unitInput.value = '2.0';
    unitInput.dispatchEvent(new Event('input', { bubbles: true }));

    expect(extInput.value).toBe('10.00');
  });

  it('ext→unit linkage works via onInput/setValue', () => {
    const fm = defineFormModal('fm-ext-to-unit-test', {
      title: 'Ext to Unit',
      fields: [
        { key: 'unit', label: 'Unit', type: 'number' },
        { key: 'ext', label: 'Ext', type: 'number' },
      ],
      onPopulate: () => ({ unit: '', ext: '' }),
      onInput: (key, values, setValue) => {
        const qty = 10;
        if (key === 'unit') {
          const up = parseFloat(values.unit);
          if (!isNaN(up) && qty > 0) setValue('ext', (up * qty).toFixed(2));
        }
        if (key === 'ext') {
          const ep = parseFloat(values.ext);
          if (!isNaN(ep) && qty > 0) setValue('unit', (ep / qty).toFixed(4));
        }
      },
      onConfirm: vi.fn().mockResolvedValue(true),
    });

    fm.open({});
    const extInput  = getInput('fm-ext-to-unit-test', 'ext');
    const unitInput = getInput('fm-ext-to-unit-test', 'unit');

    extInput.value = '15.00';
    extInput.dispatchEvent(new Event('input', { bubbles: true }));

    expect(unitInput.value).toBe('1.5000');
  });
});

describe('defineFormModal — undo.save called before onConfirm', () => {
  it('calls UndoRedo.save with undo.type and snapshot data before onConfirm', async () => {
    const snapshot = vi.fn().mockReturnValue({ partKey: 'C111', oldUp: 0 });
    const onConfirm = vi.fn().mockResolvedValue([{ qty: 1 }]);
    const fm = defineFormModal('fm-undo-order-test', {
      title: 'Undo Order',
      fields: [{ key: 'unit', label: 'Unit', type: 'number' }],
      onPopulate: (ctx) => ({ unit: String(ctx.up) }),
      undo: { type: 'price', snapshot, restore: vi.fn() },
      onConfirm,
    });

    const ctx = { up: 1.0 };
    fm.open(ctx);
    getInput('fm-undo-order-test', 'unit').value = '2.0';
    clickConfirm('fm-undo-order-test');
    await new Promise(r => setTimeout(r, 50));

    expect(UndoRedoMock.save).toHaveBeenCalledWith('price', { partKey: 'C111', oldUp: 0 });
    // save is called before onConfirm (mock call order)
    const saveCallOrder = UndoRedoMock.save.mock.invocationCallOrder[0];
    const confirmCallOrder = onConfirm.mock.invocationCallOrder[0];
    expect(saveCallOrder).toBeLessThan(confirmCallOrder);
  });
});
