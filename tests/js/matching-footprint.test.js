import { describe, it, expect } from 'vitest';
import {
  extractFootprintCode,
  footprintsCompatible,
  isFuzzyMatchValid,
  findValueMatch,
  buildLookupMaps,
} from '../../js/matching.js';
import { matchBOM } from '../../js/matching.js';

describe('extractFootprintCode', () => {
  it('returns canonical chip size from a KiCad footprint string', () => {
    expect(extractFootprintCode('Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder')).toBe('0402');
    expect(extractFootprintCode('Capacitor_SMD:C_0603_1608Metric_Pad1.08x0.95mm_HandSolder')).toBe('0603');
    expect(extractFootprintCode('0805')).toBe('0805');
  });

  it('returns canonical IC package code', () => {
    expect(extractFootprintCode('Package_TO_SOT_SMD:SOT-23-5_HandSoldering')).toBe('SOT-23-5');
    expect(extractFootprintCode('Package_TO_SOT_SMD:SOT-363_SC-70-6')).toBe('SOT-363');
    expect(extractFootprintCode('Package_SO:VSSOP-10_3x3mm_P0.5mm')).toBe('VSSOP-10');
    expect(extractFootprintCode('Package_SO:MSOP-8_3x3mm_P0.65mm')).toBe('MSOP-8');
    expect(extractFootprintCode('Diode_SMD:D_SOD-123')).toBe('SOD-123');
  });

  it('is word-boundary anchored — does not pull codes out of MPN-like strings', () => {
    expect(extractFootprintCode('0603WAF0000T5E')).toBeNull();
    expect(extractFootprintCode('RC0402FR-07620RL')).toBeNull();
    expect(extractFootprintCode('BLM15AG601SN1D')).toBeNull();
  });

  it('returns null for empty or unknown strings', () => {
    expect(extractFootprintCode('')).toBeNull();
    expect(extractFootprintCode(null)).toBeNull();
    expect(extractFootprintCode(undefined)).toBeNull();
    expect(extractFootprintCode('some weird footprint')).toBeNull();
  });

  it('handles case variations consistently', () => {
    expect(extractFootprintCode('sot-23')).toBe('SOT-23');
    expect(extractFootprintCode('R_0402_1005metric')).toBe('0402');
  });
});

