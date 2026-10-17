// @ts-check
/* bom-status.property.test.mjs — CI guard for a whole CLASS of bug: a BOM
 * status that one consumer knows about and another does not.
 *
 * The reported instance: fiducials, mounting holes and Tag-Connect footprints
 * came through as `missing` parts and inflated UTBP. Fixing it meant a new
 * status, and before js/bom-status.js existed a new status was a shotgun edit
 * — matchBOM, computeRows, countStatuses, buildLinkableKeys,
 * buildMissingCartEntries, prepareConsumption, the sort weights, the filter
 * buttons, the CSS classes, even an E2E helper each re-encoded "which statuses
 * are short / placed / bought / consumed" with their own OR-chain, and
 * forgetting one was silent: a status missing from the cart's chain is simply
 * never bought.
 *
 * Every consumer now reads the registry. These tests guard the registry
 * itself, in the house style of tests/js/panel-reopen-noop-refresh.test.js
 * (every REOPEN_TRIGGERS entry must be classified):
 *   1. every entry declares every property, with the right type;
 *   2. computeRows can never emit a status the registry does not declare
 *      (fast-check over generated match results);
 *   3. every declared status has a witness — an input that produces it — so
 *      a dead entry, or a status computeRows stopped emitting, fails too;
 *   4. every row/qty class it names exists in css/tables.css.
 * A new status fails 1 or 3 until it is fully declared. */

import { describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

vi.mock('../../../js/ui-helpers.js', () => ({ escHtml: (s) => s || '' }));

import {
  BOM_STATUSES, BOM_STATUS_IDS, BOM_BUCKETS, REQUIRED_STATUS_PROPS, statusSpec, statusHas,
} from '../../../js/bom-status.js';
import {
  computeRows, buildLinkableKeys, buildMissingCartEntries, prepareConsumption,
} from '../../../js/bom/bom-logic.js';
import { countStatuses, STATUS_ICONS, STATUS_ROW_CLASS } from '../../../js/part-keys.js';
import { bomMatchResult } from './arbitraries.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TABLES_CSS = readFileSync(join(__dirname, '..', '..', '..', 'css', 'tables.css'), 'utf8');
const BUCKET_IDS = BOM_BUCKETS.map((b) => b.id);

// ── 1. Every entry is fully declared ─────────────────────────────────────────

describe('BOM_STATUSES — every entry declares every property', () => {
  it.each(BOM_STATUS_IDS)('%s', (id) => {
    const spec = BOM_STATUSES[id];
    for (const [prop, type] of Object.entries(REQUIRED_STATUS_PROPS)) {
      const value = spec[prop];
      if (type === 'array') expect(Array.isArray(value), `${id}.${prop} must be an array`).toBe(true);
      else expect(typeof value, `${id}.${prop} must be a ${type}`).toBe(type);
    }
    expect(spec.buckets.length, `${id} must count toward at least one bucket`).toBeGreaterThan(0);
    for (const b of spec.buckets) expect(BUCKET_IDS, `${id} names unknown bucket "${b}"`).toContain(b);
    // The sentences the flags encode, so a typo cannot make a row both
    // "nothing to buy" and "buy it".
    if (!spec.placed) {
      expect(spec.needsPurchase, `${id}: an unplaced row is never bought`).toBe(false);
      expect(spec.consumable, `${id}: an unplaced row is never consumed`).toBe(false);
      expect(spec.linkable, `${id}: an unplaced row is never a link target`).toBe(false);
    }
  });

  it('declares no property the guard does not know about', () => {
    // A new property is a new question every consumer may need to answer: add
    // it to REQUIRED_STATUS_PROPS so every entry is forced to answer it.
    for (const id of BOM_STATUS_IDS) {
      expect(Object.keys(BOM_STATUSES[id]).sort()).toEqual(Object.keys(REQUIRED_STATUS_PROPS).sort());
    }
  });

  it('every bucket is fed by some status (no dead filter button)', () => {
    for (const b of BUCKET_IDS) {
      expect(BOM_STATUS_IDS.some((id) => BOM_STATUSES[id].buckets.includes(b)), `bucket ${b}`).toBe(true);
    }
  });

  it('statusSpec answers only for declared statuses', () => {
    expect(statusSpec('constructor')).toBeNull();
    expect(statusSpec('toString')).toBeNull();
    expect(statusSpec(undefined)).toBeNull();
    expect(statusHas('something-new', 'placed')).toBe(false);
  });

  it('the derived lookup tables cover exactly the registry', () => {
    expect(Object.keys(STATUS_ICONS).sort()).toEqual([...BOM_STATUS_IDS].sort());
    expect(Object.keys(STATUS_ROW_CLASS).sort()).toEqual([...BOM_STATUS_IDS].sort());
  });
});

// ── 2. computeRows never emits an undeclared status ─────────────────────────

describe('computeRows — every status it emits is declared', () => {
  const inputs = () => fc.tuple(
    fc.array(bomMatchResult(), { maxLength: 20 }),
    fc.integer({ min: 1, max: 100 }),
  );

  it('for any match results and multiplier', () => {
    fc.assert(fc.property(inputs(), ([results, mult]) => {
      for (const row of computeRows(results, mult, {})) {
        expect(statusSpec(row.effectiveStatus), `undeclared status "${row.effectiveStatus}"`).not.toBeNull();
      }
    }));
  });

  it('the consumers agree with the registry flags, row by row', () => {
    // The flags are the contract; this checks the consumers actually read
    // them, not a private copy of the old OR-chains.
    fc.assert(fc.property(inputs(), ([results, mult]) => {
      const rows = computeRows(results, mult, {});
      const cart = buildMissingCartEntries(rows);
      const wantCart = rows.filter((r) => statusHas(r.effectiveStatus, 'needsPurchase')
        && (!r.inv || r.inv.lcsc || r.inv.mpn)).length;
      expect(cart.length).toBe(wantCart);

      const consumed = prepareConsumption(rows).matches.length;
      const wantConsumed = rows.filter((r) => statusHas(r.effectiveStatus, 'consumable')
        && r.inv && (r.inv.lcsc || r.inv.mpn)).length;
      expect(consumed).toBe(wantConsumed);

      const c = countStatuses(rows);
      expect(c.utbp).toBe(rows.filter((r) => statusHas(r.effectiveStatus, 'placed')).length);
      for (const key of buildLinkableKeys(rows, true)) {
        expect(rows.some((r) => statusHas(r.effectiveStatus, 'linkable')
          && (r.bom.lcsc || r.bom.mpn || '').toUpperCase() + (r.bom.dnp ? ':DNP' : '') === key)).toBe(true);
      }
    }));
  });

  it('a board-only row is never placed, bought, linked or consumed, and has a reason', () => {
    fc.assert(fc.property(inputs(), ([results, mult]) => {
      for (const row of computeRows(results, mult, {})) {
        if (row.effectiveStatus !== 'board-only') continue;
        expect(typeof row.boardReason).toBe('string');
        expect(row.inv).toBeNull();
      }
    }));
  });
});

// ── 3. Every declared status is reachable ───────────────────────────────────

/* One input per status that computeRows turns into it. A status with no
   witness here is either dead (nothing produces it, delete it) or new (add
   its witness) — either way the test names it. */
const line = (extra = {}) => ({ qty: 5, dnp: false, excluded: false, lcsc: 'C1', mpn: 'P', value: '', footprint: '', ...extra });
const part = (qty) => ({ qty, lcsc: 'C1', mpn: '' });
const WITNESSES = {
  ok: { bom: line(), inv: part(10), matchType: 'lcsc' },
  short: { bom: line(), inv: part(1), matchType: 'lcsc' },
  possible: { bom: line(), inv: part(10), matchType: 'value' },
  missing: { bom: line(), inv: null, matchType: 'none' },
  manual: { bom: line(), inv: part(10), matchType: 'manual' },
  'manual-short': { bom: line(), inv: part(1), matchType: 'manual' },
  confirmed: { bom: line(), inv: part(10), matchType: 'confirmed' },
  'confirmed-short': { bom: line(), inv: part(1), matchType: 'confirmed' },
  generic: { bom: line(), inv: part(10), matchType: 'generic' },
  'generic-short': { bom: line(), inv: part(1), matchType: 'generic' },
  dnp: { bom: line({ dnp: true }), inv: part(10), matchType: 'lcsc' },
  'board-only': { bom: line({ lcsc: '', mpn: 'Fiducial', value: 'Fiducial', footprint: 'Fiducial:Fiducial_1mm' }), inv: null, matchType: 'none' },
};

describe('every declared status has a witness computeRows turns into it', () => {
  it.each(BOM_STATUS_IDS)('%s', (id) => {
    const witness = WITNESSES[id];
    expect(witness, `add a WITNESSES entry for "${id}"`).toBeDefined();
    const [row] = computeRows([{ alts: [], ...witness }], 1, {});
    expect(row.effectiveStatus).toBe(id);
  });

  it('has no witness for a status the registry does not declare', () => {
    expect(Object.keys(WITNESSES).sort()).toEqual([...BOM_STATUS_IDS].sort());
  });
});

// ── 4. Every class the registry names is styled ─────────────────────────────

describe('every row/qty class the registry names exists in css/tables.css', () => {
  it.each(BOM_STATUS_IDS)('%s', (id) => {
    const { rowClass, qtyClass } = BOM_STATUSES[id];
    expect(TABLES_CSS, `tr.${rowClass}`).toMatch(new RegExp(`tr\\.${rowClass}\\b`));
    expect(TABLES_CSS, `.${qtyClass}`).toMatch(new RegExp(`\\.${qtyClass}\\s*\\{`));
  });
});
