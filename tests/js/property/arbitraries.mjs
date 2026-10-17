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

// ── BOM match results ────────────────────────────────────────────────────────

/** Every matchType js/matching.js's matchBOM can assign, plus the absent forms. */
export const MATCH_TYPES = /** @type {const} */ ([
  'none', null, 'lcsc', 'mpn', 'fuzzy', 'value', 'manual', 'confirmed', 'generic',
]);

/**
 * Footprints aimed at both sides of js/bom/board-features.js: real board
 * features, real parts whose footprint only looks like one, and plain parts.
 */
const FOOTPRINTS = [
  '', 'Resistor_SMD:R_0402_1005Metric', 'Package_TO_SOT_SMD:SOT-23-5',
  'Fiducial:Fiducial_1mm_Mask2mm', 'MountingHole:MountingHole_2.2mm_M2_Pad',
  'TestPoint:TestPoint_Pad_D1.0mm', 'TestPoint:TestPoint_Keystone_5000-5004_Miniature',
  'Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical',
];
const VALUES = ['', '10k', '100nF', 'Fiducial', 'MountingHole', 'TestPoint', 'TC2030', 'LTC2050'];

/**
 * One aggregated BOM line, the shape csv-parser.js's aggregateBomRows emits.
 * `mpn` is often the value itself — the Value-as-MPN fallback a KiCad BOM gets.
 */
export const bomLine = () => fc.record({
  qty: fc.integer({ min: 0, max: 500 }),
  dnp: fc.boolean(),
  excluded: fc.boolean(),
  lcsc: fc.oneof(blank(), lcscToken().map((s) => s.toUpperCase())),
  value: fc.constantFrom(...VALUES),
  footprint: fc.constantFrom(...FOOTPRINTS),
  realMpn: fc.oneof(blank(), nonLcscToken()),
  refs: fc.constant('X1'),
  desc: text(),
}).map(({ realMpn, ...line }) => ({ ...line, mpn: realMpn || line.value }));

/** One matchBOM result: a line, maybe a matched part, a matchType, alternates. */
export const bomMatchResult = () => fc.record({
  bom: bomLine(),
  inv: fc.option(fc.record({
    qty: fc.integer({ min: 0, max: 1000 }),
    lcsc: fc.oneof(blank(), lcscToken().map((s) => s.toUpperCase())),
    mpn: fc.oneof(blank(), nonLcscToken()),
    unit_price: money(),
  }), { nil: null }),
  matchType: fc.constantFrom(...MATCH_TYPES),
  alts: fc.array(fc.record({ qty: fc.integer({ min: 0, max: 100 }) }), { maxLength: 3 }),
});

// ── BOM matching (js/matching.js) ────────────────────────────────────────────
//
// A BOM line and inventory parts generated from a GROUND TRUTH — type, value,
// package, voltage — that is then *expressed* (or not) through the places a
// real row carries it: the Value column, the Description, the Footprint, or the
// distributor spec (`bom.spec`). Each generated object keeps its truth and a
// record of which facts it expressed, so a property can judge a match against
// what is actually true without re-parsing anything — the anti-tautology rule
// in docs/property-testing.md.
//
// MPNs are dealt from shared real-family stems with a tiny digit alphabet, so
// BOM lines and inventory parts collide on long prefixes the way UniOhm
// 0402WGF####TCE neighbours do. Independently drawn MPNs would never reach
// steps 3 and 4 of the matcher at all.

export const PASSIVE_TYPES = /** @type {const} */ (['C', 'R', 'L']);
const UNIT_SYMBOL = { C: 'F', R: 'Ω', L: 'H' };
const ATTR_NAME = { C: 'Capacitance', R: 'Resistance', L: 'Inductance' };
const TYPE_WORD = { C: 'Ceramic Capacitor', R: 'Thick Film Resistor', L: 'Fixed Inductor' };
const SECTION = {
  C: 'Passives - Capacitors > MLCC',
  R: 'Passives - Resistors > Chip Resistors',
  L: 'Passives - Inductors',
};
const KICAD_LIB = { C: 'Capacitor_SMD:C_', R: 'Resistor_SMD:R_', L: 'Inductor_SMD:L_' };
const MANTISSAS = [1, 2.2, 4.7, 10, 47, 100];
const PREFIXES = {
  C: [['p', 1e-12], ['n', 1e-9], ['u', 1e-6]],
  R: [['', 1], ['k', 1e3], ['M', 1e6]],
  L: [['n', 1e-9], ['u', 1e-6]],
};
export const CHIP_PACKAGES = ['0402', '0603', '0805'];
export const RATED_VOLTAGES = [6.3, 10, 16, 25, 50];
const MPN_STEMS = ['0402WGF', 'CL05B10', 'GRM155R71H', 'RC0402FR-07'];

