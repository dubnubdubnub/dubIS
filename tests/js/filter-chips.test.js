// @ts-check
/**
 * tests/js/filter-chips.test.js
 *
 * TDD unit tests for:
 *   - buildInventoryFields (fields descriptor; enum options from live inventory)
 *   - extractInventoryField (computed "value" and virtual "distributor" fields)
 *   - filterByPredicate (inventory-specific filtering across text/number/enum operators)
 */

import { describe, it, expect } from 'vitest';
import { buildInventoryFields, extractInventoryField, filterByPredicate } from '../../js/inventory/filter-chips-fields.js';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_INVENTORY = [
  {
    section: 'Resistors',
    lcsc: 'C100000',
    mpn: 'RC0402FR-0710KL',
    description: '10k Resistor 0402',
    package: '0402',
    qty: 100,
    unit_price: 0.01,
    ext_price: 1.0,
    manufacturer: 'Yageo',
    digikey: '',
    mouser: '',
    pololu: '',
  },
  {
    section: 'Capacitors',
    lcsc: 'C200000',
    mpn: 'GRM155R71C104KA88D',
    description: '100nF Capacitor 0402',
    package: '0402',
    qty: 200,
    unit_price: 0.005,
    ext_price: 1.0,
    manufacturer: 'Murata',
    digikey: '',
    mouser: '',
    pololu: '',
  },
  {
    section: 'ICs',
    lcsc: '',
    mpn: 'LM358',
    description: 'Op-Amp',
    package: 'SOIC-8',
    qty: 10,
    unit_price: 0.5,
    ext_price: 5.0,
    manufacturer: 'TI',
    digikey: 'LM358-N/NOPB',
    mouser: '',
    pololu: '',
  },
  {
    section: 'Connectors',
    lcsc: '',
    mpn: 'PRT-09140',
    description: 'USB-B Connector',
    package: 'Through-Hole',
    qty: 5,
    unit_price: 1.0,
    ext_price: 5.0,
    manufacturer: 'SparkFun',
    digikey: '',
    mouser: '',
    pololu: 'PRT-09140',
  },
  {
    section: 'Connectors',
    lcsc: '',
    mpn: 'DIRECT-PART',
    description: 'Self-made part',
    package: '',
    qty: 0,
    unit_price: 0,
    ext_price: 0,
    manufacturer: '',
    digikey: '',
    mouser: '',
    pololu: '',
  },
];

// ── buildInventoryFields ──────────────────────────────────────────────────────

describe('buildInventoryFields', () => {
  it('returns an array of field descriptors', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    expect(Array.isArray(fields)).toBe(true);
    expect(fields.length).toBeGreaterThan(0);
  });

  it('includes all expected fields: mpn, description, package, qty, unit_price, value, distributor, section', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('mpn');
    expect(keys).toContain('description');
    expect(keys).toContain('package');
    expect(keys).toContain('qty');
    expect(keys).toContain('unit_price');
    expect(keys).toContain('value');
    expect(keys).toContain('distributor');
    expect(keys).toContain('section');
  });

  it('text fields have type "text"', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    for (const key of ['mpn', 'description', 'package']) {
      const f = fields.find((f) => f.key === key);
      expect(f).toBeTruthy();
      expect(f.type).toBe('text');
    }
  });

  it('number fields have type "number"', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    for (const key of ['qty', 'unit_price', 'value']) {
      const f = fields.find((f) => f.key === key);
      expect(f).toBeTruthy();
      expect(f.type).toBe('number');
    }
  });

  it('distributor field has type "enum" with fixed options', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    const f = fields.find((f) => f.key === 'distributor');
    expect(f).toBeTruthy();
    expect(f.type).toBe('enum');
    expect(Array.isArray(f.options)).toBe(true);
    expect(f.options).toContain('lcsc');
    expect(f.options).toContain('digikey');
    expect(f.options).toContain('mouser');
    expect(f.options).toContain('pololu');
    expect(f.options).toContain('direct');
  });

  it('section field has type "enum" with options derived from live inventory', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    const f = fields.find((f) => f.key === 'section');
    expect(f).toBeTruthy();
    expect(f.type).toBe('enum');
    // Should derive unique sections from the mock inventory
    expect(f.options).toContain('Resistors');
    expect(f.options).toContain('Capacitors');
    expect(f.options).toContain('ICs');
    expect(f.options).toContain('Connectors');
  });

  it('section enum options are sorted alphabetically', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    const f = fields.find((f) => f.key === 'section');
    const sorted = [...f.options].sort();
    expect(f.options).toEqual(sorted);
  });

  it('section enum options deduplicate (Connectors appears twice in fixture)', () => {
    const fields = buildInventoryFields(MOCK_INVENTORY);
    const f = fields.find((f) => f.key === 'section');
    const connCount = f.options.filter((o) => o === 'Connectors').length;
    expect(connCount).toBe(1);
  });

  it('works on an empty inventory', () => {
    const fields = buildInventoryFields([]);
    const f = fields.find((f) => f.key === 'section');
    expect(f).toBeTruthy();
    expect(f.options).toEqual([]);
  });
});

