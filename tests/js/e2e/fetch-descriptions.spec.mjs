// tests/js/e2e/fetch-descriptions.spec.mjs
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

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');

test.describe('Fetch descriptions', () => {
  test('per-part Fetch description button fills the Description input', async ({ page }) => {
    const INVENTORY = [
      { section: 'Passives - Capacitors > MLCC',
        lcsc: 'C2040', digikey: '', pololu: '', mouser: '',
        mpn: 'CL05A104KA5NNNC', manufacturer: 'Samsung',
        package: '0402', description: '',
        qty: 200, unit_price: 0.0025, ext_price: 0.50 },
    ];
    const MOCK_PRODUCTS = {
      'lcsc:C2040': {
        productCode: 'C2040',
        description: 'Mock Cap 47uF',
        prices: [{ qty: 1, price: 0.0025 }, { qty: 100, price: 0.001 }],
        provider: 'lcsc',
      },
    };

    await addMockSetup(page, INVENTORY, {
      productMocks: MOCK_PRODUCTS,
      lastPoQty: { C2040: 100 },
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await partRow(page, 'C2040').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    // Wait for the fetch panel's row to resolve its price before relying on
    // the fetched product data (same product payload backs the description).
    await expect(page.locator('#adj-fetch-panel .fetch-drow')).toHaveCount(1);
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue()))
      .toBeCloseTo(0.001, 6);

    const descInput = page.locator('.modal-field-input[data-field="description"]');
    await expect(descInput).toHaveValue('');

    await page.getByRole('button', { name: 'Fetch description' }).click();
    await expect(descInput).toHaveValue('Mock Cap 47uF');
  });

  test('command palette Fetch Missing Descriptions toasts a summary', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.addInitScript(() => {
      window.pywebview.api.fetch_missing_descriptions = async () => ({
        inventory: window.__mockInventoryAfterFetch || [],
        summary: { updated: 2, failed: 0, skipped: 0 },
      });
    });
    // Reuse the same seeded inventory as the "post-fetch" result — the test
    // only asserts the toast summary text, not per-row description changes.
    await page.addInitScript((inv) => { window.__mockInventoryAfterFetch = inv; }, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.keyboard.press('Control+k');
    await page.locator('.cp-search').fill('Fetch Missing');
    await expect(page.locator('.cp-item')).toHaveCount(1);
    await page.getByText('Fetch Missing Descriptions').click();

    await expect(page.locator('#toast')).toHaveClass(/show/);
    await expect(page.locator('#toast')).toContainText('Fetched 2 descriptions');
  });
});
