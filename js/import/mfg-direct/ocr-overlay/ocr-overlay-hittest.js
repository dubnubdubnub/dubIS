/* ocr-overlay-hittest.js — pure geometry for rubber-band token selection. */

/**
 * Normalize a drag rectangle defined by two corner points into
 * {left, top, right, bottom} regardless of drag direction.
 * @param {{x:number,y:number}} a
 * @param {{x:number,y:number}} b
 * @returns {{left:number, top:number, right:number, bottom:number}}
 */
export function normalizeRect(a, b) {
  return {
    left: Math.min(a.x, b.x),
    top: Math.min(a.y, b.y),
    right: Math.max(a.x, b.x),
    bottom: Math.max(a.y, b.y),
  };
}

/** Axis-aligned rectangle intersection (touching edges count as overlap). */
export function rectsIntersect(r, s) {
  return r.left <= s.right && r.right >= s.left
      && r.top <= s.bottom && r.bottom >= s.top;
}

/**
 * Given a selection rectangle and a list of token boxes, return the ids of
 * tokens whose box intersects the selection. Coordinates must share one frame.
 * @param {{left:number,top:number,right:number,bottom:number}} sel
 * @param {Array<{id:string, left:number, top:number, right:number, bottom:number}>} boxes
 * @returns {string[]}
 */
export function tokensInRect(sel, boxes) {
  return boxes.filter(b => rectsIntersect(sel, b)).map(b => b.id);
}
