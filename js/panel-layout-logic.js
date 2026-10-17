// @ts-check
/* js/panel-layout-logic.js — pure arithmetic for the three-panel row.

   js/resize-panels.js is the only writer of the panels' inline flex/width (a
   source-scanning guard, tests/js/panel-geometry-owner.test.js, holds it to
   that); this module is the arithmetic it writes, kept DOM-free so it is
   unit-testable without the app's module graph.

   ── Why weights are px, not fractions ─────────────────────────────────────
   A drag used to end by writing `flex: <width / total> 0 0px` — three
   fractions summing to 1. CSS Flexbox §9.7 has a rule for exactly that case:
   when the flex-grow factors on a line sum to LESS than 1, the items receive
   only that fraction of the free space, and the rest stays empty. Collapse one
   panel (its factor is overridden to 0 by .panel-collapsed) and the survivors
   summed to ~0.78, so ~22% of the row turned into a dead strip right of the
   BOM. Fractions are only correct while every panel is visible.

   So a weight is the panel's width in authored px at the moment it was
   recorded, written as `flex: <px> 1 0px`. Every weight is ≥ 1 (a visible panel
   is at least its min-width wide), so ANY non-empty subset of panels has
   factors summing to ≥ 1 and fills the row, and the ratios between the visible
   ones are preserved. After a window resize a weight is no longer that panel's
   width — it is only a ratio — which is all flex-grow needs.

   All lengths here are authored px (see the root-zoom trap in js/ui-zoom.js):
   callers convert rects with innerRect() and pointer deltas with toInnerPx(). */

/** [import, inventory, bom] — authored px; mirrors the min-widths in css/panels/. */
export const PANEL_MIN_WIDTHS = [240, 300, 380];

/**
 * One weight per panel. `null` means "never recorded": the panel keeps the
 * stylesheet sizing (22% / flex:1 / 40%), which mixes correctly with weighted
 * neighbours because their factors are ≥ 1.
 * @typedef {(number|null)[]} Weights
 */

/**
 * The inline `flex` value for a weight; '' clears it back to the stylesheet.
 * Throws on a weight below 1 — that is precisely the value that reintroduces
 * the sum-below-1 dead space, so it is a bug in the caller, never user data.
 * @param {number|null} w
 * @returns {string}
 */
export function flexFor(w) {
  if (w === null) return '';
  if (!Number.isFinite(w) || w < 1) {
    throw new RangeError(`panel weight must be a finite px width >= 1, got ${String(w)}`);
  }
  return `${w} 1 0px`;
}

/**
 * Can a drag start on the handle between panels `leftIdx` and `leftIdx + 1`?
 * Not when either neighbour is collapsed: a zero-width panel has no width to
 * trade, and the alternatives — handing it width it cannot show, or recording
 * it at 0px so it reopens at nothing — are the two bugs this module removes.
 * Reopening is the pill's job, not the divider's.
 * @param {number} leftIdx
 * @param {boolean[]} collapsed
 */
export function canDrag(leftIdx, collapsed) {
  return !collapsed[leftIdx] && !collapsed[leftIdx + 1];
}

/**
 * Re-express every weight in the px of the row as it is right now, at the
 * start of a drag: visible panels take their measured width, and a collapsed
 * panel's remembered weight is rescaled by the same factor its visible peers
 * moved by since it was recorded (a window resize, a zoom change, another
 * drag while it was hidden). That keeps its share of the row, so reopening it
 * gives back the width it had — a raw weight from an older, narrower row would
 * reopen it at the wrong size.
 *
 * The factor comes from visible panels that already have a weight. If none do,
 * no collapsed panel can have one either (every drag records every visible
 * panel, and the inventory grid is never collapsible), so there is nothing to
 * rescale.
 * @param {Weights} prev
 * @param {number[]} widths  measured authored widths, one per panel
 * @param {boolean[]} collapsed
 * @returns {Weights}
 */
export function rebaseWeights(prev, widths, collapsed) {
  if (prev.length !== widths.length || widths.length !== collapsed.length) {
    throw new RangeError('rebaseWeights: prev, widths and collapsed must align');
  }
  let measured = 0;
  let recorded = 0;
  for (let i = 0; i < widths.length; i++) {
    const p = prev[i];
    if (!collapsed[i] && p !== null) { measured += widths[i]; recorded += p; }
  }
  const k = recorded > 0 ? measured / recorded : 1;
  return widths.map((w, i) => {
    if (!collapsed[i]) return w;
    const p = prev[i];
    return p === null ? null : p * k;
  });
}

/**
 * Split a divider drag between its two neighbours. The pair's total is
 * conserved — the rest of the row never moves — and each side is held at its
 * minimum. When the pair is already narrower than both minimums together (a
 * narrow window at high zoom, where the row overflows) there is no legal split,
 * so the drag changes nothing rather than pushing one side under its floor.
 * @param {number} startLeft
 * @param {number} startRight
 * @param {number} dx  authored px, positive = divider moves right
 * @param {number} minLeft
 * @param {number} minRight
 * @returns {{ left: number, right: number }}
 */
export function clampDrag(startLeft, startRight, dx, minLeft, minRight) {
  for (const v of [startLeft, startRight, dx, minLeft, minRight]) {
    if (!Number.isFinite(v)) throw new RangeError(`clampDrag: non-finite input ${String(v)}`);
  }
  const total = startLeft + startRight;
  if (total < minLeft + minRight) return { left: startLeft, right: startRight };
  const left = Math.min(Math.max(startLeft + dx, minLeft), total - minRight);
  return { left, right: total - left };
}
