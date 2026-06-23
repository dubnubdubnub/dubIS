// @ts-check
/**
 * js/components/predicate-ui.js — Filter predicate primitives.
 *
 * AST types (JSON-serializable):
 *   Group = { op: "and" | "or", rules: Array<Rule|Group> }
 *   Rule  = { field: string, operator: string, value?: any }
 *
 * Exports:
 *   matchesPredicate(item, ast) → boolean  — pure, no DOM dependency
 *   PredicateEditor({ fields, value, onChange }) → { el, getValue, setValue }
 */

import { el } from '../dom/html.js';

// ─── matchesPredicate ─────────────────────────────────────────────────────────

/**
 * Evaluate whether an item matches the given predicate AST.
 *
 * @param {Record<string, any>} item
 * @param {any} ast  — Group | Rule | null | undefined
 * @returns {boolean}
 */
export function matchesPredicate(item, ast) {
  if (ast === null || ast === undefined) return true;
  if ('op' in ast) return evaluateGroup(item, ast);
  return evaluateRule(item, ast);
}

/**
 * @param {Record<string, any>} item
 * @param {{ op: string, rules: any[] }} group
 * @returns {boolean}
 */
function evaluateGroup(item, group) {
  const { op, rules } = group;
  if (!rules || rules.length === 0) return true;

  if (op === 'and') {
    for (const rule of rules) {
      if (!matchesPredicate(item, rule)) return false;
    }
    return true;
  }

  if (op === 'or') {
    for (const rule of rules) {
      if (matchesPredicate(item, rule)) return true;
    }
    return false;
  }

  throw new Error(`matchesPredicate: unknown group op "${op}"`);
}

/**
 * @param {Record<string, any>} item
 * @param {{ field: string, operator: string, value?: any }} rule
 * @returns {boolean}
 */
function evaluateRule(item, rule) {
  const { field, operator, value } = rule;
  const raw = item[field];

  switch (operator) {
    // ── text ──────────────────────────────────────────────────────────────────
    case 'contains': {
      if (raw === undefined || raw === null) return false;
      return String(raw).toLowerCase().includes(String(value).toLowerCase());
    }
    case 'not_contains': {
      if (raw === undefined || raw === null) return true;
      return !String(raw).toLowerCase().includes(String(value).toLowerCase());
    }
    case 'is': {
      if (raw === undefined || raw === null) return String(value) === '';
      return String(raw).toLowerCase() === String(value).toLowerCase();
    }
    case 'is_not': {
      if (raw === undefined || raw === null) return String(value) !== '';
      return String(raw).toLowerCase() !== String(value).toLowerCase();
    }
    case 'empty': {
      return raw === undefined || raw === null || String(raw) === '';
    }
    case 'not_empty': {
      return raw !== undefined && raw !== null && String(raw) !== '';
    }

    // ── number ─────────────────────────────────────────────────────────────
    case 'eq': {
      if (raw === undefined || raw === null) return false;
      return Number(raw) === Number(value);
    }
    case 'ne': {
      if (raw === undefined || raw === null) return true;
      return Number(raw) !== Number(value);
    }
    case 'lt': {
      if (raw === undefined || raw === null) return false;
      return Number(raw) < Number(value);
    }
    case 'lte': {
      if (raw === undefined || raw === null) return false;
      return Number(raw) <= Number(value);
    }
    case 'gt': {
      if (raw === undefined || raw === null) return false;
      return Number(raw) > Number(value);
    }
    case 'gte': {
      if (raw === undefined || raw === null) return false;
      return Number(raw) >= Number(value);
    }
    case 'between': {
      if (raw === undefined || raw === null) return false;
      const n = Number(raw);
      const [lo, hi] = value;
      return n >= Number(lo) && n <= Number(hi);
    }

    // ── enum ───────────────────────────────────────────────────────────────
    case 'in': {
      if (raw === undefined || raw === null) return false;
      if (!Array.isArray(value)) throw new Error("matchesPredicate: 'in' operator requires an array value");
      return value.includes(raw);
    }

    // ── bool ───────────────────────────────────────────────────────────────
    case 'is_true': {
      return Boolean(raw);
    }
    case 'is_false': {
      return !raw;
    }

    default:
      throw new Error(`matchesPredicate: unknown operator "${operator}"`);
  }
}

// ─── Operator metadata ────────────────────────────────────────────────────────

