// The resolution spec, pinned as data — the JS half.
//
// Iterates the SAME fixture
// tests/python/domain/test_threshold_resolution_parity.py runs:
// tests/fixtures/thresholds/resolution-cases.json. Both languages must answer
// every case identically — that agreement is the whole reason the fixture
// exists (the frontend once fell back to $50 and `dubis low-stock` to 0, so
// the same part was low for one caller and fine for the other). The prose
// spec lives in domain/thresholds.py's "The resolution spec" section;
// js/threshold-logic.js mirrors it, and this file is what keeps the mirror
// flat.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { resolveThreshold } from '../../js/threshold-logic.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, '..', 'fixtures', 'thresholds', 'resolution-cases.json');
const data = JSON.parse(readFileSync(FIXTURE, 'utf8'));

describe('the resolution spec (shared parity fixture)', () => {
  it('has cases to pin', () => {
    expect(data.cases.length).toBeGreaterThan(0);
  });

  for (const testCase of data.cases) {
    it(testCase.name, () => {
      const got = resolveThreshold(testCase.section, testCase.overrides, {
        preset: testCase.preset,
        server: testCase.server,
        defaults: testCase.defaults,
        base: testCase.base,
      });
      expect(
        got,
        `${testCase.name}: resolveThreshold() answered ${got}, fixture says ` +
          `${testCase.expected}. If the spec genuinely changed, change it in ` +
          'domain/thresholds.py prose FIRST, then this fixture, then both ' +
          'languages — in one commit.',
      ).toBe(testCase.expected);
    });
  }
});
