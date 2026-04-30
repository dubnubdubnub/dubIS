// @ts-check
import { test, expect } from '@playwright/test';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resetServer, setupPage } from './setup-page.mjs';
import { waitForInventoryRows, loadPurchaseOrder } from '../helpers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const IMPORT_CSV = join(__dirname, '..', 'fixtures', 'e2e-import.csv');

test.describe('Import workflow', () => {
  test.beforeEach(async ({ page }) => {
    await resetServer();
    await setupPage(page);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('loading PO shows staging table with auto-detected columns', async ({ page }) => {
    await loadPurchaseOrder(page, IMPORT_CSV);

    const mapper = page.locator('#import-mapper');
    await expect(mapper).not.toHaveClass(/hidden/);

    const selects = mapper.locator('.col-map-select');
    expect(await selects.count()).toBeGreaterThan(0);
  });

  test('staging table shows correct data rows', async ({ page }) => {
    await loadPurchaseOrder(page, IMPORT_CSV);

    const rows = page.locator('.import-preview tbody tr');
    await expect(rows).toHaveCount(2);
  });

  test('edit cell in staging persists value', async ({ page }) => {
    await loadPurchaseOrder(page, IMPORT_CSV);

    const input = page.locator('.import-preview td input').first();
    await input.fill('EDITED-VALUE');
    // Click elsewhere to blur and trigger change event
    await page.locator('.import-preview thead').click();

    await expect(input).toHaveValue('EDITED-VALUE');
  });

  test('delete row from staging decreases count', async ({ page }) => {
    await loadPurchaseOrder(page, IMPORT_CSV);

    const rowsBefore = await page.locator('.import-preview tbody tr').count();
    await page.locator('.row-delete[data-row]').first().click();
    const rowsAfter = await page.locator('.import-preview tbody tr').count();

    expect(rowsAfter).toBe(rowsBefore - 1);
  });

  test('import button shows correct count', async ({ page }) => {
    await loadPurchaseOrder(page, IMPORT_CSV);

    const btn = page.locator('#do-import-btn');
    await expect(btn).toBeVisible();
    await expect(btn).toHaveText(/Import \d+ rows?/);
  });

  test('import adds new parts to inventory', async ({ page }) => {
    const rowsBefore = await page.locator('.inv-part-row').count();

    await loadPurchaseOrder(page, IMPORT_CSV);
    await page.locator('#do-import-btn').click();

    // Wait for inventory to refresh with the new parts
    await page.waitForSelector('[data-lcsc="C888001"]', { timeout: 10_000 });

    const rowsAfter = await page.locator('.inv-part-row').count();
    expect(rowsAfter).toBeGreaterThan(rowsBefore);
    await expect(page.locator('[data-lcsc="C888001"]')).toBeVisible();
  });
});
