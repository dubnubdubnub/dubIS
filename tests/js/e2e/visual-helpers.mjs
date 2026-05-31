// @ts-check
/**
 * Reusable VISUAL (rendered-pixel) test helpers for Playwright E2E specs.
 *
 * Motivation: property-based DOM tests that read an SVG path's `d` attribute
 * AND the button's bounding rect, then check they agree, are TAUTOLOGICAL —
 * the path is *generated from* that same rect, so the test only proves the
 * math is internally consistent, never that the pixels on screen are correct.
 * (A viewBox-scale bug that renders the frame at the wrong scale, abandoning
 * the button, still passes such a test.) These helpers verify the ACTUAL
 * rendered pixels instead.
 *
 * Two flavors are provided:
 *   - `expectStrictScreenshot` — golden-image comparison with a strict ratio.
 *   - `sampleDashedFrame` — a SEMANTIC pixel sampler that measures, from the
 *     rendered screenshot, the gap between the ★ Direct button and the dashed
 *     L-frame stroke on each side, so a test can assert "the frame wraps the
 *     button with an 8px margin" without a golden image.
 */
import { expect } from '@playwright/test';
import { PNG } from 'pngjs';

// ── Stroke detection (against the bright dragover state) ─────────────────────
//
// We force the drop-zone's `dragover` state so the dashed stroke renders in
// --color-blue (#58a6ff). The ★ Direct button is ALSO blue, so we never test
// "is this near pure blue" (that would match the button). Instead a stroke
// pixel is one that is clearly BLUISH relative to its neighbourhood: the blue
// channel sits well above the red channel and is bright enough to stand out
// from the dark navy background (~#0D1117 / #161b22). Thin anti-aliased dashes
// render as blends (e.g. (38,55,75)) — far from pure blue but still distinctly
// bluish — so this relative test is what actually detects them.
const BLUE_OVER_RED = 28;   // blue channel must exceed red by at least this much
const BLUE_MIN = 60;        // blue channel must be at least this bright

// We start each scan a few CSS px OUTSIDE the button edge so the button's own
// (blue) anti-aliased border isn't mistaken for the frame stroke; this offset
// is added back into the reported margin.
const EDGE_SKIP = 2;
// How far (CSS px) to search outward before giving up.
const MAX_SEARCH = 40;
// Perpendicular band half-width (CSS px). The stroke is dashed (6 4), so a
// single ray can land in a gap; scanning a band perpendicular to the ray
// bridges the gaps. Kept small so it can't jump to an unrelated edge.
const BAND = 4;

/**
 * Build a Playwright `clip` rect around a locator's bounding box, expanded by
 * `pad` on all sides. The drop-zone dashed stroke sits at `inset: -2px` (2px
 * OUTSIDE the border box), so a screenshot must include padding around the
 * zone or the stroke gets clipped. Coordinates are clamped to >= 0.
 *
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} locator
 * @param {number} [pad]
 * @returns {Promise<{x:number,y:number,width:number,height:number}>}
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
 * Golden-image screenshot assertion with a STRICT diff ratio.
 *
 * WHY maxDiffPixelRatio: 0 (and not the common 0.01): for thin/sparse dashed
 * lines, a 1% ratio is far too lenient — a catastrophic frame shift changes
 * well under 1% of the pixels in the clip (the stroke is only a few percent of
 * the area), so the test would pass anyway. We instead require ZERO differing
 * pixels and absorb only sub-pixel anti-aliasing noise via a small per-pixel
 * `threshold` (0.1). If real font/AA jitter makes this flaky on a platform,
 * raise `threshold` slightly — never relax the ratio.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} name  snapshot file name
 * @param {{x:number,y:number,width:number,height:number}} clip
 */
export async function expectStrictScreenshot(page, name, clip) {
  await expect(page).toHaveScreenshot(name, {
    clip,
    maxDiffPixelRatio: 0,
    threshold: 0.1,
  });
}

