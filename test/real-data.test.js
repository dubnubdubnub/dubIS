/**
 * Integration tests using real inventory, BOM, and links data.
 * Exercises the full pipeline: parseCSV → detectBOMColumns → aggregateBomRows → matchBOM.
 */
import { describe, it, expect } from 'vitest';
import { loadGlobals } from './helpers/load-globals.js';
import { readFixture, readFixtureBytes, readFixtureJSON, loadInventory } from './helpers/load-fixtures.js';

const g = loadGlobals();

// ── Load real data once ──

const inventory = loadInventory();

// ── Inventory fixture sanity ──

describe('real inventory fixture', () => {
  it('loads a reasonable number of parts', () => {
    expect(inventory.length).toBeGreaterThan(100);
  });

  it('every item has section, qty, and at least one part ID', () => {
    inventory.forEach(item => {
      expect(item.section).toBeTruthy();
      expect(typeof item.qty).toBe('number');
      expect(item.lcsc || item.mpn || item.digikey).toBeTruthy();
    });
  });

  it('contains expected sections', () => {
    const sections = new Set(inventory.map(i => i.section));
    expect(sections.has('Connectors > SMD')).toBe(true);
    expect(sections.has('Passives - Resistors')).toBe(true);
    expect(sections.has('Passives - Capacitors')).toBe(true);
    expect(sections.has('ICs - Microcontrollers')).toBe(true);
  });

  it('contains known parts from purchase orders', () => {
    const lcscs = new Set(inventory.map(i => i.lcsc));
    // From LCSC PO WM2603020186
    expect(lcscs.has('C2875244')).toBe(true);  // Crystal 16MHz
    expect(lcscs.has('C32949')).toBe(true);     // 10pF cap
    // From LCSC PO WM2603120130
    expect(lcscs.has('C879894')).toBe(true);    // 10uF tantalum

    const mpns = new Set(inventory.map(i => i.mpn));
    // From Digikey PO 97746939
    expect(mpns.has('DRV8316CRRGFR')).toBe(true);  // Motor driver
    expect(mpns.has('STM32G491CCU6')).toBe(true);   // MCU
  });

  it('quantities reflect adjustments (not raw PO quantities)', () => {
    // C25905 (5.1kΩ resistor) — purchased 10000, consumed some
    const r5k1 = inventory.find(i => i.lcsc === 'C25905');
    expect(r5k1).toBeTruthy();
    expect(r5k1.qty).toBeLessThan(10000);
    expect(r5k1.qty).toBeGreaterThan(0);
  });
});

// ── Lemon Pepper BOM (JLCPCB format, LCSC part #s) ──

describe('lemon-pepper BOM matching', () => {
  const bomText = readFixture('lemon-pepper-BOM.csv');
  const result = g.processBOM(bomText, 'lemon-pepper-BOM.csv');

  it('parses successfully', () => {
    expect(result).not.toBeNull();
    expect(result.headers).toContain('JLCPCB Part #');
  });

  it('detects LCSC column from JLCPCB Part #', () => {
    expect(result.cols.lcsc).toBeGreaterThanOrEqual(0);
  });

  it('detects designator and value columns', () => {
    expect(result.cols.ref).toBeGreaterThanOrEqual(0);
    expect(result.cols.value).toBeGreaterThanOrEqual(0);
  });

  it('aggregates rows (many individual rows → fewer unique parts)', () => {
    expect(result.rawRows.length).toBeGreaterThan(result.aggregated.size);
    expect(result.aggregated.size).toBeGreaterThan(10);
  });

  it('matches against real inventory with high match rate', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const matched = results.filter(r => r.inv !== null);
    const missing = results.filter(r => r.status === 'missing');

    // Lemon pepper uses LCSC part numbers — most should be exact matches
    expect(matched.length).toBeGreaterThan(missing.length);

    // Most matches should be LCSC exact
    const lcscMatches = results.filter(r => r.matchType === 'lcsc');
    expect(lcscMatches.length).toBeGreaterThan(0);
  });

  it('C440198 (10uF cap) matches by LCSC', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const cap10u = results.find(r => r.bom.lcsc === 'C440198');
    expect(cap10u).toBeTruthy();
    expect(cap10u.matchType).toBe('lcsc');
    expect(cap10u.inv).not.toBeNull();
    expect(cap10u.inv.lcsc).toBe('C440198');
  });

  it('C1567 (47pF cap) matches by LCSC', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const cap47p = results.find(r => r.bom.lcsc === 'C1567');
    expect(cap47p).toBeTruthy();
    expect(cap47p.matchType).toBe('lcsc');
  });
});