// ── extractInventoryField ──────────────────────────────────────────────────────

describe('extractInventoryField', () => {
  it('returns direct field values for standard fields', () => {
    const item = MOCK_INVENTORY[0];
    expect(extractInventoryField(item, 'mpn')).toBe('RC0402FR-0710KL');
    expect(extractInventoryField(item, 'qty')).toBe(100);
    expect(extractInventoryField(item, 'package')).toBe('0402');
  });

  it('computes "value" as qty × unit_price', () => {
    const item = MOCK_INVENTORY[0]; // qty=100, unit_price=0.01 → value=1.0
    expect(extractInventoryField(item, 'value')).toBeCloseTo(1.0);
  });

  it('computes "value" correctly for capacitor item', () => {
    const item = MOCK_INVENTORY[1]; // qty=200, unit_price=0.005 → value=1.0
    expect(extractInventoryField(item, 'value')).toBeCloseTo(1.0);
  });

  it('computes "value" = 0 when qty is 0', () => {
    const item = MOCK_INVENTORY[4]; // qty=0
    expect(extractInventoryField(item, 'value')).toBe(0);
  });

  it('extracts "distributor" as "lcsc" for item with lcsc PN', () => {
    const item = MOCK_INVENTORY[0]; // has lcsc
    expect(extractInventoryField(item, 'distributor')).toBe('lcsc');
  });

  it('extracts "distributor" as "digikey" for item with digikey PN but no lcsc', () => {
    const item = MOCK_INVENTORY[2]; // lcsc='', digikey='LM358-N/NOPB'
    expect(extractInventoryField(item, 'distributor')).toBe('digikey');
  });

  it('extracts "distributor" as "pololu" for item with pololu PN but no lcsc/digikey', () => {
    const item = MOCK_INVENTORY[3]; // pololu='PRT-09140'
    expect(extractInventoryField(item, 'distributor')).toBe('pololu');
  });

  it('extracts "distributor" as "direct" for item with no distributor PNs', () => {
    const item = MOCK_INVENTORY[4]; // no distributor PNs
    expect(extractInventoryField(item, 'distributor')).toBe('direct');
  });
});

// ── filterByPredicate ─────────────────────────────────────────────────────────

