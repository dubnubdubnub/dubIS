/* GENERATED FILE - do not edit.
 *
 * Source: data/constants.json's DEFAULT_THRESHOLDS and domain/thresholds.py's
 *         BASE_THRESHOLD.
 * Write:  python scripts/gen-threshold-defaults.py
 * Guard:  python scripts/gen-threshold-defaults.py --check   (verify.sh + CI)
 *
 * Generated because js/constants.js fetches data/constants.json at top level,
 * which no module a vitest file imports can depend on. The resolution order
 * this feeds lives in js/threshold-logic.js, mirrored in domain/thresholds.py,
 * and both are pinned against tests/fixtures/thresholds/legacy-defaults.json.
 */

// @ts-check

/** The threshold of last resort: a category with no override and no default. */
export const BASE_THRESHOLD = 50;

/** Shipped per-category defaults - only the categories that differ from
 *  BASE_THRESHOLD. A "Parent > Sub" section inherits its parent's entry.
 *  @type {Record<string, number>} */
export const DEFAULT_THRESHOLDS = Object.freeze({
  "Connectors": 20,
  "Crystals & Oscillators": 10,
  "Diodes": 10,
  "Passives - Capacitors": 20,
  "Passives - Resistors": 4
});
