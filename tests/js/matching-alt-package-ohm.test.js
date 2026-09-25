// Regressions found running matchBOM -> computeRows on a real inventory:
//  1. findAlternatives returned alternates in packages that don't fit the BOM
//     footprint, so coveredByAlts claimed shortages were covered.
//  2. Descriptions spelling the unit as "OHM" ("RES SMD 330 OHM ...") never
//     parsed, so the part was missing from invByValue and never value-matched.
import { describe, it, expect } from 'vitest';
import {
  buildLookupMaps,
  extractValueFromDesc,
  parseEEValue,
  findAlternatives,
  altFootprintUnverified,
  matchBOM,
} from '../../js/matching.js';
import { computeRows } from '../../js/bom/bom-logic.js';
import { bomRowDisplayData } from '../../js/bom-row-data.js';
import { bomKey } from '../../js/part-keys.js';

function bomMap(entries) {
  const m = new Map();
  entries.forEach(e => m.set(bomKey(e), e));
  return m;
}

const CAP_SECTION = 'Passives - Capacitors';

// The real 100nF inventory: primary 0402 part plus a mix of packages.
function capInventory() {
  return [
    { lcsc: 'C307331', mpn: 'CL05B104KB54PNC', section: CAP_SECTION, qty: 100,
      package: '0402', description: '100nF 50V X7R ±10% 0402 Multilayer Ceramic Capacitors MLCC' },
    { lcsc: 'C14663', mpn: 'CC0603KRX7R9BB104', section: CAP_SECTION, qty: 500,
      package: '0603', description: '100nF 50V X7R ±10% 0603 Multilayer Ceramic Capacitors MLCC' },
    // TDK metric size code: "C0603" is 0603 metric == 0201 imperial.
    { lcsc: 'C76935', mpn: 'C0603X5R1A104K030BC', section: CAP_SECTION, qty: 300,
      package: '0201', description: '100nF 10V X5R ±10% 0201 Multilayer Ceramic Capacitors MLCC' },
    { lcsc: 'C85893', mpn: 'GCM155R71H104KE02D', section: CAP_SECTION, qty: 150,
      package: '0402', description: '100nF 50V X7R ±10% 0402 Multilayer Ceramic Capacitors MLCC' },
    { lcsc: '', mpn: 'KGM05CR71H104KH', section: CAP_SECTION, qty: 120,
      package: '0402', description: 'CAP CER 0.1UF 50V X7R 0402' },
  ];
}

const CAP_BOM = {
  lcsc: 'C307331', mpn: '', value: '100nF', desc: '', refs: 'C1,C2', qty: 340,
  footprint: 'Capacitor_SMD:C_0402_1005Metric', dnp: false,
};

