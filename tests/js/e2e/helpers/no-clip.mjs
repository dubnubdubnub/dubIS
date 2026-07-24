// @ts-check
/* Sweeping "text is not clipped" assertions, shared by every zoom spec.

   The rule: a text-bearing element must not have content wider or taller than its
   own box, UNLESS it opted into truncation. This codebase marks intentional
   ellipsis with a truncate-family class or a `title` attribute (the tooltip that
   makes truncated text recoverable), so those are the opt-outs. Anything else
   overflowing is a real clipping bug — fix the CSS, never the tolerance.

   Coordinate-space note (matters for every assertion here): under
   `html { zoom: z }` element rects and window.innerWidth are post-zoom, while
   documentElement.clientWidth is authored px. Never compare across the two.
   Rect-vs-window checks below use window.innerWidth/innerHeight; the
   document-scrollbar check stays entirely within client* properties. */

import { expect } from '@playwright/test';

/**
 * Drive the app's own zoom, so specs exercise the real code path rather than
 * poking the CSS variable directly.
 * @param {import('@playwright/test').Page} page
 * @param {number} percent
 */
export async function setZoom(page, percent) {
  await page.evaluate(async (p) => {
    const { setZoom } = await import('/js/ui-zoom.js');
    setZoom(p / 100);
  }, percent);
  // Two frames: one for the zoom to apply, one for dependent layout to settle.
  await page.evaluate(() => new Promise(r => requestAnimationFrame(
    () => requestAnimationFrame(() => r(undefined)))));
}

/**
 * @param {import('@playwright/test').Page} page
 * @param {number} percent
 */
export async function expectZoomApplied(page, percent) {
  const applied = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--ui-zoom').trim());
  expect(Number(applied), `--ui-zoom should be ${percent}%`).toBeCloseTo(percent / 100, 5);
}

/* Opt-outs: elements this codebase intentionally truncates, each with its own
   recovery affordance. A `title` attribute is the usual marker; the part-number
   spans instead carry data-lcsc/digikey/pololu/mouser, which open the
   part-preview hover card (js/part-preview.js). Scrollable containers and form
   controls are allowed to hold overflowing content by definition. */
const CLIP_OPT_OUT = [
  '.truncate', '.ellipsis', '[title]', '.refs-scroll', '.desc-cell',
  '[data-lcsc]', '[data-digikey]', '[data-pololu]', '[data-mouser]',
  'input', 'textarea', 'select', '.console-entries', '.console-entry',
].join(', ');

/**
 * Every visible element under `selector` whose own text overflows its box.
 * Keyed by a stable selector signature so two zoom levels can be compared.
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 * @returns {Promise<Record<string, {dx: number, dy: number, text: string, count: number}>>}
 */
export async function collectClipping(page, selector) {
  return page.evaluate(({ sel, optOut }) => {
    const root = document.querySelector(sel);
    if (!root) return { [`MISSING:${sel}`]: { dx: 0, dy: 0, text: '', count: 1 } };

    /** @param {Element} el */
    const describe = (el) => el.tagName.toLowerCase()
      + (el.id ? '#' + el.id : '')
      + (typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\s+/).join('.') : '');

    /** @type {Record<string, {dx: number, dy: number, text: string, count: number}>} */
    const out = {};
    for (const el of root.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      // Only text-bearing leaves: an element whose *own* text could clip.
      const hasOwnText = [...el.childNodes]
        .some(n => n.nodeType === 3 && (n.textContent || '').trim().length > 0);
      if (!hasOwnText) continue;
      if (el.closest(optOut)) continue;
      if (['auto', 'scroll'].includes(cs.overflowX)
        || ['auto', 'scroll'].includes(cs.overflowY)) continue;
      // `text-overflow: ellipsis` is an explicit declaration that truncation is
      // intended here, and the ellipsis itself is the visual affordance telling
      // the user text was cut. Silent clipping is the bug; an ellipsis is not.
      if (cs.textOverflow === 'ellipsis') continue;

      const dx = el.scrollWidth - el.clientWidth;
      const dy = el.scrollHeight - el.clientHeight;
      if (dx > 1 || dy > 1) {
        const key = describe(el);
        const prev = out[key];
        out[key] = {
          dx: Math.max(dx, prev ? prev.dx : 0),
          dy: Math.max(dy, prev ? prev.dy : 0),
          text: prev ? prev.text : (el.textContent || '').trim().slice(0, 40),
          count: (prev ? prev.count : 0) + 1,
        };
      }
    }
    return out;
  }, { sel: selector, optOut: CLIP_OPT_OUT });
}

