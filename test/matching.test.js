import { describe, it, expect } from 'vitest';
import { loadGlobals } from './helpers/load-globals.js';

const g = loadGlobals();

// ── getMult ──

describe('getMult', () => {
  it('returns correct multiplier for p/n/u/k/M', () => {
    expect(g.getMult('p')).toBe(1e-12);
    expect(g.getMult('n')).toBe(1e-9);
    expect(g.getMult('u')).toBe(1e-6);
    expect(g.getMult('k')).toBe(1e3);
    expect(g.getMult('M')).toBe(1e6);
  });

  it('handles unicode mu (µ and μ)', () => {
    expect(g.getMult('\u00b5')).toBe(1e-6);
    expect(g.getMult('\u03bc')).toBe(1e-6);
  });

  it('handles R as unity multiplier', () => {
    expect(g.getMult('R')).toBe(1);
  });

  it('handles uppercase K', () => {
    expect(g.getMult('K')).toBe(1e3);
  });

  it('returns null for unknown character', () => {
    expect(g.getMult('x')).toBeNull();
    expect(g.getMult('z')).toBeNull();
  });
});

// ── parseEEValue ──

describe('parseEEValue', () => {
  it('parses inline multiplier like 1k5', () => {
    expect(g.parseEEValue('1k5')).toBeCloseTo(1500);
  });

  it('parses suffix multiplier like 10u', () => {
    expect(g.parseEEValue('10u')).toBeCloseTo(10e-6);
  });

  it('parses 100nF (strips unit)', () => {
    expect(g.parseEEValue('100nF')).toBeCloseTo(100e-9);
  });

  it('parses 4.7k', () => {
    expect(g.parseEEValue('4.7k')).toBeCloseTo(4700);
  });

  it('returns null for null/empty', () => {
    expect(g.parseEEValue(null)).toBeNull();
    expect(g.parseEEValue('')).toBeNull();
  });

  it('returns null for unparseable strings', () => {
    expect(g.parseEEValue('hello')).toBeNull();
  });

  it('strips tolerance suffix', () => {
    expect(g.parseEEValue('10k/5%')).toBeCloseTo(10000);
    expect(g.parseEEValue('100n±5%')).toBeCloseTo(100e-9);
  });
});

// ── extractValueFromDesc ──

describe('extractValueFromDesc', () => {
  it('extracts from "100nF 50V X7R"', () => {
    expect(g.extractValueFromDesc('100nF 50V X7R')).toBeCloseTo(100e-9);
  });

  it('extracts from "10kΩ 0402"', () => {
    expect(g.extractValueFromDesc('10k\u03a9 0402')).toBeCloseTo(10000);
  });

  it('extracts from "22µH"', () => {
    expect(g.extractValueFromDesc('22\u00b5H')).toBeCloseTo(22e-6);
  });

  it('returns null for null/empty', () => {
    expect(g.extractValueFromDesc(null)).toBeNull();
    expect(g.extractValueFromDesc('')).toBeNull();
  });

  it('returns null when no unit found', () => {
    expect(g.extractValueFromDesc('ATmega328P')).toBeNull();
  });
});

// ── componentTypeFromRefs ──

describe('componentTypeFromRefs', () => {
  it('returns C for capacitor refs', () => {
    expect(g.componentTypeFromRefs('C1, C2, C3')).toBe('C');
  });

  it('returns R for resistor refs', () => {
    expect(g.componentTypeFromRefs('R1, R2')).toBe('R');
  });

  it('returns L for inductor refs', () => {
    expect(g.componentTypeFromRefs('L1')).toBe('L');
  });

  it('returns null for other/null', () => {
    expect(g.componentTypeFromRefs('U1, U2')).toBeNull();
    expect(g.componentTypeFromRefs(null)).toBeNull();
    expect(g.componentTypeFromRefs('')).toBeNull();
  });
});

// ── componentTypeFromSection ──

