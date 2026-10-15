import { describe, it, expect } from 'vitest';
import {
  groupBySection,
  filterByQuery,
  bucketSection,
  computeMatchedInvKeys,
  computeCollapsedState,
  sortBomRows,
  buildRowMap,
  BOM_STATUS_SORT_ORDER,
  inferDistributor,
  countByDistributor,
  filterByDistributor,
  filterByVendor,
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

// ── filterByQuery: a term that NAMES the category keeps the whole bucket ──
//
// The bug this covers is a search that returns literally nothing. Every Diodes
// row's description reads "Diode 100V 2A Surface Mount", so "diodes" matched no
// haystack and the category was unsearchable by its own name. The rule lives in
// js/category-match.js and is pinned case-by-case by
// tests/js/category-match-cases.test.mjs; what is under test HERE is the
// predicate's contract around it — that the bucket is kept whole, that it is
// opt-in, and that a hit in one bucket does not reach into another.

describe('filterByQuery — category hit', () => {
  // Shaped like two real buckets out of groupBySection: rows whose text says the
  // singular, in a section named by the plural.
  var diodes = [
    { section: 'Diodes', lcsc: 'C14996', description: 'Diode 100V 2A Surface Mount SMA' },
    { section: 'Diodes', lcsc: 'C2128', description: 'Diode Independent 100V 150mA' },
  ];
  var leds = [
    { section: 'LEDs', lcsc: 'C2286', description: 'Red 645nm LED Indication - Discrete' },
  ];
  var ungrouped = [
    { section: 'Connectors', lcsc: 'C98732', description: 'Banana and Tip Connector Adapters' },
  ];
  var child = [
    { section: 'Connectors > SMD', lcsc: 'C429942', description: '' },
  ];

  it('keeps the whole bucket when the term names its category', () => {
    expect(filterByQuery(diodes, 'diodes', { categoryHit: true })).toHaveLength(2);
  });

  it('finds rows whose text never contains the term', () => {
    // The point of the feature: neither description contains "diodes".
    for (const row of diodes) {
      expect(row.description.toLowerCase()).not.toContain('diodes');
    }
    expect(filterByQuery(diodes, 'diodes', {})).toHaveLength(0);
  });

  it('does nothing when the toggle is off', () => {
    // Byte-identical to a plain text search — the preference is the only thing
    // that turns this on, so "off" must not be a subtly different filter.
    expect(filterByQuery(diodes, 'diodes', { categoryHit: false })).toHaveLength(0);
    expect(filterByQuery(diodes, 'diodes')).toHaveLength(0);
  });

  it('leaves every OTHER bucket to the text search', () => {
    // A category hit ADDS a category, it does not replace the search: typing
    // "diodes" must not also light up the LEDs bucket.
    expect(filterByQuery(leds, 'diodes', { categoryHit: true })).toHaveLength(0);
    expect(filterByQuery(leds, 'leds', { categoryHit: true })).toHaveLength(1);
  });

  it('does not let a short term swallow the grid', () => {
    // "ic" is a substring of nine IC section names. Whole-token matching is what
    // keeps this an empty result instead of "everything".
    var ics = [
      { section: 'ICs - Microcontrollers', lcsc: 'C1', description: 'MCU' },
      { section: 'ICs - Amplifiers', lcsc: 'C2', description: 'Op Amp' },
    ];
    expect(filterByQuery(ics, 'ic', { categoryHit: true })).toHaveLength(0);
    expect(filterByQuery(ics, 'ics', { categoryHit: true })).toHaveLength(2);
  });

  it('a parent name keeps a child bucket too', () => {
    // "Connectors > SMD" carries the parent's token, so typing the parent shows
    // the family rather than only the ungrouped rows under the parent header.
    expect(filterByQuery(child, 'connectors', { categoryHit: true })).toHaveLength(1);
    expect(filterByQuery(ungrouped, 'connectors', { categoryHit: true })).toHaveLength(1);
    // …and the child's own name does NOT reach up to the parent bucket.
    expect(filterByQuery(ungrouped, 'smd', { categoryHit: true })).toHaveLength(0);
  });

  it('still applies the text search inside a bucket the term does not name', () => {
    expect(filterByQuery(diodes, '100v', { categoryHit: true })).toHaveLength(2);
    expect(filterByQuery(diodes, '150ma', { categoryHit: true })).toHaveLength(1);
  });

  it('an empty bucket is kept empty rather than invented', () => {
    expect(filterByQuery([], 'diodes', { categoryHit: true })).toEqual([]);
  });
});

describe('bucketSection', () => {
  it('reads the section off the bucket it belongs to', () => {
    expect(bucketSection([{ section: 'Diodes' }, { section: 'Diodes' }])).toBe('Diodes');
  });

  it('reports "Other" for the bucket groupBySection files unsectioned rows under', () => {
    // Must agree with groupBySection's `item.section || "Other"` or the fallback
    // bucket would be the one category a term could never name.
    expect(bucketSection([{ section: '' }, { mpn: 'X' }])).toBe('Other');
    expect(filterByQuery([{ section: '' }], 'other', { categoryHit: true })).toHaveLength(1);
  });

  it('has no section to name when there is no bucket', () => {
    expect(bucketSection([])).toBe('');
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
  it('returns "direct" when item has no distributor fields', () => {
    expect(inferDistributor({ mpn: 'GENERIC' })).toBe('direct');
  });
  it('returns "direct" for parts with no distributor PN', () => {
    expect(inferDistributor({ mpn: 'TMR2615', primary_vendor_id: 'v_mdt' })).toBe('direct');
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
      lcsc: 2, digikey: 1, mouser: 0, pololu: 0, direct: 1,
    });
  });
  it('returns all zeros for empty inventory', () => {
    expect(countByDistributor([])).toEqual({
      lcsc: 0, digikey: 0, mouser: 0, pololu: 0, direct: 0,
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
  it('filters to direct parts only', () => {
    expect(filterByDistributor(parts, new Set(['direct']))).toEqual([parts[2]]);
  });
  it('multi-select: lcsc + direct shows combined', () => {
    expect(filterByDistributor(parts, new Set(['lcsc', 'direct']))).toEqual([parts[0], parts[2]]);
  });
  it('multi-select: all distributors shows everything', () => {
    expect(filterByDistributor(parts, new Set(['lcsc', 'digikey', 'direct']))).toEqual(parts);
  });
});

// ── filterByVendor tests ──

describe('filterByVendor', () => {
  it('returns all when set empty', () => {
    var parts = [{ primary_vendor_id: 'v_mdt' }, { primary_vendor_id: 'v_self' }];
    expect(filterByVendor(parts, new Set()).length).toBe(2);
  });
  it('returns all when vendorIds is null', () => {
    var parts = [{ primary_vendor_id: 'v_mdt' }, { primary_vendor_id: 'v_self' }];
    expect(filterByVendor(parts, null).length).toBe(2);
  });
  it('filters by id', () => {
    var parts = [{ primary_vendor_id: 'v_mdt' }, { primary_vendor_id: 'v_self' }];
    expect(filterByVendor(parts, new Set(['v_mdt'])).length).toBe(1);
    expect(filterByVendor(parts, new Set(['v_mdt']))[0].primary_vendor_id).toBe('v_mdt');
  });
  it('treats missing primary_vendor_id as v_unknown', () => {
    var parts = [{}, { primary_vendor_id: 'v_unknown' }];
    expect(filterByVendor(parts, new Set(['v_unknown'])).length).toBe(2);
  });
});
