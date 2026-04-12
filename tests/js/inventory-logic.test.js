import { describe, it, expect } from 'vitest';
import {
  groupBySection,
  filterByQuery,
  computeMatchedInvKeys,
  computeCollapsedState,
  sortBomRows,
  buildRowMap,
  BOM_STATUS_SORT_ORDER,
  inferDistributor,
  countByDistributor,
  filterByDistributor,
} from '../../js/inventory/inventory-logic.js';

// ── groupBySection tests ──

describe('groupBySection', () => {
  it('groups items by their section field', () => {
    var inventory = [
      { section: 'Resistors', mpn: 'R1' },
      { section: 'Capacitors', mpn: 'C1' },
      { section: 'Resistors', mpn: 'R2' },
    ];
    var groups = groupBySection(inventory);
    expect(Object.keys(groups)).toEqual(['Resistors', 'Capacitors']);
    expect(groups['Resistors']).toHaveLength(2);
    expect(groups['Capacitors']).toHaveLength(1);
  });

  it('puts items without section into "Other"', () => {
    var inventory = [
      { mpn: 'X1' },
      { section: '', mpn: 'X2' },
      { section: 'ICs', mpn: 'U1' },
    ];
    var groups = groupBySection(inventory);
    expect(groups['Other']).toHaveLength(2);
    expect(groups['ICs']).toHaveLength(1);
  });

  it('returns empty object for empty inventory', () => {
    expect(groupBySection([])).toEqual({});
  });

  it('handles compound section names', () => {
    var inventory = [
      { section: 'ICs > MCU', mpn: 'U1' },
      { section: 'ICs > USB', mpn: 'U2' },
      { section: 'ICs > MCU', mpn: 'U3' },
    ];
    var groups = groupBySection(inventory);
    expect(groups['ICs > MCU']).toHaveLength(2);
    expect(groups['ICs > USB']).toHaveLength(1);
  });
});

// ── filterByQuery tests ──

describe('filterByQuery', () => {
  var parts = [
    { lcsc: 'C12345', mpn: 'RC0805FR', description: '100k Resistor', manufacturer: 'Yageo', package: '0805', digikey: 'DK1', pololu: '', mouser: '' },
    { lcsc: 'C67890', mpn: 'GRM188R', description: '100nF Capacitor', manufacturer: 'Murata', package: '0603', digikey: '', pololu: 'POL1', mouser: '' },
    { lcsc: '', mpn: 'ATmega328', description: 'MCU', manufacturer: 'Microchip', package: 'QFP', digikey: '', pololu: '', mouser: 'MSR1' },
  ];

  it('returns all parts when query is empty', () => {
    expect(filterByQuery(parts, '')).toHaveLength(3);
  });

  it('filters by LCSC number', () => {
    expect(filterByQuery(parts, 'c12345')).toHaveLength(1);
    expect(filterByQuery(parts, 'c12345')[0].mpn).toBe('RC0805FR');
  });

  it('filters by MPN', () => {
    expect(filterByQuery(parts, 'grm188r')).toHaveLength(1);
  });

  it('filters by description', () => {
    expect(filterByQuery(parts, 'capacitor')).toHaveLength(1);
  });

  it('filters by manufacturer', () => {
    expect(filterByQuery(parts, 'yageo')).toHaveLength(1);
  });

  it('filters by package', () => {
    expect(filterByQuery(parts, '0805')).toHaveLength(1);
  });

  it('filters by digikey', () => {
    expect(filterByQuery(parts, 'dk1')).toHaveLength(1);
  });

  it('filters by pololu', () => {
    expect(filterByQuery(parts, 'pol1')).toHaveLength(1);
  });

  it('filters by mouser', () => {
    expect(filterByQuery(parts, 'msr1')).toHaveLength(1);
  });

  it('returns empty array when nothing matches', () => {
    expect(filterByQuery(parts, 'zzzznotfound')).toHaveLength(0);
  });

  it('is case-insensitive', () => {
    expect(filterByQuery(parts, 'RESISTOR')).toHaveLength(0); // query must be pre-lowercased
    expect(filterByQuery(parts, 'resistor')).toHaveLength(1);
  });
});

// ── computeCollapsedState tests ──

describe('computeCollapsedState', () => {
  it('returns true when section is in the set', () => {
    var collapsed = new Set(['Resistors', 'ICs']);
    expect(computeCollapsedState(collapsed, 'Resistors')).toBe(true);
  });

  it('returns false when section is not in the set', () => {
    var collapsed = new Set(['Resistors']);
    expect(computeCollapsedState(collapsed, 'Capacitors')).toBe(false);
  });

  it('returns false for empty set', () => {
    expect(computeCollapsedState(new Set(), 'Resistors')).toBe(false);
  });
});

// ── computeMatchedInvKeys tests ──