describe('componentTypeFromSection', () => {
  it('returns C for capacitor section', () => {
    expect(g.componentTypeFromSection('Passives - Capacitors')).toBe('C');
  });

  it('returns R for resistor section', () => {
    expect(g.componentTypeFromSection('Passives - Resistors')).toBe('R');
  });

  it('returns L for inductor section', () => {
    expect(g.componentTypeFromSection('Passives - Inductors')).toBe('L');
  });

  it('returns null for other/null', () => {
    expect(g.componentTypeFromSection('Connectors')).toBeNull();
    expect(g.componentTypeFromSection(null)).toBeNull();
  });
});

// ── packagesCompatible ──

describe('packagesCompatible', () => {
  it('returns true when both empty', () => {
    expect(g.packagesCompatible({ footprint: '' }, { package: '' })).toBe(true);
  });

  it('returns true on exact match', () => {
    expect(g.packagesCompatible({ footprint: '0402' }, { package: '0402' })).toBe(true);
  });

  it('returns true on substring match', () => {
    expect(g.packagesCompatible({ footprint: '0402_C' }, { package: '0402' })).toBe(true);
  });

  it('returns false on mismatch', () => {
    expect(g.packagesCompatible({ footprint: '0402' }, { package: '0805' })).toBe(false);
  });
});

// ── valuesCompatible ──

describe('valuesCompatible', () => {
  it('returns true for same value', () => {
    const bom = { value: '100n', desc: '' };
    const inv = { description: '100nF 50V' };
    expect(g.valuesCompatible(bom, inv)).toBe(true);
  });

  it('returns false for different values', () => {
    const bom = { value: '100n', desc: '' };
    const inv = { description: '10nF 50V' };
    expect(g.valuesCompatible(bom, inv)).toBe(false);
  });

  it('returns true when value unparseable (benefit of doubt)', () => {
    const bom = { value: 'XYZ', desc: '' };
    const inv = { description: 'Capacitor 100nF' };
    expect(g.valuesCompatible(bom, inv)).toBe(true);
  });

  it('handles zero values correctly', () => {
    const bom = { value: '0R', desc: '' };
    const inv = { description: '0\u03a9' };
    // Both should parse to 0, which is treated as compatible
    // If parseEEValue('0R') gives 0 and extractValueFromDesc('0Ω') gives 0, they match
    expect(g.valuesCompatible(bom, inv)).toBe(true);
  });
});

// ── buildLookupMaps ──

describe('buildLookupMaps', () => {
  it('builds invByLCSC map', () => {
    const inv = [{ lcsc: 'C123456', mpn: '', section: 'Other', description: '' }];
    const maps = g.buildLookupMaps(inv);
    expect(maps.invByLCSC['C123456']).toBe(inv[0]);
  });

  it('builds invByMPN map', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405', section: 'Other', description: '' }];
    const maps = g.buildLookupMaps(inv);
    expect(maps.invByMPN['STM32F405']).toBe(inv[0]);
  });

  it('builds invByValue map for passive items', () => {
    const inv = [{ lcsc: 'C100', mpn: '', section: 'Passives - Capacitors', description: '100nF 50V' }];
    const maps = g.buildLookupMaps(inv);
    expect(Object.keys(maps.invByValue).length).toBeGreaterThan(0);
  });
});

// ── findValueMatch ──

describe('findValueMatch', () => {
  it('returns null for unparseable value', () => {
    const bom = { value: 'XYZ', desc: 'no value here', refs: 'C1' };
    const inv = [{ section: 'Passives - Capacitors', description: '100nF 50V', qty: 10 }];
    expect(g.findValueMatch(bom, inv, {})).toBeNull();
  });

  it('finds capacitor by value', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' },
    ];
    const maps = g.buildLookupMaps(inv);
    const bom = { value: '100n', desc: '', refs: 'C1' };
    const result = g.findValueMatch(bom, inv, maps.invByValue);
    expect(result).toBe(inv[0]);
  });

  it('prefers item with higher qty', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 5, lcsc: 'C1', mpn: '' },
      { section: 'Passives - Capacitors', description: '100nF 25V', qty: 20, lcsc: 'C2', mpn: '' },
    ];
    const maps = g.buildLookupMaps(inv);
    const bom = { value: '100n', desc: '', refs: 'C1' };
    const result = g.findValueMatch(bom, inv, maps.invByValue);
    expect(result).toBe(inv[1]);
  });
});

