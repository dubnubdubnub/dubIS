// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { matchesPredicate, PredicateEditor } from '../../js/components/predicate-ui.js';

// ─── matchesPredicate ─────────────────────────────────────────────────────────

describe('matchesPredicate — null/empty ast', () => {
  it('returns true for null ast', () => {
    expect(matchesPredicate({ x: 1 }, null)).toBe(true);
  });

  it('returns true for undefined ast', () => {
    expect(matchesPredicate({ x: 1 }, undefined)).toBe(true);
  });

  it('returns true for group with empty rules array', () => {
    expect(matchesPredicate({ x: 1 }, { op: 'and', rules: [] })).toBe(true);
  });
});

describe('matchesPredicate — text operators', () => {
  const item = { name: 'Hello World', note: '' };

  it('contains — matches substring (case-insensitive)', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'contains', value: 'hello' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('contains — does not match absent substring', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'contains', value: 'xyz' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('not_contains — true when substring absent', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'not_contains', value: 'xyz' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('not_contains — false when substring present', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'not_contains', value: 'world' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('is — exact match (case-insensitive)', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'is', value: 'hello world' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('is — false when different', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'is', value: 'hello' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('is_not — true when different', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'is_not', value: 'other' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('is_not — false when same (case-insensitive)', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'is_not', value: 'HELLO WORLD' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('empty — true when field is empty string', () => {
    const ast = { op: 'and', rules: [{ field: 'note', operator: 'empty' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('empty — false when field has content', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'empty' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('not_empty — true when field has content', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'not_empty' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('not_empty — false when field is empty string', () => {
    const ast = { op: 'and', rules: [{ field: 'note', operator: 'not_empty' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('missing field treated as undefined — empty returns true', () => {
    const ast = { op: 'and', rules: [{ field: 'nonexistent', operator: 'empty' }] };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('missing field — contains returns false', () => {
    const ast = { op: 'and', rules: [{ field: 'nonexistent', operator: 'contains', value: 'x' }] };
    expect(matchesPredicate(item, ast)).toBe(false);
  });
});

describe('matchesPredicate — number operators', () => {
  const item = { qty: 10, price: 3.5 };

  it('eq — true when equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'eq', value: 10 }] })).toBe(true);
  });

  it('eq — false when not equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'eq', value: 5 }] })).toBe(false);
  });

  it('ne — true when different', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'ne', value: 5 }] })).toBe(true);
  });

  it('ne — false when equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'ne', value: 10 }] })).toBe(false);
  });

  it('lt — true when less than', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'lt', value: 20 }] })).toBe(true);
  });

  it('lt — false when equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'lt', value: 10 }] })).toBe(false);
  });

  it('lte — true when less than or equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'lte', value: 10 }] })).toBe(true);
  });

  it('gt — true when greater than', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'gt', value: 5 }] })).toBe(true);
  });

  it('gt — false when equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'gt', value: 10 }] })).toBe(false);
  });

  it('gte — true when equal', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'gte', value: 10 }] })).toBe(true);
  });

  it('between — inclusive on both ends', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'between', value: [5, 10] }] })).toBe(true);
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'between', value: [10, 20] }] })).toBe(true);
  });

  it('between — false when outside range', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'between', value: [1, 9] }] })).toBe(false);
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'qty', operator: 'between', value: [11, 20] }] })).toBe(false);
  });

  it('coerces string numbers via Number()', () => {
    const strItem = { qty: '10' };
    expect(matchesPredicate(strItem, { op: 'and', rules: [{ field: 'qty', operator: 'eq', value: 10 }] })).toBe(true);
  });

  it('missing field returns false for numeric comparisons', () => {
    expect(matchesPredicate({}, { op: 'and', rules: [{ field: 'qty', operator: 'gt', value: 0 }] })).toBe(false);
  });
});

describe('matchesPredicate — enum operators', () => {
  const item = { status: 'active', category: 'resistor' };

  it('is — matches enum value', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'status', operator: 'is', value: 'active' }] })).toBe(true);
  });

  it('is — false for non-matching', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'status', operator: 'is', value: 'inactive' }] })).toBe(false);
  });

  it('is_not — true for different', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'status', operator: 'is_not', value: 'inactive' }] })).toBe(true);
  });

  it('in — true when value is in array', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'category', operator: 'in', value: ['resistor', 'capacitor'] }] })).toBe(true);
  });

  it('in — false when value is NOT in array', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'category', operator: 'in', value: ['capacitor', 'inductor'] }] })).toBe(false);
  });
});