describe('computeMatchedInvKeys', () => {
  it('returns set of uppercase inventory part keys', () => {
    var bomData = {
      rows: [
        { inv: { lcsc: 'C12345', mpn: '' } },
        { inv: { lcsc: '', mpn: 'ATmega328' } },
        { inv: null },
      ],
    };
    var keys = computeMatchedInvKeys(bomData);
    expect(keys.has('C12345')).toBe(true);
    expect(keys.has('ATMEGA328')).toBe(true);
    expect(keys.size).toBe(2);
  });

  it('returns empty set when no rows have inventory', () => {
    var bomData = { rows: [{ inv: null }, { inv: null }] };
    expect(computeMatchedInvKeys(bomData).size).toBe(0);
  });

  it('returns empty set for null bomData', () => {
    expect(computeMatchedInvKeys(null).size).toBe(0);
  });

  it('returns empty set for bomData without rows', () => {
    expect(computeMatchedInvKeys({}).size).toBe(0);
  });
});

// ── sortBomRows tests ──

describe('sortBomRows', () => {
  it('sorts rows by status priority', () => {
    var rows = [
      { effectiveStatus: 'ok' },
      { effectiveStatus: 'missing' },
      { effectiveStatus: 'short' },
      { effectiveStatus: 'possible' },
    ];
    var sorted = sortBomRows(rows);
    expect(sorted.map(function (r) { return r.effectiveStatus; }))
      .toEqual(['missing', 'possible', 'short', 'ok']);
  });

  it('does not mutate original array', () => {
    var rows = [{ effectiveStatus: 'ok' }, { effectiveStatus: 'missing' }];
    var sorted = sortBomRows(rows);
    expect(rows[0].effectiveStatus).toBe('ok');
    expect(sorted[0].effectiveStatus).toBe('missing');
  });
});

// ── buildRowMap tests ──

describe('buildRowMap', () => {
  it('builds map from bomKey to row', () => {
    var rows = [
      { bom: { lcsc: 'C12345', mpn: '' } },
      { bom: { lcsc: '', mpn: 'ATmega328' } },
    ];
    var map = buildRowMap(rows);
    expect(map.size).toBe(2);
    expect(map.get('C12345')).toBe(rows[0]);
    expect(map.get('ATMEGA328')).toBe(rows[1]);
  });
});

// ── inferDistributor tests ──

describe('inferDistributor', () => {
  it('returns "lcsc" when item has lcsc field', () => {
    expect(inferDistributor({ lcsc: 'C12345' })).toBe('lcsc');
  });
  it('returns "digikey" when item has digikey but no lcsc', () => {
    expect(inferDistributor({ digikey: 'DK-123' })).toBe('digikey');
  });
  it('returns "mouser" when item has mouser but no lcsc/digikey', () => {
    expect(inferDistributor({ mouser: 'M-456' })).toBe('mouser');
  });
  it('returns "pololu" when item has pololu but no lcsc/digikey/mouser', () => {
    expect(inferDistributor({ pololu: 'P-789' })).toBe('pololu');
  });
  it('returns "other" when item has no distributor fields', () => {
    expect(inferDistributor({ mpn: 'GENERIC' })).toBe('other');
  });
  it('priority: lcsc wins over digikey', () => {
    expect(inferDistributor({ lcsc: 'C1', digikey: 'DK1' })).toBe('lcsc');
  });
});

// ── countByDistributor tests ──

describe('countByDistributor', () => {
  it('counts inventory items per distributor', () => {
    var inv = [
      { lcsc: 'C1' },
      { lcsc: 'C2' },
      { digikey: 'DK1' },
      { mpn: 'X' },
    ];
    expect(countByDistributor(inv)).toEqual({
      lcsc: 2, digikey: 1, mouser: 0, pololu: 0, other: 1,
    });
  });
  it('returns all zeros for empty inventory', () => {
    expect(countByDistributor([])).toEqual({
      lcsc: 0, digikey: 0, mouser: 0, pololu: 0, other: 0,
    });
  });
});

// ── filterByDistributor tests ──

describe('filterByDistributor', () => {
  var parts = [
    { lcsc: 'C1', mpn: 'A' },
    { digikey: 'DK1', mpn: 'B' },
    { mpn: 'C' },
  ];
  it('returns all parts when filter set is empty', () => {
    expect(filterByDistributor(parts, new Set())).toEqual(parts);
  });
  it('returns all parts when filter is null', () => {
    expect(filterByDistributor(parts, null)).toEqual(parts);
  });
  it('filters to lcsc parts only', () => {
    expect(filterByDistributor(parts, new Set(['lcsc']))).toEqual([parts[0]]);
  });
  it('filters to other parts only', () => {
    expect(filterByDistributor(parts, new Set(['other']))).toEqual([parts[2]]);
  });
  it('multi-select: lcsc + other shows combined', () => {
    expect(filterByDistributor(parts, new Set(['lcsc', 'other']))).toEqual([parts[0], parts[2]]);
  });
  it('multi-select: all distributors shows everything', () => {
    expect(filterByDistributor(parts, new Set(['lcsc', 'digikey', 'other']))).toEqual(parts);
  });
});
