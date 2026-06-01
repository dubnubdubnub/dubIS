// @ts-check
/**
 * Layer 1 of the visual-testing system: one screenshot → decoded pixels +
 * CSS↔device-px coordinate mapping. Everything else builds on a Frame.
 */
import { PNG } from 'pngjs';

/** @typedef {{x:number,y:number,width:number,height:number}} Rect */
/** @typedef {{png:PNG, clip:Rect, scale:number,
 *   toImg:(cssX:number,cssY:number)=>[number,number],
 *   toCss:(devX:number,devY:number)=>[number,number],
 *   pixel:(x:number,y:number)=>[number,number,number,number]|null}} Frame */

/**
 * Bounding rect of a locator in VIEWPORT CSS px.
 * @param {import('@playwright/test').Locator} locator
 * @returns {Promise<Rect>}
 */
export async function rectOf(locator) {
  return await locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, width: r.width, height: r.height };
  });
}

/**
 * Screenshot a region (locator or viewport-px clip rect), expanded by `pad`,
 * and decode it. Returns a Frame with pixel access and coordinate mappers.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator | Rect} target
 * @param {{pad?:number}} [opts]
 * @returns {Promise<Frame>}
 */
export async function capture(page, target, opts = {}) {
  const pad = opts.pad ?? 12;
  let box;
  if (typeof (/** @type {any} */ (target).boundingBox) === 'function') {
    box = await (/** @type {import('@playwright/test').Locator} */ (target)).boundingBox();
    if (!box) throw new Error('capture: target locator has no bounding box (not visible?)');
  } else {
    box = /** @type {Rect} */ (target);
  }
  const clip = {
    x: Math.max(0, box.x - pad),
    y: Math.max(0, box.y - pad),
    width: box.width + pad * 2,
    height: box.height + pad * 2,
  };
  const png = PNG.sync.read(await page.screenshot({ clip }));
  const scale = png.width / clip.width; // device px per CSS px (handles DPR)
  const toImg = (cssX, cssY) => [
    Math.round((cssX - clip.x) * scale),
    Math.round((cssY - clip.y) * scale),
  ];
  const toCss = (devX, devY) => [devX / scale + clip.x, devY / scale + clip.y];
  const pixel = (x, y) => {
    if (x < 0 || y < 0 || x >= png.width || y >= png.height) return null;
    const i = (png.width * y + x) << 2;
    return [png.data[i], png.data[i + 1], png.data[i + 2], png.data[i + 3]];
  };
  return { png, clip, scale, toImg, toCss, pixel };
}
