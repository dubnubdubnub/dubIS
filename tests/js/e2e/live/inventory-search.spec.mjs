// @ts-check
import { test, expect } from '@playwright/test';
import { resetServer, setupPage } from './setup-page.mjs';
import { waitForInventoryRows } from '../helpers.mjs';

test.describe('Inventory search', () => {
  test.beforeEach(async ({ page }) => {
    await resetServer();
    await setupPage(page);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('search by LCSC part number filters to that part', async ({ page }) => {
    const allRows = await page.locator('.inv-part-row').count();
    expect(allRows).toBeGreaterThan(1);

    await page.fill('#inv-search', 'C25794');
    // Wait for debounce + re-render
    await page.waitForTimeout(300);

    const filteredRows = await page.locator('.inv-part-row').count();
    expect(filteredRows).toBeLessThan(allRows);
    await expect(page.locator('[data-lcsc="C25794"]')).toBeVisible();
  });

  test('search by description keyword finds matching parts', async ({ page }) => {
    await page.fill('#inv-search', 'Resistor');
    await page.waitForTimeout(300);

    const visibleRows = await page.locator('.inv-part-row').count();
    // C440198, C1567, C1554 all have "Resistor" in description
    expect(visibleRows).toBeGreaterThanOrEqual(3);
  });

  test('search by MPN finds part without LCSC', async ({ page }) => {
    await page.fill('#inv-search', 'USB5744');
    await page.waitForTimeout(300);

    const visibleRows = await page.locator('.inv-part-row').count();
    expect(visibleRows).toBeGreaterThanOrEqual(1);
  });

  test('clear search restores all rows', async ({ page }) => {
    const allRows = await page.locator('.inv-part-row').count();
    expect(allRows).toBeGreaterThan(1);

    // Filter down
    await page.fill('#inv-search', 'C25794');
    await page.waitForTimeout(300);
    const filteredRows = await page.locator('.inv-part-row').count();
    expect(filteredRows).toBeLessThan(allRows);

    // Clear search
    await page.fill('#inv-search', '');
    await page.waitForTimeout(300);

    const restoredRows = await page.locator('.inv-part-row').count();
    expect(restoredRows).toBe(allRows);
  });

  test('no-match search shows no inventory rows', async ({ page }) => {
    await page.fill('#inv-search', 'ZZZZNONEXISTENT');
    await page.waitForTimeout(300);

    const visibleRows = await page.locator('.inv-part-row').count();
    expect(visibleRows).toBe(0);
  });
});
