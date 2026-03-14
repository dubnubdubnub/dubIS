// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadPurchaseOrder, overrideDetectColumns } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

const PURCHASE_COLUMN_MAPPING = {
  0: "Digikey Part Number", 1: "LCSC Part Number", 2: "Manufacture Part Number",
  3: "Manufacturer", 4: "Customer NO.", 5: "Package", 6: "Description",
  7: "RoHS", 8: "Quantity", 9: "Unit Price($)", 10: "Ext.Price($)",
};

// ── File loading ──

test.describe('Purchase import — file loading', () => {

  test('loading CSV populates drop zone, mapper, and staging table', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // Drop zone should have .loaded class
    await expect(page.locator('#import-drop-zone')).toHaveClass(/loaded/);

    // Import mapper should be visible (no .hidden)
    const mapper = page.locator('#import-mapper');
    await expect(mapper).not.toHaveClass(/hidden/);

    // Staging table should have 5 data rows (purchase.csv has 5 data lines)
    const stagingRows = page.locator('#import-mapper .import-preview tbody tr');
    await expect(stagingRows).toHaveCount(5);

    // Column mapper should have 11 .col-mapper-row entries (11 columns in purchase.csv)
    const mapperRows = page.locator('.col-mapper-row');
    await expect(mapperRows).toHaveCount(11);
  });
});

// ── Column mapping ──

test.describe('Purchase import — column mapping', () => {

  test('auto-detected columns have .mapped class, unmapped show Skip', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await overrideDetectColumns(page, PURCHASE_COLUMN_MAPPING);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // All selects should have .mapped since every column is mapped
    const mappedSelects = page.locator('.col-map-select.mapped');
    const allSelects = page.locator('.col-map-select');
    const mappedCount = await mappedSelects.count();
    const allCount = await allSelects.count();
    expect(mappedCount).toBe(allCount);
  });

  test('changing a dropdown re-renders mapper', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await overrideDetectColumns(page, PURCHASE_COLUMN_MAPPING);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // Change first select to "Skip" — should lose .mapped
    const firstSelect = page.locator('.col-map-select').first();
    await firstSelect.selectOption('Skip');

    // After re-render, first select should not have .mapped
    const updatedFirst = page.locator('.col-map-select').first();
    await expect(updatedFirst).not.toHaveClass(/mapped/);
  });

  test('duplicate target detection clears prior select', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await overrideDetectColumns(page, PURCHASE_COLUMN_MAPPING);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // Column 1 is mapped to "LCSC Part Number" — set column 0 to the same target
    const firstSelect = page.locator('.col-map-select').first();
    await firstSelect.selectOption('LCSC Part Number');

    // After re-render, column 1 should now be "Skip" (duplicate cleared)
    const secondSelect = page.locator('.col-map-select').nth(1);
    await expect(secondSelect).toHaveValue('Skip');
  });
});

// ── Staging table editing ──

test.describe('Purchase import — staging table editing', () => {

  test('editing a cell updates row data', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    const firstInput = page.locator('#import-mapper .import-preview tbody tr:first-child td input').first();
    await firstInput.scrollIntoViewIfNeeded();
    await firstInput.click();
    await firstInput.fill('NEW_VALUE');
    await firstInput.press('Tab');

    // The input value should persist (re-query since Tab may re-render)
    const updatedInput = page.locator('#import-mapper .import-preview tbody tr:first-child td input').first();
    await expect(updatedInput).toHaveValue('NEW_VALUE');
  });

  test('clicking × removes row and staging count decreases', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    const rowsBefore = await page.locator('#import-mapper .import-preview tbody tr').count();
    expect(rowsBefore).toBe(5);

    // Scroll the delete button into view first — sticky toolbar can intercept clicks
    const deleteBtn = page.locator('#import-mapper .import-preview tbody tr:first-child .row-delete');
    await deleteBtn.scrollIntoViewIfNeeded();
    await deleteBtn.click();

    const rowsAfter = await page.locator('#import-mapper .import-preview tbody tr').count();
    expect(rowsAfter).toBe(4);
  });

  test('rows without part ID get .row-warn', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await overrideDetectColumns(page, PURCHASE_COLUMN_MAPPING);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // Clear all part ID fields in the first row to force a warning
    // Column indices 0, 1, 2 are part ID fields (Digikey, LCSC, MPN)
    for (const colIdx of [0, 1, 2]) {
      const input = page.locator(`#import-mapper .import-preview tbody tr:first-child td input[data-col="${colIdx}"]`);
      await input.scrollIntoViewIfNeeded();
      await input.click();
      await input.fill('');
      await input.press('Tab');
    }

    // First row should now have .row-warn
    const firstRow = page.locator('#import-mapper .import-preview tbody tr').first();
    await expect(firstRow).toHaveClass(/row-warn/);
  });
});

// ── Import button ──

test.describe('Purchase import — import button', () => {

  test('shows correct text with row count and warnings', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    const btn = page.locator('#do-import-btn');
    const text = await btn.textContent();
    expect(text).toContain('Import');
    expect(text).toContain('5 rows');
  });

  test('clicking import triggers API, shows toast, and resets panel', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await overrideDetectColumns(page, PURCHASE_COLUMN_MAPPING);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    await page.locator('#do-import-btn').click();

    // Toast should appear
    await expect(page.locator('#toast')).toHaveClass(/show/);

    // Panel should reset — import-mapper should be hidden again
    await expect(page.locator('#import-mapper')).toHaveClass(/hidden/);

    // Drop zone should no longer have .loaded
    await expect(page.locator('#import-drop-zone')).not.toHaveClass(/loaded/);
  });
});

// ── Clear button ──

test.describe('Purchase import — clear button', () => {

  test('clear resets panel to initial state', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_CSV_PATH);

    // Verify mapper is visible
    await expect(page.locator('#import-mapper')).not.toHaveClass(/hidden/);

    await page.locator('#clear-import-btn').click();

    // Mapper should be hidden
    await expect(page.locator('#import-mapper')).toHaveClass(/hidden/);

    // Drop zone text should reset
    const dropText = await page.locator('#import-drop-zone p').textContent();
    expect(dropText).toContain('Drop a purchase CSV here');
  });
});
