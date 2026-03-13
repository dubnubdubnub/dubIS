import { describe, it, expect } from 'vitest';
import {
  getMult,
  parseEEValue,
  extractValueFromDesc,
  componentTypeFromRefs,
  componentTypeFromSection,
  packagesCompatible,
  valuesCompatible,
  buildLookupMaps,
  findValueMatch,
  findAlternatives,
  matchBOM,
} from '../../js/matching.js';
import { bomKey } from '../../js/part-keys.js';

// ── getMult ──

describe('getMult', () => {
  it('returns correct multiplier for p/n/u/k/M', () => {
    expect(getMult('p')).toBe(1e-12);
    expect(getMult('n')).toBe(1e-9);
    expect(getMult('u')).toBe(1e-6);
    expect(getMult('k')).toBe(1e3);
    expect(getMult('M')).toBe(1e6);
  });

  it('handles unicode mu (µ and μ)', () => {
    expect(getMult('\u00b5')).toBe(1e-6);
    expect(getMult('\u03bc')).toBe(1e-6);
  });

  it('handles R as unity multiplier', () => {
    expect(getMult('R')).toBe(1);
  });

  it('handles uppercase K', () => {
    expect(getMult('K')).toBe(1e3);
  });

  it('returns null for unknown character', () => {
    expect(getMult('x')).toBeNull();
    expect(getMult('z')).toBeNull();
  });
});

// ── parseEEValue ──

describe('parseEEValue', () => {
  it('parses inline multiplier like 1k5', () => {
    expect(parseEEValue('1k5')).toBeCloseTo(1500);
  });

  it('parses suffix multiplier like 10u', () => {
    expect(parseEEValue('10u')).toBeCloseTo(10e-6);
  });

  it('parses 100nF (strips unit)', () => {
    expect(parseEEValue('100nF')).toBeCloseTo(100e-9);
  });

  it('parses 4.7k', () => {
    expect(parseEEValue('4.7k')).toBeCloseTo(4700);
  });

  it('returns null for null/empty', () => {
    expect(parseEEValue(null)).toBeNull();
    expect(parseEEValue('')).toBeNull();
  });

  it('returns null for unparseable strings', () => {
    expect(parseEEValue('hello')).toBeNull();
  });

  it('strips tolerance suffix', () => {
    expect(parseEEValue('10k/5%')).toBeCloseTo(10000);
    expect(parseEEValue('100n±5%')).toBeCloseTo(100e-9);
  });
});

// ── extractValueFromDesc ──

describe('extractValueFromDesc', () => {
  it('extracts from "100nF 50V X7R"', () => {
    expect(extractValueFromDesc('100nF 50V X7R')).toBeCloseTo(100e-9);
  });

  it('extracts from "10kΩ 0402"', () => {
    expect(extractValueFromDesc('10k\u03a9 0402')).toBeCloseTo(10000);
  });

  it('extracts from "22µH"', () => {
    expect(extractValueFromDesc('22\u00b5H')).toBeCloseTo(22e-6);
  });

  it('returns null for null/empty', () => {
    expect(extractValueFromDesc(null)).toBeNull();
    expect(extractValueFromDesc('')).toBeNull();
  });

  it('returns null when no unit found', () => {
    expect(extractValueFromDesc('ATmega328P')).toBeNull();
  });
});

// ── componentTypeFromRefs ──

describe('componentTypeFromRefs', () => {
  it('returns C for capacitor refs', () => {
    expect(componentTypeFromRefs('C1, C2, C3')).toBe('C');
  });

  it('returns R for resistor refs', () => {
    expect(componentTypeFromRefs('R1, R2')).toBe('R');
  });

  it('returns L for inductor refs', () => {
    expect(componentTypeFromRefs('L1')).toBe('L');
  });

  it('returns null for other/null', () => {
    expect(componentTypeFromRefs('U1, U2')).toBeNull();
    expect(componentTypeFromRefs(null)).toBeNull();
    expect(componentTypeFromRefs('')).toBeNull();
  });
});

// ── componentTypeFromSection ──

