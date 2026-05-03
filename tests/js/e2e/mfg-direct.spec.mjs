// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);
const MDT_INVOICE = path.join(__dirname, 'fixtures', 'mdt-invoice.csv');

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown' },
  { id: 'v_self',    name: 'Self',    icon: '⚙️', type: 'self' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage' },
  { id: 'v_mdt_test', name: 'MDT', url: 'https://tmr-sensors.com',
    favicon_path: 'data/sources/favicons/test.ico', type: 'real' },
];

test.describe('Direct-from-mfg import', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      mdtInvoiceParseResult: [
        { mpn: 'TMR2615', manufacturer: 'MDT', package: 'SOIC-8', quantity: 50, unit_price: 4.20 },
        { mpn: 'TMR2305', manufacturer: 'MDT', package: 'SOIC-8', quantity: 25, unit_price: 3.10 },
      ],
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('happy path: click Direct → fill vendor → drop CSV → import', async ({ page }) => {
    // 1. Click ★ Direct button
    const directBtn = page.locator('[data-template="direct"]');
    await directBtn.scrollIntoViewIfNeeded();
    await directBtn.click();
    await expect(page.locator('.mfg-direct-editor')).toBeVisible();

    // 2. Type vendor URL
    const vendorInput = page.locator('#mfg-vendor-input');
    await vendorInput.click();
    await vendorInput.fill('tmr-sensors.com');
    await vendorInput.press('Tab');
    await expect(page.locator('.vendor-favicon, .vendor-favicon-emoji').first()).toBeVisible();

    // 3. Use file input directly (drag-drop in Playwright is via setInputFiles)
    const fileInput = page.locator('#mfg-source-input');
    await fileInput.setInputFiles(MDT_INVOICE);

    // 4. Verify line items populated
    await expect(page.locator('.mfg-items-table tbody tr')).toHaveCount(2);
    await expect(page.locator('.mfg-items-table tbody tr').nth(0).locator('input[data-field="mpn"]'))
      .toHaveValue('TMR2615');

    // 5. Click Import
    await page.locator('#mfg-import').click();
    await expect(page.locator('.toast')).toContainText('Imported');
  });

  test('popout toggle expands editor into modal', async ({ page }) => {
    await page.locator('[data-template="direct"]').click();
    await page.locator('#mfg-popout-btn').click();
    await expect(page.locator('.mfg-direct-modal')).toBeVisible();
  });

  test('Direct filter pill replaces Other', async ({ page }) => {
    const direct = page.locator('[data-distributor="direct"]');
    await expect(direct).toBeVisible();
    await expect(page.locator('[data-distributor="other"]')).toHaveCount(0);
  });

  test('vendor caret expands sub-pill panel', async ({ page }) => {
    const caret = page.locator('.dist-vendor-caret');
    await caret.click();
    await expect(page.locator('#vendor-subpills-panel')).toBeVisible();
  });

  test('pseudo-vendor chip selects Self', async ({ page }) => {
    await page.locator('[data-template="direct"]').click();
    await page.locator('.mfg-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('.mfg-direct-vendor-input')).toHaveValue('Self');
  });
});
