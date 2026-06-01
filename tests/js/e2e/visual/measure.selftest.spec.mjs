// @ts-check
// Self-tests for Layer 2 primitives. Each injects a KNOWN break and asserts the
// primitive catches it — the technique that would have caught the original bug.
//
// Subject: the image/PDF drop zone (#import-ocr-zone) and the "Scan with phone"
// button inside it. On dragover the zone's dashed border turns the bright blue
// we scan for. (The former ★ Direct button + its bespoke SVG L-frame were
// removed in the two-zone split; these primitives are general-purpose and are
// re-exercised here against the real, present drop-zone border + button.)
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from '../helpers.mjs';
import { capture, rectOf } from './capture.mjs';
import { measureGap, detectClipping, measureAlignment } from './measure.mjs';
import { channelDominant } from './color.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'inventory.json'), 'utf8'),
);
const isBluishStroke = (rgb) => channelDominant(rgb, 2, 28, 60);

async function setup(page) {
  await addMockSetup(page, MOCK_INVENTORY, {});
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(200);
  await page.locator('#import-ocr-zone').scrollIntoViewIfNeeded();
  // Force the bright-blue dragover border so there's a known stroke to scan for.
  await page.evaluate(() => document.getElementById('import-ocr-zone').classList.add('dragover'));
  await page.waitForTimeout(120);
}

test('measureGap finds the dashed zone border beside the button; Infinity when the border is removed', async ({ page }) => {
  await setup(page);
  const zone = page.locator('#import-ocr-zone');
  const btn = zone.locator('#import-scan-btn');

  const frame = await capture(page, zone, { pad: 16 });
  const btnRect = await rectOf(btn);
  // The scan button is roughly centered in the zone; its LEFT edge is a short hop
  // (the zone's 12px side padding) from the zone's (blue, dragover) dashed LEFT
  // border. measureGap scans left from the button edge to the first blue stroke.
  const gapLeft = measureGap(frame, btnRect, isBluishStroke, 'left', { maxSearch: 120 });
  expect(gapLeft).toBeGreaterThan(0);
  expect(gapLeft).toBeLessThan(Infinity);

  // INJECT BREAK: remove the zone border entirely. The button's DOM box is
  // unchanged (the old, geometry-only check would stay green); the stroke pixels
  // vanish, so a pixel scan must now report Infinity.
  await page.evaluate(() => {
    const z = document.getElementById('import-ocr-zone');
    z.style.border = 'none';
  });
  await page.waitForTimeout(60);
  const frame2 = await capture(page, zone, { pad: 16 });
  const gapBroken = measureGap(frame2, await rectOf(btn), isBluishStroke, 'left', { maxSearch: 120 });
  expect(gapBroken).toBe(Infinity);
});

test('detectClipping: clean button is visible; off-screen / overflow is caught', async ({ page }) => {
  await setup(page);
  const btn = page.locator('#import-ocr-zone #import-scan-btn');
  const clean = await detectClipping(page, btn);
  expect(clean.clipped).toBe(false);
  expect(clean.visibleRatio).toBeGreaterThanOrEqual(0.99);

  // INJECT BREAK: wrap the drop-zone in a tiny overflow:hidden container so the
  // button (which sits near the zone bottom) is pushed entirely off-screen of
  // the wrapper, making it clipped.
  await page.evaluate(() => {
    const z = document.getElementById('import-ocr-zone');
    const wrapper = document.createElement('div');
    wrapper.style.overflow = 'hidden';
    wrapper.style.height = '20px';
    wrapper.style.position = 'relative';
    z.parentElement.insertBefore(wrapper, z);
    wrapper.appendChild(z);
  });
  const broken = await detectClipping(page, btn);
  expect(broken.clipped).toBe(true);
  expect(broken.visibleRatio).toBeLessThan(0.99);
});

test('detectClipping: catches an element occluding the button', async ({ page }) => {
  await setup(page);
  const btn = page.locator('#import-ocr-zone #import-scan-btn');
  const clean = await detectClipping(page, btn);
  expect(clean.occluded).toBe(false);

  // INJECT BREAK: drop an opaque overlay exactly over the button's center.
  await page.evaluate(() => {
    const b = document.querySelector('#import-ocr-zone #import-scan-btn').getBoundingClientRect();
    const o = document.createElement('div');
    o.id = '__occluder__';
    o.style.cssText = `position:fixed; left:${b.left}px; top:${b.top}px; width:${b.width}px; height:${b.height}px; background:#f00; z-index:99999;`;
    document.body.appendChild(o);
  });
  const occ = await detectClipping(page, btn);
  expect(occ.occluded, 'overlay over button center should be detected as occlusion').toBe(true);
});

test('measureAlignment: aligned edges ~0; nudged edge detected', async ({ page }) => {
  await setup(page);
  const a = { x: 100, y: 0, width: 50, height: 10 };
  const b = { x: 100, y: 20, width: 50, height: 10 };
  expect(Math.abs(measureAlignment(a, b, 'left'))).toBeLessThanOrEqual(0.5);
  const bn = { ...b, x: 107 };
  expect(measureAlignment(a, bn, 'left')).toBeCloseTo(-7, 0);
});
