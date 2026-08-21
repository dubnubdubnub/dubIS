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
 * A rect read only once the element has stopped moving.
 *
 * Every measurement here is a separate round-trip, so a reflow landing between
 * two of them corrupts whatever compares them: a cascade delta absorbs the
 * whole shift, or a screenshot clip taken from an earlier rect no longer frames
 * what the coordinates say it does. Waiting for two consecutive identical reads
 * makes the layout quiescent *before* anything depends on it.
 *
 * This STRENGTHENS a test rather than relaxing it — a layout that genuinely
 * never settles now fails with a clear message instead of flaking on whichever
 * frame the measurement happened to catch.
 *
 * @param {import('@playwright/test').Locator} locator
 * @param {{tries?: number, interval?: number}} [opts]
 * @returns {Promise<Rect>}
 */
export async function settledRect(locator, opts = {}) {
  const tries = opts.tries ?? 20;
  const interval = opts.interval ?? 50;
  let prev = await rectOf(locator);
  for (let i = 0; i < tries; i++) {
    await locator.page().waitForTimeout(interval);
    const next = await rectOf(locator);
    if (next.x === prev.x && next.y === prev.y
      && next.width === prev.width && next.height === prev.height) {
      return next;
    }
    prev = next;
  }
  throw new Error(
    `settledRect: layout still moving after ${tries * interval}ms `
    + `(last rect ${JSON.stringify(prev)})`,
  );
}

/**
 * Wait for web fonts to finish loading.
 *
 * Icons carrying emoji glyphs reflow their row when a late font arrives, which
 * is one of the reflows settledRect would otherwise have to sit through — and
 * under parallel load it can arrive after the first measurement.
 * @param {import('@playwright/test').Page} page
 */
export async function fontsReady(page) {
  await page.evaluate(() => document.fonts && document.fonts.ready);
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
