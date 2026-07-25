// @ts-check
/* The sticky-button-column invariants from sticky-buttons.spec.mjs and
   resize-visibility.spec.mjs, re-run at other zoom levels.

   Those two specs are protected by CLAUDE.md — they may only get stricter, never
   weaker — so this file is strictly additive rather than a rewrite of them: they
   keep proving the invariants at 100%, and these cases extend the same individual
   *per-button* checks (not cell-level ones) across the zoom ladder, plus the
   collapsed-panel states that only exist now. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import {
  waitForInventoryRows, loadBom, loadBomViaEmit, loadPurchaseOrder,
} from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import { setZoom, expectNoPageScrollbars } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

const ZOOMS = [50, 80, 125, 200];

/**
 * Per-button clipping check, mirroring sticky-buttons.spec.mjs's individual-button
 * assertion — deliberately NOT a cell-level check, which can pass while the
 * buttons inside the cell are clipped.
 */
async function buttonClipIssues(page) {
  return page.evaluate(() => {
    const panelBody = document.getElementById('inventory-body');
    if (!panelBody) return ['panel body not found'];
    const bodyRect = panelBody.getBoundingClientRect();
    const rows = panelBody.querySelectorAll('tr[data-part-key]');
    const issues = [];
    const checked = Math.min(rows.length, 10);
    for (let i = 0; i < checked; i++) {
      const buttons = rows[i].querySelectorAll('td.btn-group button');
      for (const btn of buttons) {
        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) {
          issues.push(`row ${i}: ${btn.className} has zero dimensions`);
          continue;
        }
        const btnRect = btn.getBoundingClientRect();
        if (btnRect.right > bodyRect.right + 1) {
          issues.push(`row ${i}: ${btn.className} "${btn.textContent.trim()}" clipped right `
            + `(btn.right=${Math.round(btnRect.right)}, panel.right=${Math.round(bodyRect.right)})`);
        }
      }
    }
    return issues;
  });
}



test.describe('Sticky button column across the zoom ladder', () => {
  for (const zoom of ZOOMS) {
    test(`BOM action buttons are not clipped @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1100, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      const issues = await buttonClipIssues(page);
      expect(issues.length, `@${zoom}%: ` + issues.join('; ')).toBe(0);
    });

    test(`the button column header stays sticky @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1100, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      const th = page.locator('th.btn-group-hdr');
      await expect(th).toHaveCount(1);
      const position = await th.evaluate(el => getComputedStyle(el).position);
      expect(position, `@${zoom}%: the button column header must stay sticky`).toBe('sticky');
    });

    test(`buttons stay in the panel after a horizontal scroll @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1100, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      await page.evaluate(() => {
        const body = document.getElementById('inventory-body');
        if (body) body.scrollLeft = body.scrollWidth;
      });
      await page.waitForTimeout(200);

      const issues = await buttonClipIssues(page);
      expect(issues.length, `@${zoom}% after scroll: ` + issues.join('; ')).toBe(0);
    });
  }
});

test.describe('Sticky button column with a BOM and a PO loaded, across zoom', () => {
  for (const zoom of [80, 150]) {
    test(`buttons are not clipped with BOM + PO @ ${zoom}%`, async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, zoom);

      await loadPurchaseOrder(page, PO_CSV_PATH);
      await page.waitForTimeout(200);
      await loadBomViaEmit(page, BOM_CSV);
      await page.waitForTimeout(300);

      const issues = await buttonClipIssues(page);
      expect(issues.length, `@${zoom}% BOM+PO: ` + issues.join('; ')).toBe(0);
      await expectNoPageScrollbars(page, `BOM+PO @ ${zoom}%`);
    });
  }
});

test.describe('Sticky button column with panels collapsed', () => {
  test('collapsing the BOM panel does not clip the action buttons', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    await page.click('#panel-toggle-import');
    await page.waitForTimeout(200);

    const issues = await buttonClipIssues(page);
    expect(issues.length, 'import collapsed: ' + issues.join('; ')).toBe(0);
  });

  test('collapsed panels at 200% still keep the buttons inside the grid',
    async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await setZoom(page, 200);
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);
      await page.click('#panel-toggle-import');
      await page.waitForTimeout(200);

      const issues = await buttonClipIssues(page);
      expect(issues.length, 'collapsed @ 200%: ' + issues.join('; ')).toBe(0);
    });
});
