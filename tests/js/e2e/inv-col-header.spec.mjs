// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

/**
 * Adds a stateful preferences mock that round-trips inventory_view across reloads.
 * Must be called BEFORE addMockSetup so it can override its load/save methods.
 * Uses sessionStorage so the next page-load init script reads the saved state.
 */
async function addPersistentPrefsMock(page) {
  await page.addInitScript(() => {
    const STORAGE_KEY = '__test_prefs_inv_view';
    let prefs = { thresholds: {} };
    try {
      const stored = window.sessionStorage.getItem(STORAGE_KEY);
      if (stored) prefs = JSON.parse(stored);
    } catch (_) {}

    // Poll until addMockSetup installs window.pywebview.api, then override the prefs methods.
    const checkInterval = setInterval(() => {
      if (window.pywebview && window.pywebview.api) {
        clearInterval(checkInterval);
        window.pywebview.api.load_preferences = async () => prefs;
        window.pywebview.api.save_preferences = async (json) => {
          try {
            prefs = typeof json === 'string' ? JSON.parse(json) : json;
            window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
          } catch (_) {}
          return true;
        };
      }
    }, 5);
  });
}

test.describe('Inventory column header — sort/group/reset', () => {
  test.beforeEach(async ({ page }) => {
    await addPersistentPrefsMock(page);
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 800 });
  });

  test('column header is rendered and contains all expected cells', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const header = page.locator('.inv-col-header');
    await expect(header).toBeVisible();

    for (const col of ['group', 'partid', 'mpn', 'unit_price', 'value', 'qty', 'reset']) {
      await expect(page.locator(`.inv-col-cell[data-col="${col}"]`)).toBeVisible();
    }
  });

  test('Unit Price column is rendered on each part row', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const firstUnit = page.locator('.inv-part-row .part-unit-price').first();
    await expect(firstUnit).toBeVisible();
    const text = await firstUnit.innerText();
    expect(text).toMatch(/^\$|^—$/);
  });

  test('column header stays visible after scrolling', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.evaluate(() => {
      const body = document.getElementById('inventory-body');
      if (body) body.scrollTop = 500;
    });

    const header = page.locator('.inv-col-header');
    await expect(header).toBeVisible();
    const box = await header.boundingBox();
    expect(box).not.toBeNull();
    if (box) expect(box.y).toBeGreaterThanOrEqual(0);
  });

  test('Group toggle cycles 0 → 1 → 2 → 0 with correct section visibility', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const groupBtn = page.locator('.inv-col-cell[data-col="group"]');

    // Default: groupLevel=0 — section headers visible, no section chips on rows.
    await expect(page.locator('.inv-section-header').first()).toBeVisible();
    await expect(page.locator('.inv-section-chip')).toHaveCount(0);

    // Click 1 → groupLevel=1 (sections only): section headers still visible, no chips.
    await groupBtn.click();
    await expect(page.locator('.inv-section-header').first()).toBeVisible();
    await expect(page.locator('.inv-section-chip')).toHaveCount(0);

    // Click 2 → groupLevel=2 (flat): no section headers, chips shown on rows.
    await groupBtn.click();
    await expect(page.locator('.inv-section-header')).toHaveCount(0);
    await expect(page.locator('.inv-section-chip').first()).toBeVisible();

    // Click 3 → back to groupLevel=0: section headers return, chips gone.
    await groupBtn.click();
    await expect(page.locator('.inv-section-header').first()).toBeVisible();
    await expect(page.locator('.inv-section-chip')).toHaveCount(0);

    // Cleanup.
    await page.locator('.inv-col-cell[data-col="reset"]').click();
    await page.evaluate(() => window.sessionStorage.clear());
  });

  test('Group state persists across reload', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Advance to groupLevel=2 (flat mode).
    const groupBtn = page.locator('.inv-col-cell[data-col="group"]');
    await groupBtn.click(); // → 1
    await groupBtn.click(); // → 2
    await expect(page.locator('.inv-section-chip').first()).toBeVisible();

    // Reload — sessionStorage carries the saved prefs into the next load_preferences call.
    await page.reload();
    await waitForInventoryRows(page);
    await expect(page.locator('.inv-section-chip').first()).toBeVisible();
    await expect(page.locator('.inv-section-header')).toHaveCount(0);

    // Cleanup.
    await page.locator('.inv-col-cell[data-col="reset"]').click();
    await page.evaluate(() => window.sessionStorage.clear());
  });

  test('Qty column sort cycle reorders rows and eventually removes section headers', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const qtyBtn = page.locator('.inv-col-cell[data-col="qty"]');

    // Click 1 → sortScope="subsection" (first scope in cycle at groupLevel=0).
    // Section headers still visible.
    await qtyBtn.click();
    await expect(page.locator('.inv-section-header').first()).toBeVisible();

    // Verify the first visible rows are in non-increasing qty order.
    const qtyTexts = await page.locator('.inv-part-row .part-qty').allInnerTexts();
    const qtys = qtyTexts.map(t => parseInt((t || '0').replace(/[^\d-]/g, ''), 10) || 0);
    if (qtys.length >= 2) {
      expect(qtys[0]).toBeGreaterThanOrEqual(qtys[1]);
    }

    // Click 2 → sortScope="section": section headers still visible.
    await qtyBtn.click();
    await expect(page.locator('.inv-section-header').first()).toBeVisible();

    // Click 3 → sortScope="global": all parts flattened, no section headers.
    await qtyBtn.click();
    await expect(page.locator('.inv-section-header')).toHaveCount(0);

    // Click 4 → sortScope=null (off): section headers return.
    await qtyBtn.click();
    await expect(page.locator('.inv-section-header').first()).toBeVisible();

    // Cleanup.
    await page.locator('.inv-col-cell[data-col="reset"]').click();
    await page.evaluate(() => window.sessionStorage.clear());
  });

  test('Part # button creates vendor sub-headers', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.inv-col-cell[data-col="partid"]').click();
    const vendorHeaders = page.locator('.inv-vendor-header');
    await expect(vendorHeaders.first()).toBeVisible();

    // Cleanup.
    await page.locator('.inv-col-cell[data-col="reset"]').click();
    await page.evaluate(() => window.sessionStorage.clear());
  });

  test('Reset button clears sort and group level', async ({ page }) => {
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Apply a sort and change group level.
    await page.locator('.inv-col-cell[data-col="group"]').click();    // groupLevel=1
    await page.locator('.inv-col-cell[data-col="qty"]').click();      // sortColumn=qty, sortScope=section
    await expect(page.locator('.inv-col-sort-active')).toHaveCount(1);

    // Reset clears everything.
    await page.locator('.inv-col-cell[data-col="reset"]').click();
    await expect(page.locator('.inv-col-sort-active')).toHaveCount(0);

    // Group dots should reflect groupLevel=0 (●●).
    const dots = await page.locator('.inv-col-group-dots').innerText();
    expect(dots).toBe('●●');

    await page.evaluate(() => window.sessionStorage.clear());
  });
});
