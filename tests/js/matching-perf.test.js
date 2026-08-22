/**
 * matching-perf.test.js — algorithmic guards for the BOM matcher.
 *
 * Nothing here is timed, and that is the point.
 *
 * These tests used to assert wall-clock budgets ("300 rows against 1000 items
 * under 1000ms"). On the self-hosted macOS runner, which runs several CI legs
 * at once, such a budget measures the runner's load and not the matcher: PR
 * #418 — a one-line YAML change to deploy/kustomization.yaml — failed at
 * 1900ms, and passed on rerun with nothing changed. Saturating that machine
 * reproduces it exactly: the 300x1000 match takes ~24ms idle and 484-1000ms
 * under load, and the whole suite fails 2 runs in 10.
 *
 * A ratio between two timed workloads does not rescue it either. Measured on
 * the same saturated machine, best-of-5 samples each, the ratio between a
 * 500-item and a 2000-item match ranged from 1.20 to 18.36 where an idle
 * machine gives 1.92-2.05. Descheduling is bursty, so it does not cancel
 * between two workloads. Nothing derived from a clock is trustworthy here.
 *
 * What these tests protect instead is the matcher's SHAPE: `buildLookupMaps`
 * indexes inventory once, and every exact match is an O(1) lookup into those
 * maps. The regression that matters is someone replacing `invByLCSC[key]` with
 * `inventory.find(...)`, which turns matching quadratic. That is measured
 * directly, by counting how many inventory items the matcher touches — the
 * inventory handed to `matchBOM` is a Proxy that tallies every element and
 * property read. The counts are exact integers, identical on any machine at
 * any load.
 *
 * They also catch what the millisecond budgets never could. Swap that hash
 * lookup for a full `inventory.find` scan and the old suite measures 24.7ms
 * against its 1000ms budget and 0.39ms against its 50ms one — indistinguishable
 * from healthy code, because 90 scanned rows over 1000 items is nothing for a
 * CPU even though it is the whole regression. The old suite passed the bug and
 * failed the scheduler. These counts do the reverse.
 *
 * The generators are seeded for the same reason — a perf guard that reads
 * different data every run is measuring the data as well as the code.
 *
 * Deliberately NOT covered: steps 3 and 4 of `matchBOM` (MPN prefix and fuzzy
 * matching) walk the entire MPN index for every row that reaches them, so a
 * BOM of near-miss MPNs is O(rows x inventory) by design. That scan iterates
 * the keys of a map built inside `matchBOM`, so it is invisible to the Proxy,
 * and it is left unguarded rather than guarded by a clock that lies.
 */

import { describe, it, expect } from 'vitest';
import { matchBOM } from '../../js/matching.js';
import { bomKey } from '../../js/part-keys.js';

// ── Deterministic synthetic data ──

const SECTIONS = [
  'Passives - Resistors',
  'Passives - Capacitors',
  'Passives - Inductors',
  'Connectors',
  'ICs - Microcontrollers',
  'ICs - Motor Drivers',
  'ICs - Power',
  'Crystals & Oscillators',
  'LEDs',
  'Other',
];

const PACKAGES = ['0201', '0402', '0603', '0805', '1206', 'SOT-23', 'LQFP-48', 'QFN-32', 'TSSOP-16', 'SMA'];

const RES_VALUES = ['100Ω', '1kΩ', '4.7kΩ', '10kΩ', '47kΩ', '100kΩ', '1MΩ', '220Ω', '330Ω', '2.2kΩ'];
const CAP_VALUES = ['100nF', '10nF', '1µF', '10µF', '22pF', '47pF', '100pF', '4.7µF', '22µF', '470nF'];
const IND_VALUES = ['10µH', '22µH', '100µH', '1µH', '4.7µH', '47µH', '220µH', '330µH', '2.2µH', '68µH'];