/**
 * Assert nothing under `selector` clips its own text at all. Use for the chrome
 * that must always be fully legible — the header and the panel headers, which is
 * exactly the text the user complained they could not read.
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 * @param {string} label
 */
export async function expectNoClipping(page, selector, label) {
  const offenders = await collectClipping(page, selector);
  expect(offenders,
    `${label}: clipped text in ${selector} — ${JSON.stringify(offenders, null, 2)}`)
    .toEqual({});
}

/**
 * Assert zoom does not make clipping *worse* than the 100% baseline.
 *
 * The inventory grid legitimately truncates some columns at every zoom level (the
 * part-number spans are `overflow: hidden` by design), so an absolute "nothing
 * ever clips" rule there would be false. What must hold is that changing zoom
 * introduces no new clipped element and no larger overflow — which is precisely
 * the regression a layout-breaking zoom would cause.
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 * @param {Record<string, {dx: number, dy: number, text: string, count: number}>} baseline
 *   result of collectClipping at 100% zoom
 * @param {string} label
 */
export async function expectNoNewClipping(page, selector, baseline, label) {
  const current = await collectClipping(page, selector);

  const introduced = Object.keys(current).filter(k => !(k in baseline));
  expect(introduced,
    `${label}: zoom introduced newly-clipped elements in ${selector} — ${
      JSON.stringify(introduced.map(k => ({ sel: k, ...current[k] })), null, 2)}`)
    .toEqual([]);

  // Allow a 2px slack for sub-pixel rounding at fractional zoom factors, then
  // require the overflow not to have grown.
  const worsened = Object.keys(current)
    .filter(k => k in baseline)
    .filter(k => current[k].dx > baseline[k].dx + 2 || current[k].dy > baseline[k].dy + 2)
    .map(k => ({ sel: k, was: baseline[k], now: current[k] }));
  expect(worsened,
    `${label}: zoom worsened existing clipping in ${selector} — ${JSON.stringify(worsened, null, 2)}`)
    .toEqual([]);
}

/**
 * The app is `overflow: hidden` by design; a scrollbar means the layout broke.
 * This is the assertion that catches a 100vh-style regression under zoom.
 * @param {import('@playwright/test').Page} page
 * @param {string} label
 */
export async function expectNoPageScrollbars(page, label) {
  const doc = await page.evaluate(() => {
    const d = document.documentElement;
    return { sw: d.scrollWidth, cw: d.clientWidth, sh: d.scrollHeight, ch: d.clientHeight };
  });
  expect(doc.sw, `${label}: document should not scroll horizontally (${doc.sw} > ${doc.cw})`)
    .toBeLessThanOrEqual(doc.cw + 1);
  expect(doc.sh, `${label}: document should not scroll vertically (${doc.sh} > ${doc.ch})`)
    .toBeLessThanOrEqual(doc.ch + 1);
}

/**
 * Assert a floating element sits fully inside the window.
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 * @param {string} label
 */
export async function expectInsideWindow(page, selector, label) {
  const res = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    // Rects and window.inner* share the post-zoom space; clientWidth does not.
    return {
      l: r.left, t: r.top, right: r.right, b: r.bottom,
      vw: window.innerWidth, vh: window.innerHeight,
    };
  }, selector);

  expect(res, `${label}: ${selector} should exist`).not.toBeNull();
  if (!res) return;
  expect(res.right, `${label}: ${selector} overflows the right edge`).toBeLessThanOrEqual(res.vw + 1);
  expect(res.b, `${label}: ${selector} overflows the bottom edge`).toBeLessThanOrEqual(res.vh + 1);
  expect(res.l, `${label}: ${selector} overflows the left edge`).toBeGreaterThanOrEqual(-1);
  expect(res.t, `${label}: ${selector} overflows the top edge`).toBeGreaterThanOrEqual(-1);
}

/**
 * Every header control must stay inside the window at every zoom level.
 * @param {import('@playwright/test').Page} page
 * @param {string} label
 */
export async function expectHeaderControlsOnScreen(page, label) {
  const overflowing = await page.evaluate(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const out = [];
    const sel = '.header button, .header input, .header .zoom-control, .header .inv-count';
    for (const el of document.querySelectorAll(sel)) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.left < -1 || r.top < -1 || r.right > vw + 1 || r.bottom > vh + 1) {
        out.push({
          id: el.id || (typeof el.className === 'string' ? el.className : ''),
          left: Math.round(r.left), top: Math.round(r.top),
          right: Math.round(r.right), bottom: Math.round(r.bottom), vw, vh,
        });
      }
    }
    return out;
  });

  expect(overflowing,
    `${label}: header controls outside the window — ${JSON.stringify(overflowing, null, 2)}`)
    .toEqual([]);
}
