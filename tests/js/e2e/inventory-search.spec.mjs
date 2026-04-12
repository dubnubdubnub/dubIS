// @ts-check
import { test, expect } from '@playwright/test';
import { startServer, addLiveSetup, waitForInventoryRows } from './live-helpers.mjs';

test.describe('Inventory search', () => {
  let server;
  test.beforeAll(async () => { server = await startServer(); });
  test.afterAll(async () => { await server.cleanup(); });
  test.beforeEach(async ({ page }) => {
    await server.reset();
    await addLiveSetup(page, server.url);
    await page.goto('http://localhost:3123/index.html');
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
