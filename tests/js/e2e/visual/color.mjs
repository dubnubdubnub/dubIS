// @ts-check
/**
 * Pure RGB predicates used to classify pixels (e.g. "is this the blue stroke").
 * Thresholds are passed in by callers so each surface names its own intent.
 */

/** @typedef {[number,number,number]|[number,number,number,number]|null} RGB */

/**
 * True if every channel of `rgb` is within `tol` of `target`.
 * @param {RGB} rgb @param {[number,number,number]} target @param {number} tol
 */
export function isColorNear(rgb, target, tol) {
  if (!rgb) return false;
  return Math.abs(rgb[0] - target[0]) <= tol &&
         Math.abs(rgb[1] - target[1]) <= tol &&
         Math.abs(rgb[2] - target[2]) <= tol;
}

/**
 * True if channel `ch` (0=R,1=G,2=B) exceeds the max of the other channels by
 * at least `byAtLeast` AND is itself at least `min`. Robust way to detect a
 * tinted (e.g. bluish) anti-aliased stroke against a dark background.
 * @param {RGB} rgb @param {0|1|2} ch @param {number} byAtLeast @param {number} min
 */
export function channelDominant(rgb, ch, byAtLeast, min) {
  if (!rgb) return false;
  const others = [0, 1, 2].filter((i) => i !== ch).map((i) => rgb[i]);
  return rgb[ch] - Math.max(...others) >= byAtLeast && rgb[ch] >= min;
}
