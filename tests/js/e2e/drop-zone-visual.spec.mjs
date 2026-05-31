// @ts-check
// Pixel-truth visual test for the import drop-zone dashed L-frame.
// Unlike the property-based test in mfg-direct.spec.mjs (which reads the SVG
// path `d` string and the button rect — both derived from the same
// getBoundingClientRect, so it can only verify internal consistency), this
// test compares ACTUAL RENDERED PIXELS against an approved baseline. It catches
// rendering-layer bugs (viewBox/scale errors, clipping by ancestor overflow,
// z-index occlusion, stale measurement) that the path string cannot reveal.
import { test } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { paddedClip, expectStrictScreenshot } from './visual-helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

test.describe('Drop-zone dashed frame — pixel truth', () => {
  for (const vp of [{ name: 'narrow', w: 1280, h: 720 }, { name: 'medium', w: 1600, h: 900 }]) {
    test(`dashed L-frame renders correctly @ ${vp.name}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY, {});
      await page.setViewportSize({ width: vp.w, height: vp.h });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await page.waitForTimeout(300); // fonts + ResizeObserver settle

      const z = page.locator('#import-drop-zone');
      await z.scrollIntoViewIfNeeded();
      // pad includes the inset:-2 stroke that sits outside the border box.
      const clip = await paddedClip(page, z, 12);
      await expectStrictScreenshot(page, `drop-zone-${vp.name}.png`, clip);
    });
  }
});
