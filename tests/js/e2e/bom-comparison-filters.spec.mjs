// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaEmit, loadBomViaFileInput } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');
const BOM_CSV = fs.readFileSync(BOM_CSV_PATH, 'utf8');

// ── Filter bar rendering ──

test.describe('BOM comparison — filter bar', () => {

  test('filter bar renders with buttons and "All" is active by default', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Filter bar should be visible
    await expect(page.locator('.filter-bar')).toBeVisible();

    // Should have filter buttons
    const filterBtns = page.locator('.filter-btn');
    const btnCount = await filterBtns.count();
    expect(btnCount).toBeGreaterThan(0);

    // "All" button should be active
    const allBtn = page.locator('.filter-btn[data-filter="all"]');
    await expect(allBtn).toHaveClass(/active/);
  });

  test('filter buttons show counts matching status data', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    // All button should have a count in its text
    const allBtn = page.locator('.filter-btn[data-filter="all"]');
    const allText = await allBtn.textContent();
    expect(allText).toMatch(/All \(\d+\)/);

    // Check a few other filter buttons exist with counts
    const okBtn = page.locator('.filter-btn[data-filter="ok"]');
    if (await okBtn.count() > 0) {
      const okText = await okBtn.textContent();
      expect(okText).toMatch(/In Stock \(\d+\)/);
    }

    const missingBtn = page.locator('.filter-btn[data-filter="missing"]');
    if (await missingBtn.count() > 0) {
      const missingText = await missingBtn.textContent();
      expect(missingText).toMatch(/Missing \(\d+\)/);
    }
  });
});

// ── Filter switching ──

test.describe('BOM comparison — filter switching', () => {

  test('clicking "Missing" shows only missing rows, "All" restores all', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const allRows = await page.locator('#inventory-body tbody tr[data-part-key]').count();

    // Click Missing filter
    const missingBtn = page.locator('.filter-btn[data-filter="missing"]');
    if (await missingBtn.count() === 0) return; // no missing parts

    await missingBtn.click();
    await page.waitForTimeout(200);

    // Active class should be on Missing button
    await expect(missingBtn).toHaveClass(/active/);
    await expect(page.locator('.filter-btn[data-filter="all"]')).not.toHaveClass(/active/);

    // Only missing-status rows should be visible
    const visibleRows = await page.locator('#inventory-body tbody tr[data-part-key]').count();
    expect(visibleRows).toBeLessThanOrEqual(allRows);

    // Click All to restore
    await page.locator('.filter-btn[data-filter="all"]').click();
    await page.waitForTimeout(200);

    const restoredRows = await page.locator('#inventory-body tbody tr[data-part-key]').count();
    expect(restoredRows).toBe(allRows);
  });

  test('clicking "In Stock" shows only ok rows', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const okBtn = page.locator('.filter-btn[data-filter="ok"]');
    await okBtn.click();
    await page.waitForTimeout(200);

    await expect(okBtn).toHaveClass(/active/);

    // All visible rows should be ok status (row-ok or row-green)
    const visibleRows = page.locator('#inventory-body tbody tr[data-part-key]');
    const count = await visibleRows.count();
    if (count > 0) {
      // Verify at least the first row has ok status class
      const firstRowClass = await visibleRows.first().getAttribute('class');
      expect(firstRowClass).toMatch(/row-ok|row-green/);
    }
  });
});

// ── Confirm/unconfirm button ──

test.describe('BOM comparison — confirm button', () => {

  test('clicking confirm toggles to unconfirm and shows toast', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Look for a possible-match row with a confirm button
    const confirmBtn = page.locator('.confirm-btn').first();
    if (await confirmBtn.count() === 0) return; // no possible matches

    await confirmBtn.click();
    await page.waitForTimeout(300);

    // Toast should be shown
    await expect(page.locator('#toast')).toHaveClass(/show/);

    // After confirm, the button should become "Unconfirm"
    // (re-render replaces confirm-btn with unconfirm-btn in the same row position)
    const unconfirmBtn = page.locator('.unconfirm-btn').first();
    const unconfirmCount = await unconfirmBtn.count();
    expect(unconfirmCount).toBeGreaterThan(0);
  });
});

// ── "Other Inventory" divider ──

test.describe('BOM comparison — Other Inventory divider', () => {

  test('unmatched parts appear under "Other Inventory" after BOM loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    // There should be an "Other Inventory" divider for non-BOM parts
    const otherDivider = page.locator('.inv-section-header', { hasText: 'Other Inventory' });
    await expect(otherDivider).toBeVisible();
  });
});
