// @ts-check
/* arbitraries.mjs — shared fast-check arbitraries for dubIS's core data shapes.
 *
 * The JS half of the repo's property-testing harness; the Python half is
 * tests/python/strategies.py. Read docs/property-testing.md before adding here.
 *
 * ── Why the field table is generated ──────────────────────────────────────────
 * `inventoryRecord()` is built from INVENTORY_FIELDS in
 * ./inventory-schema.generated.mjs, which `scripts/gen-property-schema.py` emits
 * from domain/schema.py — the same list that produces js/inventory-record.d.ts.
 * Nothing in this file names a field. Add one to the schema, regenerate, and the
 * JS generators pick it up; forget to regenerate and verify.sh's
 * `property-schema` guard fails, exactly like the `inventory-types` guard next to
 * it. There is no hand-copied field list to drift.
 *
 * The generated table carries `jsType` rather than the schema's `tsType` because
 * `number` is two different generators: `qty` is a count, `unit_price` is money.
 * A field whose shape neither side has seen fails generation rather than being
 * guessed — see the script's docstring.
 *
 * ── Emptiness is JS emptiness ────────────────────────────────────────────────
 * `blank()` is exactly "" because that is the only value `invPartKey`'s `a || b`
 * chain skips. Whitespace is a *value* there, and `whitespace()` exists to
 * generate it on purpose: the Python properties found a real merge bug living in
 * precisely the gap between "falsy" and "blank after trimming".
 */

import fc from 'fast-check';
import { INVENTORY_FIELDS } from './inventory-schema.generated.mjs';

/** The identity fields `invPartKey` consults, in precedence order (js/part-keys.js). */
export const IDENTITY_FIELDS = /** @type {const} */ (['lcsc', 'mpn', 'digikey', 'pololu', 'mouser']);

/** Every branch `invPartKey` can take, including the one that yields "". */
export const PART_KEY_BRANCHES = /** @type {const} */ ([...IDENTITY_FIELDS, 'unkeyable']);

// ── Primitive arbitraries ────────────────────────────────────────────────────

/** Short printable text, including whitespace-only and empty. */
export const text = () => fc.string({ maxLength: 12, unit: 'grapheme-ascii' });

/** Absent. Exactly "" — the only value the `||` chain treats as "keep looking". */
export const blank = () => fc.constant('');

/** Truthy to `invPartKey`, blank to anything that trims first. Generated on purpose. */
export const whitespace = () => fc.constantFrom(' ', '   ', '\t');

/** An LCSC-shaped value: C or c then digits. */
export const lcscToken = () =>
  fc.tuple(fc.constantFrom('C', 'c'), fc.integer({ min: 0, max: 999999 }))
    .map(([prefix, n]) => `${prefix}${n}`);

/** A part number that is never C-prefixed, so it can only win via the fallback chain. */
export const nonLcscToken = () =>
  fc.string({ minLength: 1, maxLength: 8, unit: 'grapheme-ascii' })
    .filter((s) => s.trim() !== '' && !s.toUpperCase().startsWith('C'));

/** Stock. Non-negative: every dubIS write path floors at zero. */
export const qty = () => fc.integer({ min: 0, max: 1_000_000 });

/** Money. Bounded and rounded so arithmetic in properties stays legible. */
export const money = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true })
    .map((v) => Math.round(v * 10_000) / 10_000);

const ARBITRARY_BY_JS_TYPE = {
  string: text,
  int: qty,
  float: money,
  'string[]': () => fc.array(text().filter((s) => s.trim() !== ''), { maxLength: 3 }),
};

/**
 * The arbitrary for one generated schema field.
 * Throws for a shape this module has not been taught, rather than guessing —
 * the JS mirror of tests/python/strategies.py's UnknownFieldShape.
 * @param {{ key: string, jsType: string }} field
 */
export function fieldArbitrary(field) {
  const make = ARBITRARY_BY_JS_TYPE[field.jsType];
  if (!make) {
    throw new Error(
      `domain/schema.py field "${field.key}" generates as jsType "${field.jsType}", ` +
      'which tests/js/property/arbitraries.mjs has no arbitrary for. Add one to ' +
      'ARBITRARY_BY_JS_TYPE (and its Python twin in tests/python/strategies.py) ' +
      'rather than letting the property tests generate an invalid record.',
    );
  }
  return make();
}

// ── Part identity ────────────────────────────────────────────────────────────

/**
 * The five identity fields, aimed at one branch of the `invPartKey` rule.
 *
 * Returns `{ fields, branch }`: the record fragment plus the branch it was built
 * for, so a property can assert the rule *and* say which branch a counterexample
 * came from.
 *
 * @param {(typeof PART_KEY_BRANCHES)[number]} [branch]
 */
