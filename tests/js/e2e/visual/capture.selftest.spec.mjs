// @ts-check
// Self-test for the capture core. Verifies the screenshot decodes and the
// coordinate mapping round-trips, using the import drop-zone as a known target.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from '../helpers.mjs';
import { capture, rectOf } from './capture.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'inventory.json'), 'utf8'),
);

test('capture decodes a frame and maps coordinates', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY, {});
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(200);

  const zone = page.locator('#import-drop-zone');
  await zone.scrollIntoViewIfNeeded();
  const frame = await capture(page, zone, { pad: 12 });

  expect(frame.png.width).toBeGreaterThan(0);
  expect(frame.png.height).toBeGreaterThan(0);
  const r = await rectOf(zone);
  const [ix, iy] = frame.toImg(r.x, r.y);
  const [cx, cy] = frame.toCss(ix, iy);
  expect(Math.abs(cx - r.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(cy - r.y)).toBeLessThanOrEqual(1);
  expect(frame.pixel(1, 1)).toHaveLength(4);
  expect(frame.pixel(-1, -1)).toBeNull();
});
