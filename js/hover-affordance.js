// @ts-check
/* js/hover-affordance.js — primitives shared by the two hover affordances,
   js/text-popover.js (the generic Copy popover) and js/part-preview.js (the rich
   part-number tooltip).

   Two jobs, both deliberately kept out of the event handlers so they can be unit
   tested without a DOM:

   1. `copyText` — the single clipboard path for the whole app. Both affordances
      call it; nobody writes a second one.

   2. The hover *corridor*. Each popup is anchored a few px below (or above) its
      trigger, so the pointer must cross a gap that belongs to neither element.
      A hide driven only by `mouseout` + `relatedTarget` fires in that gap — the
      element under the pointer there is a sibling row, not the trigger and not
      the popup — which is exactly the "moving the cursor to the popup makes it
      disappear" bug. `inHoverCorridor` answers "is the pointer still travelling
      between the two?", so the hide path can decline instead of guessing from a
      timer.

   ── Coordinate space ──
   Every rect and point here is in AUTHORED px: callers pass rects from
   `innerRect()` and pointer coords through `toInnerPx()` (see js/ui-zoom.js).
   This module never measures anything itself, so it cannot mix the two spaces —
   but a caller that feeds it a raw `getBoundingClientRect()` alongside a
   `toInnerPx`'d pointer would get a corridor that only lines up at 100% zoom. */

import { AppLog } from './api.js';

/**
 * @typedef {{ left: number, top: number, right: number, bottom: number }} Box
 */

/**
 * Copy text to the clipboard, falling back to the legacy execCommand path when
 * the async Clipboard API is unavailable or rejects (no permission, no secure
 * context).
 * @param {string|null|undefined} text
 * @returns {Promise<boolean>} whether the text reached the clipboard
 */
export async function copyText(text) {
  if (text === null || text === undefined || text === '') return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {
    AppLog.warn('clipboard.writeText failed, trying execCommand: ' + e);
  }
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (!ok) AppLog.warn('execCommand("copy") returned false');
    return ok;
  } catch (e2) {
    AppLog.error('copyText failed entirely: ' + e2);
    return false;
  }
}

/**
 * Grow a box by `pad` on every side. Returns a new object; the input is not
 * mutated.
 * @param {Box} r
 * @param {number} pad
 * @returns {Box}
 */
export function padBox(r, pad) {
  return { left: r.left - pad, top: r.top - pad, right: r.right + pad, bottom: r.bottom + pad };
}

/**
 * Inclusive point-in-box test.
 * @param {Box|null} r
 * @param {number} x
 * @param {number} y
 */
export function boxContains(r, x, y) {
  if (!r) return false;
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
}

/**
 * The band of empty space the pointer has to cross to get from `trigger` to
 * `pop` — the visual gap, widened to the combined horizontal extent of the two
 * so a diagonal approach stays inside it.
 *
 * Returns null when the two boxes already overlap vertically (nothing to
 * bridge) or when the popup is neither clearly below nor clearly above.
 * @param {Box} trigger
 * @param {Box} pop
 * @returns {Box|null}
 */
export function bridgeBox(trigger, pop) {
  if (!trigger || !pop) return null;
  var left = Math.min(trigger.left, pop.left);
  var right = Math.max(trigger.right, pop.right);
  var top, bottom;
  if (pop.top >= trigger.bottom) {          // popup below the trigger
    top = trigger.bottom;
    bottom = pop.top;
  } else if (pop.bottom <= trigger.top) {   // popup flipped above the trigger
    top = pop.bottom;
    bottom = trigger.top;
  } else {
    return null;                            // overlapping — no gap to cross
  }
  if (bottom <= top) return null;           // flush against each other
  return { left: left, top: top, right: right, bottom: bottom };
}

/**
 * Is the pointer inside the trigger, inside the popup, or in the gap between
 * them? A true answer means the user is still engaged with the affordance and
 * the popup must stay up.
 *
 * @param {number} x pointer x in authored px
 * @param {number} y pointer y in authored px
 * @param {Box|null} trigger the hovered element's box, authored px
 * @param {Box|null} pop the popup's box, authored px
 * @param {number} [pad] slack on every edge; absorbs sub-pixel rounding and the
 *   one-frame lag between a rect being measured and the pointer being sampled
 * @returns {boolean}
 */
export function inHoverCorridor(x, y, trigger, pop, pad) {
  if (!trigger || !pop) return false;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
  var p = typeof pad === 'number' ? pad : 6;
  if (boxContains(padBox(trigger, p), x, y)) return true;
  if (boxContains(padBox(pop, p), x, y)) return true;
  var band = bridgeBox(trigger, pop);
  return band ? boxContains(padBox(band, p), x, y) : false;
}
