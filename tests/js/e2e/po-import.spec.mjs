// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const PO_LCSC = path.join(__dirname, 'fixtures', 'po-lcsc.csv');
const PO_DIGIKEY = path.join(__dirname, 'fixtures', 'po-digikey.csv');
const PO_POLOLU = path.join(__dirname, 'fixtures', 'po-pololu.csv');
const PO_MOUSER = path.join(__dirname, 'fixtures', 'po-mouser.csv');

/**
 * Get the column mapping from the import panel as { sourceHeader: mappedTarget }.
 */
async function getColumnMapping(page) {
  return page.evaluate(() => {
    const rows = document.querySelectorAll('.col-mapper-row');
    const mapping = {};
    rows.forEach(row => {
      const header = row.querySelector('.source-col')?.textContent?.trim() || '';
      const select = row.querySelector('.col-map-select');
      const value = select?.value || 'Skip';
      mapping[header] = value;
    });
    return mapping;
  });
}

/** Get the number of data rows in the staging table. */
async function getStagingRowCount(page) {
  return page.locator('#import-mapper .import-preview tbody tr').count();
}

test.describe('Purchase order import — column auto-detection', () => {

  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
  });

  test('LCSC PO: columns auto-detected correctly', async ({ page }) => {
    await loadPurchaseOrder(page, PO_LCSC);
    const mapping = await getColumnMapping(page);

    expect(mapping['LCSC Part Number']).toBe('LCSC Part Number');
    expect(mapping['Manufacture Part Number']).toBe('Manufacture Part Number');
    expect(mapping['Manufacturer']).toBe('Manufacturer');
    expect(mapping['Package']).toBe('Package');
    expect(mapping['Description']).toBe('Description');
    expect(mapping['Quantity']).toBe('Quantity');
    expect(mapping['Unit Price($)']).toBe('Unit Price($)');
    expect(mapping['Ext.Price($)']).toBe('Ext.Price($)');

    // 2 data rows in staging table
    expect(await getStagingRowCount(page)).toBe(2);
  });

  test('DigiKey PO: columns auto-detected correctly', async ({ page }) => {
    await loadPurchaseOrder(page, PO_DIGIKEY);
    const mapping = await getColumnMapping(page);

    expect(mapping['DigiKey Part #']).toBe('Digikey Part Number');
    expect(mapping['Manufacturer Part Number']).toBe('Manufacture Part Number');
    expect(mapping['Manufacturer']).toBe('Manufacturer');
    expect(mapping['Description']).toBe('Description');
    expect(mapping['Quantity']).toBe('Quantity');
    expect(mapping['Unit Price']).toBe('Unit Price($)');
    expect(mapping['Extended Price']).toBe('Ext.Price($)');

    expect(await getStagingRowCount(page)).toBe(2);
  });

  test('Pololu PO: columns auto-detected correctly', async ({ page }) => {
    await loadPurchaseOrder(page, PO_POLOLU);
    const mapping = await getColumnMapping(page);

    expect(mapping['Pololu Part Number']).toBe('Pololu Part Number');
    expect(mapping['Manufacture Part Number']).toBe('Manufacture Part Number');
    expect(mapping['Manufacturer']).toBe('Manufacturer');
    expect(mapping['Description']).toBe('Description');
    expect(mapping['Package']).toBe('Package');
    expect(mapping['Quantity']).toBe('Quantity');
    expect(mapping['Unit Price($)']).toBe('Unit Price($)');

    expect(await getStagingRowCount(page)).toBe(2);
  });

  test('Mouser PO: columns auto-detected correctly', async ({ page }) => {
    await loadPurchaseOrder(page, PO_MOUSER);
    const mapping = await getColumnMapping(page);

    expect(mapping['Mouser #']).toBe('Mouser Part Number');
    expect(mapping['Mfr. #']).toBe('Manufacture Part Number');
    expect(mapping['Manufacturer']).toBe('Manufacturer');
    expect(mapping['Description']).toBe('Description');
    expect(mapping['Order Qty.']).toBe('Quantity');
    expect(mapping['Price (USD)']).toBe('Unit Price($)');
    expect(mapping['Ext.: (USD)']).toBe('Ext.Price($)');

    expect(await getStagingRowCount(page)).toBe(2);
  });

  test('staging table shows actual data from CSV', async ({ page }) => {
    await loadPurchaseOrder(page, PO_LCSC);

    // Data is in input elements inside the staging table cells
    const inputs = page.locator('#import-mapper .import-preview tbody tr:first-child input[type="text"]');
    const values = await inputs.evaluateAll(els => els.map(el => el.value));
    expect(values).toContain('C2040');
    expect(values).toContain('CL05A104KA5NNNC');
    expect(values).toContain('Samsung');
  });

  test('data rows are classified as ok (not warn)', async ({ page }) => {
    await loadPurchaseOrder(page, PO_MOUSER);

    // Rows with valid part ID + quantity should NOT have the warn class
    const rows = page.locator('#import-mapper .import-preview tbody tr');
    const count = await rows.count();
    expect(count).toBe(2);
    for (let i = 0; i < count; i++) {
      await expect(rows.nth(i)).not.toHaveClass(/row-warn/);
    }
  });

  test('import button shows correct row count', async ({ page }) => {
    await loadPurchaseOrder(page, PO_POLOLU);

    const btn = page.locator('#do-import-btn');
    await expect(btn).toContainText('Import 2 rows');
  });
});
