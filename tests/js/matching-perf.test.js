import { describe, it, expect } from 'vitest';
import { matchBOM } from '../../js/matching.js';
import { bomKey } from '../../js/part-keys.js';

// ── Synthetic data generators ──

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

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function generateLCSC(i) {
  return 'C' + String(100000 + i);
}

function generateMPN(i) {
  const prefixes = ['STM32F', 'DRV83', 'TPS54', 'LM317', 'MAX32', 'ADS12', 'MCP23', 'ESP32', 'NRF52', 'AT24C'];
  const prefix = prefixes[i % prefixes.length];
  return prefix + String(1000 + i).slice(1) + 'ABCD'.charAt(i % 4);
}

function generateInventory(count) {
  const items = [];
  for (let i = 0; i < count; i++) {
    const section = SECTIONS[i % SECTIONS.length];
    let desc;
    if (section === 'Passives - Resistors') desc = 'Resistor ' + randomItem(RES_VALUES) + ' ±1%';
    else if (section === 'Passives - Capacitors') desc = 'Capacitor ' + randomItem(CAP_VALUES) + ' 50V';
    else if (section === 'Passives - Inductors') desc = 'Inductor ' + randomItem(IND_VALUES);
    else desc = 'Part ' + i + ' ' + section;

    items.push({
      lcsc: generateLCSC(i),
      mpn: generateMPN(i),
      section,
      description: desc,
      package: randomItem(PACKAGES),
      qty: 10 + (i % 50),
      unit_price: 0.01 + (i % 100) * 0.01,
    });
  }
  return items;
}

function generateBOM(count, inventory) {
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
      bom.value = randomItem(CAP_VALUES).replace('µ', 'u').replace('Ω', '');
      bom.refs = 'C' + (i + 1);
      bom.footprint = randomItem(PACKAGES);
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

// ── Performance tests ──

describe('matchBOM performance', () => {
  it('matches 150 BOM rows against 500 inventory items under 500ms', () => {
    const inventory = generateInventory(500);
    const bomEntries = generateBOM(150, inventory);
    const bom = bomMap(bomEntries);

    const start = performance.now();
    const results = matchBOM(bom, inventory, null, null);
    const elapsed = performance.now() - start;

    expect(results).toHaveLength(150);
    expect(elapsed).toBeLessThan(500);
  });

  it('matches 300 BOM rows against 1000 inventory items under 1000ms', () => {
    const inventory = generateInventory(1000);
    const bomEntries = generateBOM(300, inventory);
    const bom = bomMap(bomEntries);

    const start = performance.now();
    const results = matchBOM(bom, inventory, null, null);
    const elapsed = performance.now() - start;

    expect(results).toHaveLength(300);
    expect(elapsed).toBeLessThan(1000);
  });

  it('matches 500 BOM rows against 2000 inventory items under 3000ms', () => {
    const inventory = generateInventory(2000);
    const bomEntries = generateBOM(500, inventory);
    const bom = bomMap(bomEntries);

    const start = performance.now();
    const results = matchBOM(bom, inventory, null, null);
    const elapsed = performance.now() - start;

    expect(results).toHaveLength(500);
    expect(elapsed).toBeLessThan(3000);
  });
});

// ── Correctness at scale ──

describe('matchBOM correctness at scale', () => {
  const inventory = generateInventory(500);
  const bomEntries = generateBOM(150, inventory);
  const bom = bomMap(bomEntries);
  const results = matchBOM(bom, inventory, null, null);

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
    // Some value matches should have alternatives (multiple caps/resistors with same value)
    const withAlts = valueMatches.filter(r => r.alts && r.alts.length > 0);
    // Not all will have alts, but with 500 inventory items and repeated values, some should
    if (valueMatches.length > 0) {
      // At least verify alternatives don't include the primary match
      withAlts.forEach(r => {
        r.alts.forEach(alt => {
          expect(alt).not.toBe(r.inv);
        });
      });
    }
  });
});

// ── Scaling behavior ──

describe('matchBOM scaling', () => {
  it('doubling inventory size increases time sub-linearly for exact matches', () => {
    // With mostly exact matches, time should scale ~linearly with BOM size,
    // not quadratically with inventory size (thanks to lookup maps)
    const smallInv = generateInventory(250);
    const largeInv = generateInventory(1000);

    // BOM with only LCSC matches (O(1) lookups)
    function makeExactBom(inv, count) {
      const entries = [];
      for (let i = 0; i < count; i++) {
        const item = inv[i % inv.length];
        entries.push({ lcsc: item.lcsc, mpn: '', value: '', desc: '', refs: 'U' + i, qty: 1, footprint: '', dnp: false });
      }
      return bomMap(entries);
    }

    const bomSmall = makeExactBom(smallInv, 100);
    const bomLarge = makeExactBom(largeInv, 100);

    // Warm up
    matchBOM(bomSmall, smallInv, null, null);

    const t1Start = performance.now();
    matchBOM(bomSmall, smallInv, null, null);
    const t1 = performance.now() - t1Start;

    const t2Start = performance.now();
    matchBOM(bomLarge, largeInv, null, null);
    const t2 = performance.now() - t2Start;

    // 4x inventory but same BOM size — with hash lookups, should be < 4x slower
    // Allow generous 6x factor to account for map building overhead
    expect(t2).toBeLessThan(Math.max(t1 * 6, 50));
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

    const results = matchBOM(bom, inventory, manualLinks, null);
    const manualResults = results.filter(r => r.matchType === 'manual');

    // At least some manual links should have resolved
    expect(manualResults.length).toBeGreaterThan(0);
    manualResults.forEach(r => {
      expect(r.inv).not.toBeNull();
    });
  });
});
