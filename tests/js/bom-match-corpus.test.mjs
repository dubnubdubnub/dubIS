// @ts-check
/* bom-match-corpus.test.mjs — score BOM matching against labelled real-shaped cases.
 *
 * Each directory under tests/fixtures/bom-match-corpus/ is one case: a BOM as
 * the user's EDA tool exported it, an inventory, the LCSC answers for the BOM's
 * codes, and the expected outcome per line. See the README there for how a bug
 * report becomes a case.
 *
 * The pipeline is the app's, end to end and in the app's order
 * (js/bom/bom-panel.js): processBOM -> match -> look up the unidentified rows'
 * LCSC codes -> match again with the specs applied. Only the network is
 * replaced, by a fetcher that answers from the captured lookups and throws on
 * any code it was not given — so a case can never silently test a lookup that
 * did not happen.
 *
 * Two kinds of assertion, and the second is the one that matters:
 *
 *   expected table   each line lands where the case says (status, part, how).
 *                    A regression or an improvement both fail it, on purpose:
 *                    an improvement updates the table in the same commit.
 *
 *   the gate         ANY non-identity suggestion (value, fuzzy, prefix,
 *                    generic, an alternate) whose ground truth contradicts the
 *                    line's ground truth fails — wrong value, wrong package,
 *                    wrong type, or a capacitor rated lower. It runs on BOTH
 *                    passes, because the reported bug (a 1kΩ line "possibly"
 *                    matching a 100Ω part) lived in the pass before any lookup.
 *                    Truth comes from the fixtures' hand-checked `truth`
 *                    blocks, never from the matcher's own parsers: a gate that
 *                    re-parsed descriptions with extractBomValue would agree
 *                    with every bug extractBomValue has.
 */

import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { processBOM } from '../../js/csv-parser.js';
import { matchBOM, VALUE_TOLERANCE } from '../../js/matching.js';
import { bomAggKey, invPartKey } from '../../js/part-keys.js';
import { createSpecLookup, codesNeedingSpec, IDENTITY_MATCH_TYPES } from '../../js/bom/bom-spec-lookup.js';

const CORPUS = join(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'bom-match-corpus');

const CASES = readdirSync(CORPUS)
  .filter((name) => statSync(join(CORPUS, name)).isDirectory())
  .sort();

function readJson(dir, file) {
  return JSON.parse(readFileSync(join(dir, file), 'utf8'));
}

function loadCase(name) {
  const dir = join(CORPUS, name);
  const inventoryDoc = readJson(dir, 'inventory.json');
  const parts = inventoryDoc.parts.map((p) => p.record);
  /** @type {Record<string, any>} */
  const partTruth = {};
  for (const p of inventoryDoc.parts) {
    const key = invPartKey(p.record);
    if (!p.truth) throw new Error(`${name}: inventory part ${key} has no truth block`);
    partTruth[key] = p.truth;
  }
  let genericParts = [];
  try { genericParts = readJson(dir, 'generic-parts.json'); } catch { /* optional */ }
  return {
    dir,
    bomText: readFileSync(join(dir, 'bom.csv'), 'utf8'),
    parts,
    partTruth,
    lookups: readJson(dir, 'lcsc-lookups.json'),
    expected: readJson(dir, 'expected.json').lines,
    genericParts,
  };
}

/** Answers from the capture; an uncaptured code is a broken case, not a miss. */
function fixtureFetcher(caseName, lookups) {
  return async (code) => {
    if (!(code in lookups)) {
      throw new Error(`corpus case ${caseName} asked LCSC about ${code}, which lcsc-lookups.json `
        + 'does not capture — run scripts/capture-bom-corpus-lookups.mjs on the case BOM');
    }
    const entry = lookups[code];
    if (entry.error) throw Object.assign(new Error(entry.error.code), entry.error);
    return entry.product;
  };
}

/** The app's two-pass flow, without the DOM. */
async function runPipeline(c, caseName) {
  const parsed = processBOM(c.bomText, 'bom.csv');
  if (!parsed) throw new Error(`${caseName}: bom.csv did not parse`);
  const lookup = createSpecLookup({ fetchProduct: fixtureFetcher(caseName, c.lookups) });
  const match = () => {
    lookup.apply(parsed.aggregated);
    return matchBOM(parsed.aggregated, c.parts, [], [], c.genericParts);
  };
  const before = match();
  const outcome = await lookup.lookup(codesNeedingSpec(before.results));
  const after = match();
  return { before, after, outcome };
}

