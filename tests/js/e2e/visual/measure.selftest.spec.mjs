// @ts-check
// Self-tests for Layer 2 primitives. Each injects a KNOWN break and asserts the
// primitive catches it — the technique that would have caught the original bug.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from '../helpers.mjs';
import { capture, rectOf } from './capture.mjs';
import { scanRay, measureGap, detectClipping, measureAlignment } from './measure.mjs';
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
  await page.locator('#import-drop-zone').scrollIntoViewIfNeeded();
  await page.evaluate(() => document.getElementById('import-drop-zone').classList.add('dragover'));
  await page.waitForTimeout(120);
}

test('measureGap finds the dashed frame ~8px from the button; Infinity when frame is broken', async ({ page }) => {
  await setup(page);
  const zone = page.locator('#import-drop-zone');
  const btn = zone.locator('[data-template="direct"]');

  const frame = await capture(page, zone, { pad: 12 });
  const btnRect = await rectOf(btn);
  const gapLeft = measureGap(frame, btnRect, isBluishStroke, 'left');
  expect(gapLeft).toBeGreaterThanOrEqual(5);
  expect(gapLeft).toBeLessThanOrEqual(11);

  // INJECT BREAK: scale the SVG viewBox so the frame renders away from the
  // button. The path `d` stays correct (the old test stayed green); pixels move.
  await page.evaluate(() => {
    const svg = document.querySelector('#import-drop-zone .drop-zone-frame');
    const vb = svg.getAttribute('viewBox').split(' ').map(Number);
    svg.setAttribute('viewBox', `0 0 ${vb[2] + 60} ${vb[3] + 60}`);
  });
  await page.waitForTimeout(60);
  const frame2 = await capture(page, zone, { pad: 12 });
  const gapBroken = measureGap(frame2, await rectOf(btn), isBluishStroke, 'left');
  expect(gapBroken).toBe(Infinity);
});

test('detectClipping: clean button is visible; off-screen / overflow is caught', async ({ page }) => {
  await setup(page);
  const btn = page.locator('#import-drop-zone [data-template="direct"]');
  const clean = await detectClipping(page, btn);
  expect(clean.clipped).toBe(false);
  expect(clean.visibleRatio).toBeGreaterThanOrEqual(0.99);

  // INJECT BREAK: wrap the drop-zone in a tiny overflow:hidden container so the
  // button (which sits near the zone bottom) is pushed entirely off-screen of
  // the wrapper, making it clipped.
  await page.evaluate(() => {
    const z = document.getElementById('import-drop-zone');
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

test('measureAlignment: aligned edges ~0; nudged edge detected', async ({ page }) => {
  await setup(page);
  const a = { x: 100, y: 0, width: 50, height: 10 };
  const b = { x: 100, y: 20, width: 50, height: 10 };
  expect(Math.abs(measureAlignment(a, b, 'left'))).toBeLessThanOrEqual(0.5);
  const bn = { ...b, x: 107 };
  expect(measureAlignment(a, bn, 'left')).toBeCloseTo(-7, 0);
});