// mulberry32. The fixtures used to come from Math.random, which meant the work
// the matcher did varied run to run — an avoidable source of noise in a suite
// whose whole job is to notice when that work changes.
function makeRng(seed) {
  let s = seed >>> 0;
  return function next() {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function generateLCSC(i) {
  return 'C' + String(100000 + i);
}

function generateMPN(i) {
  const prefixes = ['STM32F', 'DRV83', 'TPS54', 'LM317', 'MAX32', 'ADS12', 'MCP23', 'ESP32', 'NRF52', 'AT24C'];
  const prefix = prefixes[i % prefixes.length];
  return prefix + String(1000 + i).slice(1) + 'ABCD'.charAt(i % 4);
}

function generateInventory(count, seed = 1) {
  const rnd = makeRng(seed);
  const pick = arr => arr[Math.floor(rnd() * arr.length)];
  const items = [];
  for (let i = 0; i < count; i++) {
    const section = SECTIONS[i % SECTIONS.length];
    let desc;
    if (section === 'Passives - Resistors') desc = 'Resistor ' + pick(RES_VALUES) + ' ±1%';
    else if (section === 'Passives - Capacitors') desc = 'Capacitor ' + pick(CAP_VALUES) + ' 50V';
    else if (section === 'Passives - Inductors') desc = 'Inductor ' + pick(IND_VALUES);
    else desc = 'Part ' + i + ' ' + section;

    items.push({
      lcsc: generateLCSC(i),
      mpn: generateMPN(i),
      section,
      description: desc,
      package: pick(PACKAGES),
      qty: 10 + (i % 50),
      unit_price: 0.01 + (i % 100) * 0.01,
    });
  }
  return items;
}

function generateBOM(count, inventory, seed = 2) {
  const rnd = makeRng(seed);
  const pick = arr => arr[Math.floor(rnd() * arr.length)];
  const entries = [];
  for (let i = 0; i < count; i++) {
    const bom = { lcsc: '', mpn: '', value: '', desc: '', refs: '', qty: 1 + (i % 5), footprint: '', dnp: false };

    if (i < count * 0.3) {
      // 30% exact LCSC match
      const inv = inventory[i % inventory.length];
      bom.lcsc = inv.lcsc;
      bom.refs = 'C' + (i + 1);
    } else if (i < count * 0.5) {
      // 20% exact MPN match
      const inv = inventory[(i * 3) % inventory.length];
      bom.mpn = inv.mpn;
      bom.refs = 'U' + (i + 1);
    } else if (i < count * 0.65) {
      // 15% prefix match (truncated MPN)
      const inv = inventory[(i * 7) % inventory.length];
      bom.mpn = inv.mpn.slice(0, Math.max(6, inv.mpn.length - 2));
      bom.refs = 'U' + (i + 1);
    } else if (i < count * 0.75) {
      // 10% fuzzy match (1-2 chars different at end)
      const inv = inventory[(i * 11) % inventory.length];
      const mpn = inv.mpn;
      bom.mpn = mpn.slice(0, -1) + 'Z';
      bom.refs = 'U' + (i + 1);
    } else if (i < count * 0.85) {
      // 10% value match (passive)
      bom.value = pick(CAP_VALUES).replace('µ', 'u').replace('Ω', '');
      bom.refs = 'C' + (i + 1);
      bom.footprint = pick(PACKAGES);
    } else {
      // 15% missing (no match)
      bom.mpn = 'NONEXISTENT_' + i;
      bom.refs = 'X' + (i + 1);
    }

    entries.push(bom);
  }
  return entries;
}

function bomMap(entries) {
  const m = new Map();
  entries.forEach(e => m.set(bomKey(e) || ('row-' + m.size), e));
  return m;
}

// A BOM of nothing but exact LCSC hits — the path that must be O(1) per row.
//
// Rows are spread across the whole inventory by a stride coprime with every
// size used here, not taken off the front. That matters: a linear scan finds
// item N after N comparisons, so a BOM that only ever asks for the first few
// hundred items costs the same to scan whether inventory holds 500 records or
// 8000, and the scan hides from any test that compares the two. Spread rows
// make the average scan depth half the inventory, which is what a scan
// actually costs in the field. The stride also walks all ten sections evenly,
// keeping the per-row read count identical across inventory sizes.
//
// `count` must not exceed `inv.length`: two rows landing on the same item would
// collide on bomKey and the Map would dedupe them. Callers read the row count
// off the returned Map rather than assuming `count`.
const BOM_STRIDE = 977;

function exactMatchBom(inv, count) {
  expect(count, 'exactMatchBom needs one distinct inventory item per row').toBeLessThanOrEqual(inv.length);
  const entries = [];
  for (let i = 0; i < count; i++) {
    const item = inv[(i * BOM_STRIDE) % inv.length];
    entries.push({ lcsc: item.lcsc, mpn: '', value: '', desc: '', refs: 'U' + i, qty: 1, footprint: '', dnp: false });
  }
  return bomMap(entries);
}

// ── The instrument ──

const ARRAY_INDEX = /^\d+$/;

/**
 * Wrap an inventory list so every element access and every property read on an
 * element is tallied. One "read" is one unit of work the matcher spent looking
 * at inventory, so the tally is a direct, machine-independent measure of how
 * much of the inventory the matcher had to touch.
 *
 * Item proxies are created once and reused, so identity comparisons inside
 * `matchBOM` (`findAlternatives` filters `c !== primaryInv`) behave normally.
 */
function meterInventory(items) {
  const meter = { reads: 0 };
  const tallyProperty = {
    get(target, prop, receiver) {
      meter.reads++;
      return Reflect.get(target, prop, receiver);
    },
  };
  const rows = items.map(item => new Proxy(item, tallyProperty));
  const inventory = new Proxy(rows, {
    get(target, prop, receiver) {
      if (typeof prop === 'string' && ARRAY_INDEX.test(prop)) meter.reads++;
      return Reflect.get(target, prop, receiver);
    },
  });
  return { inventory, meter };
}

/** Run one match and report how many inventory reads it took. */
function inventoryWork(bom, items) {
  const { inventory, meter } = meterInventory(items);
  meter.reads = 0;
  const { results } = matchBOM(bom, inventory, null, null);
  return { reads: meter.reads, results };
}

/** Reads spent purely on indexing `size` items — matching an empty BOM. */
function indexingWork(size) {
  return inventoryWork(new Map(), generateInventory(size)).reads;
}

/**
 * The cost of one more exact-match BOM row, at a given inventory size.
 * Taking a difference between two BOM sizes cancels the fixed indexing cost,
 * leaving only what a row itself spends looking at inventory.
 */
function marginalReadsPerExactRow(invSize) {
  const items = generateInventory(invSize);
  const few = exactMatchBom(items, 100);
  const many = exactMatchBom(items, 500);
  const a = inventoryWork(few, items);
  const b = inventoryWork(many, items);
  expect(a.results).toHaveLength(few.size);
  expect(b.results).toHaveLength(many.size);
  return (b.reads - a.reads) / (many.size - few.size);
}

// ── Indexing ──

describe('matchBOM indexing', () => {
  it('reads each inventory item a fixed number of times, however big inventory is', () => {
    // buildLookupMaps is one pass: reads-per-item is a property of the record
    // shape, not of how many records there are. If indexing ever became
    // quadratic — a dedupe that rescans what it has accumulated, say — this is
    // the number that moves.
    const perItem = [500, 2000, 8000].map(n => indexingWork(n) / n);
    expect(perItem[1], 'reads/item grew when inventory quadrupled').toBeLessThan(perItem[0] * 1.1);
    expect(perItem[2], 'reads/item grew when inventory grew 16x').toBeLessThan(perItem[0] * 1.1);
  });
});

// ── The O(1) exact-match invariant ──

describe('matchBOM exact matches', () => {
  it('costs the same per BOM row whether inventory is small or large', () => {
    // The whole reason buildLookupMaps exists. A hash hit reads the one item it
    // found; a linear scan reads a fraction of the entire inventory, so this
    // ratio would come back at roughly the 16x the inventory grew by.
    const small = marginalReadsPerExactRow(500);
    const large = marginalReadsPerExactRow(8000);
    expect(large, `per-row cost scaled with inventory: ${small} -> ${large} reads/row for 16x the items`)
      .toBeLessThan(small * 1.5);
  });

  it('reads only a handful of inventory items per BOM row', () => {
    // The absolute companion to the ratio above: a scan of even 1% of an 8000
    // item inventory is 80 reads a row. Current cost is 2.3, so this bound has
    // room for the matcher to grow a few more per-row reads legitimately.
    const perRow = marginalReadsPerExactRow(8000);
    expect(perRow, 'an exact match should be a lookup, not a search').toBeLessThan(10);
  });
});

// ── The realistic mixed BOM ──

describe('matchBOM on a realistic BOM', () => {
  it('costs little more than indexing the inventory it matches against', () => {
    // 300 rows across every match path — exact, prefix, fuzzy, value, missing.
    // Measured against the cost of merely indexing the same inventory, so the
    // bound scales with the record shape instead of hard-coding a read count.
    for (const size of [1000, 4000]) {
      const items = generateInventory(size);
      const bom = bomMap(generateBOM(300, items));
      const { reads, results } = inventoryWork(bom, items);
      expect(results).toHaveLength(bom.size);

      const ratio = reads / indexingWork(size);
      expect(ratio, `matching ${bom.size} rows against ${size} items cost ${ratio.toFixed(2)}x indexing them`)
        .toBeLessThan(2);
    }
  });
});

// ── Correctness at scale ──

describe('matchBOM correctness at scale', () => {
  const inventory = generateInventory(500);
  const bomEntries = generateBOM(150, inventory);
  const bom = bomMap(bomEntries);
  const { results } = matchBOM(bom, inventory, null, null);

  it('every result has bom, inv, status, and matchType', () => {
    results.forEach(r => {
      expect(r).toHaveProperty('bom');
      expect(r).toHaveProperty('status');
      expect(r).toHaveProperty('matchType');
      expect(['lcsc', 'mpn', 'fuzzy', 'value', 'none']).toContain(r.matchType);
      expect(['ok', 'short', 'missing', 'possible']).toContain(r.status);
    });
  });

  it('LCSC exact matches resolve correctly', () => {
    // First 30% of BOM entries have LCSC matches
    const lcscCount = Math.floor(150 * 0.3);
    const lcscResults = results.slice(0, lcscCount);
    lcscResults.forEach(r => {
      expect(r.matchType).toBe('lcsc');
      expect(r.inv).not.toBeNull();
      expect(r.inv.lcsc).toBe(r.bom.lcsc);
    });
  });

  it('missing entries have no inventory match', () => {
    const missingResults = results.filter(r => r.status === 'missing');
    missingResults.forEach(r => {
      expect(r.inv).toBeNull();
      expect(r.matchType).toBe('none');
    });
  });

  it('match rate is reasonable (at least 50% matched)', () => {
    const matched = results.filter(r => r.inv !== null).length;
    expect(matched / results.length).toBeGreaterThanOrEqual(0.5);
  });

  it('alternatives are found for passive value matches', () => {
    const valueMatches = results.filter(r => r.matchType === 'value');
    expect(valueMatches.length, 'Should have value matches with real data').toBeGreaterThan(0);
    // Some value matches should have alternatives (multiple caps/resistors with same value)
    const withAlts = valueMatches.filter(r => r.alts && r.alts.length > 0);
    expect(withAlts.length, 'Some value matches should have alternatives').toBeGreaterThan(0);
    // Verify alternatives don't include the primary match
    withAlts.forEach(r => {
      r.alts.forEach(alt => {
        expect(alt).not.toBe(r.inv);
      });
    });
  });
});

// ── Manual links + confirmed matches at scale ──

describe('matchBOM with manual links at scale', () => {
  it('applies manual links correctly with large datasets', () => {
    const inventory = generateInventory(500);
    const bomEntries = generateBOM(150, inventory);
    const bom = bomMap(bomEntries);

    // Create manual links for "missing" entries (last 15% of BOM)
    const missingStart = Math.floor(150 * 0.85);
    const manualLinks = [];
    for (let i = missingStart; i < 150; i++) {
      const entry = bomEntries[i];
      const bk = bomKey(entry);
      if (bk) {
        // Link to a random inventory item
        const targetInv = inventory[i % inventory.length];
        manualLinks.push({ bomKey: bk, invPartKey: targetInv.lcsc });
      }
    }

    const { results } = matchBOM(bom, inventory, manualLinks, null);
    const manualResults = results.filter(r => r.matchType === 'manual');

    // At least some manual links should have resolved
    expect(manualResults.length).toBeGreaterThan(0);
    manualResults.forEach(r => {
      expect(r.inv).not.toBeNull();
    });
  });
});
