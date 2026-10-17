/* matching-spec.test.js — the evidence rule in js/matching.js, by example.
 *
 * Covers what a BOM row's distributor spec (`bom.spec`, from
 * js/bom/bom-spec-lookup.js) contributes, the "unknown is never a pass" rule
 * for passive MPN prefix/fuzzy matches, and the capacitor voltage gate. The
 * cases marked PINNED are shrunk counterexamples from
 * tests/js/property/matching.property.test.mjs — kept here because the
 * property suite's example database is local, not a regression test.
 */
import { describe, it, expect } from 'vitest';
import {
  extractBomValue,
  extractBomVoltage,
  extractVoltage,
  bomFootprintCode,
  bomComponentType,
  componentTypeFromRefs,
  isFuzzyMatchValid,
  voltageAdequate,
  valueFromText,
  matchBOM,
} from '../../js/matching.js';

const CAP = 'Passives - Capacitors > MLCC';
const RES = 'Passives - Resistors > Chip Resistors';

function spec(fields) {
  return { source: 'lcsc', code: 'C1', mpn: '', manufacturer: '', description: '', package: '',
    category: '', subcategory: '', attributes: [], ...fields };
}

function match(boms, inventory, genericParts = []) {
  const m = new Map(boms.map((b, i) => [String(i), { qty: 1, dnp: false, lcsc: '', desc: '', footprint: '', ...b }]));
  return matchBOM(m, inventory, [], [], genericParts).results;
}

// The user's report, reduced: KiCad Value=MPN line for a 1kΩ UniOhm resistor.
const LINE_1K = { lcsc: 'C11702', mpn: '0402WGF1001TCE', value: '0402WGF1001TCE', refs: 'R1,R2',
  footprint: 'Resistor_SMD:R_0402_1005Metric' };
const SPEC_1K = spec({ description: '1kΩ ±1% 62.5mW 0402 Thick Film Resistor', package: '0402',
  category: 'Resistors', attributes: [{ name: 'Resistance', value: '1kΩ' }] });
const INV_100R = { lcsc: 'C25076', mpn: '0402WGF1000TCE', section: RES, package: '0402', qty: 900,
  description: '100Ω ±1% 62.5mW 0402 Thick Film Resistor' };
const INV_1K = { lcsc: 'C106235', mpn: 'RC0402FR-071KL', section: RES, package: '0402', qty: 400,
  description: '1kΩ 62.5mW 50V Thick Film Resistor ±100ppm/℃ ±1% 0402 Chip Resistor - Surface Mount RoHS' };

describe('MPN prefix/fuzzy for passives: unknown is never a pass', () => {
  it('a Value=MPN resistor line no longer "possibly" matches its prefix neighbour', () => {
    const [r] = match([LINE_1K], [INV_100R]);
    expect(r.status).toBe('missing');
    expect(r.inv).toBeNull();
  });

  it('with the LCSC spec, it lands on the real 1kΩ part instead of the 100Ω neighbour', () => {
    const [r] = match([{ ...LINE_1K, spec: SPEC_1K }], [INV_100R, INV_1K]);
    expect(r.matchType).toBe('value');
    expect(r.inv.lcsc).toBe('C106235');
  });

  it('fuzzy still matches a passive when the values are known and equal', () => {
    const [r] = match([{ ...LINE_1K, spec: SPEC_1K }],
      [{ ...INV_1K, mpn: '0402WGF1001TC5' }]);
    expect(r.matchType).toBe('fuzzy');
  });

  it('the BOM side alone makes it passive, even against an "Other"-filed part', () => {
    const inv = { ...INV_100R, section: 'Other' };
    expect(isFuzzyMatchValid({ refs: 'R1', value: '0402WGF1001TCE', footprint: '' }, inv)).toBe(false);
  });

  it('non-passives keep MPN similarity as their evidence', () => {
    const [r] = match([{ mpn: 'DRV8301DCAX', value: '', refs: 'U1' }],
      [{ lcsc: '', mpn: 'DRV8301DCAR', section: 'ICs - Motor Drivers', description: 'Motor Driver', qty: 10, package: '' }]);
    expect(r.matchType).toBe('fuzzy');
  });

  it('PINNED: a designator-less line on a KiCad Inductor_SMD footprint is a passive', () => {
    // Soundness counterexample: no refs, no value anywhere, inventory filed
    // under "Other" — the shared 14-char MPN prefix alone matched it.
    const [r] = match([{ mpn: 'RC0402FR-07011TCE', value: 'RC0402FR-07011TCE', refs: '',
      footprint: 'Inductor_SMD:L_0402_1005Metric' }],
    [{ lcsc: 'C100002', mpn: 'RC0402FR-07011XL', section: 'Other', description: 'Fixed Inductor', package: '', qty: 0 }]);
    expect(r.inv).toBeNull();
  });
});

