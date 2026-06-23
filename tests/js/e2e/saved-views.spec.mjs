// @ts-check
/**
 * E2E tests for the Saved Views feature.
 * Tests: set distributor filter + search, save a view, clear filters, re-apply, assert restored.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

// Screenshot destination for the open dropdown (throwaway visual check)
const SCREENSHOT_PATH = 'C:/Users/isaac/AppData/Local/Temp/claude/D--gehub-dubIS/915009e0-4141-437d-821d-d43e35d13964/scratchpad/shot-savedviews.png';

test.describe('Saved Views', () => {

  test('Views button is visible in the inventory toolbar', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const btn = page.locator('#saved-views-btn');
    await expect(btn).toBeVisible();
    await expect(btn).toContainText('Views');
  });

  test('clicking Views button opens the dropdown menu', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#saved-views-btn').click();
    const menu = page.locator('.saved-views-menu');
    await expect(menu).toBeVisible();

    // Should show "Save current view…" item
    await expect(menu.locator('[data-action="save-view"]')).toBeVisible();
  });

  test('dropdown shows empty state message when no views saved', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#saved-views-btn').click();
    const menu = page.locator('.saved-views-menu');
    await expect(menu.locator('.sv-empty-msg')).toBeVisible();
  });

  test('clicking outside the menu closes it', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#saved-views-btn').click();
    await expect(page.locator('.saved-views-menu')).toBeVisible();

    // Click somewhere outside the menu
    await page.locator('#inventory-body').click({ position: { x: 400, y: 200 } });
    await expect(page.locator('.saved-views-menu')).not.toBeVisible();
  });

  test('Esc closes the open dropdown', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#saved-views-btn').click();
    await expect(page.locator('.saved-views-menu')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('.saved-views-menu')).not.toBeVisible();
  });

  test('save a view, clear filters, re-apply view restores filter state', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Step 1: Set a distributor filter (click LCSC)
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).toHaveClass(/active/);

    // Step 2: Type a search term
    await page.locator('#inv-search').fill('res');
    await page.waitForTimeout(300);

    // Record current row count with filter+search active
    const filteredCount = await page.locator('.inv-part-row').count();

    // Step 3: Open Views menu and click "Save current view…"
    await page.locator('#saved-views-btn').click();
    await page.locator('[data-action="save-view"]').click();

    // Step 4: A form modal should appear with a name field
    const modal = page.locator('#sv-name-modal');
    await expect(modal).toBeVisible();

    // Step 5: Type a name and confirm
    await modal.locator('[data-field="name"]').fill('LCSC resistors');
    await modal.locator('.form-modal-confirm').click();
    await page.waitForTimeout(300);

    // Modal should close
    await expect(modal).not.toBeVisible();

    // Step 6: Clear all filters
    await page.locator('#clear-dist-filter').click();
    await page.waitForTimeout(200);

    // Clear search
    await page.locator('#inv-search').fill('');
    await page.waitForTimeout(300);

    // Verify filters are cleared
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).not.toHaveClass(/active/);
    const unfiltered = await page.locator('.inv-part-row').count();
    expect(unfiltered).toBeGreaterThan(filteredCount);

    // Step 7: Open Views menu and apply the saved view
    await page.locator('#saved-views-btn').click();
    const menu = page.locator('.saved-views-menu');
    await expect(menu).toBeVisible();

    // The saved view should appear in the menu
    const viewItem = menu.locator('.sv-view-name').filter({ hasText: 'LCSC resistors' });
    await expect(viewItem).toBeVisible();

    // Click the view name to apply
    await viewItem.click();
    await page.waitForTimeout(300);

    // Step 8: Assert filter + search are restored
    await expect(page.locator('.dist-filter-btn[data-distributor="lcsc"]')).toHaveClass(/active/);
    const searchValue = await page.locator('#inv-search').inputValue();
    expect(searchValue).toBe('res');

    // Row count should match the earlier filtered count
    const restoredCount = await page.locator('.inv-part-row').count();
    expect(restoredCount).toBe(filteredCount);
  });

  test('saved view can be deleted', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Save a view
    await page.locator('#saved-views-btn').click();
    await page.locator('[data-action="save-view"]').click();
    await page.locator('#sv-name-modal [data-field="name"]').fill('Temp view');
    await page.locator('#sv-name-modal .form-modal-confirm').click();
    await page.waitForTimeout(300);

    // Open menu, confirm view is there, then delete it
    await page.locator('#saved-views-btn').click();
    await expect(page.locator('.sv-view-name').filter({ hasText: 'Temp view' })).toBeVisible();

    // Click the delete button for this view (the menu re-opens after delete)
    const deleteBtn = page.locator('.sv-delete-btn').first();
    await deleteBtn.click();
    await page.waitForTimeout(300);

    // After delete the menu re-opens showing empty state (no saved views)
    const menu = page.locator('.saved-views-menu');
    await expect(menu).toBeVisible();
    await expect(menu.locator('.sv-empty-msg')).toBeVisible();
    await expect(menu.locator('.sv-view-name').filter({ hasText: 'Temp view' })).not.toBeVisible();
  });

  test('screenshot: open Views dropdown for visual check', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Pre-save a view so the menu shows a non-empty list
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(150);
    await page.locator('#saved-views-btn').click();
    await page.locator('[data-action="save-view"]').click();
    await page.locator('#sv-name-modal [data-field="name"]').fill('LCSC filter');
    await page.locator('#sv-name-modal .form-modal-confirm').click();
    await page.waitForTimeout(300);

    // Open the dropdown
    await page.locator('#saved-views-btn').click();
    await expect(page.locator('.saved-views-menu')).toBeVisible();

    // Capture screenshot to the exact scratchpad path
    await page.screenshot({ path: SCREENSHOT_PATH });
  });

});
