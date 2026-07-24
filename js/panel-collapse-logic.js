// @ts-check
/* js/panel-collapse-logic.js — pure collapse-state logic.

   The region table, the toggle reducer, and the trigger→reopen mapping. No DOM
   and no event-bus import (triggers arrive as plain strings), so the whole table
   is unit-testable and vitest never has to load the app's module graph. */

/** @typedef {Record<string, boolean>} CollapseState */

/**
 * The three independently collapsible regions.
 * `console` nests inside the `import` panel — that containment is why a log
 * warning has to reopen both (see REOPEN_TRIGGERS).
 */
export const REGIONS = [
  { id: 'import',  panelId: 'panel-import', label: 'Purchase Import' },
  { id: 'bom',     panelId: 'panel-bom',    label: 'BOM Comparison' },
  { id: 'console', panelId: 'console-log',  label: 'Log' },
];

export const REGION_IDS = REGIONS.map(r => r.id);

/**
 * Which regions a trigger must force open.
 *
 * A collapsed panel that silently changes is a trap: the user cannot see the PO
 * they just imported. So anything that mutates a region's content reopens it.
 *
 * Two deliberate asymmetries:
 *  - `LOG_INFO` is absent. Routine logging fires constantly; forcing the panel
 *    open on it would make collapsing the console useless. It flags the toggle
 *    with an activity dot instead.
 *  - `LOG_WARN`/`LOG_ERROR` reopen `import` as well as `console`, because
 *    #console-log lives inside #panel-import and reopening it alone would be
 *    invisible.
 * @type {Record<string, string[]>}
 */
export const REOPEN_TRIGGERS = {
  PO_CHANGED: ['import'],
  IMPORT_MAPPER_OPENED: ['import'],
  LABEL_MODE: ['import'],
  CART_ADD_MODE: ['import'],
  BOM_LOADED: ['bom'],
  BOM_CLEARED: ['bom'],
  // A BOM becomes dirty through exactly these two events; setBomDirty() itself is
  // a documented non-emitting setter and must stay that way.
  CONFIRMED_CHANGED: ['bom'],
  LINKS_CHANGED: ['bom'],
  LINKING_MODE: ['bom'],
  LOG_WARN: ['import', 'console'],
  LOG_ERROR: ['import', 'console'],
};

/** @param {string} id */
function assertRegion(id) {
  if (!REGION_IDS.includes(id)) {
    throw new RangeError(`Unknown collapse region: ${String(id)}`);
  }
}

/**
 * Coerce persisted (or absent) state into a full, boolean-valued record.
 * User data, so it repairs rather than throws.
 * @param {unknown} v
 * @returns {CollapseState}
 */
export function normalizeCollapsed(v) {
  const src = (v && typeof v === 'object' && !Array.isArray(v)) ? /** @type {any} */ (v) : {};
  /** @type {CollapseState} */
  const out = {};
  for (const id of REGION_IDS) out[id] = !!src[id];
  return out;
}

/**
 * @param {CollapseState} state
 * @param {string} id
 * @param {boolean} collapsed
 * @returns {CollapseState}
 */
export function setRegion(state, id, collapsed) {
  assertRegion(id);
  return { ...normalizeCollapsed(state), [id]: !!collapsed };
}

/**
 * @param {CollapseState} state
 * @param {string} id
 * @returns {CollapseState}
 */
export function toggleRegion(state, id) {
  assertRegion(id);
  const cur = normalizeCollapsed(state);
  return { ...cur, [id]: !cur[id] };
}

/**
 * @param {string} trigger
 * @returns {string[]} region ids to force open; empty for an unknown trigger
 */
export function panelsToReopen(trigger) {
  return REOPEN_TRIGGERS[trigger] ? [...REOPEN_TRIGGERS[trigger]] : [];
}

/**
 * Did anything actually move? Guards the render + preference write, so a burst
 * of events on already-open panels cannot storm the disk.
 * @param {CollapseState} prev
 * @param {CollapseState} next
 */
export function regionsChanged(prev, next) {
  const a = normalizeCollapsed(prev);
  const b = normalizeCollapsed(next);
  return REGION_IDS.some(id => a[id] !== b[id]);
}
