// @ts-check
/**
 * Layer 2: portable, baseline-free semantic checks over a captured Frame.
 */

/** @typedef {{x:number,y:number,width:number,height:number}} Rect */
/** @typedef {import('./capture.mjs').Frame} Frame */
/** @typedef {(rgb:[number,number,number,number]|null)=>boolean} PixelPredicate */

const DEFAULT_BAND = 4;        // CSS px perpendicular half-width (bridge dash gaps)
const DEFAULT_MAX_SEARCH = 40; // CSS px to search before giving up
const EDGE_SKIP = 2;           // start this many CSS px past the origin

/**
 * Scan a ray from CSS point (fromX,fromY) in CSS direction (dx,dy) until a pixel
 * matching `predicate` is hit, scanning a ±band perpendicular strip each step to
 * bridge dashed gaps. Returns CSS-px distance to the first hit, or Infinity.
 * @param {Frame} frame
 * @param {[number,number]} fromCss
 * @param {[number,number]} dir  unit-ish direction in CSS px, e.g. [1,0]
 * @param {PixelPredicate} predicate
 * @param {{band?:number, maxSearch?:number}} [opts]
 * @returns {number}
 */
export function scanRay(frame, fromCss, dir, predicate, opts = {}) {
  const band = opts.band ?? DEFAULT_BAND;
  const maxSearch = opts.maxSearch ?? DEFAULT_MAX_SEARCH;
  const [dx, dy] = dir;
  const px = -dy, py = dx; // perpendicular
  const maxSteps = Math.round(maxSearch * frame.scale);
  const bandR = Math.max(2, Math.round(band * frame.scale));
  const [sx, sy] = frame.toImg(fromCss[0], fromCss[1]);
  for (let step = 0; step <= maxSteps; step++) {
    for (let o = -bandR; o <= bandR; o++) {
      const x = Math.round(sx + dx * step + px * o);
      const y = Math.round(sy + dy * step + py * o);
      if (predicate(frame.pixel(x, y))) return step / frame.scale;
    }
  }
  return Infinity;
}

/**
 * CSS-px gap from `rect`'s `side` edge (scanned at the edge midpoint, starting
 * EDGE_SKIP px outside the rect) to the nearest pixel matching `predicate`.
 * Returns Infinity if none found. For geometry where the stroke is NOT at the
 * edge midpoint (e.g. a notch), call scanRay directly with a custom origin.
 * @param {Frame} frame @param {Rect} rect @param {PixelPredicate} predicate
 * @param {'top'|'right'|'bottom'|'left'} side
 * @param {{band?:number, maxSearch?:number}} [opts]
 * @returns {number}
 */
export function measureGap(frame, rect, predicate, side, opts = {}) {
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  let from, dir;
  switch (side) {
    case 'top':    from = [cx, rect.y - EDGE_SKIP];               dir = [0, -1]; break;
    case 'bottom': from = [cx, rect.y + rect.height + EDGE_SKIP]; dir = [0, 1];  break;
    case 'left':   from = [rect.x - EDGE_SKIP, cy];               dir = [-1, 0]; break;
    case 'right':  from = [rect.x + rect.width + EDGE_SKIP, cy];  dir = [1, 0];  break;
    default: throw new Error(`measureGap: bad side ${side}`);
  }
  const d = scanRay(frame, /** @type {[number,number]} */ (from), /** @type {[number,number]} */ (dir), predicate, opts);
  return d === Infinity ? Infinity : d + EDGE_SKIP;
}
