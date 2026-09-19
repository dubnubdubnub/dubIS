// @ts-check
/* part-keys.property.test.mjs — properties of js/part-keys.js's identity rules.
 *
 * The JS reference implementation of the property harness; the Python one is
 * tests/python/test_federation_properties.py. Read docs/property-testing.md.
 *
 * Why `invPartKey` earns one: it is the identity function for the whole
 * inventory table. `js/inventory/inv-state.js` builds `rowMap` from it and
 * `js/inventory/inv-html-builders.js` writes it into `tr.dataset.partKey`, so
 * two records that key the same collapse into one row and one record that keys
 * two different ways appears twice. Either way the stock number on screen is
 * wrong and nothing throws — the silent-wrong-answer shape these tests exist
 * for. `domain/federation.py` ports this exact function server-side, and the
 * port's property suite has already found two real merge bugs by attacking the
 * seam between "falsy" and "blank after trimming".
 *
 * `compressRefs` gets the second half: it is pure, total, and its output is what
 * a user reads off the BOM panel, so an invariant ("no designator is lost") is
 * both statable and worth stating.
 */

import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import { compressRefs, countStatuses, invPartKey } from '../../../js/part-keys.js';
import {
  IDENTITY_FIELDS,
  PART_KEY_BRANCHES,
  identityFields,
  inventoryRecord,
  isSchemaValid,
} from './arbitraries.mjs';

describe('invPartKey — identity', () => {
  it('always returns one of the five fields it was given, verbatim', () => {
    // Never synthesised: a key the frontend invented would not match the one
    // the hub grouped merged rows under, and the two views would disagree about
    // which rows are the same part.
    fc.assert(fc.property(identityFields(), ({ fields }) => {
      const key = invPartKey(fields);
      expect(new Set(IDENTITY_FIELDS.map((f) => fields[f]))).toContain(key);
    }));
  });

  it('is total and always returns a string', () => {
    fc.assert(fc.property(inventoryRecord({ keyable: false }), (record) => {
      expect(typeof invPartKey(record)).toBe('string');
    }));
  });

  it('returns "" exactly when every identity field is falsy', () => {
    // The rule the Python port deliberately diverges from (it raises instead),
    // so it is worth pinning on this side too.
    fc.assert(fc.property(identityFields(), ({ fields }) => {
      const allFalsy = IDENTITY_FIELDS.every((f) => !fields[f]);
      expect(invPartKey(fields) === '').toBe(allFalsy);
    }));
  });

  it('lets a C-prefixed lcsc win outright, whatever else is filled in', () => {
    fc.assert(fc.property(identityFields('lcsc'), ({ fields }) => {
      expect(invPartKey(fields)).toBe(fields.lcsc);
    }));
  });

  it('returns the lcsc as written — only the prefix test is case-insensitive', () => {
    fc.assert(fc.property(identityFields('lcsc'), ({ fields }) => {
      const key = invPartKey(fields);
      expect(key).toBe(fields.lcsc);
      expect(key === key.toUpperCase() || key === fields.lcsc).toBe(true);
    }));
  });

  it('falls through a non-C lcsc rather than being blocked by it', () => {
    // A stray "n/a" in the lcsc column must not stop the MPN winning — the
    // documented behaviour that separates this rule from derive_key's.
    fc.assert(fc.property(
      identityFields('mpn').filter(({ fields }) => Boolean(fields.lcsc)),
      ({ fields }) => {
        expect(invPartKey(fields)).toBe(fields.mpn);
      },
    ));
  });

  it('returns the field the generator aimed at', () => {
    // Pins the generator against the function rather than restating the rule:
    // if identityFields('pololu') ever stops actually producing a pololu-keyed
    // record, every property that leans on the branch label goes quietly weak.
    fc.assert(fc.property(identityFields(), ({ branch, fields }) => {
      if (branch === 'unkeyable') {
        expect(invPartKey(fields)).toBe('');
        return;
      }
      expect(invPartKey(fields)).toBe(fields[branch]);
    }));
  });

  it('blanking an earlier field cannot change the key', () => {
    // Metamorphic, so it says something the function does not: precedence is a
    // real order. An implementation that consulted the fields in a different
    // order, or that let a later field pre-empt an earlier one, breaks this
    // without breaking any single-value assertion. The first draft of this test
    // instead recomputed `find(Boolean)` over all five fields and failed
    // immediately on `{lcsc: " "}` — a tautological property that was not even
    // a correct tautology, which is the anti-pattern the guide warns about.
    fc.assert(fc.property(identityFields(), ({ branch, fields }) => {
      if (branch === 'unkeyable') return;
      const before = IDENTITY_FIELDS.slice(0, IDENTITY_FIELDS.indexOf(branch));
      const blanked = { ...fields };
      for (const f of before) blanked[f] = '';
      expect(invPartKey(blanked)).toBe(invPartKey(fields));
    }));
  });

  it('blanking the winning field promotes a later one, never an earlier one', () => {
    fc.assert(fc.property(identityFields(), ({ branch, fields }) => {
      if (branch === 'unkeyable') return;
      const winner = IDENTITY_FIELDS.indexOf(branch);
      const demoted = invPartKey({ ...fields, [branch]: '' });
      if (demoted === '') return;
      const later = IDENTITY_FIELDS.slice(winner + 1).map((f) => fields[f]);
      expect(later).toContain(demoted);
    }));
  });

  it('aims at every branch of the rule', () => {
    // Coverage of the generator itself. A property suite whose generator has
    // quietly stopped reaching a branch still passes, which is the failure mode
    // that hollows out a property suite from the inside.
    const seen = new Set();
    fc.assert(fc.property(identityFields(), ({ branch }) => {
      seen.add(branch);
      return true;
    }), { numRuns: 500 });
    expect([...seen].sort()).toEqual([...PART_KEY_BRANCHES].sort());
  });
});

