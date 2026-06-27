// @ts-check
/**
 * js/inventory/filter-chips-bar.js — Composable filter chips toolbar bar.
 *
 * Renders a bar below the distributor-pills row containing:
 *   - A "+ Filter" button that opens a PredicateEditor popover
 *   - Applied chips (one per rule: field ▸ operator ▸ value), each editable + removable
 *   - An AND/OR toggle when there are 2+ chips
 *   - Cleared automatically when the existing "Clear Filters" button fires
 *
 * The active predicate AST is held in inv-state.js (`activePredicate`).
 * Re-renders call state._render() which runs the normal inventory render path
 * (inv-render.js now applies filterByPredicate alongside the existing filters).
 *
 * Exports:
 *   initFilterChipsBar(state, renderFn)  → void  (call once after inventory init)
 *   clearFilterChips()                   → void  (external call from Clear Filters btn)
 */

import { PredicateEditor } from '../components/predicate-ui.js';
import { buildInventoryFields } from './filter-chips-fields.js';
import { store } from '../store.js';
import { AppLog } from '../api.js';
import state from './inv-state.js';

// ── Module-level state ────────────────────────────────────────────────────────

/** @type {HTMLElement|null} */
let _bar = null;
/** @type {HTMLElement|null} */
let _popover = null;
/** @type {(() => void)|null} */
let _renderFn = null;
/** @type {Array<() => void>} */
let _popoverRemovers = [];

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Inject the filter-chips bar into the inventory panel and wire it up.
 * Must be called after inventory-panel.js init() has set state.body etc.
 *
 * @param {typeof state} inventoryState  inv-state.js default export
 * @param {() => void} renderFn  Full inventory re-render callback
 */
export function initFilterChipsBar(inventoryState, renderFn) {
  _renderFn = renderFn;

  // Insert bar after the dist-filter-bar (which is inside the panel-header)
  const panelHeader = document.querySelector('.panel-inventory .panel-header');
  if (!panelHeader) {
    AppLog.warn('filter-chips-bar: .panel-inventory .panel-header not found');
    return;
  }

  const bar = document.createElement('div');
  bar.className = 'filter-chips-bar';
  bar.id = 'filter-chips-bar';
  panelHeader.appendChild(bar);
  _bar = bar;

  // Initial render (empty bar)
  _renderBar(inventoryState);
}

/**
 * Clear all active filter chips and trigger a re-render.
 * Called by the existing "Clear Filters" button handler.
 */
export function clearFilterChips() {
  state.activePredicate = null;
  _renderBar(state);
  if (_renderFn) _renderFn();
}

/**
 * Sync the bar UI to reflect whatever activePredicate is currently in state
 * (called after applyView restores a saved predicate).
 * Does NOT trigger a full inventory re-render — the caller does that.
 *
 * @param {typeof state} inventoryState
 */
export function syncFilterChipsBar(inventoryState) {
  _renderBar(inventoryState);
}

// ── Bar rendering ─────────────────────────────────────────────────────────────

/**
 * Re-render the filter-chips bar to reflect the current activePredicate.
 * @param {typeof state} inventoryState
 */
function _renderBar(inventoryState) {
  if (!_bar) return;

  _bar.innerHTML = '';

  const ast = inventoryState.activePredicate;
  const rules = (ast && ast.rules) ? ast.rules : [];
  const hasChips = rules.length > 0;

  // AND/OR toggle (only when 2+ chips)
  if (rules.length >= 2) {
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'fc-op-toggle';
    toggle.title = 'Toggle AND / OR logic between filters';
    toggle.textContent = (ast && ast.op === 'or') ? 'OR' : 'AND';
    toggle.addEventListener('click', () => {
      if (!inventoryState.activePredicate) return;
      inventoryState.activePredicate = {
        op: inventoryState.activePredicate.op === 'and' ? 'or' : 'and',
        rules: inventoryState.activePredicate.rules,
      };
      _renderBar(inventoryState);
      if (_renderFn) _renderFn();
    });
    _bar.appendChild(toggle);
  }

  // Render one chip per rule
  const fields = buildInventoryFields(store.inventory || []);
  rules.forEach((rule, idx) => {
    const chip = _buildChip(rule, idx, fields, inventoryState);
    _bar.appendChild(chip);
  });

  // "+ Filter" button (always shown)
  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'fc-add-btn';
  addBtn.id = 'fc-add-filter-btn';
  addBtn.setAttribute('aria-label', 'Add filter');
  addBtn.textContent = '+ Filter';
  addBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    _openAddPopover(addBtn, inventoryState, fields);
  });
  _bar.appendChild(addBtn);

  // Show/hide the bar itself (always visible — acts as the filter affordance)
  _bar.classList.toggle('has-chips', hasChips);

  // Update clear-filter button disabled state to account for predicate chips
  _updateClearBtnState();
}