/** @type {Record<string, { label: string, types: string[], hasValue: boolean }>} */
const OPERATOR_META = {
  contains:    { label: 'contains',       types: ['text'],                    hasValue: true  },
  not_contains:{ label: 'not contains',   types: ['text'],                    hasValue: true  },
  is:          { label: 'is',             types: ['text', 'enum'],            hasValue: true  },
  is_not:      { label: 'is not',         types: ['text', 'enum'],            hasValue: true  },
  empty:       { label: 'is empty',       types: ['text'],                    hasValue: false },
  not_empty:   { label: 'is not empty',   types: ['text'],                    hasValue: false },
  eq:          { label: '=',              types: ['number'],                  hasValue: true  },
  ne:          { label: '≠',              types: ['number'],                  hasValue: true  },
  lt:          { label: '<',              types: ['number'],                  hasValue: true  },
  lte:         { label: '≤',             types: ['number'],                  hasValue: true  },
  gt:          { label: '>',              types: ['number'],                  hasValue: true  },
  gte:         { label: '≥',             types: ['number'],                  hasValue: true  },
  between:     { label: 'between',        types: ['number'],                  hasValue: true  },
  in:          { label: 'in',             types: ['enum'],                    hasValue: true  },
  is_true:     { label: 'is true',        types: ['bool'],                    hasValue: false },
  is_false:    { label: 'is false',       types: ['bool'],                    hasValue: false },
};

/**
 * Return the list of operators applicable for a given field type.
 * @param {string} type
 * @returns {string[]}
 */
function opsForType(type) {
  return Object.entries(OPERATOR_META)
    .filter(([, meta]) => meta.types.includes(type))
    .map(([op]) => op);
}

/**
 * Return a sensible default operator for a field type.
 * @param {string} type
 * @returns {string}
 */
function defaultOp(type) {
  const ops = opsForType(type);
  return ops[0] ?? 'is';
}

// ─── PredicateEditor ──────────────────────────────────────────────────────────

/**
 * @typedef {{ key: string, label: string, type: string, options?: string[] }} FieldDef
 */

/**
 * @typedef {{ op: string, rules: any[] }} GroupAst
 */

/**
 * Build a minimal editable predicate editor.
 *
 * @param {{ fields: FieldDef[], value: GroupAst|null, onChange: (ast: GroupAst) => void }} opts
 * @returns {{ el: HTMLElement, getValue: () => GroupAst, setValue: (ast: GroupAst|null) => void }}
 */