describe('matchesPredicate — bool operators', () => {
  const item = { active: true, archived: false };

  it('is_true — true when field is truthy', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'active', operator: 'is_true' }] })).toBe(true);
  });

  it('is_true — false when field is false', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'archived', operator: 'is_true' }] })).toBe(false);
  });

  it('is_false — true when field is falsy', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'archived', operator: 'is_false' }] })).toBe(true);
  });

  it('is_false — false when field is truthy', () => {
    expect(matchesPredicate(item, { op: 'and', rules: [{ field: 'active', operator: 'is_false' }] })).toBe(false);
  });
});

describe('matchesPredicate — and/or combinations', () => {
  const item = { qty: 5, status: 'active', name: 'Resistor' };

  it('and — true only when ALL rules match', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'qty', operator: 'gt', value: 3 },
        { field: 'status', operator: 'is', value: 'active' },
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('and — false when any rule fails', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'qty', operator: 'gt', value: 3 },
        { field: 'status', operator: 'is', value: 'inactive' },
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('or — true when at least one rule matches', () => {
    const ast = {
      op: 'or',
      rules: [
        { field: 'qty', operator: 'gt', value: 100 },    // false
        { field: 'status', operator: 'is', value: 'active' }, // true
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('or — false when no rule matches', () => {
    const ast = {
      op: 'or',
      rules: [
        { field: 'qty', operator: 'gt', value: 100 },
        { field: 'status', operator: 'is', value: 'inactive' },
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(false);
  });

  it('nested groups — and containing an or sub-group', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'name', operator: 'contains', value: 'Res' },
        {
          op: 'or',
          rules: [
            { field: 'qty', operator: 'gt', value: 100 },    // false
            { field: 'status', operator: 'is', value: 'active' }, // true
          ],
        },
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(true);
  });

  it('nested groups — and where sub-group fails', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'name', operator: 'contains', value: 'Res' },
        {
          op: 'or',
          rules: [
            { field: 'qty', operator: 'gt', value: 100 },
            { field: 'status', operator: 'is', value: 'inactive' },
          ],
        },
      ],
    };
    expect(matchesPredicate(item, ast)).toBe(false);
  });
});

describe('matchesPredicate — unknown operator throws', () => {
  it('throws for unknown operator (error policy)', () => {
    expect(() =>
      matchesPredicate({ x: 1 }, { op: 'and', rules: [{ field: 'x', operator: 'bogus' }] })
    ).toThrow();
  });
});

// ─── PredicateEditor ──────────────────────────────────────────────────────────