/**
 * Update the existing "Clear Filters" button disabled state to reflect chips.
 */
function _updateClearBtnState() {
  const clearBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('clear-dist-filter'));
  if (!clearBtn) return;
  const hasChips = !!(state.activePredicate &&
    state.activePredicate.rules &&
    state.activePredicate.rules.length > 0);
  const hasDistributor = state.activeDistributors && state.activeDistributors.size > 0;
  const hasSearch = state.searchInput && !!state.searchInput.value;
  clearBtn.disabled = !hasChips && !hasDistributor && !hasSearch;
}

// ── Chip rendering ────────────────────────────────────────────────────────────

const OPERATOR_LABELS = {
  contains: 'contains', not_contains: 'not contains',
  is: 'is', is_not: 'is not',
  empty: 'is empty', not_empty: 'is not empty',
  eq: '=', ne: '≠', lt: '<', lte: '≤', gt: '>', gte: '≥', between: 'between',
  in: 'in',
  is_true: 'is true', is_false: 'is false',
};

/**
 * @param {{ field: string, operator: string, value?: any }} rule
 * @param {number} idx
 * @param {Array<{ key: string, label: string, type: string, options?: string[] }>} fields
 * @param {typeof state} inventoryState
 * @returns {HTMLElement}
 */
function _buildChip(rule, idx, fields, inventoryState) {
  const chip = document.createElement('div');
  chip.className = 'fc-chip';
  chip.setAttribute('data-chip-idx', String(idx));

  const fieldDef = fields.find((f) => f.key === rule.field);
  const fieldLabel = fieldDef ? fieldDef.label : rule.field;
  const opLabel = OPERATOR_LABELS[rule.operator] || rule.operator;

  // Format value display
  let valueDisplay = '';
  const opMeta_needsValue = !['empty', 'not_empty', 'is_true', 'is_false'].includes(rule.operator);
  if (opMeta_needsValue) {
    if (rule.operator === 'between' && Array.isArray(rule.value)) {
      valueDisplay = rule.value[0] + '–' + rule.value[1];
    } else if (rule.operator === 'in' && Array.isArray(rule.value)) {
      valueDisplay = rule.value.join(', ');
    } else if (rule.value !== undefined && rule.value !== null && rule.value !== '') {
      valueDisplay = String(rule.value);
    }
  }

  chip.innerHTML =
    '<span class="fc-chip-field">' + _esc(fieldLabel) + '</span>' +
    '<span class="fc-chip-op">' + _esc(opLabel) + '</span>' +
    (valueDisplay ? '<span class="fc-chip-value">' + _esc(valueDisplay) + '</span>' : '') +
    '<button class="fc-chip-remove" type="button" title="Remove filter" aria-label="Remove ' + _esc(fieldLabel) + ' filter">×</button>';

  // Click on chip body (not remove btn) opens inline editor popover
  chip.addEventListener('click', (e) => {
    if (/** @type {Element} */ (e.target).closest('.fc-chip-remove')) return;
    e.stopPropagation();
    _openEditPopover(chip, idx, fields, inventoryState);
  });

  // Remove button
  const removeBtn = chip.querySelector('.fc-chip-remove');
  if (removeBtn) {
    removeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!inventoryState.activePredicate) return;
      inventoryState.activePredicate.rules.splice(idx, 1);
      if (inventoryState.activePredicate.rules.length === 0) {
        inventoryState.activePredicate = null;
      }
      _closePopover();
      _renderBar(inventoryState);
      if (_renderFn) _renderFn();
    });
  }

  return chip;
}

// ── Popover (add + edit) ──────────────────────────────────────────────────────

function _closePopover() {
  if (_popover) {
    _popover.remove();
    _popover = null;
  }
  for (const r of _popoverRemovers) r();
  _popoverRemovers = [];
}

/**
 * Open a popover anchored to the "+ Filter" button to add a new chip.
 * @param {HTMLElement} anchor
 * @param {typeof state} inventoryState
 * @param {Array<{ key: string, label: string, type: string, options?: string[] }>} fields
 */
function _openAddPopover(anchor, inventoryState, fields) {
  if (_popover) { _closePopover(); return; } // toggle off

  const initialAst = {
    op: (inventoryState.activePredicate && inventoryState.activePredicate.op) || 'and',
    rules: [{ field: fields[0].key, operator: _defaultOp(fields[0].type), value: fields[0].type === 'number' ? 0 : '' }],
  };

  _openPredicatePopover(anchor, initialAst, fields, inventoryState, (newRule) => {
    // Add the new rule to the existing predicate
    if (!inventoryState.activePredicate) {
      inventoryState.activePredicate = { op: 'and', rules: [] };
    }
    inventoryState.activePredicate.rules.push(newRule);
    _closePopover();
    _renderBar(inventoryState);
    if (_renderFn) _renderFn();
  }, true /* isAdd */);
}