// ── findAlternatives ──

describe('findAlternatives', () => {
  it('returns empty when no primary', () => {
    expect(g.findAlternatives({}, null, {})).toEqual([]);
  });

  it('returns empty when no alternatives exist', () => {
    const inv = [{ section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' }];
    const maps = g.buildLookupMaps(inv);
    const bom = { value: '100n', refs: 'C1' };
    const result = g.findAlternatives(bom, inv[0], maps.invByValue);
    expect(result).toEqual([]);
  });

  it('returns alternatives excluding primary', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' },
      { section: 'Passives - Capacitors', description: '100nF 25V', qty: 5, lcsc: 'C2', mpn: '' },
    ];
    const maps = g.buildLookupMaps(inv);
    const bom = { value: '100n', refs: 'C1' };
    const result = g.findAlternatives(bom, inv[0], maps.invByValue);
    expect(result).toContain(inv[1]);
    expect(result).not.toContain(inv[0]);
  });
});

// ── matchBOM ──

describe('matchBOM', () => {
  // Helper to create a BOM Map
  function bomMap(entries) {
    const m = new Map();
    entries.forEach(e => m.set(e.key || g.bomKey(e), e));
    return m;
  }

  it('returns empty for empty inputs', () => {
    const results = g.matchBOM(new Map(), [], null, null);
    expect(results).toEqual([]);
  });

  it('matches by LCSC exact match', () => {
    const inv = [{ lcsc: 'C123456', mpn: 'MPN1', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: 'C123456', mpn: '', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('lcsc');
    expect(results[0].inv).toBe(inv[0]);
  });

  it('matches by MPN exact match', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by MPN with underscore/dot normalization', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405.RGT6', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405_RGT6', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by MPN prefix', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405RGT6XX', section: 'Other', description: '', qty: 10, package: '' }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405RGT6', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by fuzzy MPN', () => {
    const inv = [{ lcsc: '', mpn: 'DRV8301DCAR', section: 'ICs - Motor Drivers', description: 'Motor Driver', qty: 10, package: '' }];
    const bom = bomMap([{ lcsc: '', mpn: 'DRV8301DCAX', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('fuzzy');
  });

  it('falls back to value match', () => {
    const inv = [{ lcsc: 'C999', mpn: '', section: 'Passives - Capacitors', description: '100nF 50V', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: '', value: '100n', desc: '', refs: 'C1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('value');
    expect(results[0].status).toBe('possible');
  });

  it('sets missing status when no match found', () => {
    const inv = [{ lcsc: 'C999', mpn: 'OTHER', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'NONEXISTENT', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = g.matchBOM(bom, inv, null, null);
    expect(results[0].status).toBe('missing');
    expect(results[0].inv).toBeNull();
  });

  it('uses manual link override', () => {
    const inv = [
      { lcsc: 'C111', mpn: 'PARTA', section: 'Other', description: '', qty: 10 },
      { lcsc: 'C222', mpn: 'PARTB', section: 'Other', description: '', qty: 5 },
    ];
    const bom = bomMap([{ lcsc: '', mpn: '', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const bomEntry = bom.values().next().value;
    const bk = g.bomKey(bomEntry);
    const links = [{ bomKey: bk, invPartKey: 'C222' }];
    const results = g.matchBOM(bom, inv, links, null);
    expect(results[0].matchType).toBe('manual');
    expect(results[0].inv).toBe(inv[1]);
  });

  it('uses confirmed match override', () => {
    const inv = [
      { lcsc: 'C111', mpn: 'PARTA', section: 'Other', description: '', qty: 10 },
      { lcsc: 'C222', mpn: 'PARTB', section: 'Other', description: '', qty: 5 },
    ];
    const bom = bomMap([{ lcsc: '', mpn: '', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const bomEntry = bom.values().next().value;
    const bk = g.bomKey(bomEntry);
    const confirmed = [{ bomKey: bk, invPartKey: 'C111' }];
    const results = g.matchBOM(bom, inv, null, confirmed);
    expect(results[0].matchType).toBe('confirmed');
    expect(results[0].inv).toBe(inv[0]);
  });
});