describe('footprintsCompatible', () => {
  it('returns true when either side yields no code (uncertainty → allow)', () => {
    expect(footprintsCompatible({ footprint: '' }, { package: '0402' })).toBe(true);
    expect(footprintsCompatible({ footprint: 'R_0402_1005Metric' }, { package: '' })).toBe(true);
    expect(footprintsCompatible({ footprint: 'unknown' }, { package: '0402' })).toBe(true);
    expect(footprintsCompatible({ footprint: '' }, { package: '' })).toBe(true);
  });

  it('returns true when both sides yield the same canonical code', () => {
    expect(footprintsCompatible(
      { footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder' },
      { package: '0402' }
    )).toBe(true);
    expect(footprintsCompatible(
      { footprint: 'Package_SO:MSOP-8_3x3mm_P0.65mm' },
      { package: 'MSOP-8' }
    )).toBe(true);
  });

  it('returns false when both sides yield different canonical codes', () => {
    expect(footprintsCompatible(
      { footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder' },
      { package: '0603' }
    )).toBe(false);
    expect(footprintsCompatible(
      { footprint: 'SOT-23' },
      { package: 'SOT-363' }
    )).toBe(false);
  });
});

describe('isFuzzyMatchValid (tightened to canonical footprint)', () => {
  it('rejects when BOM wants 0402 but inventory is 0603', () => {
    const bom = { footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder', value: '100' };
    const inv = { package: '0603', section: 'Passives - Resistors > Chip Resistors', description: '100Ω ±1% 100mW 0603 Thick Film Resistor' };
    expect(isFuzzyMatchValid(bom, inv)).toBe(false);
  });

  it('accepts when footprints match and values match', () => {
    const bom = { footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder', value: '100' };
    const inv = { package: '0402', section: 'Passives - Resistors > Chip Resistors', description: '100Ω ±1% 62.5mW 0402 Thick Film Resistor' };
    expect(isFuzzyMatchValid(bom, inv)).toBe(true);
  });

  it('falls back to permissive when one side lacks a canonical code', () => {
    const bom = { footprint: '', value: '100' };
    const inv = { package: '0402', section: 'Passives - Resistors > Chip Resistors', description: '100Ω ±1% 62.5mW 0402 Thick Film Resistor' };
    expect(isFuzzyMatchValid(bom, inv)).toBe(true);
  });
});

describe('findValueMatch (filters by footprint)', () => {
  const inv0603 = {
    lcsc: 'C22936', mpn: '0603WAF100KT5E',
    section: 'Passives - Resistors > Chip Resistors',
    package: '0603',
    description: '1Ω ±1% 100mW 0603 Thick Film Resistor',
    qty: 100,
  };
  const inv0402 = {
    lcsc: 'C25079', mpn: '0402WGF1200TCE',
    section: 'Passives - Resistors > Chip Resistors',
    package: '0402',
    description: '120Ω ±1% 62.5mW 0402 Thick Film Resistor',
    qty: 50,
  };

  it('rejects the 0603 candidate when BOM wants 0402', () => {
    const bom = {
      refs: 'R1', value: '1k',
      footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
    };
    const inv0603_1k = { ...inv0603, description: '1kΩ ±1% 100mW 0603 Thick Film Resistor' };
    const inventory = [inv0603_1k];
    const maps = buildLookupMaps(inventory);
    expect(findValueMatch(bom, inventory, maps.invByValue)).toBeNull();
  });

  it('accepts the 0402 candidate when BOM wants 0402', () => {
    const bom = {
      refs: 'R1', value: '120',
      footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
      desc: '120Ω',  // forces extractBomValue to parse; plain-number path arrives in Task 4
    };
    const inventory = [inv0402];
    const maps = buildLookupMaps(inventory);
    const result = findValueMatch(bom, inventory, maps.invByValue);
    expect(result).toBe(inv0402);
  });

  it('rejects in the type-unknown fallback branch when footprint mismatches', () => {
    // No refs → componentTypeFromRefs returns null → falls into the scan-all-groups path.
    // This exercises the second footprintsCompatible filter in findValueMatch.
    const bom = {
      refs: '', value: '',
      desc: '120Ω',
      footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
    };
    const inv0603_120 = { ...inv0603, description: '120Ω ±1% 100mW 0603 Thick Film Resistor' };
    const inventory = [inv0603_120];
    const maps = buildLookupMaps(inventory);
    expect(findValueMatch(bom, inventory, maps.invByValue)).toBeNull();
  });
});

describe('matchBOM: near-miss tracking and new return shape', () => {
  const inv0603_1k = {
    lcsc: 'C1234', mpn: 'DUMMY-1K-0603', manufacturer: 'ACME',
    section: 'Passives - Resistors > Chip Resistors',
    package: '0603',
    description: '1kΩ ±1% 100mW 0603 Thick Film Resistor',
    qty: 100, unit_price: 0.001, ext_price: 0.1,
  };

  it('returns { results, footprintNearMisses } shape', () => {
    const agg = new Map();
    agg.set('X', { lcsc: '', mpn: 'DOES-NOT-EXIST', qty: 1, refs: 'R1', value: '', desc: '', footprint: '', dnp: false });
    const out = matchBOM(agg, [inv0603_1k], [], [], []);
    expect(out).toHaveProperty('results');
    expect(Array.isArray(out.results)).toBe(true);
    expect(out).toHaveProperty('footprintNearMisses');
    expect(Array.isArray(out.footprintNearMisses)).toBe(true);
  });

  it('records a near-miss when value matches but footprint differs', () => {
    const agg = new Map();
    agg.set('ABC', {
      lcsc: '', mpn: 'ABCDEFGHIJ', qty: 1, refs: 'R1',
      value: '1k', desc: '',
      footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
      dnp: false,
    });
    const out = matchBOM(agg, [inv0603_1k], [], [], []);
    expect(out.results).toHaveLength(1);
    expect(out.results[0].status).toBe('missing');
    expect(out.footprintNearMisses).toHaveLength(1);
    const nm = out.footprintNearMisses[0];
    expect(nm.inv).toBe(inv0603_1k);
    expect(nm.bomFootprintCode).toBe('0402');
    expect(nm.invPackage).toBe('0603');
    expect(nm.bomValue).toBe('1k');
  });

  it('does not emit a near-miss when the footprint matches', () => {
    const inv0402_1k = { ...inv0603_1k, lcsc: 'C5678', package: '0402', description: '1kΩ ±1% 62.5mW 0402 Thick Film Resistor' };
    const agg = new Map();
    agg.set('ABC', {
      lcsc: '', mpn: 'ABCDEFGHIJ', qty: 1, refs: 'R1',
      value: '1k', desc: '',
      footprint: 'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
      dnp: false,
    });
    const out = matchBOM(agg, [inv0402_1k], [], [], []);
    expect(out.results).toHaveLength(1);
    expect(out.results[0].status).not.toBe('missing');
    expect(out.footprintNearMisses).toHaveLength(0);
  });
});