export function PredicateEditor({ fields, value, onChange }) {
  /** @type {GroupAst} */
  let state = value
    ? JSON.parse(JSON.stringify(value))
    : { op: 'and', rules: [] };

  const root = el('div', { class: 'pred-editor' });

  function emit() {
    onChange(JSON.parse(JSON.stringify(state)));
  }

  function render() {
    root.innerHTML = '';

    // AND/OR toggle (shown when there is at least one rule)
    if (state.rules.length >= 1) {
      const toggle = el('button', { class: 'pred-op-toggle', type: 'button' },
        state.op === 'and' ? 'AND' : 'OR'
      );
      toggle.addEventListener('click', () => {
        state.op = state.op === 'and' ? 'or' : 'and';
        emit();
        render();
      });
      root.appendChild(toggle);
    }

    // Rule chips
    const chipList = el('div', { class: 'pred-chips' });
    state.rules.forEach((rule, idx) => {
      chipList.appendChild(buildChip(rule, idx));
    });
    root.appendChild(chipList);

    // Add filter button
    const addBtn = el('button', { class: 'pred-add', type: 'button' }, '+ Add filter');
    addBtn.addEventListener('click', () => {
      const firstField = fields[0];
      if (!firstField) return;
      const newRule = {
        field: firstField.key,
        operator: defaultOp(firstField.type),
        value: firstField.type === 'number' ? 0 : '',
      };
      state.rules.push(newRule);
      emit();
      render();
    });
    root.appendChild(addBtn);
  }

  /**
   * Build a single chip element for a rule.
   * @param {{ field: string, operator: string, value?: any }} rule
   * @param {number} idx
   * @returns {HTMLElement}
   */
  function buildChip(rule, idx) {
    const chip = el('div', { class: 'pred-chip' });

    // Field selector
    const fieldSel = /** @type {HTMLSelectElement} */ (el('select', { class: 'pred-field-sel' }));
    for (const f of fields) {
      const opt = el('option', { value: f.key }, f.label);
      if (f.key === rule.field) opt.setAttribute('selected', '');
      fieldSel.appendChild(opt);
    }
    fieldSel.addEventListener('change', () => {
      rule.field = fieldSel.value;
      const fieldDef = fields.find((f) => f.key === rule.field);
      const type = fieldDef ? fieldDef.type : 'text';
      rule.operator = defaultOp(type);
      rule.value = type === 'number' ? 0 : '';
      state.rules[idx] = rule;
      emit();
      render();
    });
    chip.appendChild(fieldSel);

    // Operator selector
    const fieldDef = fields.find((f) => f.key === rule.field);
    const fieldType = fieldDef ? fieldDef.type : 'text';
    const opSel = /** @type {HTMLSelectElement} */ (el('select', { class: 'pred-op-sel' }));
    for (const op of opsForType(fieldType)) {
      const meta = OPERATOR_META[op];
      const opt = el('option', { value: op }, meta ? meta.label : op);
      if (op === rule.operator) opt.setAttribute('selected', '');
      opSel.appendChild(opt);
    }
    opSel.addEventListener('change', () => {
      rule.operator = opSel.value;
      state.rules[idx] = rule;
      emit();
      render();
    });
    chip.appendChild(opSel);

    // Value input (when operator needs a value)
    const opMeta = OPERATOR_META[rule.operator];
    if (opMeta && opMeta.hasValue) {
      /** @type {HTMLElement|null} */
      let valueEl = null;
      if (fieldType === 'enum' && fieldDef && fieldDef.options) {
        if (rule.operator === 'in') {
          // For "in" operator, use a text input (comma-separated)
          const inp = /** @type {HTMLInputElement} */ (el('input', { class: 'pred-value', type: 'text', value: Array.isArray(rule.value) ? rule.value.join(', ') : '' }));
          inp.addEventListener('change', () => {
            rule.value = inp.value.split(',').map((s) => s.trim()).filter(Boolean);
            state.rules[idx] = rule;
            emit();
          });
          valueEl = inp;
        } else {
          const sel = /** @type {HTMLSelectElement} */ (el('select', { class: 'pred-value' }));
          for (const opt of fieldDef.options) {
            const optEl = el('option', { value: opt }, opt);
            if (opt === rule.value) optEl.setAttribute('selected', '');
            sel.appendChild(optEl);
          }
          sel.addEventListener('change', () => {
            rule.value = sel.value;
            state.rules[idx] = rule;
            emit();
          });
          valueEl = sel;
        }
      } else if (fieldType === 'number') {
        if (rule.operator === 'between') {
          const lo = /** @type {HTMLInputElement} */ (el('input', { class: 'pred-value pred-value-lo', type: 'number', value: Array.isArray(rule.value) ? String(rule.value[0]) : '0' }));
          const hi = /** @type {HTMLInputElement} */ (el('input', { class: 'pred-value pred-value-hi', type: 'number', value: Array.isArray(rule.value) ? String(rule.value[1]) : '0' }));
          const onChange2 = () => {
            rule.value = [Number(lo.value), Number(hi.value)];
            state.rules[idx] = rule;
            emit();
          };
          lo.addEventListener('change', onChange2);
          hi.addEventListener('change', onChange2);
          chip.appendChild(lo);
          chip.appendChild(hi);
          valueEl = null;
        } else {
          const inp = /** @type {HTMLInputElement} */ (el('input', { class: 'pred-value', type: 'number', value: rule.value !== undefined ? String(rule.value) : '0' }));
          inp.addEventListener('change', () => {
            rule.value = Number(inp.value);
            state.rules[idx] = rule;
            emit();
          });
          valueEl = inp;
        }
      } else {
        // text
        const inp = /** @type {HTMLInputElement} */ (el('input', { class: 'pred-value', type: 'text', value: rule.value !== undefined ? String(rule.value) : '' }));
        inp.addEventListener('change', () => {
          rule.value = inp.value;
          state.rules[idx] = rule;
          emit();
        });
        valueEl = inp;
      }
      if (valueEl) chip.appendChild(valueEl);
    }

    // Remove button
    const removeBtn = el('button', { class: 'pred-chip-remove', type: 'button' }, '×');
    removeBtn.addEventListener('click', () => {
      state.rules.splice(idx, 1);
      emit();
      render();
    });
    chip.appendChild(removeBtn);

    return chip;
  }

  render();

  return {
    el: root,
    getValue() {
      return JSON.parse(JSON.stringify(state));
    },
    /** @param {GroupAst|null} ast */
    setValue(ast) {
      state = ast ? JSON.parse(JSON.stringify(ast)) : { op: 'and', rules: [] };
      render();
    },
  };
}