describe('componentTypeFromSection', () => {
  it('returns C for capacitor section', () => {
    expect(componentTypeFromSection('Passives - Capacitors')).toBe('C');
  });

  it('returns R for resistor section', () => {
    expect(componentTypeFromSection('Passives - Resistors')).toBe('R');
  });

  it('returns L for inductor section', () => {
    expect(componentTypeFromSection('Passives - Inductors')).toBe('L');
  });

  it('returns null for other/null', () => {
    expect(componentTypeFromSection('Connectors')).toBeNull();
    expect(componentTypeFromSection(null)).toBeNull();
  });

  it('returns C for compound capacitor section', () => {
    expect(componentTypeFromSection('Passives - Capacitors > MLCC')).toBe('C');
    expect(componentTypeFromSection('Passives - Capacitors > Tantalum')).toBe('C');
  });
});

// ── packagesCompatible ──

describe('packagesCompatible', () => {
  it('returns true when both empty', () => {
    expect(packagesCompatible({ footprint: '' }, { package: '' })).toBe(true);
  });

  it('returns true on exact match', () => {
    expect(packagesCompatible({ footprint: '0402' }, { package: '0402' })).toBe(true);
  });

  it('returns true on substring match', () => {
    expect(packagesCompatible({ footprint: '0402_C' }, { package: '0402' })).toBe(true);
  });

  it('returns false on mismatch', () => {
    expect(packagesCompatible({ footprint: '0402' }, { package: '0805' })).toBe(false);
  });
});

// ── valuesCompatible ──

describe('valuesCompatible', () => {
  it('returns true for same value', () => {
    const bom = { value: '100n', desc: '' };
    const inv = { description: '100nF 50V' };
    expect(valuesCompatible(bom, inv)).toBe(true);
  });

  it('returns false for different values', () => {
    const bom = { value: '100n', desc: '' };
    const inv = { description: '10nF 50V' };
    expect(valuesCompatible(bom, inv)).toBe(false);
  });

  it('returns true when value unparseable (benefit of doubt)', () => {
    const bom = { value: 'XYZ', desc: '' };
    const inv = { description: 'Capacitor 100nF' };
    expect(valuesCompatible(bom, inv)).toBe(true);
  });

  it('handles zero values correctly', () => {
    const bom = { value: '0R', desc: '' };
    const inv = { description: '0\u03a9' };
    // Both should parse to 0, which is treated as compatible
    // If parseEEValue('0R') gives 0 and extractValueFromDesc('0Ω') gives 0, they match
    expect(valuesCompatible(bom, inv)).toBe(true);
  });
});

// ── buildLookupMaps ──

describe('buildLookupMaps', () => {
  it('builds invByLCSC map', () => {
    const inv = [{ lcsc: 'C123456', mpn: '', section: 'Other', description: '' }];
    const maps = buildLookupMaps(inv);
    expect(maps.invByLCSC['C123456']).toBe(inv[0]);
  });

  it('builds invByMPN map', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405', section: 'Other', description: '' }];
    const maps = buildLookupMaps(inv);
    expect(maps.invByMPN['STM32F405']).toBe(inv[0]);
  });

  it('builds invByValue map for passive items', () => {
    const inv = [{ lcsc: 'C100', mpn: '', section: 'Passives - Capacitors', description: '100nF 50V' }];
    const maps = buildLookupMaps(inv);
    expect(Object.keys(maps.invByValue).length).toBeGreaterThan(0);
  });
});

// ── findValueMatch ──

describe('findValueMatch', () => {
  it('returns null for unparseable value', () => {
    const bom = { value: 'XYZ', desc: 'no value here', refs: 'C1' };
    const inv = [{ section: 'Passives - Capacitors', description: '100nF 50V', qty: 10 }];
    expect(findValueMatch(bom, inv, {})).toBeNull();
  });

  it('finds capacitor by value', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' },
    ];
    const maps = buildLookupMaps(inv);
    const bom = { value: '100n', desc: '', refs: 'C1' };
    const result = findValueMatch(bom, inv, maps.invByValue);
    expect(result).toBe(inv[0]);
  });

  it('prefers item with higher qty', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 5, lcsc: 'C1', mpn: '' },
      { section: 'Passives - Capacitors', description: '100nF 25V', qty: 20, lcsc: 'C2', mpn: '' },
    ];
    const maps = buildLookupMaps(inv);
    const bom = { value: '100n', desc: '', refs: 'C1' };
    const result = findValueMatch(bom, inv, maps.invByValue);
    expect(result).toBe(inv[1]);
  });
});

