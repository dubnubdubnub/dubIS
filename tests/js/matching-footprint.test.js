import { describe, it, expect } from 'vitest';
import {
  extractFootprintCode,
  footprintsCompatible,
} from '../../js/matching.js';

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