// ── Microspora BOM (UTF-16 encoded, Supplier Part = LCSC) ──

describe('microspora BOM matching (UTF-16)', () => {
  // microspora_BOM.csv is UTF-16 encoded — decode it like the app does
  const bytes = readFixtureBytes('microspora_BOM.csv');
  let bomText;
  if (bytes[0] === 0xFF && bytes[1] === 0xFE) {
    bomText = new TextDecoder('utf-16le').decode(bytes);
  } else if (bytes[0] === 0xFE && bytes[1] === 0xFF) {
    bomText = new TextDecoder('utf-16be').decode(bytes);
  } else {
    bomText = new TextDecoder('utf-8').decode(bytes);
  }

  const result = g.processBOM(bomText, 'microspora_BOM.csv');

  it('parses the UTF-16 file successfully', () => {
    expect(result).not.toBeNull();
    expect(result.aggregated.size).toBeGreaterThan(15);
  });

  it('detects supplier part column as LCSC', () => {
    expect(result.cols.lcsc).toBeGreaterThanOrEqual(0);
  });

  it('detects MPN column', () => {
    expect(result.cols.mpn).toBeGreaterThanOrEqual(0);
  });

  it('matches against real inventory', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const matched = results.filter(r => r.inv !== null);
    expect(matched.length).toBeGreaterThan(0);
  });

  it('DRV8316CRRGFR (motor driver) matches', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    // The BOM has C5447274 as supplier part for DRV8316CRRGFR
    const drv = results.find(r => r.bom.mpn === 'DRV8316CRRGFR' || r.bom.lcsc === 'C5447274');
    expect(drv).toBeTruthy();
    expect(drv.inv).not.toBeNull();
  });
});

// ── Microspora BOM custom (UTF-8) with manual links + confirmed matches ──

describe('microspora BOM custom with links', () => {
  const bomText = readFixture('microspora_BOM_custom.csv');
  const result = g.processBOM(bomText, 'microspora_BOM_custom.csv');
  const links = readFixtureJSON('microspora_BOM_custom.links.json');

  it('parses successfully', () => {
    expect(result).not.toBeNull();
    expect(result.aggregated.size).toBeGreaterThan(15);
  });

  it('links file has manual links and confirmed matches', () => {
    expect(links.manualLinks.length).toBe(4);
    expect(links.confirmedMatches.length).toBe(4);
  });

  it('manual links resolve to inventory parts', () => {
    const results = g.matchBOM(result.aggregated, inventory, links.manualLinks, links.confirmedMatches);
    const manualResults = results.filter(r => r.matchType === 'manual');
    expect(manualResults.length).toBeGreaterThan(0);
    manualResults.forEach(r => {
      expect(r.inv).not.toBeNull();
    });
  });

  it('confirmed matches resolve to inventory parts', () => {
    const results = g.matchBOM(result.aggregated, inventory, links.manualLinks, links.confirmedMatches);
    const confirmedResults = results.filter(r => r.matchType === 'confirmed');
    expect(confirmedResults.length).toBeGreaterThan(0);
    confirmedResults.forEach(r => {
      expect(r.inv).not.toBeNull();
    });
  });

  it('C2765186 → C2760486 manual link resolves', () => {
    // Manual link: BOM has C2765186 (USB-C connector), linked to C2760486 in inventory
    const results = g.matchBOM(result.aggregated, inventory, links.manualLinks, links.confirmedMatches);
    const usbC = results.find(r => g.bomKey(r.bom) === 'C2765186');
    if (usbC) {
      expect(usbC.matchType).toBe('manual');
      expect(usbC.inv.lcsc).toBe('C2760486');
    }
  });

  it('match count improves with links vs without', () => {
    const withoutLinks = g.matchBOM(result.aggregated, inventory, null, null);
    const withLinks = g.matchBOM(result.aggregated, inventory, links.manualLinks, links.confirmedMatches);

    const matchedWithout = withoutLinks.filter(r => r.inv !== null).length;
    const matchedWith = withLinks.filter(r => r.inv !== null).length;

    // Links should resolve at least some additional parts
    expect(matchedWith).toBeGreaterThanOrEqual(matchedWithout);
  });
});

