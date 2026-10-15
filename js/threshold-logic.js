// @ts-check
/* threshold-logic.js — the stock-value threshold resolution order, pure.

   A threshold is the dollar value of a category's stock at which the qty cell
   is fully green (`stockValueColor` in js/ui-helpers.js ramps red→green across
   it). Three things decide it, in this order:

     1. the user's override   — preferences.json's `thresholds`, what the
                                Preferences sliders write;
     2. the shipped default   — DEFAULT_THRESHOLDS, generated from
                                data/constants.json;
     3. BASE_THRESHOLD        — a category with neither.

   A "Parent > Sub" section inherits its parent at each level, so an override
   on "Passives - Resistors" covers "… > Chip Resistors" without either table
   listing every child. A parent's override outranks a child's default: the
   user spoke about that family, and the shipped table must not overrule
   something they set on purpose.

   This is the mirror of `resolve()` in domain/thresholds.py. Both are pinned
   against tests/fixtures/thresholds/legacy-defaults.json, the per-category
   numbers this app was configured with by hand for years — so the two sides
   cannot drift apart, which is exactly how the old arrangement failed: the
   frontend fell back to $50 and `dubis low-stock` fell back to 0, and the same
   part was low for one caller and fine for the other.

   Pure and store-free on purpose: js/store.js's getThreshold() delegates here,
   and a test can exercise the order without mocking the store or the network. */

import { BASE_THRESHOLD, DEFAULT_THRESHOLDS } from './threshold-defaults.generated.js';

/** Between a section and its sub-category, as the inventory writes it. */
export const SECTION_SEPARATOR = ' > ';

export { BASE_THRESHOLD, DEFAULT_THRESHOLDS };

/** The parent half of a compound section, or "" for a top-level one.
 *  @param {string} section
 *  @returns {string} */
export function parentOf(section) {
  const sep = section.indexOf(SECTION_SEPARATOR);
  return sep === -1 ? '' : section.slice(0, sep);
}

/** The effective threshold for `section`.
 *  @param {string} section
 *  @param {Record<string, number>|null|undefined} overrides - the user's
 *        `thresholds` from preferences; may be missing entirely.
 *  @returns {number} */
export function resolveThreshold(section, overrides) {
  const tables = [overrides, DEFAULT_THRESHOLDS];
  for (const table of tables) {
    if (!table) continue;
    if (section in table) return Number(table[section]);
    const parent = parentOf(section);
    if (parent && parent in table) return Number(table[parent]);
  }
  return BASE_THRESHOLD;
}