describe('findAlternatives — package filtering', () => {
  it('returns only alternates whose package fits the BOM footprint', () => {
    const inv = capInventory();
    const { invByValue } = buildLookupMaps(inv);
    const alts = findAlternatives(CAP_BOM, inv[0], invByValue);
    expect(alts.map(a => a.mpn).sort()).toEqual(['GCM155R71H104KE02D', 'KGM05CR71H104KH']);
  });

  it('does not derive a package from an MPN that embeds a size-like digit run', () => {
    // No package, no size in description: the "0603" inside the TDK MPN must
    // not be read as 0603 imperial, nor as a match for 0402.
    const inv = capInventory();
    inv[2] = { ...inv[2], package: '', description: '100nF 10V X5R ±10% MLCC' };
    const { invByValue } = buildLookupMaps(inv);
    const bom0603 = { ...CAP_BOM, footprint: 'Capacitor_SMD:C_0603_1608Metric' };
    const alts = findAlternatives(bom0603, inv[1], invByValue);
    const tdk = alts.find(a => a.mpn === 'C0603X5R1A104K030BC');
    // Unknown package stays permissive, but is flagged as unverified.
    expect(tdk).toBeDefined();
    expect(altFootprintUnverified(bom0603, tdk)).toBe(true);
    expect(altFootprintUnverified(bom0603, inv[0])).toBe(false);
  });

  it('keeps unknown-package alternates but reports them as unverified', () => {
    const inv = capInventory();
    inv.push({ lcsc: 'C999', mpn: 'MYSTERY104', section: CAP_SECTION, qty: 7,
      package: '', description: '100nF 16V X7R' });
    const { results } = matchBOM(bomMap([CAP_BOM]), inv, null, null);
    const mpns = results[0].alts.map(a => a.mpn);
    expect(mpns).toContain('MYSTERY104');
    expect(results[0].altsUnverified.map(a => a.mpn)).toEqual(['MYSTERY104']);
  });

  it('computeRows does not count wrong-package alternates toward coverage', () => {
    const inv = capInventory();
    const { results } = matchBOM(bomMap([CAP_BOM]), inv, null, null);
    const [row] = computeRows(results, 1);
    expect(row.matchType).toBe('lcsc');
    expect(row.effectiveStatus).toBe('short');
    // 100 primary + 150 + 120 (the 0402 alts) = 370 >= 340.
    expect(row.altQty).toBe(270);
    expect(row.coveredByAlts).toBe(true);

    // With a need above what the fitting parts hold, it must stay short even
    // though the 0603/0201 stock (800 more) would numerically cover it.
    const bigger = { ...CAP_BOM, qty: 400 };
    const [row2] = computeRows(matchBOM(bomMap([bigger]), inv, null, null).results, 1);
    expect(row2.altQty).toBe(270);
    expect(row2.coveredByAlts).toBe(false);
  });

  it('surfaces the unverified count on the alt badge', () => {
    const inv = capInventory();
    inv.push({ lcsc: 'C999', mpn: 'MYSTERY104', section: CAP_SECTION, qty: 7,
      package: '', description: '100nF 16V X7R' });
    const [row] = computeRows(matchBOM(bomMap([CAP_BOM]), inv, null, null).results, 1);
    const d = bomRowDisplayData(row, '', 'all', new Set(), {});
    expect(d.altBadge.unverifiedCount).toBe(1);
  });
});

describe('value parsing — OHM word form', () => {
  it.each([
    ['RES SMD 330 OHM 0.5% 1/16W 0402', 330],
    ['RES 4.7K OHM 1% 1/10W 0603', 4700],
    ['RES 10 OHMS 5% 0805', 10],
    ['RES 1 MOHM 1% 0402', 1e6],
    ['RES 2.2KOHM 1%', 2200],
    ['330 Ohm', 330],
  ])('extractValueFromDesc(%j) = %d', (desc, expected) => {
    expect(extractValueFromDesc(desc)).toBeCloseTo(expected, 6);
  });

  it('keeps lowercase m as milli (mOhm)', () => {
    expect(extractValueFromDesc('RES 10 mOhm 1% 2512')).toBeCloseTo(0.01, 9);
    expect(extractValueFromDesc('Current sense 5mOHM 1W')).toBeCloseTo(0.005, 9);
    expect(parseEEValue('10mOhm')).toBeCloseTo(0.01, 9);
  });

  it('parseEEValue strips OHM/OHMS', () => {
    expect(parseEEValue('4.7k OHM')).toBeCloseTo(4700);
    expect(parseEEValue('1M Ohms')).toBeCloseTo(1e6);
  });

  it('does not treat OHM inside a longer word as a unit', () => {
    expect(extractValueFromDesc('Ohmite 10 OHMITE')).toBeNull();
  });

  it('value-matches RR0510P-331-D to a 330Ω 0402 BOM line', () => {
    const inv = [
      { lcsc: '', mpn: 'RR0510P-331-D', section: 'Passives - Resistors', qty: 1000,
        package: '', description: 'RES SMD 330 OHM 0.5% 1/16W 0402' },
    ];
    const bom = {
      lcsc: 'C25104', mpn: '', value: '0402WGF3300TCE', desc: 'RES 330Ω ±1% 62.5mW 0402',
      refs: 'R8', qty: 1, footprint: 'Resistor_SMD:R_0402_1005Metric', dnp: false,
    };
    const { results } = matchBOM(bomMap([bom]), inv, null, null);
    expect(results[0].matchType).toBe('value');
    expect(results[0].inv).toBe(inv[0]);
  });
});