/** Read a pixel from a decoded PNG as [r,g,b]; null if out of bounds. */
function pixelAt(png, x, y) {
  if (x < 0 || y < 0 || x >= png.width || y >= png.height) return null;
  const i = (png.width * y + x) << 2;
  return [png.data[i], png.data[i + 1], png.data[i + 2]];
}

/** Whether a pixel is part of the bright-blue dashed stroke (see constants). */
function isStrokePixel(png, x, y) {
  const p = pixelAt(png, x, y);
  if (!p) return false;
  return p[2] - p[0] >= BLUE_OVER_RED && p[2] >= BLUE_MIN;
}

/**
 * Scan a ray from (sx,sy) in device-pixel direction (dx,dy) up to `maxSteps`,
 * scanning a band of ±bandR pixels perpendicular to the ray at each step so
 * dash GAPS don't cause false misses. Returns the step index of the first
 * stroke hit, or null. Coordinates are device pixels.
 */
function scanForStroke(png, sx, sy, dx, dy, maxSteps, bandR) {
  const px = -dy; // perpendicular
  const py = dx;
  for (let step = 0; step <= maxSteps; step++) {
    for (let o = -bandR; o <= bandR; o++) {
      const x = Math.round(sx + dx * step + px * o);
      const y = Math.round(sy + dy * step + py * o);
      if (isStrokePixel(png, x, y)) return step;
    }
  }
  return null;
}

/**
 * SEMANTIC pixel sampler: measures the dashed L-frame's margins around the
 * ★ Direct button directly from rendered pixels, plus whether the NW concave
 * corner is rounded.
 *
 * GEOMETRY NOTE (why each side is scanned where it is): the button is anchored
 * in the zone's bottom-right corner; the dashed L-frame wraps its TOP and LEFT
 * (the concave "notch", 8px away) and otherwise follows the zone's outer
 * perimeter. Consequently:
 *   - The TOP and LEFT margins are read directly off the notch edges at the
 *     button's center line.
 *   - To the right of / below the button's CENTER there is no stroke (the
 *     notch turns up-and-over there). The frame's right edge exists ABOVE the
 *     notch and its bottom edge exists LEFT of the notch — both still 8px from
 *     the button. So we read marginRight on a horizontal ray above the notch
 *     and marginBottom on a vertical ray left of the notch. A viewBox/scale bug
 *     shifts all of these, so all four remain sensitive bug detectors.
 *
 * Strategy: (1) read zone+button rects, (2) force the bright-blue dragover
 * state for high-contrast detection, (3) screenshot the padded clip + decode
 * with pngjs, (4) scan outward from the button edges.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{zoneSelector?:string, btnSelector?:string, pad?:number}} [opts]
 * @returns {Promise<{marginRight:number, marginBottom:number, marginTop:number,
 *   marginLeft:number, cornerRounded:boolean}>}
 */
