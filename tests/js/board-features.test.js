/* board-features.test.js — which BOM lines are board features, not parts.
 *
 * The reported bug: fiducials, mounting holes, test pads and Tag-Connect
 * footprints showed up as `missing` parts and inflated UTBP, because with no
 * MPN column the parser uses Value as the part number. The table below is the
 * rule, both directions: every near-miss negative is a REAL part that must
 * stay one (a supplier code, a real MPN, or a footprint that only looks like a
 * feature's).
 */
import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

vi.mock('../../js/ui-helpers.js', () => ({ escHtml: vi.fn(s => s || '') }));

import { boardFeatureReason, rawRowBoardReason, hasPartNumber, EXCLUDED_FROM_BOM_REASON } from '../../js/bom/board-features.js';
import { processBOM, detectBOMColumns, aggregateBomRows } from '../../js/csv-parser.js';
import { matchBOM } from '../../js/matching.js';
import { computeRows, classifyBomRow, buildMissingCartEntries, buildLinkableKeys } from '../../js/bom/bom-logic.js';
import { countStatuses } from '../../js/part-keys.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const KICAD_BOM = readFileSync(join(__dirname, 'e2e', 'fixtures', 'bom-kicad-board-features.csv'), 'utf8');

// [label, bom line, expected reason pattern | null]
const CASES = [
  // ── the reported rows (KiCad, Value-as-MPN, no LCSC) ──
  ['test pad', { value: 'TestPoint', mpn: 'TestPoint', footprint: 'TestPoint:TestPoint_Pad_D1.0mm' }, /test pad/i],
  ['test pad in a custom library', { value: '12V6', mpn: '12V6', footprint: 'MyLib:TestPoint_Pad_D1.0mm_Compact' }, /test pad/i],
  ['plated test hole', { value: 'TP', mpn: 'TP', footprint: 'TestPoint:TestPoint_Plated_Hole_D2.0mm' }, /test pad/i],
  ['THT test pad', { value: 'TP', mpn: 'TP', footprint: 'TestPoint:TestPoint_THTPad_D1.5mm_Drill0.7mm' }, /test pad/i],
  ['mounting hole', { value: 'MountingHole_Pad', mpn: 'MountingHole_Pad', footprint: 'MountingHole:MountingHole_1.7mm_M1.6_Pad_Via' }, /mounting hole/i],
  ['fiducial', { value: 'Fiducial', mpn: 'Fiducial', footprint: 'Fiducial:Fiducial_1mm_Mask2mm' }, /fiducial/i],
  ['Tag-Connect TC2070', { value: 'TC2070-NL-FP', mpn: 'TC2070-NL-FP', footprint: 'Connector_TagConnect:Tag-Connect_TC2070-IDC-NL_2x07_P1.27mm_Vertical' }, /tag-connect/i],
  ['Tag-Connect TC2030', { value: 'TC2030', mpn: 'TC2030', footprint: 'Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical' }, /tag-connect/i],
  ['bare TC2050 footprint name', { value: 'PROG', mpn: 'PROG', footprint: 'MyLib:TC2050-IDC-NL' }, /tag-connect/i],
  // ── no footprint column: the value decides ──
  ['fiducial by value', { value: 'Fiducial', mpn: 'Fiducial', footprint: '' }, /fiducial/i],
  ['mounting hole by value', { value: 'MountingHole', mpn: 'MountingHole', footprint: '' }, /mounting hole/i],
  ['test point by value', { value: 'TestPoint', mpn: 'TestPoint', footprint: '' }, /test pad/i],
  ['TC2030 by value', { value: 'TC2030-IDC', mpn: 'TC2030-IDC', footprint: '' }, /tag-connect/i],
  // ── the design's own flag, even on a line with a part number ──
  ['excluded from BOM', { value: 'Logo', mpn: 'Logo', footprint: 'Symbol:OSHW-Logo', excluded: true }, /excluded from bom/i],
  ['excluded with an LCSC code', { value: '10k', lcsc: 'C25744', footprint: 'Resistor_SMD:R_0402', excluded: true }, /excluded from bom/i],

  // ── near-miss negatives: real parts ──
  ['Keystone test point with an LCSC code', { value: 'TestPoint', mpn: 'TestPoint', lcsc: 'C238122', footprint: 'TestPoint:TestPoint_Keystone_5000-5004_Miniature' }, null],
  ['Keystone test point footprint, no code', { value: 'TestPoint', mpn: 'TestPoint', footprint: 'TestPoint:TestPoint_Keystone_5000-5004_Miniature' }, null],
  ['wire test loop', { value: 'TP', mpn: 'TP', footprint: 'TestPoint:TestPoint_Loop_D2.50mm_Drill1.0mm' }, null],
  ['standoff on a mounting-hole footprint, LCSC', { value: 'Standoff', mpn: 'Standoff', lcsc: 'C2916370', footprint: 'MountingHole:MountingHole_2.2mm_M2_Pad' }, null],
  ['standoff on a mounting-hole footprint, real MPN', { value: 'Standoff', mpn: '9774020151R', footprint: 'MountingHole:MountingHole_2.2mm_M2_Pad' }, null],
  ['op-amp whose MPN contains TC2050', { value: 'LTC2050', mpn: 'LTC2050', footprint: 'Package_TO_SOT_SMD:SOT-23-5' }, null],
  ['op-amp LTC2050, no footprint', { value: 'LTC2050', mpn: 'LTC2050', footprint: '' }, null],
  ['LDO whose name starts TC20', { value: 'TC2054', mpn: 'TC2054', footprint: '' }, null],
  ['Tag-Connect footprint with a real MPN (a part is placed)', { value: 'TC2030', mpn: 'TC2030-CTX', footprint: 'Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical' }, null],
  ['plain resistor', { value: '10k', mpn: '10k', footprint: 'Resistor_SMD:R_0402_1005Metric' }, null],
  ['empty line', { value: '', mpn: '', footprint: '' }, null],
];

