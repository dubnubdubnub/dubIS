// @ts-check
/* inventory/inv-inline-edit.js — Inline editing of qty and unit-price cells.
   Entry point: activateInlineEdit(row, item) wires dblclick on .part-qty and
   .part-unit-price for a single rendered row.

   Interaction contract:
   - Double-click on .part-qty or .part-unit-price → enter edit mode.
   - Guard: no-op if link mode active or flyout drag active.
   - Enter → commit (adjust_part SET for qty, update_part_price for unit price).
   - Esc or blur-without-change → cancel, restore display text.
   - Reuses: UndoRedo.save → api → onInventoryUpdated → showToast exactly as
     inventory-modals.js does (same code paths, same undo registration keys).
*/

import { api, AppLog } from '../api.js';
import { showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { onInventoryUpdated } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { isFlyoutDragActive } from './inv-events.js';
import { store } from '../store.js';

// ── Constants ──────────────────────────────────────────────────────────────

/** Minimum input width (px) so the cursor fits even in narrow fixed-width cols. */
const MIN_INPUT_W = 48;

// ── Active-edit tracker (one edit at a time across the whole panel) ────────

/** @type {{ cell: HTMLElement, restore: () => void } | null} */
let _activeEdit = null;

/** Cancel any in-progress inline edit without committing. */
export function cancelActiveInlineEdit() {
  if (_activeEdit) {
    _activeEdit.restore();
    _activeEdit = null;
  }
}

// ── Guard helpers ──────────────────────────────────────────────────────────

/** Returns true when inline editing is blocked by an active UI mode. */
function isEditBlocked() {
  // Link mode: rows behave as link targets — clicks and dblclicks have other
  // meanings; don't intercept.
  if (store.links.linkingMode) return true;
  // Flyout drag active: rows are draggable drop targets; dblclick could fire
  // on a cell between two drags — don't intercept.
  if (isFlyoutDragActive()) return true;
  return false;
}

// ── Core inline edit implementation ───────────────────────────────────────

/**
 * Enter inline-edit mode on a single cell.
 *
 * @param {object} opts
 * @param {HTMLElement}  opts.cell        - the span element to edit (.part-qty or .part-unit-price)
 * @param {string}       opts.initialText - the current display text (e.g. "42" or "$1.23")
 * @param {number}       opts.numericValue - the raw numeric value (item.qty or item.unit_price)
 * @param {function(string, function(): void): Promise<void>} opts.commit - async fn(rawValue, restore) that commits
 * @param {'qty'|'price'} opts.kind
 */
function enterEditMode({ cell, initialText, numericValue, commit, kind }) {
  // Cancel any other in-progress edit first.
  cancelActiveInlineEdit();

  // Snapshot the cell's original content so we can restore it on cancel/failure.
  const origHTML = cell.innerHTML;

  // Build input sized to the cell's current geometry so the row doesn't shift.
  const cellW = cell.offsetWidth;
  const inputW = Math.max(cellW > 0 ? cellW : 0, MIN_INPUT_W);

  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'inv-inline-input';
  input.style.width = inputW + 'px';
  input.setAttribute('aria-label', kind === 'qty' ? 'Edit quantity' : 'Edit unit price');
  if (kind === 'price') {
    input.step = '0.0001';
    input.min = '0';
  } else {
    input.step = '1';
    input.min = '0';
  }
  input.value = String(numericValue);

  // Swap display text for input.
  cell.innerHTML = '';
  cell.appendChild(input);
  cell.classList.add('inv-inline-editing');

  // Focus and select all text.
  input.focus();
  input.select();

  // Track this edit.
  function restore() {
    cell.innerHTML = origHTML;
    cell.classList.remove('inv-inline-editing');
  }
  _activeEdit = { cell, restore };

  // ── Event handlers ──

  let committed = false;

  async function doCommit() {
    if (committed) return;
    committed = true;
    _activeEdit = null;
    try {
      await commit(input.value, restore);
    } catch (err) {
      AppLog.error('Inline edit commit error: ' + err.message);
      restore();
    }
  }

  function doCancel() {
    if (committed) return;
    committed = true;
    _activeEdit = null;
    restore();
  }

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      doCommit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      doCancel();
    }
  }, { once: false });

  // Blur commits only if the value changed; otherwise treats as cancel.
  input.addEventListener('blur', function () {
    if (committed) return;
    const newVal = parseFloat(input.value);
    if (isNaN(newVal) || String(newVal) === String(numericValue)) {
      doCancel();
    } else {
      doCommit();
    }
  });
}

