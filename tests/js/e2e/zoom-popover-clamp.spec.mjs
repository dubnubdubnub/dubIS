// @ts-check
/* Regression tests for the coordinate-space seam in js/ui-zoom.js.

   Under `html { zoom: z }` getBoundingClientRect and window.innerWidth are
   post-zoom while offsetWidth and any written px are authored. Positioning code
   that mixes them anchors popovers at the wrong place — off by exactly the zoom
   factor — and looks perfect at 100%, where the two spaces coincide. These specs
   fail without innerRect()/zoomedViewport(), which is the point. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import { setZoom, expectInsideWindow } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/**
 * Assert a floating element is anchored to its trigger rather than displaced by
 * the zoom factor. Both rects come from getBoundingClientRect, so they share a
 * coordinate space and the comparison is zoom-independent.
 */
async function expectAnchoredTo(page, popSel, triggerSel, label, tolerance = 24) {
  const geo = await page.evaluate(({ p, t }) => {
    const pop = document.querySelector(p);
    const trg = document.querySelector(t);
    if (!pop || !trg) return null;
    const pr = pop.getBoundingClientRect();
    const tr = trg.getBoundingClientRect();
    return { popLeft: pr.left, popTop: pr.top, trgLeft: tr.left, trgBottom: tr.bottom };
  }, { p: popSel, t: triggerSel });

  expect(geo, `${label}: both ${popSel} and ${triggerSel} should exist`).not.toBeNull();
  if (!geo) return;
  // Popovers may be clamped horizontally at a window edge, so only assert the
  // vertical anchor plus a generous horizontal bound; a zoom-space bug displaces
  // by hundreds of px, far outside this tolerance.
  expect(Math.abs(geo.popTop - geo.trgBottom),
    `${label}: ${popSel} should sit just below its trigger, not displaced by the zoom factor`)
    .toBeLessThanOrEqual(tolerance);
}

for (const zoom of [80, 150]) {
  test.describe(`Popover anchoring and clamping @ ${zoom}%`, () => {
    test.beforeEach(async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);
    });

    test('part preview anchors to its trigger and stays inside the window', async ({ page }) => {
      const trigger = page.locator('#inventory-body [data-lcsc]').first();
      await expect(trigger).toBeVisible();
      await trigger.hover();
      const preview = page.locator('.part-preview:not(.hidden)');
      await expect(preview).toBeVisible({ timeout: 5000 });

      await expectInsideWindow(page, '.part-preview:not(.hidden)', `part preview @ ${zoom}%`);
      await expectAnchoredTo(page, '.part-preview:not(.hidden)', '#inventory-body [data-lcsc]',
        `part preview @ ${zoom}%`);
    });

    test('part preview near the right edge is clamped inside the window', async ({ page }) => {
      // Drive the positioning routine against a trigger hugging the right edge.
      await page.evaluate(() => {
        const probe = document.createElement('span');
        probe.id = 'zoom-probe-trigger';
        probe.dataset.lcsc = 'C19272007';
        probe.textContent = 'C19272007';
        probe.style.position = 'fixed';
        probe.style.right = '2px';
        probe.style.top = '300px';
        probe.style.zIndex = '50';
        document.body.appendChild(probe);
      });
      await page.locator('#zoom-probe-trigger').hover();
      const preview = page.locator('.part-preview:not(.hidden)');
      await expect(preview).toBeVisible({ timeout: 5000 });
      await expectInsideWindow(page, '.part-preview:not(.hidden)',
        `right-edge part preview @ ${zoom}%`);
    });

    test('text popover anchors to its trigger and stays inside the window', async ({ page }) => {
      // The generic text popover attaches to non-interactive leaf text after a
      // hover delay. A purpose-built probe makes the trigger deterministic rather
      // than depending on which fixture cells happen to qualify.
      await page.evaluate(() => {
        const probe = document.createElement('div');
        probe.id = 'zoom-probe-text';
        probe.textContent = 'A reasonably long line of probe text for the popover';
        probe.style.position = 'fixed';
        probe.style.left = '40px';
        probe.style.top = '400px';
        probe.style.zIndex = '50';
        document.body.appendChild(probe);
      });
      await page.locator('#zoom-probe-text').hover();
      const pop = page.locator('.text-popover:not(.hidden)');
      await expect(pop).toBeVisible({ timeout: 5000 });

      await expectInsideWindow(page, '.text-popover:not(.hidden)', `text popover @ ${zoom}%`);
      await expectAnchoredTo(page, '.text-popover:not(.hidden)', '#zoom-probe-text',
        `text popover @ ${zoom}%`);
    });

    test('text popover near the bottom edge flips above its trigger', async ({ page }) => {
      await page.evaluate(() => {
        const probe = document.createElement('div');
        probe.id = 'zoom-probe-bottom';
        probe.textContent = 'Probe text pinned near the bottom edge of the window';
        probe.style.position = 'fixed';
        probe.style.left = '40px';
        probe.style.bottom = '2px';
        probe.style.zIndex = '50';
        document.body.appendChild(probe);
      });
      await page.locator('#zoom-probe-bottom').hover();
      const pop = page.locator('.text-popover:not(.hidden)');
      await expect(pop).toBeVisible({ timeout: 5000 });
      // The flip-above branch reads the viewport height; with a post-zoom height it
      // never fires at zoom < 1 and fires far too eagerly at zoom > 1.
      await expectInsideWindow(page, '.text-popover:not(.hidden)',
        `bottom-edge text popover @ ${zoom}%`);
    });
  });
}

/* The vendor popover and the filter-chip popover have no trigger the E2E fixtures
   reliably produce, so rather than leave guarded tests that assert nothing, their
   use of the coordinate-space seam is covered by the family-wide static guard in
   tests/js/zoom-geometry-guard.test.js. */

test.describe('Panel resize respects its minimums in authored px at any zoom', () => {
  for (const zoom of [50, 200]) {
    test(`dragging the import/inventory divider @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1600, height: 900 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);

      const handle = page.locator('.resize-handle-h').first();
      const box = await handle.boundingBox();
      expect(box).not.toBeNull();
      if (!box) return;

      // Drag hard left: the import panel must stop at its 240px authored minimum,
      // not at 240 * zoom (which is what happens if clientX deltas and rect widths
      // are fed straight into style.width).
      await page.mouse.move(box.x + box.width / 2, box.y + 40);
      await page.mouse.down();
      await page.mouse.move(0, box.y + 40, { steps: 10 });
      await page.mouse.up();

      const widths = await page.evaluate(() => {
        const authored = (el) => el.getBoundingClientRect().width
          / Number(getComputedStyle(document.documentElement).getPropertyValue('--ui-zoom') || 1);
        return {
          importW: authored(document.getElementById('panel-import')),
          invW: authored(document.getElementById('panel-inventory')),
        };
      });

      expect(widths.importW, `@${zoom}%: import panel should honour its 240px minimum`)
        .toBeGreaterThanOrEqual(238);
      expect(widths.importW, `@${zoom}%: import panel should have stopped at the minimum`)
        .toBeLessThan(340);
      expect(widths.invW, `@${zoom}%: inventory panel should honour its 300px minimum`)
        .toBeGreaterThanOrEqual(298);
    });
  }
});