describe('boardFeatureReason', () => {
  it.each(CASES)('%s', (_label, bom, expected) => {
    const reason = boardFeatureReason({ lcsc: '', ...bom });
    if (expected === null) expect(reason).toBeNull();
    else expect(reason).toMatch(expected);
  });

  it('answers null for a missing line', () => {
    expect(boardFeatureReason(null)).toBeNull();
    expect(boardFeatureReason(undefined)).toBeNull();
  });

  it('reads an MPN identical to the value as "no part number" (the Value fallback)', () => {
    expect(hasPartNumber({ mpn: 'TC2030', value: 'tc2030' })).toBe(false);
    expect(hasPartNumber({ mpn: 'TC2030-CTX', value: 'TC2030' })).toBe(true);
    expect(hasPartNumber({ lcsc: 'C1', mpn: '', value: '' })).toBe(true);
  });
});

describe('rawRowBoardReason / classifyBomRow agree with the aggregated line', () => {
  const headers = ['Value', 'Reference', 'Footprint', 'LCSC', 'DNP', 'Exclude from BOM'];
  const cols = detectBOMColumns(headers);

  it('reads every field through the detected columns', () => {
    expect(rawRowBoardReason(['Fiducial', 'FID1', 'Fiducial:Fiducial_1mm', '', '', ''], cols)).toMatch(/fiducial/i);
    expect(rawRowBoardReason(['10k', 'R1', 'R_0402', 'C25744', '', ''], cols)).toBeNull();
    expect(rawRowBoardReason(['10k', 'R1', 'R_0402', 'C25744', '', 'Excluded from BOM'], cols)).toBe(EXCLUDED_FROM_BOM_REASON);
  });

  it('classifies a board feature as board-only, ahead of DNP, and never as a warning', () => {
    expect(classifyBomRow(['Fiducial', 'FID1', 'Fiducial:Fiducial_1mm', '', 'DNP', ''], cols)).toBe('board-only');
    expect(classifyBomRow(['', 'LOGO1', 'Symbol:Logo', '', '', 'Excluded from BOM'], cols)).toBe('board-only');
    expect(classifyBomRow(['10k', 'R1', 'R_0402', 'C25744', 'DNP', ''], cols)).toBe('dnp');
    expect(classifyBomRow(['10k', 'R1', 'R_0402', 'C25744', '', ''], cols)).toBe('ok');
  });

  it('copes with hand-built cols that carry no exclude column', () => {
    const legacy = { lcsc: 0, mpn: 1, qty: -1, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
    expect(rawRowBoardReason(['C1', 'X'], legacy)).toBeNull();
    expect(classifyBomRow(['C1', 'X'], legacy)).toBe('ok');
  });
});

describe('detectBOMColumns — DNP and "Exclude from BOM" are separate columns', () => {
  it('maps both when KiCad exports both (the exclude flag used to be dropped)', () => {
    const cols = detectBOMColumns(['Value', 'Reference', 'Footprint', 'LCSC', 'DNP', 'Exclude from BOM']);
    expect(cols.dnp).toBe(4);
    expect(cols.exclude).toBe(5);
  });

  it('maps an exclude-only BOM to exclude, not DNP', () => {
    const cols = detectBOMColumns(['Reference', 'Value', 'Exclude from BOM']);
    expect(cols.dnp).toBe(-1);
    expect(cols.exclude).toBe(2);
  });

  it('keeps JLC Procurement Type as the DNP column alongside an exclude column', () => {
    const cols = detectBOMColumns(['Designator*', 'Quantity*', 'Manufacturer Part Number*', 'Value', 'Procurement Type', 'Exclude from BOM', 'Exclude from Board']);
    expect(cols.dnp).toBe(4);
    expect(cols.exclude).toBe(5);
  });

  it('marks a merged line excluded only when every merged row is', () => {
    const headers = ['LCSC', 'Reference', 'Exclude from BOM'];
    const cols = detectBOMColumns(headers);
    const both = aggregateBomRows([['C1', 'R1', 'Excluded from BOM'], ['C1', 'R2', 'Excluded from BOM']], headers, cols).aggregated;
    expect(both.get('C1').excluded).toBe(true);
    const mixed = aggregateBomRows([['C1', 'R1', 'Excluded from BOM'], ['C1', 'R2', '']], headers, cols).aggregated;
    expect(mixed.get('C1').excluded).toBe(false);
    expect(mixed.get('C1').qty).toBe(2);
  });
});

describe('the reported KiCad BOM shape, end to end (synthetic fixture)', () => {
  // 41 unique lines: 32 real parts (incl. three near-miss negatives), 2 DNP,
  // 7 board features. With no inventory every real part is missing.
  const { aggregated } = processBOM(KICAD_BOM, 'bom-kicad-board-features.csv');
  const { results } = matchBOM(aggregated, [], [], [], []);
  const rows = computeRows(results, 1, {});
  const c = countStatuses(rows);

  it('counts 41 total, 2 DNP, 7 board-only, so UTBP is 32', () => {
    expect(c.total).toBe(41);
    expect(c.dnp).toBe(2);
    expect(c.boardOnly).toBe(7);
    expect(c.utbp).toBe(32);
  });

  it('takes the 7 board features out of Missing', () => {
    expect(c.missing).toBe(32);
  });

  it('never sends a board feature to the cart or offers it as a link target', () => {
    const boardMpns = rows.filter(r => r.effectiveStatus === 'board-only').map(r => r.bom.mpn);
    expect(boardMpns.sort()).toEqual(['12V6', '5V', 'Fiducial', 'MountingHole_Pad', 'TC2030', 'TC2070-NL-FP', 'TestPoint'].sort());
    const cart = buildMissingCartEntries(rows);
    expect(cart).toHaveLength(32);
    expect(cart.map(e => e.raw && e.raw.mpn)).not.toEqual(expect.arrayContaining(['Fiducial']));
    const linkable = buildLinkableKeys(rows, true);
    for (const mpn of boardMpns) expect(linkable.has(mpn.toUpperCase())).toBe(false);
  });

  it('gives every board-only row its user-facing reason', () => {
    for (const r of rows.filter(x => x.effectiveStatus === 'board-only')) {
      expect(r.boardReason).toMatch(/nothing to place/);
    }
  });
});

describe('computeRows strips a board feature of whatever matchBOM matched it to', () => {
  it('drops inv, alts and generic data so nothing downstream acts on the match', () => {
    const [row] = computeRows([{
      bom: { qty: 1, dnp: false, lcsc: '', mpn: 'TC2030', value: 'TC2030', footprint: 'Connector:Tag-Connect_TC2030-IDC-NL' },
      inv: { lcsc: '', mpn: 'TC2030', qty: 3, unit_price: 40 }, matchType: 'mpn',
      alts: [{ qty: 2 }], genericPartId: 'g1', genericPartName: 'G', genericMembers: [{}],
    }], 1, {});
    expect(row.effectiveStatus).toBe('board-only');
    expect(row.inv).toBeNull();
    expect(row.alts).toEqual([]);
    expect(row.altQty).toBe(0);
    expect(row.genericPartId).toBeNull();
    expect(row.matchType).toBe('none');
  });
});