describe('filterByPredicate', () => {
  it('returns all parts when predicate is null', () => {
    const result = filterByPredicate(MOCK_INVENTORY, null);
    expect(result).toHaveLength(MOCK_INVENTORY.length);
  });

  it('returns all parts when predicate is undefined', () => {
    const result = filterByPredicate(MOCK_INVENTORY, undefined);
    expect(result).toHaveLength(MOCK_INVENTORY.length);
  });

  it('returns all parts when predicate has empty rules', () => {
    const result = filterByPredicate(MOCK_INVENTORY, { op: 'and', rules: [] });
    expect(result).toHaveLength(MOCK_INVENTORY.length);
  });

  it('filters by text field: mpn contains "RC"', () => {
    const ast = { op: 'and', rules: [{ field: 'mpn', operator: 'contains', value: 'RC' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('RC0402FR-0710KL');
  });

  it('filters by text field: description contains "Resistor" (case-insensitive)', () => {
    const ast = { op: 'and', rules: [{ field: 'description', operator: 'contains', value: 'resistor' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1);
    expect(result[0].section).toBe('Resistors');
  });

  it('filters by text field: package is "0402"', () => {
    const ast = { op: 'and', rules: [{ field: 'package', operator: 'is', value: '0402' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(2);
  });

  it('filters by number field: qty > 50', () => {
    const ast = { op: 'and', rules: [{ field: 'qty', operator: 'gt', value: 50 }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result.every((r) => r.qty > 50)).toBe(true);
    expect(result).toHaveLength(2); // 100 and 200
  });

  it('filters by number field: qty < 10', () => {
    const ast = { op: 'and', rules: [{ field: 'qty', operator: 'lt', value: 10 }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result.every((r) => r.qty < 10)).toBe(true);
    expect(result).toHaveLength(2); // qty=0 (DIRECT-PART) and qty=5 (PRT-09140)
  });

  it('filters by computed "value" field: value >= 1.0', () => {
    const ast = { op: 'and', rules: [{ field: 'value', operator: 'gte', value: 1.0 }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    // qty*unit_price: 100*0.01=1.0, 200*0.005=1.0, 10*0.5=5.0, 5*1.0=5.0, 0*0=0
    expect(result.length).toBeGreaterThanOrEqual(4);
    expect(result.every((r) => r.qty * r.unit_price >= 1.0)).toBe(true);
  });

  it('filters by virtual "distributor" field: distributor is "lcsc"', () => {
    const ast = { op: 'and', rules: [{ field: 'distributor', operator: 'is', value: 'lcsc' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result.every((r) => r.lcsc)).toBe(true);
    expect(result).toHaveLength(2); // C100000, C200000
  });

  it('filters by virtual "distributor" field: distributor is "digikey"', () => {
    const ast = { op: 'and', rules: [{ field: 'distributor', operator: 'is', value: 'digikey' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('LM358');
  });

  it('filters by virtual "distributor" field: distributor is "direct"', () => {
    const ast = { op: 'and', rules: [{ field: 'distributor', operator: 'is', value: 'direct' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('DIRECT-PART');
  });

  it('filters by section enum: section is "Resistors"', () => {
    const ast = { op: 'and', rules: [{ field: 'section', operator: 'is', value: 'Resistors' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('RC0402FR-0710KL');
  });

  it('AND combination: package=0402 AND qty > 100', () => {
    const ast = {
      op: 'and',
      rules: [
        { field: 'package', operator: 'is', value: '0402' },
        { field: 'qty', operator: 'gt', value: 100 },
      ],
    };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(1); // only capacitor has qty=200 > 100
    expect(result[0].mpn).toBe('GRM155R71C104KA88D');
  });

  it('OR combination: section=Resistors OR section=ICs', () => {
    const ast = {
      op: 'or',
      rules: [
        { field: 'section', operator: 'is', value: 'Resistors' },
        { field: 'section', operator: 'is', value: 'ICs' },
      ],
    };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(2);
    const sections = result.map((r) => r.section);
    expect(sections).toContain('Resistors');
    expect(sections).toContain('ICs');
  });

  it('text field "not_empty" matches parts with a package set', () => {
    const ast = { op: 'and', rules: [{ field: 'package', operator: 'not_empty' }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    // DIRECT-PART has empty package; '-' counts as non-empty for the XT60 items
    // In our mock, all except DIRECT-PART have a non-empty package
    expect(result.every((r) => r.package && r.package !== '')).toBe(true);
  });

  it('number "between" operator: qty between 5 and 100', () => {
    const ast = { op: 'and', rules: [{ field: 'qty', operator: 'between', value: [5, 100] }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result.every((r) => r.qty >= 5 && r.qty <= 100)).toBe(true);
    expect(result.map((r) => r.qty).sort((a, b) => a - b)).toEqual([5, 10, 100]);
  });

  it('returns empty array when no items match', () => {
    const ast = { op: 'and', rules: [{ field: 'qty', operator: 'gt', value: 9999 }] };
    const result = filterByPredicate(MOCK_INVENTORY, ast);
    expect(result).toHaveLength(0);
  });

  it('preserves reference equality for unfiltered items (same objects)', () => {
    const result = filterByPredicate(MOCK_INVENTORY, null);
    expect(result).toBe(MOCK_INVENTORY);
  });
});