/** One part's ground truth. `voltage` is null except for capacitors. */
export function passiveTruth(type) {
  const t = type ? fc.constant(type) : fc.constantFrom(...PASSIVE_TYPES);
  return t.chain((ty) => fc.record({
    type: fc.constant(ty),
    mantissa: fc.constantFrom(...MANTISSAS),
    prefix: fc.constantFrom(...PREFIXES[ty]),
    package: fc.constantFrom(...CHIP_PACKAGES),
    voltage: ty === 'C' ? fc.constantFrom(...RATED_VOLTAGES) : fc.constant(null),
  })).map(({ prefix, ...rest }) => ({
    ...rest,
    symbol: prefix[0],
    value: rest.mantissa * prefix[1],
  }));
}

/** "4.7kΩ", "100nF", "10uH" — how a value reads in text. */
export function valueText(truth) {
  return `${truth.mantissa}${truth.symbol}${UNIT_SYMBOL[truth.type]}`;
}

/** An MPN from the shared stems; never parses as a value or a package. */
export const pooledMpn = () => fc.tuple(
  fc.constantFrom(...MPN_STEMS),
  fc.stringMatching(/^[01]{3,4}$/),
  fc.constantFrom('TCE', 'KB5NNNC', 'KE14D', 'XL'),
).map(([stem, digits, tail]) => stem + digits + tail);

/** A truth that differs from `t` in exactly one fact, or matches it outright. */
function neighbourOf(t) {
  return fc.oneof(
    fc.constant(t),
    passiveTruth(t.type).map((o) => ({ ...t, mantissa: o.mantissa, symbol: o.symbol, value: o.value })),
    fc.constantFrom(...CHIP_PACKAGES).map((p) => ({ ...t, package: p })),
    t.type === 'C' ? fc.constantFrom(...RATED_VOLTAGES).map((v) => ({ ...t, voltage: v })) : fc.constant(t),
    passiveTruth(),
  );
}

/**
 * One inventory part around a truth. The record comes from inventoryRecord()
 * so it stays schema-valid; the fields matching reads are then set explicitly,
 * identity included — matching needs MPN collisions that identityFields()
 * deliberately cannot produce. `lcsc` is "C1…" and unique per part; BOM lines
 * never carry one, so step 1 never fires and every match is earned by the
 * steps under test.
 */
function inventoryPartAround(truth, index) {
  return fc.record({
    base: inventoryRecord(),
    mpn: pooledMpn(),
    valueShown: fc.boolean(),
    packageShown: fc.boolean(),
    voltageShown: fc.boolean(),
    sectioned: fc.boolean(),
    qty: fc.integer({ min: 0, max: 5000 }),
  }).map((g) => {
    const desc = [
      g.valueShown ? valueText(truth) : '',
      truth.voltage !== null && g.voltageShown ? `±10% ${truth.voltage}V` : '',
      TYPE_WORD[truth.type],
    ].filter(Boolean).join(' ');
    return {
      record: {
        ...g.base,
        lcsc: 'C1' + String(index).padStart(5, '0'),
        mpn: g.mpn,
        description: desc,
        package: g.packageShown ? truth.package : '',
        section: g.sectioned ? SECTION[truth.type] : 'Other',
        qty: g.qty,
      },
      truth,
      shown: {
        value: g.valueShown,
        package: g.packageShown,
        voltage: truth.voltage !== null && g.voltageShown,
      },
    };
  });
}

