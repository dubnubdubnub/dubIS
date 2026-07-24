// @ts-check
/* Pixel-truth checks for zoom, per docs/visual-testing.md: geometry assertions can
   pass while a control is visually occluded or a column header drifts off its body
   column. These use the baseline-free layer (visual/measure.mjs) rather than golden
   images, so they run identically on every CI platform — font metrics differ across
   ubuntu/macos/win11, and a committed PNG would only be valid on one of them. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBom } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import { setZoom } from './helpers/no-clip.mjs';
import { detectClipping, measureAlignment } from './visual/measure.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

const ZOOMS = [80, 100, 125];

async function boot(page, zoom, vp = { width: 1600, height: 900 }) {
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.setViewportSize(vp);
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await setZoom(page, zoom);
}

test.describe('Zoom — column headers stay aligned to their body columns', () => {
  for (const zoom of ZOOMS) {
    test(`header/body column edges align @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom);

      /* The column-header overlay is a separate element from the rows, so their
         edges aligning is independent layout truth, not a tautology. A zoom that
         rounded the two differently would show up here as drift. */
      const pairs = [
        ['.inv-col-partid', '.inv-part-row .part-ids'],
        ['.inv-col-mpn', '.inv-part-row .part-mpn'],
        ['.inv-col-qty', '.inv-part-row .part-qty'],
      ];

      for (const [headerSel, rowSel] of pairs) {
        const header = page.locator(headerSel).first();
        const row = page.locator(rowSel).first();
        if (await header.count() === 0 || await row.count() === 0) continue;

        const hBox = await header.boundingBox();
        const rBox = await row.boundingBox();
        if (!hBox || !rBox) continue;

        const drift = measureAlignment(hBox, rBox, 'left');
        expect(Math.abs(drift),
          `@${zoom}%: ${headerSel} drifted ${drift.toFixed(2)}px from ${rowSel}`)
          .toBeLessThanOrEqual(2);
      }
    });
  }
});

test.describe('Zoom — controls are not occluded', () => {
  for (const zoom of ZOOMS) {
    test(`the zoom control itself is hit-testable @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom);

      // A control the user cannot actually click is worse than one that is absent.
      for (const sel of ['#zoom-out', '#zoom-in', '#zoom-slider', '#zoom-percent']) {
        const res = await detectClipping(page, page.locator(sel));
        expect(res.occluded, `@${zoom}%: ${sel} is occluded — ${res.reason}`).toBe(false);
        expect(res.clipped, `@${zoom}%: ${sel} is clipped — ${res.reason}`).toBe(false);
      }
    });

    test(`the collapse pills actually receive a real click @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom);

      /* A trusted click, not a hit-test: `.resize-handle-h`'s z-index makes it a
         stacking context, and before that was accounted for, the pill was visible
         and correctly positioned while `.panel-header` silently swallowed every
         click. detectClipping's centre-point hit-test does NOT see that (verified
         by sabotaging the fix), so only a real click proves the control works. */
      for (const [sel, panelId] of [
        ['#panel-toggle-import', 'panel-import'],
        ['#panel-toggle-bom', 'panel-bom'],
      ]) {
        const before = await page.evaluate(
          (i) => document.getElementById(i).getBoundingClientRect().width, panelId);
        expect(before, `@${zoom}%: ${panelId} should start open`).toBeGreaterThan(0);

        // Times out with "subtree intercepts pointer events" if the pill is buried.
        await page.click(sel, { timeout: 5000 });
        expect(await page.evaluate(
          (i) => document.getElementById(i).getBoundingClientRect().width, panelId),
        `@${zoom}%: clicking ${sel} did not collapse ${panelId}`).toBe(0);

        await page.click(sel, { timeout: 5000 });
        expect(await page.evaluate(
          (i) => document.getElementById(i).getBoundingClientRect().width, panelId),
        `@${zoom}%: clicking ${sel} again did not reopen ${panelId}`).toBeGreaterThan(0);
      }
    });

    test(`both pills stay fully on-screen once collapsed @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom);
      await page.click('#panel-toggle-import');
      await page.click('#panel-toggle-bom');

      /* Complements the click test: with the panel at zero width its handle sits
         flush against the window edge, and a pill centred on that 5px handle hangs
         half off-screen. Its centre stays clickable, so only a pixel-level check
         sees it — which is the point of this file. */
      for (const sel of ['#panel-toggle-import', '#panel-toggle-bom']) {
        const r = await detectClipping(page, page.locator(sel));
        expect(r.clipped, `@${zoom}%: ${sel} is clipped — ${r.reason}`).toBe(false);
        expect(r.visibleRatio, `@${zoom}%: ${sel} is mostly hidden`).toBeGreaterThan(0.9);
      }
    });

    test(`the reopen pill is fully drawn after collapsing @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom);
      await page.click('#panel-toggle-import');
      const res = await detectClipping(page, page.locator('#panel-toggle-import'));
      expect(res.clipped,
        `@${zoom}%: the only way to reopen the panel is clipped — ${res.reason}`).toBe(false);
      expect(res.visibleRatio, `@${zoom}%: the reopen pill is mostly hidden`)
        .toBeGreaterThan(0.9);
    });
  }
});

test.describe('Zoom — BOM action buttons are not occluded', () => {
  for (const zoom of [80, 125]) {
    test(`row action buttons are hit-testable @ ${zoom}%`, async ({ page }) => {
      await boot(page, zoom, { width: 1280, height: 800 });
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      const btn = page.locator('td.btn-group button').first();
      if (await btn.count() === 0) return;
      const res = await detectClipping(page, btn);
      expect(res.occluded, `@${zoom}%: a row action button is occluded — ${res.reason}`)
        .toBe(false);
      expect(res.visibleRatio, `@${zoom}%: a row action button is mostly hidden`)
        .toBeGreaterThan(0.9);
    });
  }
});
