// @ts-check
/* threshold-logic.js — the stock-value threshold resolution order, pure.

   A threshold is the dollar value of a category's stock at which the qty cell
   is fully green (`stockValueColor` in js/ui-helpers.js ramps red→green across
   it). Resolution runs in this order — the spec is written down canonically
   under "The resolution spec" in domain/thresholds.py's module docstring, and
   mirrored here:

     1. the user's override   — preferences.json's `thresholds`, the sparse
                                deltas the Preferences sliders write;
     2. the chosen preset     — a bundle from PRESET_BUNDLES, keyed by
                                preferences.json's `threshold_preset`;
     3. the server snapshot   — the ACTIVE snapshot's values this server says
                                (GET /v1/thresholds' `active.values`, or
                                /v1/meta's `active_threshold_profile`); a
                                server that predates the feature sends
                                nothing, and resolution is exactly the old
                                order;
     4. the shipped default   — DEFAULT_THRESHOLDS, generated from
                                data/constants.json — which IS snapshot 0 of
                                the server's snapshot history, not a separate
                                entity;
     5. derivation            — the family tables (FAMILY_THRESHOLDS), unless
                                the category is named in BASE_BY_DECISION;
     6. BASE_THRESHOLD        — a string no table names and no family rule has
                                an opinion about.

   A "Parent > Sub" section inherits its parent at each level, so an override
   on "Passives - Resistors" covers "… > Chip Resistors" without either table
   listing every child. A parent's override outranks a child's default: the
   user spoke about that family, and the shipped table must not overrule
   something they set on purpose.

   Derivation is why a brand-new category (the distributor imports keep
   bringing them) no longer silently gets $50: an "ICs - *" nobody tuned
   derives the IC family, a new passive the passives family, and so on —
   seeded from the operator's own hand edits, mirrored from
   domain/thresholds.py (the tables are GENERATED into
   js/threshold-defaults.generated.js; the matching is mirrored here by hand).
   A category named in BASE_BY_DECISION keeps the base because the hand-tuned
   era left it there on purpose — the legacy fixture pins those names.

   This is the mirror of `resolve()` in domain/thresholds.py. Both are pinned
   against tests/fixtures/thresholds/legacy-defaults.json — the legacy entries
   AND the derived cases — which is what keeps the two sides from drifting
   into two answers again: the frontend once fell back to $50 and
   `dubis low-stock` fell back to 0, so the same part was low for one caller
   and fine for the other.

   Preset semantics copy the cart (domain/cart_plan.py): `presetBundle` is the
   strict CALLER view — an unrecognised id raises — and `storedPresetId` is
   the lenient STORED view — a stale string in preferences falls back to
   "shipped" rather than making the preference unreadable.

   Pure and store-free on purpose: js/store.js's getThreshold() delegates here,
   and a test can exercise the order without mocking the store or the network. */

import {
  BASE_BY_DECISION,
  BASE_THRESHOLD,
  DEFAULT_THRESHOLDS,
  FAMILY_THRESHOLDS,
  PRESET_BUNDLES,
  PRESET_INFO,
  PRESETS,
} from './threshold-defaults.generated.js';

/** Between a section and its sub-category, as the inventory writes it. */
export const SECTION_SEPARATOR = ' > ';

export {
  BASE_BY_DECISION,
  BASE_THRESHOLD,
  DEFAULT_THRESHOLDS,
  FAMILY_THRESHOLDS,
  PRESET_BUNDLES,
  PRESET_INFO,
  PRESETS,
};

/** The preset id meaning "no bundle". @type {string} */
export const PRESET_SHIPPED = PRESETS[0];

/** Between a section and its sub-category, or "" for a top-level one.
 *  @param {string} section
 *  @returns {string} */
export function parentOf(section) {
  const sep = section.indexOf(SECTION_SEPARATOR);
  return sep === -1 ? '' : section.slice(0, sep);
}

/** The family-class threshold for a category no table names, or null for "no
 *  opinion". Mirrors derive() in domain/thresholds.py: a category named in
 *  BASE_BY_DECISION answers null as a DECISION to keep the base (the legacy
 *  fixture pins it), and the longest matching family prefix wins.
 *  @param {string} section
 *  @returns {number|null} */
export function deriveThreshold(section) {
  if (BASE_BY_DECISION.has(section)) return null;
  /** @type {[string, number]|null} */
  let best = null;
  for (const entry of FAMILY_THRESHOLDS) {
    const [prefix, value] = entry;
    if (!section.startsWith(prefix)) continue;
    if (best === null || prefix.length > best[0].length) best = [prefix, value];
  }
  return best === null ? null : best[1];
}

/** The bundle named by a preset id, for a CALLER: unknown ids throw.
 *  The plan_cart(default_preset=...) asymmetry — a caller can fix its
 *  request, so quietly serving a different bundle than the one asked for is
 *  worse than saying no.
 *  @param {string} presetId
 *  @returns {Record<string, number>} */
export function presetBundle(presetId) {
  if (presetId === PRESET_SHIPPED) return {};
  if (Object.prototype.hasOwnProperty.call(PRESET_BUNDLES, presetId)) {
    return PRESET_BUNDLES[presetId];
  }
  throw new Error(
    `unknown threshold preset ${JSON.stringify(presetId)}; expected one of ${PRESETS.join(', ')}`,
  );
}

/** A preset id read back from stored state: unrecognised falls back to
 *  "shipped". The _effective_preset asymmetry — one stale string should
 *  degrade to the shipped order, not make the whole preference unusable.
 *  @param {unknown} raw
 *  @returns {string} */
export function storedPresetId(raw) {
  const id = typeof raw === 'string' ? raw.trim() : '';
  return PRESETS.includes(id) ? id : PRESET_SHIPPED;
}

/**
 * @typedef {object} ResolveOptions
 * @property {Record<string, number>|null} [preset] - the chosen bundle
 *           (presetBundle()/PRESET_BUNDLES[id], or null for none).
 * @property {Record<string, number>|null} [server] - the server's ACTIVE
 *           threshold snapshot values (GET /v1/thresholds `active.values`, or
 *           /v1/meta's `active_threshold_profile`); null/absent for a server
 *           that predates snapshot history.
 * @property {Record<string, number>} [defaults] - shipped defaults; the
 *           generated table unless the caller names another (a client of a
 *           remote server passes that server's /v1/meta answer).
 * @property {number} [base] - base threshold from the same remote answer.
 */

/** The effective threshold for `section`.
 *  @param {string} section
 *  @param {Record<string, number>|null|undefined} overrides - the user's
 *        `thresholds` deltas from preferences; may be missing entirely.
 *  @param {ResolveOptions} [options]
 *  @returns {number} */
export function resolveThreshold(section, overrides, options = {}) {
  const tables = [
    overrides,
    options.preset || null,
    options.server || null,
    options.defaults || DEFAULT_THRESHOLDS,
  ];
  for (const table of tables) {
    if (!table) continue;
    if (section in table) return Number(table[section]);
    const parent = parentOf(section);
    if (parent && parent in table) return Number(table[parent]);
  }
  const derived = deriveThreshold(section);
  if (derived !== null) return derived;
  return options.base === undefined ? BASE_THRESHOLD : Number(options.base);
}
