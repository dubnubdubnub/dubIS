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

// Pre-compute expected distributor counts using the same logic as inventory-logic.js
function inferDistributor(item) {
  if (item.lcsc) return 'lcsc';
  if (item.digikey) return 'digikey';
  if (item.mouser) return 'mouser';
  if (item.pololu) return 'pololu';
  return 'other';
}
const DIST_COUNTS = { lcsc: 0, digikey: 0, mouser: 0, pololu: 0, other: 0 };
for (const item of MOCK_INVENTORY) DIST_COUNTS[inferDistributor(item)]++;

test.describe('Distributor filter buttons', () => {

  test('filter buttons visible at default viewport', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const filterBar = page.locator('#dist-filter-bar');
    await expect(filterBar).toBeVisible();

    const buttons = filterBar.locator('.dist-filter-btn');
    await expect(buttons).toHaveCount(5);

    for (const dist of ['lcsc', 'digikey', 'mouser', 'pololu', 'other']) {
      const btn = filterBar.locator(`.dist-filter-btn[data-distributor="${dist}"]`);
      await expect(btn, `${dist} filter button should be visible`).toBeVisible();
    }

    const clearBtn = page.locator('#clear-dist-filter');
    await expect(clearBtn).toBeVisible();
    await expect(clearBtn).toBeDisabled();
  });

  test('clicking a filter button filters inventory to that distributor', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const totalBefore = await page.locator('.inv-part-row').count();
    expect(totalBefore).toBe(MOCK_INVENTORY.length);

    // Click the LCSC filter button
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);

    // Button should now have .active class
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).toHaveClass(/active/);

    // Count visible rows — should match LCSC count
    const rowCount = await page.locator('.inv-part-row').count();
    expect(rowCount).toBe(DIST_COUNTS.lcsc);

    // Every visible row should have an LCSC part ID span
    const rows = page.locator('.inv-part-row');
    for (let i = 0; i < Math.min(rowCount, 10); i++) {
      const hasLcsc = await rows.nth(i).locator('.part-id-lcsc').count();
      expect(hasLcsc, `row ${i} should have an LCSC part ID`).toBeGreaterThan(0);
    }
  });

  test('clicking Digikey filter shows only Digikey parts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.dist-filter-btn[data-distributor="digikey"]').click();
    await page.waitForTimeout(200);

    await expect(page.locator('.dist-filter-btn[data-distributor="digikey"]')).toHaveClass(/active/);

    const rowCount = await page.locator('.inv-part-row').count();
    expect(rowCount).toBe(DIST_COUNTS.digikey);

    // Every visible row should have a Digikey part ID span
    const rows = page.locator('.inv-part-row');
    for (let i = 0; i < rowCount; i++) {
      const hasDigikey = await rows.nth(i).locator('.part-id-digikey').count();
      expect(hasDigikey, `row ${i} should have a Digikey part ID`).toBeGreaterThan(0);
    }
  });

  test('clicking same button again deactivates filter and shows all parts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const totalBefore = await page.locator('.inv-part-row').count();

    // Click LCSC to activate
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);
    const filteredCount = await page.locator('.inv-part-row').count();
    expect(filteredCount).toBeLessThan(totalBefore);

    // Click LCSC again to deactivate
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);

    // Button should no longer have .active class
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).not.toHaveClass(/active/);

    // All parts should be shown again
    const totalAfter = await page.locator('.inv-part-row').count();
    expect(totalAfter).toBe(totalBefore);
  });

  test('Clear Filters button resets active filter', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const totalBefore = await page.locator('.inv-part-row').count();

    // Activate a filter
    await page.locator('.dist-filter-btn[data-distributor="digikey"]').click();
    await page.waitForTimeout(200);

    // Clear Filters button should now be enabled
    const clearBtn = page.locator('#clear-dist-filter');
    await expect(clearBtn).toBeEnabled();

    // Click Clear Filters
    await clearBtn.click();
    await page.waitForTimeout(200);

    // Filter should be reset
    await expect(clearBtn).toBeDisabled();
    await expect(page.locator('.dist-filter-btn[data-distributor="digikey"]')).not.toHaveClass(/active/);

    // All parts shown again
    const totalAfter = await page.locator('.inv-part-row').count();
    expect(totalAfter).toBe(totalBefore);
  });

  test('button text shows distributor counts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    for (const [dist, count] of Object.entries(DIST_COUNTS)) {
      const btn = page.locator(`.dist-filter-btn[data-distributor="${dist}"]`);
      const text = await btn.textContent();
      const label = dist.charAt(0).toUpperCase() + dist.slice(1);
      expect(text, `${dist} button should show count`).toBe(`${label} (${count})`);
    }
  });

  test('filter bar hidden at narrow panel width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    // At 800px viewport, the inventory panel is narrow enough to trigger the hide.
    // The ResizeObserver hides filters when panel body width < 700px.
    await page.setViewportSize({ width: 800, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const filterBar = page.locator('#dist-filter-bar');
    await expect(filterBar).toHaveClass(/hidden/);

    const clearBtn = page.locator('#clear-dist-filter');
    await expect(clearBtn).toHaveClass(/hidden/);
  });
});
