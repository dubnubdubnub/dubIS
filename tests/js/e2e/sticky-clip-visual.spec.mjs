// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBom } from './helpers.mjs';
import { detectClipping } from './visual/measure.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Selector for the per-row action buttons in the inventory panel (base mode).
// Discovered from resize-visibility.spec.mjs section 11: checkRowButtonsNotClipped uses
// '.adj-btn, .link-btn' inside '.inv-part-row'.  We focus on .adj-btn which is always
// present in base state (no BOM needed).
const ACTION_BTN_SEL = '.inv-part-row .adj-btn';

// Header button selectors from resize-visibility.spec.mjs section 1 (header elements).
const HEADER_BTN_SELS = [
  '#prefs-btn',
  '#global-undo',
  '#global-redo',
  '#inv-count',
];

// Scroll target discovered from sticky-buttons.spec.mjs: `#inventory-body .table-wrap`
// scrolled via `el.scrollLeft = 200`.  The BOM table (.table-wrap) is only present after
// loading a BOM, so this test loads one.  However, the action-button clipping checks here
// are for the plain inventory rows which don't use table-wrap.  For the scroll test we
// replicate the sticky-buttons approach: load BOM to get the scrollable table, then check
// td.btn-group (the BOM sticky cell).
const SCROLL_TARGET_SEL = '#inventory-body .table-wrap';
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

/**
 * Boot the app at the given viewport, waiting for inventory rows.
 * @param {import('@playwright/test').Page} page
 * @param {number} w
 * @param {number} h
 */
async function boot(page, w, h) {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: w, height: h });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(150);
}

// ════════════════════════════════════════════════════════════
// 1. Action buttons not clipped at standard widths (base state)
// ════════════════════════════════════════════════════════════

test.describe('Action buttons — clipping/occlusion at standard widths', () => {
  for (const width of [1024, 1280, 1600]) {
    test(`action buttons not clipped at ${width}px`, async ({ page }) => {
      await boot(page, width, 700);

      const allBtns = page.locator(ACTION_BTN_SEL);
      const count = await allBtns.count();
      expect(count, `Expected at least one ${ACTION_BTN_SEL} at ${width}px`).toBeGreaterThan(0);

      // Check the first row's button (representative — all rows share same layout)
      const btn = allBtns.first();
      const c = await detectClipping(page, btn);
      expect(
        c.clipped,
        `Action button clipped at ${width}px — reason: "${c.reason}", visibleRatio: ${c.visibleRatio}`,
      ).toBe(false);
      expect(
        c.occluded,
        `Action button occluded at ${width}px — reason: "${c.reason}", visibleRatio: ${c.visibleRatio}`,
      ).toBe(false);

      // Also check the second button if it exists (defensive: some rows may have link btn)
      if (count > 1) {
        const btn2 = allBtns.nth(1);
        const c2 = await detectClipping(page, btn2);
        expect(
          c2.clipped,
          `Action button[1] clipped at ${width}px — reason: "${c2.reason}", visibleRatio: ${c2.visibleRatio}`,
        ).toBe(false);
      }

      console.log(`[${width}px] Checked ${count} action btn(s). visibleRatio=${c.visibleRatio}, reason="${c.reason}"`);
    });
  }
});

// ════════════════════════════════════════════════════════════
// 2. BOM sticky btn-group not clipped after horizontal scroll
// ════════════════════════════════════════════════════════════

test.describe('BOM sticky button — not clipped after horizontal scroll', () => {
  test('td.btn-group remains unclipped after scrolling table-wrap right', async ({ page }) => {
    // Use 1100px — the width sticky-buttons.spec.mjs uses to force horizontal overflow.
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    // Load BOM to produce the scrollable table with td.btn-group cells
    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const tableWrap = page.locator(SCROLL_TARGET_SEL);
    await expect(tableWrap, 'table-wrap must exist after BOM load').toHaveCount(1);

    // Scroll all the way right (same pattern as sticky-buttons.spec.mjs)
    const scrollLeft = await tableWrap.evaluate((el) => {
      el.scrollLeft = el.scrollWidth;
      return el.scrollLeft;
    });
    await page.waitForTimeout(100);

    expect(scrollLeft, 'table-wrap must have scrolled (overflow must exist at 1100px)').toBeGreaterThan(0);

    // Check the first visible td.btn-group
    const btnCell = page.locator('td.btn-group').first();
    const c = await detectClipping(page, btnCell);
    console.log(`[scroll test] td.btn-group after scroll — visibleRatio=${c.visibleRatio}, reason="${c.reason}"`);
    expect(
      c.clipped,
      `td.btn-group clipped after horizontal scroll — reason: "${c.reason}", visibleRatio: ${c.visibleRatio}`,
    ).toBe(false);
  });
});

// ════════════════════════════════════════════════════════════
// 3. Header buttons — clip sweep
// ════════════════════════════════════════════════════════════

test.describe('Header buttons — clipping sweep', () => {
  test('header buttons not clipped at 1024px', async ({ page }) => {
    await boot(page, 1024, 700);

    for (const sel of HEADER_BTN_SELS) {
      const el = page.locator(sel);
      const exists = await el.count();
      if (exists === 0) {
        console.log(`[header clip] ${sel} — not present in DOM, skipping`);
        continue;
      }
      const c = await detectClipping(page, el.first());
      console.log(`[header clip] ${sel} — visibleRatio=${c.visibleRatio}, reason="${c.reason}"`);
      expect(
        c.clipped,
        `${sel} is clipped at 1024px — reason: "${c.reason}", visibleRatio: ${c.visibleRatio}`,
      ).toBe(false);
    }
  });

  test('header buttons not clipped at 1280px', async ({ page }) => {
    await boot(page, 1280, 700);

    for (const sel of HEADER_BTN_SELS) {
      const el = page.locator(sel);
      const exists = await el.count();
      if (exists === 0) {
        console.log(`[header clip] ${sel} — not present in DOM, skipping`);
        continue;
      }
      const c = await detectClipping(page, el.first());
      console.log(`[header clip] ${sel} — visibleRatio=${c.visibleRatio}, reason="${c.reason}"`);
      expect(
        c.clipped,
        `${sel} is clipped at 1280px — reason: "${c.reason}", visibleRatio: ${c.visibleRatio}`,
      ).toBe(false);
    }
  });
});