describe('BOM value: own columns, then the spec', () => {
  it('reads the spec value attribute when the columns carry none', () => {
    expect(extractBomValue({ ...LINE_1K, spec: SPEC_1K })).toBe(1000);
  });

  it('own columns win over the spec', () => {
    expect(extractBomValue({ value: '4.7k', refs: 'R1', spec: SPEC_1K })).toBe(4700);
  });

  it('an MPN in the Value cell is not a value (GRM…71H… is not 71 henries)', () => {
    expect(extractBomValue({ value: 'GRM155R71H104KE14D', refs: 'C1' })).toBeNull();
    expect(extractBomValue({ value: 'RC0402FR-071KL', refs: 'R1' })).toBeNull();
    expect(valueFromText('GRM155R71H104KE14D', null, true)).toBeNull();
  });

  it('a value with another passive\'s unit is not this line\'s value', () => {
    expect(extractBomValue({ value: '', desc: '120mΩ ESR 10uF', refs: 'C1' })).toBeCloseTo(1e-5);
  });

  it('PINNED: LCSC\'s plain "100Ω" Resistance (no multiplier) is a value', () => {
    // Metamorphic counterexample: parseEEValue needs a multiplier, so the same
    // 100Ω matched from the Description and went missing from the spec.
    expect(extractBomValue({ value: 'X', refs: 'R1',
      spec: spec({ attributes: [{ name: 'Resistance', value: '100Ω' }] }) })).toBe(100);
  });

  it('PINNED: a spec carrying a Capacitance types a designator-less line', () => {
    const bom = { value: 'RC0402FR-07000TCE', refs: '', footprint: 'MyLib:0603',
      spec: spec({ attributes: [{ name: 'Capacitance', value: '2.2nF' }] }) };
    expect(bomComponentType(bom)).toBe('C');
  });

  it('a designator is authoritative both ways: U1 stays a non-passive', () => {
    expect(bomComponentType({ refs: 'U1', desc: 'LDO, 1uF output cap',
      footprint: 'Capacitor_SMD:C_0402' })).toBeNull();
  });
});

describe('componentTypeFromRefs reads the whole prefix', () => {
  it('CN/CON/LED/RV are not passives', () => {
    expect(componentTypeFromRefs('CN1')).toBeNull();
    expect(componentTypeFromRefs('CON3')).toBeNull();
    expect(componentTypeFromRefs('LED2')).toBeNull();
    expect(componentTypeFromRefs('RV1')).toBeNull();
    expect(componentTypeFromRefs('c12, c13')).toBe('C');
  });
});

describe('BOM footprint: own column, then the spec package', () => {
  it('uses the spec package when the footprint names none', () => {
    expect(bomFootprintCode({ footprint: 'Inductor_SMD:L_Sunlord_MWSA0402S', spec: spec({ package: '0603' }) })).toBe('0603');
  });

  it('keeps the BOM footprint over the spec package', () => {
    expect(bomFootprintCode({ footprint: 'C_0402_1005Metric', spec: spec({ package: '0603' }) })).toBe('0402');
  });
});

describe('capacitor voltage gate', () => {
  it('parses ratings', () => {
    expect(extractVoltage('100nF ±10% 50V Ceramic Capacitor X7R 0402')).toBe(50);
    expect(extractVoltage('CAP CER 0.1UF 6.3V X5R')).toBe(6.3);
    expect(extractVoltage('22uF 6V3')).toBe(6.3);
    expect(extractVoltage('1nF 2kV')).toBe(2000);
    expect(extractVoltage('25VDC')).toBe(25);
    expect(extractVoltage('100mV ripple')).toBeNull();
    expect(extractVoltage('GRM155R71H104KE14D')).toBeNull();
  });

  it('reads the BOM rating from the spec when the columns carry none', () => {
    expect(extractBomVoltage({ value: 'CL05B104KB54PNC', spec: spec({ attributes: [{ name: 'Voltage Rating', value: '50V' }] }) })).toBe(50);
  });

  const LINE_50V = { value: '100nF', desc: '50V', refs: 'C1', footprint: 'C_0402' };
  const inv = (v, extra = {}) => ({ lcsc: 'C' + (v || 0), mpn: 'M' + v, section: CAP, package: '0402', qty: 10,
    description: v ? `100nF ±10% ${v}V Ceramic Capacitor X7R 0402` : '100nF Ceramic Capacitor 0402', ...extra });

  it('rejects a known-lower rating, allows equal/higher, allows unknown', () => {
    expect(voltageAdequate(LINE_50V, inv(16))).toBe(false);
    expect(voltageAdequate(LINE_50V, inv(50))).toBe(true);
    expect(voltageAdequate(LINE_50V, inv(100))).toBe(true);
    expect(voltageAdequate(LINE_50V, inv(0))).toBe(true);
  });

  it('value match skips a lower-rated part even with more stock', () => {
    const [r] = match([LINE_50V], [inv(16, { qty: 5000 }), inv(50, { qty: 10 })]);
    expect(r.inv.lcsc).toBe('C50');
  });

  it('a line whose only same-value part is rated lower stays missing', () => {
    const [r] = match([LINE_50V], [inv(16)]);
    expect(r.status).toBe('missing');
  });

  it('alternates rated lower are dropped', () => {
    const [r] = match([{ ...LINE_50V, lcsc: 'C50' }], [inv(50), inv(16), inv(63)]);
    expect(r.matchType).toBe('lcsc');
    expect(r.alts.map((a) => a.lcsc)).toEqual(['C63']);
  });

  it('generic resolution skips a lower-rated member and a lower-rated group', () => {
    const group = (members, specVoltage) => ({
      generic_part_id: 'g', name: '100nF 0402', part_type: 'capacitor',
      spec: { value: '100nF', package: '0402', ...(specVoltage ? { voltage: specVoltage } : {}) },
      members,
    });
    const parts = [inv(16, { qty: 900 }), inv(50, { qty: 10 })];
    const [r] = match([LINE_50V], parts, [group([
      { part_id: 'C16', preferred: 1, quantity: 900 },
      { part_id: 'C50', preferred: 0, quantity: 10 },
    ])]);
    expect(r.matchType).toBe('generic');
    expect(r.inv.lcsc).toBe('C50');
    const [r2] = match([LINE_50V], parts, [group([{ part_id: 'C50', preferred: 1, quantity: 10 }], 16)]);
    expect(r2.matchType).not.toBe('generic');
  });
});
