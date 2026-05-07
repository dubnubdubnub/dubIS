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
  return 'direct';
}
const DIST_COUNTS = { lcsc: 0, digikey: 0, mouser: 0, pololu: 0, direct: 0 };
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

    const ICON_SRCS = {
      lcsc: 'data/lcsc-icon.ico',
      digikey: 'data/digikey-icon.png',
      mouser: 'data/mouser-icon.svg',
      pololu: 'data/pololu-icon.svg',
    };

    for (const dist of ['lcsc', 'digikey', 'mouser', 'pololu', 'direct']) {
      const btn = filterBar.locator(`.dist-filter-btn[data-distributor="${dist}"]`);
      await expect(btn, `${dist} filter button should be visible`).toBeVisible();

      // Each button should have a visible label and icon
      const label = btn.locator('.dist-label');
      await expect(label, `${dist} should have a text label`).toBeVisible();

      if (ICON_SRCS[dist]) {
        const icon = btn.locator('.vendor-icon');
        await expect(icon, `${dist} should have a vendor icon`).toBeVisible();
        const src = await icon.getAttribute('src');
        expect(src, `${dist} icon src`).toBe(ICON_SRCS[dist]);
      } else {
        // "direct" uses a text icon
        const directIcon = btn.locator('.dist-icon-direct');
        await expect(directIcon, `direct should have a text icon`).toBeVisible();
      }
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

  test('multi-select: clicking two distributors shows combined inventory', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Click LCSC
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);

    // Click Digikey too
    await page.locator('.dist-filter-btn[data-distributor="digikey"]').click();
    await page.waitForTimeout(200);

    // Both buttons should have .active class
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).toHaveClass(/active/);
    await expect(page.locator('.dist-filter-btn[data-distributor="digikey"]')).toHaveClass(/active/);

    // Row count should be LCSC + Digikey combined
    const rowCount = await page.locator('.inv-part-row').count();
    expect(rowCount).toBe(DIST_COUNTS.lcsc + DIST_COUNTS.digikey);

    // Every visible row should have either LCSC or Digikey part ID
    const rows = page.locator('.inv-part-row');
    for (let i = 0; i < Math.min(rowCount, 10); i++) {
      const hasLcsc = await rows.nth(i).locator('.part-id-lcsc').count();
      const hasDigikey = await rows.nth(i).locator('.part-id-digikey').count();
      expect(hasLcsc + hasDigikey, `row ${i} should have LCSC or Digikey part ID`).toBeGreaterThan(0);
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
      const btn = page.locator(`.dist-filter-btn[data-distributor="${dist}"] .dist-label`);
      const text = await btn.textContent();
      const label = dist.charAt(0).toUpperCase() + dist.slice(1);
      expect(text, `${dist} button should show count`).toBe(`${label} (${count})`);
    }
  });

  test('filter bar compact at narrow panel width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    // At 800px viewport, the inventory panel is narrow enough to trigger compact mode.
    // The ResizeObserver adds .compact when panel body width < 700px.
    await page.setViewportSize({ width: 800, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const filterBar = page.locator('#dist-filter-bar');
    await expect(filterBar).toHaveClass(/compact/);

    // Filter bar should still be in the DOM and have buttons
    const buttons = filterBar.locator('.dist-filter-btn');
    await expect(buttons).toHaveCount(5);

    // Text labels should be CSS-hidden in compact mode
    const firstLabel = filterBar.locator('.dist-label').first();
    await expect(firstLabel).toHaveCSS('display', 'none');
  });

  test('filter buttons not clipped at minimum window size (1200x700)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const header = await page.locator('.panel-inventory .panel-header').boundingBox();
    const searchGroup = await page.locator('.inv-search-group').boundingBox();

    // Search group should fit within the header
    expect(searchGroup.x + searchGroup.width, 'search group right edge within header').toBeLessThanOrEqual(header.x + header.width + 1);

    // Each filter button should be within the header bounds
    const buttons = page.locator('.dist-filter-btn');
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const box = await buttons.nth(i).boundingBox();
      expect(box, `filter button ${i} should be rendered`).toBeTruthy();
      expect(box.x + box.width, `filter button ${i} right edge within header`).toBeLessThanOrEqual(header.x + header.width + 1);
      expect(box.height, `filter button ${i} should have height`).toBeGreaterThan(0);
    }

    // Clear button should also be within bounds
    const clearBox = await page.locator('#clear-dist-filter').boundingBox();
    expect(clearBox, 'clear button should be rendered').toBeTruthy();
    expect(clearBox.x + clearBox.width, 'clear button right edge within header').toBeLessThanOrEqual(header.x + header.width + 1);
  });
});