describe('PredicateEditor', () => {
  const fields = [
    { key: 'name', label: 'Name', type: 'text' },
    { key: 'qty', label: 'Qty', type: 'number' },
    { key: 'status', label: 'Status', type: 'enum', options: ['active', 'inactive'] },
    { key: 'active', label: 'Active', type: 'bool' },
  ];

  function makeEditor(value, onChange) {
    return PredicateEditor({ fields, value, onChange: onChange ?? vi.fn() });
  }

  it('returns an object with el, getValue, setValue', () => {
    const ed = makeEditor(null);
    expect(ed).toHaveProperty('el');
    expect(ed).toHaveProperty('getValue');
    expect(ed).toHaveProperty('setValue');
    expect(ed.el).toBeInstanceOf(HTMLElement);
  });

  it('renders with .pred-editor class on root element', () => {
    const ed = makeEditor(null);
    expect(ed.el.classList.contains('pred-editor')).toBe(true);
  });

  it('getValue returns null/empty ast when initialized with null', () => {
    const ed = makeEditor(null);
    const val = ed.getValue();
    // Either null or an empty group
    if (val !== null) {
      expect(Array.isArray(val.rules)).toBe(true);
      expect(val.rules.length).toBe(0);
    }
  });

  it('renders existing rules as chips with .pred-chip class', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'name', operator: 'contains', value: 'hello' },
      ],
    };
    const ed = makeEditor(ast);
    const chips = ed.el.querySelectorAll('.pred-chip');
    expect(chips.length).toBe(1);
  });

  it('getValue round-trips setValue', () => {
    const ast = {
      op: 'or',
      rules: [
        { field: 'qty', operator: 'gt', value: 5 },
        { field: 'name', operator: 'contains', value: 'foo' },
      ],
    };
    const ed = makeEditor(null);
    ed.setValue(ast);
    const result = ed.getValue();
    expect(result.op).toBe('or');
    expect(result.rules).toHaveLength(2);
    expect(result.rules[0]).toMatchObject({ field: 'qty', operator: 'gt', value: 5 });
    expect(result.rules[1]).toMatchObject({ field: 'name', operator: 'contains', value: 'foo' });
  });

  it('renders an "add filter" button (.pred-add)', () => {
    const ed = makeEditor(null);
    const addBtn = ed.el.querySelector('.pred-add');
    expect(addBtn).toBeTruthy();
  });

  it('renders an AND/OR toggle (.pred-op-toggle)', () => {
    const ast = { op: 'and', rules: [{ field: 'name', operator: 'contains', value: 'x' }] };
    const ed = makeEditor(ast);
    const toggle = ed.el.querySelector('.pred-op-toggle');
    expect(toggle).toBeTruthy();
  });

  it('clicking "add filter" adds a rule chip and calls onChange', () => {
    const onChange = vi.fn();
    const ed = makeEditor(null, onChange);
    const addBtn = ed.el.querySelector('.pred-add');
    addBtn.click();
    expect(ed.el.querySelectorAll('.pred-chip').length).toBeGreaterThan(0);
    expect(onChange).toHaveBeenCalled();
  });

  it('clicking remove button on a chip removes it and calls onChange', () => {
    const onChange = vi.fn();
    const ast = {
      op: 'and',
      rules: [{ field: 'name', operator: 'contains', value: 'hello' }],
    };
    const ed = makeEditor(ast, onChange);
    const removeBtn = ed.el.querySelector('.pred-chip-remove');
    expect(removeBtn).toBeTruthy();
    removeBtn.click();
    expect(ed.el.querySelectorAll('.pred-chip').length).toBe(0);
    expect(onChange).toHaveBeenCalled();
  });

  it('toggling op between AND and OR calls onChange with updated op', () => {
    const onChange = vi.fn();
    const ast = {
      op: 'and',
      rules: [
        { field: 'name', operator: 'contains', value: 'x' },
        { field: 'qty', operator: 'gt', value: 0 },
      ],
    };
    const ed = makeEditor(ast, onChange);
    const toggle = ed.el.querySelector('.pred-op-toggle');
    toggle.click();
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(lastCall[0].op).toBe('or');
  });

  it('setValue re-renders chips correctly', () => {
    const ed = makeEditor(null);
    const ast = {
      op: 'and',
      rules: [
        { field: 'name', operator: 'contains', value: 'foo' },
        { field: 'qty', operator: 'gt', value: 10 },
      ],
    };
    ed.setValue(ast);
    const chips = ed.el.querySelectorAll('.pred-chip');
    expect(chips.length).toBe(2);
  });

  it('chips contain field selector elements (.pred-field-sel)', () => {
    const ast = {
      op: 'and',
      rules: [{ field: 'name', operator: 'contains', value: 'hello' }],
    };
    const ed = makeEditor(ast);
    const fieldSel = ed.el.querySelector('.pred-field-sel');
    expect(fieldSel).toBeTruthy();
  });

  it('chips contain operator selector elements (.pred-op-sel)', () => {
    const ast = {
      op: 'and',
      rules: [{ field: 'name', operator: 'contains', value: 'hello' }],
    };
    const ed = makeEditor(ast);
    const opSel = ed.el.querySelector('.pred-op-sel');
    expect(opSel).toBeTruthy();
  });

  it('changing operator fires onChange with updated AST', () => {
    const onChange = vi.fn();
    const ast = {
      op: 'and',
      rules: [{ field: 'name', operator: 'contains', value: 'hello' }],
    };
    const ed = makeEditor(ast, onChange);
    const opSel = ed.el.querySelector('.pred-op-sel');
    // Simulate changing operator
    opSel.value = 'not_contains';
    opSel.dispatchEvent(new Event('change', { bubbles: true }));
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(lastCall[0].rules[0].operator).toBe('not_contains');
  });
});