/**
 * A BOM line (the aggregated-entry shape matchBOM reads) expressing `truth`
 * through the chosen channels, plus `shown` — which facts it actually carries.
 * @param {any} truth
 * @param {{ valueVia: 'column'|'desc'|'spec'|'none', voltageVia: 'desc'|'spec'|'none',
 *   packageVia: 'footprint'|'spec'|'none', refs: boolean, stdLib: boolean, mpn: string }} how
 */
export function buildBomLine(truth, how) {
  const descParts = [];
  if (how.valueVia === 'desc') descParts.push(valueText(truth));
  if (how.voltageVia === 'desc' && truth.voltage !== null) descParts.push(`${truth.voltage}V`);
  const attributes = [];
  if (how.valueVia === 'spec') attributes.push({ name: ATTR_NAME[truth.type], value: valueText(truth) });
  if (how.voltageVia === 'spec' && truth.voltage !== null) {
    attributes.push({ name: 'Voltage Rating', value: `${truth.voltage}V` });
  }
  const specPackage = how.packageVia === 'spec' ? truth.package : '';
  // KiCad shape: no MPN column, so the Value cell IS the MPN unless it holds the value.
  const value = how.valueVia === 'column' ? valueText(truth) : how.mpn;
  const bom = {
    lcsc: '',
    mpn: value,
    value,
    desc: descParts.join(' '),
    // A KiCad standard-library footprint names the passive type even when it
    // names no package; a private library ("MyLib:") names neither.
    footprint: (how.stdLib ? KICAD_LIB[truth.type] : 'MyLib:')
      + (how.packageVia === 'footprint' ? `${truth.package}_1005Metric` : 'Custom_Pad'),
    refs: how.refs ? `${truth.type}1` : '',
    qty: 1,
    dnp: false,
  };
  if (attributes.length || specPackage) {
    bom.spec = {
      source: 'lcsc', code: 'C999', mpn: '', manufacturer: '', description: '',
      package: specPackage, category: '', subcategory: '', attributes,
    };
  }
  return {
    bom,
    truth,
    // Whether anything on the line itself says it is a passive at all — a
    // designator, a standard-library footprint, or a value written with a unit.
    typed: how.refs || how.stdLib || how.valueVia !== 'none',
    shown: {
      value: how.valueVia !== 'none',
      package: how.packageVia !== 'none',
      voltage: truth.voltage !== null && how.voltageVia !== 'none',
    },
  };
}

export const bomLineHow = () => fc.record({
  valueVia: fc.constantFrom('column', 'desc', 'spec', 'none'),
  voltageVia: fc.constantFrom('desc', 'spec', 'none'),
  packageVia: fc.constantFrom('footprint', 'spec', 'none'),
  refs: fc.boolean(),
  stdLib: fc.boolean(),
  mpn: pooledMpn(),
});

/**
 * A whole matching scenario: one BOM line and an inventory of its neighbours.
 * With `identity`, the line is one of the inventory parts by exact MPN — its
 * truth becomes that part's truth, since it IS that part.
 */
export function bomMatchScenario() {
  return passiveTruth().chain((truth) => fc.tuple(
    bomLineHow(),
    fc.array(neighbourOf(truth), { minLength: 1, maxLength: 7 }),
    fc.boolean(),
  ).chain(([how, truths, identity]) => fc.tuple(
    ...truths.map((t, i) => inventoryPartAround(t, i)),
  ).map((drawn) => {
    // One part per MPN: two different parts sharing an MPN is an inventory no
    // matcher can disambiguate (invByMPN keeps one of them), not a case to judge.
    const seen = new Set();
    const parts = drawn.filter((p) => !seen.has(p.record.mpn) && seen.add(p.record.mpn));
    // A line whose drawn MPN happens to equal a stocked part's IS that part,
    // so it takes that part's truth — same as a deliberate identity line.
    const collided = how.valueVia !== 'column'
      ? parts.find((p) => p.record.mpn.toUpperCase() === how.mpn.toUpperCase()) : undefined;
    const target = identity ? parts[0] : collided;
    if (target) {
      const line = buildBomLine(target.truth, { ...how, valueVia: how.valueVia === 'column' ? 'none' : how.valueVia, mpn: target.record.mpn });
      return { line, parts };
    }
    return { line: buildBomLine(truth, how), parts };
  })));
}