export function identityFields(branch) {
  if (branch === undefined) {
    return fc.constantFrom(...PART_KEY_BRANCHES).chain((b) => identityFields(b));
  }
  const anything = fc.oneof(blank(), whitespace(), nonLcscToken(), lcscToken());
  const winnable = fc.oneof(whitespace(), nonLcscToken(), lcscToken());

  if (branch === 'unkeyable') {
    return fc.constant({
      branch,
      fields: Object.fromEntries(IDENTITY_FIELDS.map((f) => [f, ''])),
    });
  }
  if (branch === 'lcsc') {
    return fc.record({
      lcsc: lcscToken(),
      mpn: anything, digikey: anything, pololu: anything, mouser: anything,
    }).map((fields) => ({ branch, fields }));
  }
  const winner = IDENTITY_FIELDS.indexOf(branch);
  const spec = {
    // lcsc must not win: blank, whitespace, or present but not C-prefixed.
    lcsc: fc.oneof(blank(), whitespace(), nonLcscToken()),
  };
  IDENTITY_FIELDS.slice(1).forEach((name, i) => {
    const pos = i + 1;
    spec[name] = pos < winner ? blank() : pos === winner ? winnable : anything;
  });
  return fc.record(spec).map((fields) => ({ branch, fields }));
}

// ── Inventory records ────────────────────────────────────────────────────────

const NON_IDENTITY_FIELDS = INVENTORY_FIELDS.filter(
  (f) => !(/** @type {readonly string[]} */ (IDENTITY_FIELDS)).includes(f.key),
);

/**
 * One schema-valid inventory record: every INVENTORY_FIELDS key, nothing else.
 *
 * @param {{ keyable?: boolean, overrides?: Record<string, any> }} [opts]
 *   `keyable` (default true) guarantees `invPartKey` returns a non-empty key.
 *   `overrides` replaces a field's arbitrary; an unknown key throws, so the
 *   generator can never quietly produce a record the schema does not define.
 */
export function inventoryRecord(opts = {}) {
  const { keyable = true, overrides = {} } = opts;
  const known = new Set(INVENTORY_FIELDS.map((f) => f.key));
  for (const key of Object.keys(overrides)) {
    if (!known.has(key)) {
      throw new Error(
        `inventoryRecord() override "${key}" is not in domain/schema.py's ` +
        'INVENTORY_FIELDS — add the field to the schema first (see the ' +
        '"Inventory record field change" trap in CLAUDE.md).',
      );
    }
    if ((/** @type {readonly string[]} */ (IDENTITY_FIELDS)).includes(key)) {
      throw new Error(
        `inventoryRecord() cannot override identity field "${key}" — which one ` +
        'wins the part key is the whole point of identityFields(branch); use that.',
      );
    }
  }
  const branches = keyable ? IDENTITY_FIELDS : PART_KEY_BRANCHES;
  const spec = {};
  for (const field of NON_IDENTITY_FIELDS) {
    spec[field.key] = overrides[field.key] ?? fieldArbitrary(field);
  }
  return fc.tuple(
    fc.constantFrom(...branches).chain((b) => identityFields(b)),
    fc.record(spec),
  ).map(([ident, rest]) => ({ ...rest, ...ident.fields }));
}

/** Whether a value has exactly the schema's keys with the schema's types. */
export function isSchemaValid(record) {
  const keys = new Set(Object.keys(record));
  if (keys.size !== INVENTORY_FIELDS.length) return false;
  for (const field of INVENTORY_FIELDS) {
    if (!keys.has(field.key)) return false;
    const value = record[field.key];
    switch (field.jsType) {
      case 'string': if (typeof value !== 'string') return false; break;
      case 'int': if (!Number.isInteger(value)) return false; break;
      case 'float': if (typeof value !== 'number' || !Number.isFinite(value)) return false; break;
      case 'string[]':
        if (!Array.isArray(value) || value.some((v) => typeof v !== 'string')) return false;
        break;
      default: return false;
    }
  }
  return true;
}

// ── Sources ──────────────────────────────────────────────────────────────────

/** A roster id: lowercase, hyphenated, never empty. */
export const sourceId = () =>
  fc.stringMatching(/^[a-z0-9-]{1,8}$/);

/** One `{ id, name }` source, as js/server-tabs-logic.js and /v1/sources use. */
export const sourceInfo = () => fc.record({ id: sourceId(), name: text() });

/** A roster of sources with distinct ids — duplicates are an error elsewhere. */
export const roster = (opts = {}) =>
  fc.uniqueArray(sourceInfo(), {
    minLength: opts.minLength ?? 1,
    maxLength: opts.maxLength ?? 4,
    selector: (s) => s.id,
  });

/**
 * `[{ source, records }]` with genuinely overlapping key sets.
 *
 * Built from a shared pool of identity fragments and dealt out, for the reason
 * spelled out in tests/python/strategies.py: independently drawn part numbers
 * essentially never collide, and a merge test where nothing merges proves
 * nothing.
 */
export function federatedInputs(opts = {}) {
  const { minSources = 1, maxSources = 3, maxParts = 5 } = opts;
  return fc.tuple(
    roster({ minLength: minSources, maxLength: maxSources }),
    fc.array(
      fc.constantFrom(...IDENTITY_FIELDS).chain((b) => identityFields(b)),
      { minLength: 1, maxLength: maxParts },
    ),
  ).chain(([sources, pool]) =>
    fc.tuple(
      ...sources.map(() => fc.tuple(
        ...pool.map(() => fc.tuple(fc.boolean(), inventoryRecord())),
      )),
    ).map((perSource) => sources.map((source, i) => ({
      source,
      records: perSource[i]
        .map(([keep, record], j) => (keep ? { ...record, ...pool[j].fields } : null))
        .filter(Boolean),
    }))),
  );
}