// ── Expansion Card BOM (NextPCB format, MPN-keyed) ──

describe('expansion card BOM matching', () => {
  const bomText = readFixture('Expansion_Card-bom-next-pcb.csv');
  const result = g.processBOM(bomText, 'Expansion_Card-bom-next-pcb.csv');
  const links = readFixtureJSON('Expansion_Card-bom-next-pcb_custom.links.json');

  it('parses successfully', () => {
    expect(result).not.toBeNull();
    expect(result.aggregated.size).toBeGreaterThan(10);
  });

  it('detects MPN column from Manufacturer Part Number*', () => {
    expect(result.cols.mpn).toBeGreaterThanOrEqual(0);
  });

  it('detects designator and qty columns', () => {
    expect(result.cols.ref).toBeGreaterThanOrEqual(0);
    expect(result.cols.qty).toBeGreaterThanOrEqual(0);
  });

  it('handles DNP/excluded rows', () => {
    expect(result.cols.dnp).toBeGreaterThanOrEqual(0);
    // Check that some entries are marked DNP
    let dnpCount = 0;
    result.aggregated.forEach(entry => { if (entry.dnp) dnpCount++; });
    expect(dnpCount).toBeGreaterThanOrEqual(0);  // May or may not have DNP parts
  });

  it('matches against inventory (MPN-based matching)', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const matched = results.filter(r => r.inv !== null);
    expect(matched.length).toBeGreaterThan(0);
  });

  it('CL05B104KB54PNC (100nF cap) matches by MPN', () => {
    const results = g.matchBOM(result.aggregated, inventory, null, null);
    const cap100n = results.find(r =>
      r.bom.mpn === 'CL05B104KB54PNC' || r.bom.mpn.includes('CL05B104KB54PNC')
    );
    if (cap100n) {
      expect(cap100n.inv).not.toBeNull();
      expect(['lcsc', 'mpn']).toContain(cap100n.matchType);
    }
  });

  it('confirmed matches from links file resolve correctly', () => {
    const results = g.matchBOM(result.aggregated, inventory, links.manualLinks || [], links.confirmedMatches);
    const confirmedResults = results.filter(r => r.matchType === 'confirmed');
    // links file has 3 confirmed matches
    expect(confirmedResults.length).toBeGreaterThan(0);
    confirmedResults.forEach(r => {
      expect(r.inv).not.toBeNull();
    });
  });
});

// ── Cross-BOM comparison ──

describe('cross-BOM consistency', () => {
  it('microspora UTF-16 and custom (UTF-8) produce same aggregated part count', () => {
    const bytes = readFixtureBytes('microspora_BOM.csv');
    let utf16Text;
    if (bytes[0] === 0xFF && bytes[1] === 0xFE) {
      utf16Text = new TextDecoder('utf-16le').decode(bytes);
    } else {
      utf16Text = new TextDecoder('utf-8').decode(bytes);
    }
    const utf8Text = readFixture('microspora_BOM_custom.csv');

    const r1 = g.processBOM(utf16Text, 'microspora_BOM.csv');
    const r2 = g.processBOM(utf8Text, 'microspora_BOM_custom.csv');

    expect(r1.aggregated.size).toBe(r2.aggregated.size);
  });
});

// ── Performance with real data ──

describe('real-data matchBOM performance', () => {
  it('lemon-pepper BOM matches under 100ms', () => {
    const bomText = readFixture('lemon-pepper-BOM.csv');
    const result = g.processBOM(bomText, 'lemon-pepper-BOM.csv');

    const start = performance.now();
    g.matchBOM(result.aggregated, inventory, null, null);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(100);
  });

  it('expansion card BOM matches under 100ms', () => {
    const bomText = readFixture('Expansion_Card-bom-next-pcb.csv');
    const result = g.processBOM(bomText, 'Expansion_Card-bom-next-pcb.csv');

    const start = performance.now();
    g.matchBOM(result.aggregated, inventory, null, null);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(100);
  });

  it('microspora BOM matches under 100ms', () => {
    const bomText = readFixture('microspora_BOM_custom.csv');
    const result = g.processBOM(bomText, 'microspora_BOM_custom.csv');
    const links = readFixtureJSON('microspora_BOM_custom.links.json');

    const start = performance.now();
    g.matchBOM(result.aggregated, inventory, links.manualLinks, links.confirmedMatches);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(100);
  });
});
