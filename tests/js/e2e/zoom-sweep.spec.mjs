// @ts-check
/* Sweeping zoom coverage: every zoom level x every supported viewport must render
   the chrome and the grid without clipping text, without a page scrollbar, with
   every header control on screen, and with the panel row exactly filling the
   space under the header. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import {
  setZoom, expectZoomApplied, expectNoClipping, expectNoNewClipping, collectClipping,
  expectNoPageScrollbars, expectHeaderControlsOnScreen,
} from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

const ZOOMS = [50, 67, 80, 100, 125, 150, 200];
const VIEWPORTS = [
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1600, height: 900 },
  { width: 1920, height: 1080 },
];

/* Chrome that must be fully legible at every zoom level — this is the text the
   feature exists to make readable, so the rule here is absolute. */
const STRICT_REGIONS = [
  ['.header', 'header'],
  ['#panel-import .panel-header', 'import panel header'],
  ['#panel-inventory .panel-header', 'inventory panel header'],
  ['#panel-bom .panel-header', 'bom panel header'],
];

/* The grid truncates some columns by design at every zoom (the part-number spans
   are overflow:hidden with a hover card as the recovery affordance), so the rule
   here is differential: zoom must not clip anything the 100% baseline didn't. */
const DIFF_REGIONS = [
  ['#inventory-body', 'inventory grid'],
];

test.describe('Zoom sweep — no clipped text at any zoom or viewport', () => {
  for (const vp of VIEWPORTS) {
    for (const zoom of ZOOMS) {
      test(`${vp.width}x${vp.height} @ ${zoom}%`, async ({ page }) => {
        await installRouteMocks(page, MOCK_INVENTORY);
        await page.setViewportSize(vp);
        await page.goto('/index.html');
        await waitForInventoryRows(page);
        const label = `${vp.width}x${vp.height} @ ${zoom}%`;

        // Baseline the differential regions at 100% in this same viewport, so the
        // comparison isolates zoom rather than viewport width. Go through
        // setZoom(100) rather than measuring the pristine page, so the baseline is
        // captured in the same settled render state as the comparison.
        await setZoom(page, 100);
        const baselines = {};
        for (const [sel] of DIFF_REGIONS) baselines[sel] = await collectClipping(page, sel);

        await setZoom(page, zoom);

        await expectZoomApplied(page, zoom);
        await expectNoPageScrollbars(page, label);
        await expectHeaderControlsOnScreen(page, label);
        for (const [sel, name] of STRICT_REGIONS) {
          await expectNoClipping(page, sel, `${label} — ${name}`);
        }
        for (const [sel, name] of DIFF_REGIONS) {
          await expectNoNewClipping(page, sel, baselines[sel], `${label} — ${name}`);
        }

        // The panel row must exactly fill the space under the header: no gap
        // below it, no overflow past the bottom. This is what catches a
        // viewport-unit regression, which zoom makes very easy to introduce.
        const geom = await page.evaluate(() => {
          const h = document.querySelector('.header').getBoundingClientRect();
          const p = document.querySelector('.panels').getBoundingClientRect();
          return {
            headerBottom: h.bottom, panelTop: p.top, panelBottom: p.bottom,
            windowH: window.innerHeight,
          };
        });
        expect(Math.abs(geom.panelTop - geom.headerBottom),
          `${label}: panels should start at the header's bottom edge`).toBeLessThanOrEqual(1);
        expect(Math.abs(geom.panelBottom - geom.windowH),
          `${label}: panels should end at the window's bottom edge`).toBeLessThanOrEqual(1);
      });
    }
  }
});

test.describe('Zoom sweep — zooming out actually reveals more', () => {
  test('the inventory grid gains usable width as zoom decreases', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const widthAt = async (z) => {
      await setZoom(page, z);
      return page.evaluate(() => document.getElementById('inventory-body').clientWidth);
    };

    const at100 = await widthAt(100);
    const at80 = await widthAt(80);
    const at50 = await widthAt(50);

    // This is the entire point of the feature: zooming out buys CSS pixels, so a
    // truncated column has more room to show what it says.
    expect(at80, 'inventory body should be wider in CSS px at 80%').toBeGreaterThan(at100);
    expect(at50, 'inventory body should be wider still at 50%').toBeGreaterThan(at80);
  });

  test('zooming in shrinks usable width symmetrically', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const at100 = await page.evaluate(() => document.getElementById('inventory-body').clientWidth);
    await setZoom(page, 150);
    const at150 = await page.evaluate(() => document.getElementById('inventory-body').clientWidth);
    expect(at150, 'inventory body should be narrower in CSS px at 150%').toBeLessThan(at100);
  });
});

test.describe('Zoom sweep — modal viewport caps compensate for zoom', () => {
  // Regression test for the vh/vw trap: viewport units resolve against the
  // viewport and are *then* scaled by the root zoom, so a raw `max-height: 80vh`
  // becomes 160% of the window at 200% and the modal's buttons go off-screen.
  for (const zoom of [50, 200]) {
    test(`preferences modal stays within the window @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);

      await page.click('#prefs-btn');
      const modal = page.locator('#prefs-modal .modal');
      await expect(modal).toBeVisible();

      const box = await page.evaluate(() => {
        const r = document.querySelector('#prefs-modal .modal').getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
          vw: window.innerWidth, vh: window.innerHeight };
      });
      expect(box.bottom, `@${zoom}%: modal bottom past window`).toBeLessThanOrEqual(box.vh + 1);
      expect(box.top, `@${zoom}%: modal top above window`).toBeGreaterThanOrEqual(-1);
      expect(box.right, `@${zoom}%: modal right past window`).toBeLessThanOrEqual(box.vw + 1);
      expect(box.left, `@${zoom}%: modal left before window`).toBeGreaterThanOrEqual(-1);

      // The Save button is what a too-tall modal actually costs you. It may sit
      // below the modal's own scroll position at high zoom — that is fine — but it
      // must be reachable by scrolling and land inside the window when it is.
      // Before the modal was made scrollable, its content spilled outside a
      // clamped box on an overflow:hidden page, so this could not be reached.
      const save = page.locator('#prefs-save');
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport();
    });
  }
});