const near = (a, b) => (a === 0 || b === 0)
  ? a === b
  : Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b)) <= VALUE_TOLERANCE;

/**
 * Why a suggested part contradicts the line, or null when it does not.
 * A passive line whose own truth is unknown cannot vouch for any suggestion.
 */
function contradiction(lineTruth, partTruth) {
  if (!lineTruth) return 'the line has no truth block';
  if (!partTruth) return 'the suggested part has no truth block';
  const passive = ['C', 'R', 'L'].includes(lineTruth.type);
  if (lineTruth.type && partTruth.type && lineTruth.type !== partTruth.type) {
    return `type ${partTruth.type} for a ${lineTruth.type} line`;
  }
  if (passive) {
    if (typeof lineTruth.value !== 'number') return 'the line value is unknown, so no suggestion can be verified';
    if (typeof partTruth.value !== 'number' || !near(lineTruth.value, partTruth.value)) {
      return `value ${partTruth.value} for a ${lineTruth.value} line`;
    }
  }
  if (lineTruth.package && partTruth.package && lineTruth.package !== partTruth.package) {
    return `package ${partTruth.package} for a ${lineTruth.package} line`;
  }
  if (lineTruth.type === 'C' && typeof lineTruth.voltage === 'number'
      && typeof partTruth.voltage === 'number' && partTruth.voltage < lineTruth.voltage) {
    return `${partTruth.voltage}V for a ${lineTruth.voltage}V line`;
  }
  return null;
}

/** Every non-identity suggestion in a match, as [lineKey, partKey, kind]. */
function suggestions(results) {
  const out = [];
  for (const r of results) {
    const key = bomAggKey(r.bom);
    if (r.inv && !IDENTITY_MATCH_TYPES.has(r.matchType)) out.push([key, invPartKey(r.inv), r.matchType]);
    // Alternates are suggestions whatever matched the primary.
    for (const a of r.alts || []) out.push([key, invPartKey(a), 'alt']);
  }
  return out;
}

describe('bom-match corpus', () => {
  it('has at least one case', () => {
    expect(CASES.length).toBeGreaterThan(0);
  });

  describe.each(CASES)('%s', (caseName) => {
    const c = loadCase(caseName);

    it('the expected table covers exactly the BOM lines', async () => {
      const { after } = await runPipeline(c, caseName);
      const lines = after.results.map((r) => bomAggKey(r.bom)).sort();
      expect(lines).toEqual(Object.keys(c.expected).sort());
    });

    it('every lookup the pipeline made was answered from the capture', async () => {
      const { outcome } = await runPipeline(c, caseName);
      expect(outcome.failed, JSON.stringify(outcome.failed)).toEqual([]);
    });

    it('identity-matched lines are never looked up', async () => {
      const { before, outcome } = await runPipeline(c, caseName);
      const identity = new Set(before.results
        .filter((r) => IDENTITY_MATCH_TYPES.has(r.matchType)).map((r) => r.bom.lcsc));
      expect(outcome.asked.filter((code) => identity.has(code))).toEqual([]);
    });

    it('every line lands where the case says', async () => {
      const { after } = await runPipeline(c, caseName);
      const got = {};
      const want = {};
      for (const r of after.results) {
        const key = bomAggKey(r.bom);
        const exp = c.expected[key];
        got[key] = { status: r.status, part: r.inv ? invPartKey(r.inv) : null,
          ...(exp && exp.match ? { match: r.matchType } : {}) };
        want[key] = exp ? { status: exp.status, part: exp.part,
          ...(exp.match ? { match: exp.match } : {}) } : null;
      }
      expect(got).toEqual(want);
    });

    it.each(['before', 'after'])('GATE (%s lookup): no suggestion contradicts its line', async (pass) => {
      const run = await runPipeline(c, caseName);
      const violations = suggestions(run[pass].results)
        .map(([line, part, kind]) => {
          const why = contradiction(c.expected[line] && c.expected[line].truth, c.partTruth[part]);
          return why ? `${line} -> ${part} (${kind}): ${why}` : null;
        })
        .filter(Boolean);
      expect(violations, 'suggested matches that are provably wrong:\n  ' + violations.join('\n  '))
        .toEqual([]);
    });
  });
});
