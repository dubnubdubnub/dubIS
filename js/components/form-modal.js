// @ts-check
/**
 * js/components/form-modal.js — Declarative form-modal builder.
 *
 * Builds a complete modal DOM dynamically (no index.html markup needed) and
 * wires it to the existing Modal() factory for backdrop/Esc/Enter/focus-trap.
 *
 * Usage:
 *   const fm = defineFormModal('my-modal', { title, fields, onPopulate, onConfirm, … });
 *   fm.open(ctx);   // populate + show
 *   fm.close();     // hide
 *   fm.el;          // the .modal-overlay element
 */

import { el } from '../dom/html.js';
import { Modal, showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';

// ── Internal IDs derived from the modal id ────────────────────────────────

/** @param {string} id */
function cancelId(id) { return `${id}-cancel`; }
/** @param {string} id */
function confirmId(id) { return `${id}-confirm`; }

// ── DOM builders ──────────────────────────────────────────────────────────

/**
 * Create one field row: a label + the appropriate input/select/textarea.
 *
 * @param {FieldSpec} field
 * @returns {{ row: HTMLElement, input: HTMLElement }}
 */
function buildFieldRow(field) {
  /** @type {HTMLElement} */
  let input;

  if (field.type === 'select') {
    const opts = (field.options || []).map((opt) => {
      const o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      return o;
    });
    input = el('select', {
      id: field.key,
      'data-field': field.key,
    }, ...opts);
  } else if (field.type === 'textarea') {
    input = el('textarea', {
      id: field.key,
      'data-field': field.key,
      rows: '3',
      ...(field.placeholder ? { placeholder: field.placeholder } : {}),
      ...(field.attrs || {}),
    });
  } else {
    // 'text' | 'number'
    input = el('input', {
      type: field.type || 'text',
      id: field.key,
      'data-field': field.key,
      ...(field.placeholder ? { placeholder: field.placeholder } : {}),
      ...(field.attrs || {}),
    });
  }

  if (field.mono) input.classList.add('mono');

  const label = el('label', { for: field.key }, field.label);
  const errorEl = el('span', {
    class: 'form-modal-field-error',
    'data-field-error': field.key,
    style: 'color:var(--color-red,#f85149);font-size:var(--text-sm,11px);display:none',
  });

  const formRow = el('div', { class: 'modal-form form-modal-row' },
    label,
    input,
    errorEl,
  );

  return { row: formRow, input };
}

/**
 * Build the complete modal overlay + .modal shell and append it to document.body.
 *
 * @param {string} id
 * @param {FormModalSpec} spec
 * @returns {HTMLElement} the .modal-overlay element
 */
function buildModalDom(id, spec) {
  const titleEl    = el('div', { class: 'modal-title', id: `${id}-title` });
  const subtitleEl = el('div', { class: 'modal-subtitle', id: `${id}-subtitle` });

  /** @type {Map<string, {input: HTMLElement, row: HTMLElement}>} */
  const fieldEls = new Map();
  const fieldRows = (spec.fields || []).map((f) => {
    const { row, input } = buildFieldRow(f);
    fieldEls.set(f.key, { input, row });
    return row;
  });

  const cancelButton = el('button', {
    class: 'btn-lg btn btn-cancel form-modal-cancel',
    id: cancelId(id),
    type: 'button',
  }, 'Cancel');

  const confirmButton = el('button', {
    class: 'btn-lg btn btn-apply form-modal-confirm',
    id: confirmId(id),
    type: 'button',
  }, spec.confirmLabel || 'Apply');

  const actions = el('div', { class: 'modal-actions' }, cancelButton, confirmButton);

  const modalInner = el('div', {
    class: ['modal', spec.className].filter(Boolean).join(' '),
  },
    titleEl,
    subtitleEl,
    ...fieldRows,
    actions,
  );

  const overlay = el('div', {
    class: 'modal-overlay hidden',
    id,
  }, modalInner);

  document.body.appendChild(overlay);
  return overlay;
}

// ── Public API ────────────────────────────────────────────────────────────

/**
 * @typedef {{
 *   key: string,
 *   label: string,
 *   type?: 'text'|'number'|'textarea'|'select',
 *   options?: Array<{value:string, label:string}>,
 *   placeholder?: string,
 *   attrs?: Record<string,any>,
 *   mono?: boolean,
 * }} FieldSpec
 *
 * @typedef {{
 *   title: string | ((ctx: any) => string),
 *   subtitle?: string | ((ctx: any) => string),
 *   className?: string,
 *   fields: FieldSpec[],
 *   onPopulate: (ctx: any) => Record<string,any>,
 *   onInput?: (changedKey: string, values: Record<string,string>, setValue: (k:string,v:string)=>void) => void,
 *   validate?: (values: Record<string,string>, ctx: any) => Record<string,string>|null,
 *   onConfirm: (values: Record<string,string>, ctx: any) => Promise<any>,
 *   confirmLabel?: string,
 *   successToast?: (values: Record<string,string>, ctx: any, result: any) => string,
 *   undo?: {
 *     type: string,
 *     snapshot: (ctx: any) => any,
 *     restore: (data: any) => Promise<void>,
 *   },
 * }} FormModalSpec
 */

/**
 * Define (or reuse) a form-modal with the given id and spec.
 *
 * @param {string} id
 * @param {FormModalSpec} spec
 * @returns {{ open(ctx: any): void, close(): void, el: HTMLElement }}
 */
export function defineFormModal(id, spec) {
  // Reuse existing DOM if already created (idempotent)
  let overlay = document.getElementById(id);
  if (!overlay) {
    overlay = buildModalDom(id, spec);
  }

  /** Current invocation context — set in open(), used in confirm handler */
  let currentCtx = null;

  const modal = Modal(id, {
    cancelId: cancelId(id),
    confirmId: confirmId(id),
    onClose: () => { currentCtx = null; },
  });

  // ── Helpers ─────────────────────────────────────────────────────────────

  /** @returns {Record<string,string>} */
  function gatherValues() {
    /** @type {Record<string,string>} */
    const values = {};
    for (const field of spec.fields) {
      const inputEl = /** @type {HTMLInputElement|null} */ (overlay.querySelector(`[data-field="${field.key}"]`));
      values[field.key] = inputEl ? inputEl.value : '';
    }
    return values;
  }

  /** @param {string} key @param {string} val */
  function setValue(key, val) {
    const inputEl = /** @type {HTMLInputElement|null} */ (overlay.querySelector(`[data-field="${key}"]`));
    if (inputEl) inputEl.value = val;
  }

  /** @param {Record<string,string>|null} errors */
  function renderErrors(errors) {
    for (const field of spec.fields) {
      const errEl = /** @type {HTMLElement|null} */ (overlay.querySelector(`[data-field-error="${field.key}"]`));
      if (!errEl) continue;
      const msg = errors && errors[field.key];
      if (msg) {
        errEl.textContent = msg;
        errEl.style.display = '';
      } else {
        errEl.textContent = '';
        errEl.style.display = 'none';
      }
    }
  }

  function clearErrors() {
    renderErrors(null);
  }

  // ── onInput wiring ───────────────────────────────────────────────────────

  if (spec.onInput) {
    overlay.addEventListener('input', (e) => {
      const target = /** @type {Element} */ (e.target);
      if (!(target instanceof Element)) return;
      const fieldEl = target.closest('[data-field]');
      if (!fieldEl || !overlay.contains(fieldEl)) return;
      const key = fieldEl.getAttribute('data-field');
      if (!key) return;
      const values = gatherValues();
      spec.onInput(key, values, setValue);
    });
  }

  // ── Confirm handler ──────────────────────────────────────────────────────

  const confirmButton = document.getElementById(confirmId(id));
  if (!confirmButton) throw new Error(`form-modal: confirm button #${confirmId(id)} not found`);

  confirmButton.addEventListener('click', async () => {
    const ctx = currentCtx;
    const values = gatherValues();

    // Validate
    if (spec.validate) {
      const errors = spec.validate(values, ctx);
      if (errors && Object.keys(errors).length > 0) {
        renderErrors(errors);
        return;
      }
    }
    clearErrors();

    // Save undo state before mutating
    if (spec.undo) {
      UndoRedo.save(spec.undo.type, spec.undo.snapshot(ctx));
    }

    // Call the mutation
    const result = await spec.onConfirm(values, ctx);

    if (!result) {
      // api already toasted the error; roll back the undo save
      if (spec.undo) UndoRedo.popLast();
      // keep modal open
      return;
    }

    // Success
    modal.close();
    const toastMsg = spec.successToast
      ? spec.successToast(values, ctx, result)
      : 'Done';
    showToast(toastMsg);
  });

  // ── Public interface ─────────────────────────────────────────────────────

  function open(ctx) {
    currentCtx = ctx;

    // Update title / subtitle
    const titleEl = document.getElementById(`${id}-title`);
    const subtitleEl = document.getElementById(`${id}-subtitle`);
    if (titleEl) {
      titleEl.textContent = typeof spec.title === 'function'
        ? spec.title(ctx)
        : spec.title;
    }
    if (subtitleEl) {
      const sub = typeof spec.subtitle === 'function'
        ? spec.subtitle(ctx)
        : (spec.subtitle || '');
      subtitleEl.textContent = sub;
    }

    // Populate fields
    const initial = spec.onPopulate(ctx);
    for (const field of spec.fields) {
      const v = initial[field.key];
      setValue(field.key, (v === null || v === undefined) ? '' : String(v));
    }

    // Clear errors from previous open
    clearErrors();

    modal.open();

    // Focus first field
    const first = overlay.querySelector('[data-field]');
    if (first instanceof HTMLElement) first.focus();
  }

  return { open, close: modal.close, el: overlay };
}
