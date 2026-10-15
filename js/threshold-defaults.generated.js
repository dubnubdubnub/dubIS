/* GENERATED FILE - do not edit.
 *
 * Source: data/constants.json's DEFAULT_THRESHOLDS and domain/thresholds.py's
 *         BASE_THRESHOLD, FAMILY_THRESHOLDS, BASE_BY_DECISION, PRESETS,
 *         PRESET_BUNDLES and PRESET_INFO.
 * Write:  python scripts/gen-threshold-defaults.py
 * Guard:  python scripts/gen-threshold-defaults.py --check   (verify.sh + CI)
 *
 * Generated because js/constants.js fetches data/constants.json at top level,
 * which no module a vitest file imports can depend on. The resolution order
 * this feeds lives in js/threshold-logic.js, mirrored in domain/thresholds.py,
 * and both are pinned against tests/fixtures/thresholds/legacy-defaults.json.
 *
 * The derivation tables (family prefixes, base-by-decision) are generated from
 * the Python originals for the same reason two hand-maintained copies of one
 * table is one copy too many: the drifting one is the one nobody is looking
 * at. The *matching* (prefix match, decision-first) is mirrored by hand in
 * both languages and pinned by the shared fixture's derived cases.
 */

// @ts-check

/** The threshold of last resort: a category no table names and no family rule
 *  has an opinion about. */
export const BASE_THRESHOLD = 50;

/** Shipped per-category defaults - only the categories that differ from
 *  BASE_THRESHOLD. A "Parent > Sub" section inherits its parent's entry.
 *  @type {Readonly<Record<string, number>>} */
export const DEFAULT_THRESHOLDS = Object.freeze({
  "Connectors": 20,
  "Crystals & Oscillators": 10,
  "Diodes": 10,
  "Passives - Capacitors": 20,
  "Passives - Resistors": 4
});

/** Family-class derivation: prefix -> threshold, longest prefix wins, applied
 *  only when no override, preset or default names the category. Mirrors
 *  domain/thresholds.py's FAMILY_THRESHOLDS.
 *  @type {ReadonlyArray<Readonly<[string, number]>>} */
export const FAMILY_THRESHOLDS = Object.freeze([
  ["ICs - ", 100],
  ["Passives - ", 20],
  ["Discrete Semiconductors", 20],
  ["Connectors", 20]
]);

/** Categories the hand-tuned era left at the base ON PURPOSE - derivation must
 *  not re-decide them, and the legacy fixture pins them. Mirrors
 *  domain/thresholds.py's BASE_BY_DECISION. @type {ReadonlySet<string>} */
export const BASE_BY_DECISION = Object.freeze(new Set([
  "Development Boards, Kits, Programmers",
  "Discrete Semiconductors",
  "Discrete Semiconductors > MOSFETs",
  "ICs - Amplifiers",
  "ICs - ESD Protection",
  "ICs - Interface",
  "ICs - Microcontrollers",
  "ICs - Motor Drivers",
  "ICs - Power / Voltage Regulators",
  "ICs - Power / Voltage Regulators > LDOs",
  "ICs - Power / Voltage Regulators > Load Switches",
  "ICs - Power / Voltage Regulators > Switchers",
  "ICs - Sensors",
  "ICs - USB",
  "ICs - Voltage References",
  "LEDs",
  "Mechanical & Hardware",
  "Other",
  "Passives - Inductors",
  "Switches"
]));

/** Preset ids, in picker order. The first is the shipped sentinel (no bundle).
 *  Mirrors domain/thresholds.py's PRESETS. @type {ReadonlyArray<string>} */
export const PRESETS = Object.freeze(["shipped", "ic-heavy", "tight"]);

/** Sparse preset bundles - only the categories a bundle moves, in the same
 *  shape as DEFAULT_THRESHOLDS (" > " parent inheritance included).
 *  Mirrors domain/thresholds.py's PRESET_BUNDLES.
 *  @type {Readonly<Record<string, Readonly<Record<string, number>>>>} */
export const PRESET_BUNDLES = Object.freeze({
  "ic-heavy": Object.freeze({
    "ICs - Amplifiers": 100,
    "ICs - ESD Protection": 100,
    "ICs - Microcontrollers": 200,
    "ICs - Motor Drivers": 100,
    "ICs - Power / Voltage Regulators > LDOs": 30,
    "ICs - Power / Voltage Regulators > Load Switches": 100,
    "ICs - Power / Voltage Regulators > Switchers": 100,
    "ICs - Sensors": 200,
    "ICs - USB": 100
  }),
  "tight": Object.freeze({
    "Connectors": 40,
    "Crystals & Oscillators": 20,
    "Diodes": 20,
    "Discrete Semiconductors": 100,
    "LEDs": 100,
    "Mechanical & Hardware": 100,
    "Passives - Capacitors": 40,
    "Passives - Inductors": 100,
    "Passives - Resistors": 8,
    "Switches": 100
  })
});

/** Display name + description per preset id, for the Preferences picker.
 *  Mirrors domain/thresholds.py's PRESET_INFO.
 *  @typedef {{ name: string, description: string }} PresetInfo */
/** @type {Readonly<Record<string, PresetInfo>>} */
export const PRESET_INFO = Object.freeze({
  "shipped": Object.freeze({ name: "Server defaults", description: "No bundle \u2014 the shipped table, derived families, and the base." }),
  "ic-heavy": Object.freeze({ name: "Expensive ICs", description: "IC classes carry their real price: MCUs and sensors at $200, the rest of the IC families at $100, LDOs pulled below the base to $30." }),
  "tight": Object.freeze({ name: "Flag sooner", description: "Twice the shipped number for every cheap category \u2014 a shelf stays amber until it holds double the stock." })
});