// ── Qty commit ─────────────────────────────────────────────────────────────

/**
 * Commit a qty change via adjust_part (SET mode), mirroring the Adjust modal path.
 * @param {import('../types.js').InventoryItem} item
 * @param {string} rawValue
 * @param {() => void} restore
 */
async function commitQty(item, rawValue, restore) {
  const newQty = parseInt(rawValue, 10);
  if (isNaN(newQty) || newQty < 0) {
    showToast('Invalid quantity');
    restore();
    return;
  }
  const pk = invPartKey(item);

  UndoRedo.save('adjust', {
    _undoType: 'adjust',
    partKey: pk,
    adjType: 'set',
    qty: newQty,
    note: 'inline-edit',
    priceChanged: false,
    oldUp: item.unit_price || 0,
    oldEp: item.ext_price  || 0,
    newUp: null,
    newEp: null,
  });

  const result = await api('adjust_part', 'set', pk, newQty, 'inline-edit');
  if (!result) {
    UndoRedo.popLast();
    restore();
    return;
  }

  onInventoryUpdated(result);
  showToast('Updated qty for ' + pk);
}

// ── Price commit ───────────────────────────────────────────────────────────

/**
 * Commit a unit-price change via update_part_price, mirroring the Price modal path.
 * @param {import('../types.js').InventoryItem} item
 * @param {string} rawValue
 * @param {() => void} restore
 */
async function commitPrice(item, rawValue, restore) {
  const newUp = parseFloat(rawValue);
  if (isNaN(newUp) || newUp < 0) {
    showToast('Invalid price');
    restore();
    return;
  }
  const pk = invPartKey(item);
  const oldUp = item.unit_price || 0;
  const oldEp = item.ext_price  || 0;

  UndoRedo.save('price', {
    _undoType: 'price',
    partKey: pk,
    oldUp,
    oldEp,
    newUp,
    newEp: null,
  });

  const result = await api('update_part_price', pk, newUp, null);
  if (!result) {
    UndoRedo.popLast();
    restore();
    return;
  }

  onInventoryUpdated(result);
  showToast('Updated price for ' + pk);
}

// ── Public: wire a rendered row ────────────────────────────────────────────

/**
 * Wire dblclick inline-edit handlers onto qty and unit-price cells of a
 * freshly-rendered inventory part row.
 *
 * Called by inv-row-build.js createPartRow() after innerHTML is set.
 *
 * @param {HTMLElement} row   - the .inv-part-row element
 * @param {import('../types.js').InventoryItem} item - the item rendered in this row
 */
export function activateInlineEdit(row, item) {
  const qtyCell = /** @type {HTMLElement|null} */ (row.querySelector('.part-qty'));
  const priceCell = /** @type {HTMLElement|null} */ (row.querySelector('.part-unit-price'));

  if (qtyCell) {
    // title affordance — discoverable without visual clutter
    qtyCell.title = 'Double-click to edit qty';
    qtyCell.addEventListener('dblclick', function (e) {
      if (isEditBlocked()) return;
      e.stopPropagation();
      e.preventDefault();

      // Suppress the warnBtn click that normally fires after dblclick
      const warnBtn = /** @type {HTMLElement|null} */ (qtyCell.querySelector('.price-warn-btn'));
      const numericQty = item.qty;
      enterEditMode({
        cell: qtyCell,
        initialText: String(numericQty),
        numericValue: numericQty,
        kind: 'qty',
        commit: (rawValue, restore) => commitQty(item, rawValue, restore),
      });
      if (warnBtn) warnBtn.blur();
    });
  }

  if (priceCell) {
    priceCell.title = 'Double-click to edit unit price';
    priceCell.addEventListener('dblclick', function (e) {
      if (isEditBlocked()) return;
      e.stopPropagation();
      e.preventDefault();

      const numericPrice = Number(item.unit_price) || 0;
      enterEditMode({
        cell: priceCell,
        initialText: priceCell.textContent || '',
        numericValue: numericPrice,
        kind: 'price',
        commit: (rawValue, restore) => commitPrice(item, rawValue, restore),
      });
    });
  }
}
