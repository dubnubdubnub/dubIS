// @ts-check
/* js/ui-zoom-logic.js — pure zoom arithmetic.

   No DOM, no store, no imports: vitest can load this directly without dragging
   in js/constants.js's top-level fetch (see the CLAUDE.md trap). The DOM- and
   preference-facing half lives in js/ui-zoom.js. */

/**
 * The zoom ladder. Shared by the keyboard steps and the header slider so the two
 * input paths can never produce a level the other cannot reach.
 * @type {number[]}
 */
export const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];

export const DEFAULT_ZOOM = 1;

/**
 * Snap an arbitrary factor onto the nearest ladder value.
 * Throws on non-finite input: that can only come from a programming error, and
 * silently substituting a default would hide the bug.
 * @param {number} z
 * @returns {number}
 */
export function clampToStep(z) {
  if (typeof z !== 'number' || !Number.isFinite(z)) {
    throw new TypeError(`clampToStep: expected a finite number, got ${String(z)}`);
  }
  let best = ZOOM_STEPS[0];
  for (const s of ZOOM_STEPS) {
    if (Math.abs(s - z) < Math.abs(best - z)) best = s;
  }
  return best;
}

/**
 * Move one rung up (dir 1) or down (dir -1), clamped at both ends — no wrapping.
 * An off-ladder input snaps to the ladder first.
 * @param {number} z
 * @param {1|-1} dir
 * @returns {number}
 */
export function stepZoom(z, dir) {
  return zoomFromIndex(zoomIndex(clampToStep(z)) + dir);
}

/**
 * Ladder position of a factor. This is what the header slider's value holds.
 * @param {number} z
 * @returns {number}
 */
export function zoomIndex(z) {
  return ZOOM_STEPS.indexOf(clampToStep(z));
}

/**
 * Factor at a ladder position, clamped to the ends.
 * @param {number} i
 * @returns {number}
 */
export function zoomFromIndex(i) {
  const n = Math.round(Number(i));
  if (!Number.isFinite(n)) return DEFAULT_ZOOM;
  return ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, n))];
}

/**
 * Integer percentage for display.
 * @param {number} z
 * @returns {number}
 */
export function zoomPercent(z) {
  return Math.round(clampToStep(z) * 100);
}

/**
 * Recover a usable factor from whatever preferences.json held. This is user
 * data, not a call argument, so it repairs rather than throws.
 * @param {unknown} v
 * @returns {number}
 */
export function normalizePersistedZoom(v) {
  const n = typeof v === 'string' ? Number(v) : v;
  if (typeof n !== 'number' || !Number.isFinite(n) || n <= 0) return DEFAULT_ZOOM;
  return clampToStep(n);
}

/**
 * Window dimensions in *authored* (pre-zoom) CSS px — the same space as
 * `offsetWidth`, `document.documentElement.clientWidth`, and any px value you
 * write to `style.left`.
 *
 * Under `html { zoom: z }` the page has two coordinate spaces and the DOM API
 * straddles them:
 *   - `getBoundingClientRect()` and `event.clientX` report *post*-zoom px
 *     (authored × z), as does `window.innerWidth`;
 *   - `offsetWidth`/`clientWidth` and any px you *write* are *pre*-zoom.
 * Mixing them silently mispositions things at any zoom ≠ 1 while looking
 * perfect at 100%. Convert rects with `scaleRect(rect, 1/z)` and take window
 * bounds from here, then every number in a positioning routine is pre-zoom.
 *
 * @param {number} innerW window.innerWidth
 * @param {number} innerH window.innerHeight
 * @param {number} zoom
 * @returns {{ w: number, h: number }}
 */
export function viewportFor(innerW, innerH, zoom) {
  const z = (typeof zoom === 'number' && Number.isFinite(zoom) && zoom > 0) ? zoom : 1;
  return { w: innerW / z, h: innerH / z };
}

/**
 * @typedef {{ left: number, top: number, right: number, bottom: number,
 *             width: number, height: number }} RectLike
 */

/**
 * Multiply every edge of a rect by `factor`, returning a plain object.
 * Pass `1 / zoom` to convert a `getBoundingClientRect()` result into the
 * authored coordinate space.
 * @param {RectLike} rect
 * @param {number} factor
 * @returns {RectLike}
 */
export function scaleRect(rect, factor) {
  const f = (typeof factor === 'number' && Number.isFinite(factor) && factor > 0) ? factor : 1;
  return {
    left: rect.left * f,
    top: rect.top * f,
    right: rect.right * f,
    bottom: rect.bottom * f,
    width: rect.width * f,
    height: rect.height * f,
  };
}
