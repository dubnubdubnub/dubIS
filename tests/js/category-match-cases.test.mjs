/* The category-name match, pinned for the JS half against the SAME case table
   the Python half is pinned against (tests/python/test_category_name_match.py).

   Why a fixture file rather than cases written here: the grid (js/category-
   match.js) and `dubis search --category-hit` (domain/category_match.py) answer
   the same question for the same data, and two hand-written case lists drift by
   construction — whoever edits one does not know the other exists. One table,
   two readers, so a rule change has to be made in both implementations or one of
   the two suites goes red naming the case.

   The case that makes this worth the ceremony is `ic` → `ICs - Microcontrollers`
   = false. Any "simpler" substring implementation passes every other case in the
   table and fails that one, by lighting up nine sections for two letters. */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { matchesCategoryName, nameTokens } from '../../js/category-match.js';

const CASES = JSON.parse(readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'category-name', 'cases.json'), 'utf8',
)).cases;

describe('the shared category-name case table', () => {
  it('is not empty and covers both verdicts', () => {
    expect(CASES.length).toBeGreaterThan(20);
    const trues = CASES.filter((c) => c.expect).length;
    expect(trues).toBeGreaterThan(0);
    expect(CASES.length - trues).toBeGreaterThan(0);
  });

  it('gives every case a reason', () => {
    // A table nobody can read gets "fixed" by deleting the row that fails.
    for (const c of CASES) {
      expect(c.why, `${c.query} / ${c.section} has no why`).toBeTruthy();
    }
  });

  it('never contradicts itself: one query+section has one verdict', () => {
    const seen = new Map();
    for (const c of CASES) {
      const key = `${c.query}\u0000${c.section}`;
      if (seen.has(key)) {
        expect(seen.get(key), `"${c.query}" vs "${c.section}" has two expectations`)
          .toBe(c.expect);
      }
      seen.set(key, c.expect);
    }
  });
});

describe('matchesCategoryName (JS half of the shared table)', () => {
  for (const c of CASES) {
    it(`"${c.query}" ${c.expect ? 'names' : 'does not name'} "${c.section || '(no section)'}"`, () => {
      expect(matchesCategoryName(c.query, c.section)).toBe(c.expect);
    });
  }

  it('is symmetric in case on both sides', () => {
    // The grid hands it an already-lowercased term; the CLI lowercases too. But
    // the matcher must not DEPEND on that, or a future caller gets a different
    // answer than the table promises.
    expect(matchesCategoryName('DIODES', 'Diodes')).toBe(true);
    expect(matchesCategoryName('diodes', 'DIODES')).toBe(true);
  });

  it('tolerates a missing section rather than throwing', () => {
    // A row with no `section` is a real shape (groupBySection files it under
    // "Other", but bom-row-data.js passes r.inv.section straight through).
    for (const missing of [undefined, null, '']) {
      expect(matchesCategoryName('diodes', missing)).toBe(false);
    }
  });
});

describe('nameTokens', () => {
  it('drops every separator the section vocabulary uses', () => {
    expect(nameTokens('ICs - Power / Voltage Regulators'))
      .toEqual(['ics', 'power', 'voltage', 'regulators']);
    expect(nameTokens('Connectors > SMD')).toEqual(['connectors', 'smd']);
    expect(nameTokens('Mechanical & Hardware')).toEqual(['mechanical', 'hardware']);
    expect(nameTokens('Development Boards, Kits, Programmers'))
      .toEqual(['development', 'boards', 'kits', 'programmers']);
  });

  it('produces no tokens for a name that is all separators', () => {
    expect(nameTokens(' - / & > ')).toEqual([]);
    expect(nameTokens(undefined)).toEqual([]);
    expect(nameTokens(null)).toEqual([]);
  });
});
