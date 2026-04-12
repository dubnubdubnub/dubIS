// @ts-check
import { test, expect } from '@playwright/test';
import { startServer, addLiveSetup, waitForInventoryRows } from './live-helpers.mjs';

let server;

test.beforeAll(async () => {
  server = await startServer();
});

test.afterAll(async () => {
  await server.cleanup();
});

test.describe('Live-backend smoke tests', () => {

  test('inventory loads from real Python backend', async ({ page }) => {
    await addLiveSetup(page, server.url);
    await page.goto('http://localhost:3123/index.html');
    await waitForInventoryRows(page);

    const rowCount = await page.locator('.inv-part-row').count();
    expect(rowCount).toBeGreaterThanOrEqual(10);

    await expect(page.locator('[data-lcsc="C25794"]')).toBeVisible();
  });

  test('server reset restores original state', async ({ page }) => {
    await addLiveSetup(page, server.url);
    await page.goto('http://localhost:3123/index.html');
    await waitForInventoryRows(page);

    // Mutate: set C25794 quantity to 1
    await page.evaluate(() =>
      window.pywebview.api.adjust_part('set', 'C25794', 1, 'smoke test')
    );

    // Reset server to restore fixture data
    await server.reset();

    // Reload and verify original quantity is back
    await page.reload();
    await waitForInventoryRows(page);

    // The row containing C25794 — find the .inv-part-row that has [data-lcsc="C25794"]
    const qtyText = await page.locator('.inv-part-row:has([data-lcsc="C25794"]) .part-qty').textContent();
    expect(qtyText).toContain('500');
  });

});