/**
 * Open a popover anchored to a chip to edit an existing rule.
 * @param {HTMLElement} anchor
 * @param {number} idx
 * @param {Array<{ key: string, label: string, type: string, options?: string[] }>} fields
 * @param {typeof state} inventoryState
 */
function _openEditPopover(anchor, idx, fields, inventoryState) {
  if (_popover) { _closePopover(); return; } // toggle off

  if (!inventoryState.activePredicate || !inventoryState.activePredicate.rules[idx]) return;

  const rule = inventoryState.activePredicate.rules[idx];
  const editAst = { op: 'and', rules: [Object.assign({}, rule)] };

  _openPredicatePopover(anchor, editAst, fields, inventoryState, (updatedRule) => {
    if (!inventoryState.activePredicate) return;
    inventoryState.activePredicate.rules[idx] = updatedRule;
    _closePopover();
    _renderBar(inventoryState);
    if (_renderFn) _renderFn();
  }, false /* isAdd */);
}

/**
 * Generic predicate popover builder.
 *
 * @param {HTMLElement} anchor
 * @param {{ op: string, rules: any[] }} initialAst
 * @param {Array<{ key: string, label: string, type: string, options?: string[] }>} fields
 * @param {typeof state} inventoryState
 * @param {(rule: any) => void} onApply  Called with the single rule when "Apply" is clicked
 * @param {boolean} isAdd
 */
function _openPredicatePopover(anchor, initialAst, fields, inventoryState, onApply, isAdd) {
  const popover = document.createElement('div');
  popover.className = 'fc-popover';
  popover.setAttribute('role', 'dialog');
  popover.setAttribute('aria-label', isAdd ? 'Add filter' : 'Edit filter');

  // Live AST — updated by PredicateEditor onChange
  let liveAst = JSON.parse(JSON.stringify(initialAst));

  const editor = PredicateEditor({
    fields,
    value: liveAst,
    onChange: (ast) => {
      liveAst = ast;
    },
  });

  const applyBtn = document.createElement('button');
  applyBtn.type = 'button';
  applyBtn.className = 'fc-popover-apply btn-md';
  applyBtn.textContent = isAdd ? 'Add filter' : 'Apply';
  applyBtn.addEventListener('click', () => {
    const rules = liveAst.rules || [];
    if (rules.length === 0) {
      _closePopover();
      return;
    }
    onApply(rules[0]);
  });

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'fc-popover-cancel btn-md';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.addEventListener('click', () => _closePopover());

  const footer = document.createElement('div');
  footer.className = 'fc-popover-footer';
  footer.appendChild(cancelBtn);
  footer.appendChild(applyBtn);

  popover.appendChild(editor.el);
  popover.appendChild(footer);

  // Position below anchor
  document.body.appendChild(popover);
  _popover = popover;

  const anchorRect = anchor.getBoundingClientRect();
  popover.style.position = 'fixed';
  popover.style.top = (anchorRect.bottom + 4) + 'px';
  popover.style.left = anchorRect.left + 'px';
  popover.style.zIndex = '10000';

  // Clamp to viewport right edge
  const popRect = popover.getBoundingClientRect();
  if (popRect.right > window.innerWidth - 8) {
    popover.style.left = Math.max(8, window.innerWidth - popRect.width - 8) + 'px';
  }

  // Outside-click closes (deferred to skip current event)
  setTimeout(() => {
    const onDocClick = (/** @type {MouseEvent} */ e) => {
      if (_popover && !_popover.contains(/** @type {Node} */ (e.target)) &&
          !anchor.contains(/** @type {Node} */ (e.target))) {
        _closePopover();
      }
    };
    document.addEventListener('click', onDocClick);
    _popoverRemovers.push(() => document.removeEventListener('click', onDocClick));
  }, 0);

  // Esc closes
  const onKeydown = (/** @type {KeyboardEvent} */ e) => {
    if (e.key === 'Escape') { _closePopover(); anchor.focus(); }
  };
  document.addEventListener('keydown', onKeydown);
  _popoverRemovers.push(() => document.removeEventListener('keydown', onKeydown));
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const DEFAULT_OPS = { text: 'contains', number: 'lt', enum: 'is', bool: 'is_true' };

/**
 * @param {string} type
 * @returns {string}
 */
function _defaultOp(type) {
  return DEFAULT_OPS[type] || 'is';
}

/**
 * Minimal HTML escaping for chip label display.
 * @param {any} s
 * @returns {string}
 */
function _esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
