// @ts-check
/**
 * Thin snapshot layer. Golden-image comparison with a STRICT diff ratio.
 *
 * WHY maxDiffPixelRatio: 0 (not the common 0.01): for thin/sparse dashed lines,
 * a 1% ratio is far too lenient — a catastrophic frame shift changes well under
 * 1% of the pixels in the clip, so the test would pass anyway. We require ZERO
 * differing pixels and absorb only sub-pixel AA noise via a small per-pixel
 * `threshold`. If AA jitter makes a snapshot flaky, raise `threshold` slightly —
 * never relax the ratio. Used sparingly (anchor views only).
 */
import { expect } from '@playwright/test';

/** @typedef {{x:number,y:number,width:number,height:number}} Rect */

/**
 * Playwright clip rect around a locator's bounding box, expanded by `pad`
 * (so an inset stroke that sits outside the border box isn't clipped).
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} locator
 * @param {number} [pad]
 * @returns {Promise<Rect>}
 */
export async function paddedClip(page, locator, pad = 12) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('paddedClip: locator has no bounding box (not visible?)');
  return {
    x: Math.max(0, box.x - pad),
    y: Math.max(0, box.y - pad),
    width: box.width + pad * 2,
    height: box.height + pad * 2,
  };
}

/**
 * @param {import('@playwright/test').Page} page
 * @param {string} name @param {Rect} clip
 */
export async function expectStrictScreenshot(page, name, clip) {
  await expect(page).toHaveScreenshot(name, { clip, maxDiffPixelRatio: 0, threshold: 0.1 });
}
