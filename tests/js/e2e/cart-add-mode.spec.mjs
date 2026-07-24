// tests/js/e2e/cart-add-mode.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Cart-add mode', () => {
  test('toggle then click a row adds it to the cart', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-badge')).toHaveText('0');

    await page.click('#cart-add-toggle');
    await expect(page.locator('#cart-add-toggle')).toHaveClass(/active/);
    await expect(page.locator('#cart-add-toggle')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('body')).toHaveClass(/cart-add-active/);

    await page.locator('.inv-part-row').first().click();

    await expect(page.locator('#cart-badge')).toHaveText('1');
  });

  test('first-use add-to-cart on a fresh install (no carts yet) auto-creates a cart', async ({ page }) => {
    // No options.carts passed — route-mocks.mjs defaults ctx.cartsState to
    // {carts: [], active_cart_id: null}, the real first-run state (Fix 1
    // regression test: this used to silently no-op).
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-badge')).toHaveText('0');

    await page.click('#cart-add-toggle');
    await expect(page.locator('#cart-add-toggle')).toHaveClass(/active/);

    await page.locator('.inv-part-row').first().click();

    await expect(page.locator('#cart-badge')).toHaveText('1');
  });

  test('toggle off reverts the visual mode and stops adding on row click', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-add-toggle');
    await expect(page.locator('#cart-add-toggle')).toHaveClass(/active/);
    await page.click('#cart-add-toggle');
    await expect(page.locator('#cart-add-toggle')).not.toHaveClass(/active/);
    await expect(page.locator('#cart-add-toggle')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('body')).not.toHaveClass(/cart-add-active/);

    await page.locator('.inv-part-row').first().click();

    await expect(page.locator('#cart-badge')).toHaveText('0');
  });
});
