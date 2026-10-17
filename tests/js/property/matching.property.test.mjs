// @ts-check
/* matching.property.test.mjs — invariants of js/matching.js's suggestions.
 *
 * The example suites (tests/js/matching*.test.js) and the labelled corpus
 * (tests/js/bom-match-corpus.test.mjs) prove the cases someone wrote down. This
 * attacks the rule behind them — the evidence rule at the top of matching.js —
 * with generated BOM lines and inventories:
 *
 *   soundness    every non-identity suggestion for a passive (a value, prefix,
 *                fuzzy or generic match, or an alternate) is TRUE: same type,
 *                same value, no known-different package, no known-lower
 *                capacitor rating — and a non-identity primary needs the line's
 *                value to have been expressed at all. Judged against the
 *                generator's ground truth, never by re-parsing text, so the
 *                assertion cannot share a parsing bug with the code under test.
 *
 *   metamorphic  a fact moved from the row's own columns into `bom.spec` (the
 *                LCSC lookup's answer) yields the same match. The spec is
 *                another way to say the same thing; it must not be a weaker or
 *                stronger one.
 *
 *   identity     a spec never changes an exact-MPN match.
 *
 * Why the suite earns its keep: run against the pre-fix matcher, `soundness`
 * fails within its first hundred examples — the reported bug's class, a
 * suggestion for a line whose value was never established ("unknown value =
 * compatible") — and `metamorphic` fails too, since that matcher never read
 * `bom.spec`. Against the fixed one it found three more real gaps, each now
 * pinned in tests/js/matching-spec.test.js: LCSC's plain "100Ω" Resistance
 * read as unknown (parseEEValue wants a multiplier); a spec carrying only a
 * Capacitance did not make a designator-less line a capacitor; and a
 * designator-less line on a KiCad "Inductor_SMD:" footprint was not treated
 * as a passive, so MPN similarity alone matched it.
 */

import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import { matchBOM, VALUE_TOLERANCE } from '../../../js/matching.js';
import { invPartKey } from '../../../js/part-keys.js';
import { bomMatchScenario, bomLineHow, buildBomLine, passiveTruth } from './arbitraries.mjs';

const near = (a, b) => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b)) <= VALUE_TOLERANCE;

function runMatch(bom, parts) {
  const aggregated = new Map([['LINE', bom]]);
  return matchBOM(aggregated, parts.map((p) => p.record), [], [], []).results[0];
}

/** Exact MPN identity, the way matchBOM's step 2 defines it (incl. `_`/`.`). */
function sameMpn(a, b) {
  const x = (a || '').toUpperCase();
  const y = (b || '').toUpperCase();
  return !!x && (x === y || x.replace(/_/g, '.') === y || x.replace(/\./g, '_') === y);
}

/** Why `part` is a wrong suggestion for `line`, or null. */
function wrongness(line, part, { needLineValue }) {
  const lt = line.truth;
  const pt = part.truth;
  if (lt.type !== pt.type) return `type ${pt.type} offered for a ${lt.type} line`;
  if (needLineValue && !line.shown.value) return 'line value was never expressed, yet a match was proposed';
  if (!near(lt.value, pt.value)) return `value ${pt.value} offered for ${lt.value}`;
  if (line.shown.package && part.shown.package && lt.package !== pt.package) {
    return `package ${pt.package} offered for ${lt.package}`;
  }
  if (lt.type === 'C' && line.shown.voltage && part.shown.voltage && pt.voltage < lt.voltage) {
    return `${pt.voltage}V offered for a ${lt.voltage}V line`;
  }
  return null;
}

describe('matchBOM — soundness of suggestions', () => {
  it('never proposes a part that contradicts what the line is', () => {
    fc.assert(fc.property(bomMatchScenario(), ({ line, parts }) => {
      const r = runMatch(line.bom, parts);
      const byKey = new Map(parts.map((p) => [invPartKey(p.record), p]));
      const problems = [];
      const primaryIsIdentity = !!r.inv && sameMpn(r.inv.mpn, line.bom.mpn);
      // Out of scope only when NOTHING the matcher can see says passive: no
      // designator, no standard-library footprint, no value with a unit, and a
      // part filed under "Other". Then MPN similarity is honestly all there is,
      // exactly as for an IC.
      const knowable = (part) => line.typed || part.record.section !== 'Other';
      if (r.inv && !primaryIsIdentity && knowable(byKey.get(invPartKey(r.inv)))) {
        const why = wrongness(line, byKey.get(invPartKey(r.inv)), { needLineValue: true });
        if (why) problems.push(`${r.matchType}: ${why}`);
      }
      for (const alt of r.alts) {
        const why = wrongness(line, byKey.get(invPartKey(alt)), { needLineValue: false });
        if (why) problems.push(`alt: ${why}`);
      }
      expect(problems).toEqual([]);
    }));
  });
});

describe('matchBOM — a spec says the same thing as the columns', () => {
  it('moving value, voltage and package into bom.spec does not change the match', () => {
    fc.assert(fc.property(
      bomMatchScenario(),
      bomLineHow(),
      ({ line, parts }, how) => {
        // Same truth and MPN, expressed twice: once in the row's own columns,
        // once only through the spec. An explicit MPN column (mpn !== value)
        // keeps steps 2-4 identical between the two, so the value's channel is
        // the only thing that differs.
        const mpn = how.mpn;
        const viaColumns = buildBomLine(line.truth, { ...how, valueVia: 'desc', voltageVia: 'desc', packageVia: 'footprint' });
        const viaSpec = buildBomLine(line.truth, { ...how, valueVia: 'spec', voltageVia: 'spec', packageVia: 'spec' });
        viaColumns.bom.mpn = mpn;
        viaSpec.bom.mpn = mpn;
        const a = runMatch(viaColumns.bom, parts);
        const b = runMatch(viaSpec.bom, parts);
        const shape = (r) => ({
          inv: r.inv ? invPartKey(r.inv) : null,
          matchType: r.matchType,
          status: r.status,
          alts: r.alts.map(invPartKey).sort(),
        });
        expect(shape(b)).toEqual(shape(a));
      },
    ));
  });
});

describe('matchBOM — identity is untouched by a spec', () => {
  it('an exact-MPN match stays that match whatever the spec claims', () => {
    fc.assert(fc.property(
      bomMatchScenario(),
      passiveTruth(),
      bomLineHow(),
      ({ parts }, otherTruth, how) => {
        const target = parts[0].record;
        // A spec describing a DIFFERENT part — even a contradicting one cannot
        // override the user's own MPN.
        const line = buildBomLine(otherTruth, { ...how, valueVia: 'spec', mpn: target.mpn });
        const r = runMatch(line.bom, parts);
        expect(r.matchType).toBe('mpn');
        expect(sameMpn(r.inv && r.inv.mpn, target.mpn)).toBe(true);
      },
    ));
  });
});