// ── findAlternatives ──

describe('findAlternatives', () => {
  it('returns empty when no primary', () => {
    expect(findAlternatives({}, null, {})).toEqual([]);
  });

  it('returns empty when no alternatives exist', () => {
    const inv = [{ section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' }];
    const maps = buildLookupMaps(inv);
    const bom = { value: '100n', refs: 'C1' };
    const result = findAlternatives(bom, inv[0], maps.invByValue);
    expect(result).toEqual([]);
  });

  it('returns alternatives excluding primary', () => {
    const inv = [
      { section: 'Passives - Capacitors', description: '100nF 50V', qty: 10, lcsc: 'C1', mpn: '' },
      { section: 'Passives - Capacitors', description: '100nF 25V', qty: 5, lcsc: 'C2', mpn: '' },
    ];
    const maps = buildLookupMaps(inv);
    const bom = { value: '100n', refs: 'C1' };
    const result = findAlternatives(bom, inv[0], maps.invByValue);
    expect(result).toContain(inv[1]);
    expect(result).not.toContain(inv[0]);
  });
});

// ── matchBOM ──

describe('matchBOM', () => {
  // Helper to create a BOM Map
  function bomMap(entries) {
    const m = new Map();
    entries.forEach(e => m.set(e.key || bomKey(e), e));
    return m;
  }

  it('returns empty for empty inputs', () => {
    const results = matchBOM(new Map(), [], null, null);
    expect(results).toEqual([]);
  });

  it('matches by LCSC exact match', () => {
    const inv = [{ lcsc: 'C123456', mpn: 'MPN1', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: 'C123456', mpn: '', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('lcsc');
    expect(results[0].inv).toBe(inv[0]);
  });

  it('matches by MPN exact match', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by MPN with underscore/dot normalization', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405.RGT6', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405_RGT6', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by MPN prefix', () => {
    const inv = [{ lcsc: '', mpn: 'STM32F405RGT6XX', section: 'Other', description: '', qty: 10, package: '' }];
    const bom = bomMap([{ lcsc: '', mpn: 'STM32F405RGT6', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('mpn');
  });

  it('matches by fuzzy MPN', () => {
    const inv = [{ lcsc: '', mpn: 'DRV8301DCAR', section: 'ICs - Motor Drivers', description: 'Motor Driver', qty: 10, package: '' }];
    const bom = bomMap([{ lcsc: '', mpn: 'DRV8301DCAX', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('fuzzy');
  });

  it('falls back to value match', () => {
    const inv = [{ lcsc: 'C999', mpn: '', section: 'Passives - Capacitors', description: '100nF 50V', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: '', value: '100n', desc: '', refs: 'C1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
    expect(results[0].matchType).toBe('value');
    expect(results[0].status).toBe('possible');
  });

  it('sets missing status when no match found', () => {
    const inv = [{ lcsc: 'C999', mpn: 'OTHER', section: 'Other', description: '', qty: 10 }];
    const bom = bomMap([{ lcsc: '', mpn: 'NONEXISTENT', value: '', desc: '', refs: 'U1', qty: 1, footprint: '' }]);
    const results = matchBOM(bom, inv, null, null);
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
    const bk = bomKey(bomEntry);
    const links = [{ bomKey: bk, invPartKey: 'C222' }];
    const results = matchBOM(bom, inv, links, null);
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
    const bk = bomKey(bomEntry);
    const confirmed = [{ bomKey: bk, invPartKey: 'C111' }];
    const results = matchBOM(bom, inv, null, confirmed);
    expect(results[0].matchType).toBe('confirmed');
    expect(results[0].inv).toBe(inv[0]);
  });
});
