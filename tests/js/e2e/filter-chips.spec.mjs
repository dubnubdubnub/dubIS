// @ts-check
/**
 * E2E tests for composable filter chips on the inventory panel.
 *
 * Tests:
 *   - "+ Filter" button visible; clicking opens a popover
 *   - Add a chip (qty < N) → row count drops to matching subset
 *   - Edit operator/value via chip click → count changes
 *   - Remove chip (×) → full list restored
 *   - AND logic with distributor pills + search box
 *   - Compose with saved views (save view with chip, clear, re-apply, chip restored)
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

// Scratchpad PNG for visual check
const SCREENSHOT_PATH = 'C:/Users/isaac/AppData/Local/Temp/claude/D--gehub-dubIS/915009e0-4141-437d-821d-d43e35d13964/scratchpad/shot-filterchips.png';

// Helper: compute total row count without any filters
const TOTAL_ROWS = MOCK_INVENTORY.length;

// Helper: compute text used by filterByQuery for a row (mirrors inventory-logic.js)
function searchText(r) {
  return [r.lcsc, r.mpn, r.description, r.manufacturer, r.package, r.digikey, r.pololu, r.mouser]
    .join(' ').toLowerCase();
}

// Helper: count rows in the fixture that would pass qty < threshold
function rowsWithQtyLessThan(threshold) {
  return MOCK_INVENTORY.filter((r) => r.qty < threshold).length;
}

// Helper: count rows that have lcsc PN
function rowsWithLCSC() {
  return MOCK_INVENTORY.filter((r) => r.lcsc).length;
}

// Helper: count rows that have lcsc AND qty < threshold
function rowsWithLCSCAndQtyLessThan(threshold) {
  return MOCK_INVENTORY.filter((r) => r.lcsc && r.qty < threshold).length;
}

// Helper: count rows matching qty < threshold AND search term
function rowsWithQtyLessThanAndSearch(threshold, term) {
  return MOCK_INVENTORY.filter((r) => r.qty < threshold && searchText(r).includes(term.toLowerCase())).length;
}

test.describe('Filter chips bar', () => {

  test('filter chips bar is rendered in the inventory panel header', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const bar = page.locator('#filter-chips-bar');
    await expect(bar).toBeAttached();
  });

  test('"+ Filter" button is visible and labelled', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const addBtn = page.locator('#fc-add-filter-btn');
    await expect(addBtn).toBeVisible();
    await expect(addBtn).toContainText('Filter');
  });

  test('clicking "+ Filter" opens a popover with a predicate editor', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await expect(popover).toBeVisible();

    // PredicateEditor should be inside with a field selector
    await expect(popover.locator('.pred-field-sel')).toBeVisible();
    await expect(popover.locator('.pred-op-sel')).toBeVisible();
  });

  test('cancelling the popover does not add a chip', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#fc-add-filter-btn').click();
    await expect(page.locator('.fc-popover')).toBeVisible();

    // Click cancel
    await page.locator('.fc-popover-cancel').click();
    await expect(page.locator('.fc-popover')).not.toBeVisible();

    // No chips added
    await expect(page.locator('.fc-chip')).toHaveCount(0);
    // Row count unchanged
    const rows = await page.locator('.inv-part-row').count();
    expect(rows).toBe(TOTAL_ROWS);
  });

  test('add a qty < N chip → row count drops to matching subset', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Total rows before filtering
    const totalBefore = await page.locator('.inv-part-row').count();
    expect(totalBefore).toBe(TOTAL_ROWS);

    // Pick a threshold that actually filters out some rows
    // Use qty < 50 — pick threshold based on fixture data
    const threshold = 50;
    const expectedCount = rowsWithQtyLessThan(threshold);
    // Make sure the filter actually reduces rows
    expect(expectedCount).toBeGreaterThan(0);
    expect(expectedCount).toBeLessThan(TOTAL_ROWS);

    // Open popover
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await expect(popover).toBeVisible();

    // Select field "qty"
    const fieldSel = popover.locator('.pred-field-sel');
    await fieldSel.selectOption('qty');
    await page.waitForTimeout(100);

    // Select operator "lt" (<)
    const opSel = popover.locator('.pred-op-sel');
    await opSel.selectOption('lt');
    await page.waitForTimeout(100);

    // Set value and commit via Tab (native blur/change)
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill(String(threshold));
    await valueInput.press('Tab');

    // Apply
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    // Popover should close
    await expect(popover).not.toBeVisible();

    // Chip should appear in the bar
    await expect(page.locator('.fc-chip')).toHaveCount(1);

    // Row count should match
    const rowsAfter = await page.locator('.inv-part-row').count();
    expect(rowsAfter).toBe(expectedCount);
  });

  test('chip shows field, operator, and value labels', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Add a chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill('50');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(100);

    const chip = page.locator('.fc-chip').first();
    await expect(chip.locator('.fc-chip-field')).toBeVisible();
    await expect(chip.locator('.fc-chip-op')).toBeVisible();
    await expect(chip.locator('.fc-chip-value')).toContainText('50');
  });

  test('clicking chip × removes it and restores full row count', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Add a chip first
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill('50');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    const filteredCount = await page.locator('.inv-part-row').count();
    expect(filteredCount).toBeLessThan(TOTAL_ROWS);

    // Remove the chip
    await page.locator('.fc-chip-remove').first().click();
    await page.waitForTimeout(200);

    // Chip should be gone
    await expect(page.locator('.fc-chip')).toHaveCount(0);

    // Full row count restored
    const restored = await page.locator('.inv-part-row').count();
    expect(restored).toBe(TOTAL_ROWS);
  });

  test('chip filter ANDs with distributor pill filter', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const threshold = 50;
    const lcscCount = rowsWithLCSC();
    const lcscAndQtyCount = rowsWithLCSCAndQtyLessThan(threshold);

    // Fixture must support a meaningful intersection — assert it rather than silently bail
    expect(lcscCount).toBeGreaterThan(0);
    expect(lcscAndQtyCount).toBeGreaterThan(0);
    expect(lcscAndQtyCount).toBeLessThan(lcscCount);

    // Step 1: click LCSC distributor pill
    await page.locator('.dist-filter-btn[data-distributor="lcsc"]').click();
    await page.waitForTimeout(200);
    const lcscFiltered = await page.locator('.inv-part-row').count();
    expect(lcscFiltered).toBe(lcscCount);

    // Step 2: add qty < 50 chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill(String(threshold));
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    // Both filters active: rows should be subset of LCSC rows with qty < threshold
    const intersection = await page.locator('.inv-part-row').count();
    expect(intersection).toBe(lcscAndQtyCount);
    expect(intersection).toBeLessThanOrEqual(lcscCount);
  });

  test('chip filter ANDs with search box', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const threshold = 50;
    const searchTerm = 'connector';
    const expectedAfterChip = rowsWithQtyLessThan(threshold);
    const expectedAfterBoth = rowsWithQtyLessThanAndSearch(threshold, searchTerm);

    // Fixture must support a meaningful reduction from the combined filters
    expect(expectedAfterBoth).toBeGreaterThan(0);
    expect(expectedAfterBoth).toBeLessThan(expectedAfterChip);

    // Step 1: add qty < 50 chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill(String(threshold));
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(300);

    const afterChip = await page.locator('.inv-part-row').count();
    expect(afterChip).toBe(expectedAfterChip);

    // Step 2: type a search query that further reduces results
    await page.locator('#inv-search').fill(searchTerm);
    await page.waitForTimeout(300);

    const afterSearch = await page.locator('.inv-part-row').count();
    // Both filters AND together: concrete expected count from fixture analysis
    expect(afterSearch).toBe(expectedAfterBoth);
  });

  test('editing a chip (clicking chip body) opens a popover for editing', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Add a chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill('50');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    const afterFirst = await page.locator('.inv-part-row').count();

    // Click the chip body (not the × button) to open edit popover
    const chip = page.locator('.fc-chip').first();
    await chip.locator('.fc-chip-field').click();
    await page.waitForTimeout(200);

    // Edit popover should appear
    const editPopover = page.locator('.fc-popover');
    await expect(editPopover).toBeVisible();

    // Change value to 100
    const editValue = editPopover.locator('.pred-value');
    await editValue.fill('100');
    await editValue.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    const afterEdit = await page.locator('.inv-part-row').count();
    // qty < 100 should match more rows than qty < 50
    expect(afterEdit).toBeGreaterThanOrEqual(afterFirst);
  });

  test('Clear Filters button also clears filter chips', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Add a chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill('50');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    // Clear Filters button should now be enabled
    const clearBtn = page.locator('#clear-dist-filter');
    await expect(clearBtn).not.toBeDisabled();

    // Click it
    await clearBtn.click();
    await page.waitForTimeout(200);

    // Chips cleared
    await expect(page.locator('.fc-chip')).toHaveCount(0);
    // All rows restored
    const rows = await page.locator('.inv-part-row').count();
    expect(rows).toBe(TOTAL_ROWS);
  });

  test('saved view captures and restores a filter chip', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const threshold = 50;
    const expectedCount = rowsWithQtyLessThan(threshold);
    // Fixture must have some rows below threshold and some above — assert rather than silently bail
    expect(expectedCount).toBeGreaterThan(0);
    expect(expectedCount).toBeLessThan(TOTAL_ROWS);

    // Step 1: add a qty < threshold chip
    await page.locator('#fc-add-filter-btn').click();
    const popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    const valueInput = popover.locator('.pred-value');
    await valueInput.fill(String(threshold));
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    const filteredRows = await page.locator('.inv-part-row').count();
    expect(filteredRows).toBe(expectedCount);

    // Step 2: save the current view
    await page.locator('#saved-views-btn').click();
    await page.locator('[data-action="save-view"]').click();
    const modal = page.locator('#sv-name-modal');
    await expect(modal).toBeVisible();
    await modal.locator('[data-field="name"]').fill('Qty filter view');
    await modal.locator('.form-modal-confirm').click();
    await page.waitForTimeout(300);

    // Step 3: clear all filters
    await page.locator('#clear-dist-filter').click();
    await page.waitForTimeout(200);

    // Verify cleared
    await expect(page.locator('.fc-chip')).toHaveCount(0);
    const allRows = await page.locator('.inv-part-row').count();
    expect(allRows).toBe(TOTAL_ROWS);

    // Step 4: re-apply the saved view
    await page.locator('#saved-views-btn').click();
    const menu = page.locator('.saved-views-menu');
    await expect(menu).toBeVisible();
    const viewItem = menu.locator('.sv-view-name').filter({ hasText: 'Qty filter view' });
    await expect(viewItem).toBeVisible();
    await viewItem.click();
    await page.waitForTimeout(300);

    // Step 5: chip should be restored and row count should match
    await expect(page.locator('.fc-chip')).toHaveCount(1);
    const restoredRows = await page.locator('.inv-part-row').count();
    expect(restoredRows).toBe(filteredRows);
  });

  test('screenshot: inventory with active filter chips', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Add chip 1: qty < 50
    await page.locator('#fc-add-filter-btn').click();
    let popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('qty');
    await page.waitForTimeout(50);
    await popover.locator('.pred-op-sel').selectOption('lt');
    await page.waitForTimeout(50);
    let valueInput = popover.locator('.pred-value');
    await valueInput.fill('50');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    // Add chip 2: section contains "C" (to show 2 chips + AND/OR toggle)
    await page.locator('#fc-add-filter-btn').click();
    popover = page.locator('.fc-popover');
    await popover.locator('.pred-field-sel').selectOption('description');
    await page.waitForTimeout(50);
    // operator defaults to 'contains' for text fields
    valueInput = popover.locator('.pred-value');
    await valueInput.fill('cap');
    await valueInput.press('Tab');
    await page.locator('.fc-popover-apply').click();
    await page.waitForTimeout(200);

    // Screenshot for visual inspection
    await page.screenshot({ path: SCREENSHOT_PATH });
  });

});