describe('inventoryRecord — the generator itself', () => {
  it('produces schema-valid records', () => {
    fc.assert(fc.property(inventoryRecord(), (record) => {
      expect(isSchemaValid(record)).toBe(true);
    }));
  });

  it('produces keyable records when asked to', () => {
    fc.assert(fc.property(inventoryRecord({ keyable: true }), (record) => {
      expect(invPartKey(record)).not.toBe('');
    }));
  });

  it('refuses an override for a field the schema does not define', () => {
    expect(() => inventoryRecord({ overrides: { not_a_field: fc.constant(1) } }))
      .toThrow(/INVENTORY_FIELDS/);
  });
});

// ── compressRefs ─────────────────────────────────────────────────────────────

/** Designators like R1, C12, U3 — the shape compressRefs is built for. */
const refList = () => fc.uniqueArray(
  fc.tuple(
    fc.constantFrom('R', 'C', 'U', 'D', 'L', 'Q', 'Y', 'JP'),
    fc.integer({ min: 1, max: 99 }),
  ).map(([prefix, n]) => `${prefix}${n}`),
  { minLength: 1, maxLength: 12 },
);

/** Expand "R1–R4" / "R1–4" style ranges back into individual designators. */
function expand(compressed) {
  const out = [];
  for (const part of compressed.split(/,\s*/)) {
    const m = part.match(/^([A-Za-z]+)(\d+)–(?:([A-Za-z]+))?(\d+)$/);
    if (!m) { out.push(part); continue; }
    const [, prefix, from, , to] = m;
    for (let n = Number(from); n <= Number(to); n += 1) out.push(`${prefix}${n}`);
  }
  return out;
}

describe('compressRefs — no designator is lost', () => {
  it('round-trips: expanding the compressed form gives the input back', () => {
    // The invariant that matters. A compression that drops R7 reads perfectly
    // and sends someone to the bench one resistor short.
    fc.assert(fc.property(refList(), (refs) => {
      expect(expand(compressRefs(refs.join(', ')))).toEqual(refs);
    }));
  });

  it('never grows the string', () => {
    fc.assert(fc.property(refList(), (refs) => {
      const joined = refs.join(', ');
      expect(compressRefs(joined).length).toBeLessThanOrEqual(joined.length);
    }));
  });

  it('is idempotent on a single designator', () => {
    fc.assert(fc.property(refList(), (refs) => {
      const one = refs[0];
      expect(compressRefs(one)).toBe(one);
    }));
  });
});

// ── countStatuses ────────────────────────────────────────────────────────────

const STATUSES = [
  'ok', 'short', 'possible', 'missing', 'manual', 'confirmed',
  'manual-short', 'confirmed-short', 'generic', 'generic-short', 'dnp',
];

describe('countStatuses — the tallies reconcile', () => {
  const rows = () => fc.array(
    fc.record({
      effectiveStatus: fc.constantFrom(...STATUSES),
      coveredByAlts: fc.boolean(),
    }),
    { maxLength: 40 },
  );

  it('total is every row and utbp is total minus the DNPs', () => {
    fc.assert(fc.property(rows(), (list) => {
      const c = countStatuses(list);
      expect(c.total).toBe(list.length);
      expect(c.utbp).toBe(c.total - c.dnp);
    }));
  });

  it('is additive: counting two lists separately equals counting them joined', () => {
    // The strong statement about a fold, and the one that catches double
    // counting: countStatuses must be a sum over rows, not something that
    // depends on how the rows arrived.
    fc.assert(fc.property(rows(), rows(), (a, b) => {
      const joined = countStatuses([...a, ...b]);
      const ca = countStatuses(a);
      const cb = countStatuses(b);
      for (const key of Object.keys(joined)) {
        expect(joined[key]).toBe(ca[key] + cb[key]);
      }
    }));
  });

  it('the exclusive buckets never over-count the rows', () => {
    // ok / short / possible / missing / dnp are mutually exclusive, but they do
    // NOT partition: a plain `manual`, `confirmed` or `generic` row lands in
    // none of them, which the first draft of this test wrongly assumed. They
    // get their own counters and their own filter buttons, so the honest
    // invariant is a bound, not an equality.
    fc.assert(fc.property(rows(), (list) => {
      const c = countStatuses(list);
      expect(c.ok + c.short + c.possible + c.missing + c.dnp).toBeLessThanOrEqual(c.total);
      for (const key of ['manual', 'confirmed', 'generic', 'covered']) {
        expect(c[key]).toBeLessThanOrEqual(c.total);
      }
    }));
  });

  it('never reports a negative count', () => {
    fc.assert(fc.property(rows(), (list) => {
      for (const [, n] of Object.entries(countStatuses(list))) {
        expect(n).toBeGreaterThanOrEqual(0);
      }
    }));
  });
});