export async function sampleDashedFrame(page, opts = {}) {
  const {
    zoneSelector = '#import-drop-zone',
    btnSelector = '[data-template="direct"]',
    pad = 12,
  } = opts;

  // (1)+(2): grab rects (CSS px) and force the high-contrast dragover state.
  const rects = await page.evaluate(({ zoneSel, btnSel }) => {
    const zone = document.querySelector(zoneSel);
    const btn = document.querySelector(btnSel);
    if (!zone || !btn) throw new Error('sampleDashedFrame: zone or button not found');
    zone.classList.add('dragover'); // bright-blue stroke → robust pixel detection
    const zr = zone.getBoundingClientRect();
    const br = btn.getBoundingClientRect();
    return {
      zone: { x: zr.left, y: zr.top, width: zr.width, height: zr.height },
      btn: { x: br.left, y: br.top, width: br.width, height: br.height },
    };
  }, { zoneSel: zoneSelector, btnSel: btnSelector });

  await page.waitForTimeout(150); // let the stroke-color transition settle

  // (3): screenshot the padded clip and decode.
  const clipX = Math.max(0, rects.zone.x - pad);
  const clipY = Math.max(0, rects.zone.y - pad);
  const clip = {
    x: clipX,
    y: clipY,
    width: rects.zone.width + pad * 2,
    height: rects.zone.height + pad * 2,
  };
  const png = PNG.sync.read(await page.screenshot({ clip }));

  // Screenshots are in device pixels; rects are CSS px. Derive the scale.
  const scale = png.width / clip.width;
  // Map a CSS-px viewport point to device-px screenshot-local coords.
  const toImg = (vx, vy) => [
    Math.round((vx - clipX) * scale),
    Math.round((vy - clipY) * scale),
  ];

  const b = rects.btn;
  const btnLeft = b.x;
  const btnRight = b.x + b.width;
  const btnTop = b.y;
  const btnBottom = b.y + b.height;
  const btnCx = b.x + b.width / 2;
  const btnCy = b.y + b.height / 2;

  const maxSteps = Math.round(MAX_SEARCH * scale);
  const bandR = Math.max(2, Math.round(BAND * scale));
  const skipDev = EDGE_SKIP * scale;
  // Convert a found device-px step (measured from a point EDGE_SKIP px outside
  // the button) back into a CSS-px margin from the button edge.
  const toMargin = (step) => (step == null ? Infinity : step / scale + EDGE_SKIP);

  // marginTop: notch top edge. Scan UP from just above the button, in a column
  // a few px right of the button's left edge (the notch top runs btnLeft→right).
  let [sx, sy] = toImg(btnLeft + 6, btnTop - EDGE_SKIP);
  const stepTop = scanForStroke(png, sx, sy, 0, -1, maxSteps, bandR);

  // marginLeft: notch left edge. Scan LEFT from just left of the button, at the
  // button's vertical center.
  [sx, sy] = toImg(btnLeft - EDGE_SKIP, btnCy);
  const stepLeft = scanForStroke(png, sx, sy, -1, 0, maxSteps, bandR);

  // marginRight: the frame's right vertical edge exists ABOVE the notch. Scan
  // RIGHT from just right of the button, at a y well above the notch top.
  [sx, sy] = toImg(btnRight + EDGE_SKIP, btnTop - 20);
  const stepRight = scanForStroke(png, sx, sy, 1, 0, maxSteps, bandR);

  // marginBottom: the frame's bottom horizontal edge exists LEFT of the notch.
  // Scan DOWN from just below the button, at an x well left of the notch left.
  [sx, sy] = toImg(btnLeft - 20, btnBottom + EDGE_SKIP);
  const stepBottom = scanForStroke(png, sx, sy, 0, 1, maxSteps, bandR);

  // cornerRounded heuristic: the NW notch corner is where the notch TOP edge
  // (y = btnTop-8) and notch LEFT edge (x = btnLeft-8) would meet if extended —
  // the exact point (btnLeft-8, btnTop-8). A SQUARE corner has stroke right at
  // that L-vertex; a ROUNDED corner replaces it with a quarter-arc that bulges
  // toward the button, leaving the vertex empty while the arc passes a couple
  // px away diagonally.
  //
  // We check only the vertex pixel itself. The four orthogonal neighbours are
  // deliberately excluded: at sub-integer device scales the +x neighbour lands
  // on the notch top-edge stroke and the -y neighbour lands on the notch left-
  // edge stroke, both producing false positives on a correctly-rounded corner.
  // The vertex alone is sufficient: a SQUARE corner places stroke exactly ON the
  // vertex, whereas a ROUNDED corner's arc passes ~2px away diagonally leaving
  // the vertex pixel clear.
  const [cxImg, cyImg] = toImg(btnLeft - 8, btnTop - 8);
  const vertexHasStroke = isStrokePixel(png, cxImg, cyImg);
  const edgesPresent = stepTop != null && stepLeft != null;
  const cornerRounded = edgesPresent && !vertexHasStroke;

  return {
    marginRight: toMargin(stepRight),
    marginBottom: toMargin(stepBottom),
    marginTop: toMargin(stepTop),
    marginLeft: toMargin(stepLeft),
    cornerRounded,
  };
}
