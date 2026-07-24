// tests/js/e2e/cart-header.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Header cart button', () => {
  test('cart button shows in header with a badge reflecting item count', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [{ ref: 'a' }, { ref: 'b' }] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const btn = page.locator('#cart-btn');
    await expect(btn).toBeVisible();
    await expect(page.locator('#cart-btn .cart-badge')).toHaveText('2');
  });

  test('badge defaults to 0 with no active cart', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-btn .cart-badge')).toHaveText('0');
  });

  test('cart-add-toggle button is rendered beside the cart button', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-add-toggle')).toBeVisible();
  });

  test('clicking the cart button does not throw (stub openCartModal)', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.locator('#cart-btn').click();
    expect(errors).toEqual([]);
  });
});
