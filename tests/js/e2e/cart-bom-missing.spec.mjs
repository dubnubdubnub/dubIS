// tests/js/e2e/cart-bom-missing.spec.mjs
// @ts-check
// Task B5: the BOM panel's "Add missing to cart" button (#bom-add-to-cart)
// gathers every missing/short/possible BOM row and posts it to the active
// cart via add_bom_missing_to_cart, then reflects the new count in the
// header's #cart-badge.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
// Both rows' Mfg P/N (RC0402FR-071KL / RC0402FR-07620RL) have no match in
// MOCK_INVENTORY (see grep in the task report) — both come through as
// effectiveStatus "missing", i.e. raw-stub cart entries.
const BOM_CSV = path.join(__dirname, 'fixtures', 'bom-footprint.csv');

test.describe('BOM panel "Add missing to cart"', () => {
  test('is disabled until a BOM is loaded', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#bom-add-to-cart')).toBeDisabled();
  });

  test('adds all missing/short BOM parts to the active cart', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);

    await expect(page.locator('#cart-badge')).toHaveText('0');
    await expect(page.locator('#bom-add-to-cart')).toBeEnabled();

    await page.click('#bom-add-to-cart');

    await expect(page.locator('#cart-badge')).not.toHaveText('0');
    await expect(page.locator('#cart-badge')).toHaveText('2');
  });

  test('disables the button again after the BOM is cleared', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);

    await expect(page.locator('#bom-add-to-cart')).toBeEnabled();

    await page.click('#bom-clear-btn');

    await expect(page.locator('#bom-add-to-cart')).toBeDisabled();
  });
});
