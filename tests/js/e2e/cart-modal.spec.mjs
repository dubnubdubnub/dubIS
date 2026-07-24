// tests/js/e2e/cart-modal.spec.mjs
// @ts-check
// Task B6: the cart modal — DataGrid of active-cart line items with editable
// qty, per-row delete, and a Clear cart button. Display fields (description/
// package/on-hand) resolve by joining cart item.part_id against the loaded
// inventory via invPartKey (js/part-keys.js) — see js/cart/cart-modal.js.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

// C496552 exists in MOCK_INVENTORY (description "Connector Receptacle IPEX
// Male Pin 50Ω Surface Mount", package "SMD", qty 100) — seeding a cart item
// against it lets the modal resolve display fields via invPartKey.
const CARTS_SEED = {
  carts: [{
    id: 'cart-1',
    name: 'My Cart',
    items: [
      { ref: 'item-1', part_id: 'C496552', raw: null, qty: 5, target_distributor: 'lcsc' },
    ],
  }],
  active_cart_id: 'cart-1',
};

test.describe('Cart modal', () => {
  test('open, edit qty, delete a line, clear', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: CARTS_SEED });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-badge')).toHaveText('1');

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect(page.locator('.cart-modal')).toContainText('My Cart');
    await expect(page.locator('.cart-modal')).toContainText('Connector Receptacle IPEX Male Pin 50Ω Surface Mount');
    await expect(page.locator('.cart-modal')).toContainText('SMD');

    const qtyCell = page.locator('.cart-modal .cart-qty-input').first();
    await qtyCell.fill('12');
    await qtyCell.blur();
    await expect(page.locator('.cart-modal .cart-qty-input').first()).toHaveValue('12');

    await page.locator('.cart-modal .cart-del-line').first().click();
    await expect(page.locator('#cart-badge')).toHaveText('0');
    await expect(page.locator('.cart-modal')).toContainText(/no items|empty/i);
  });

  test('clear cart empties every line via the top-bar button', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{
          id: 'cart-1',
          name: 'My Cart',
          items: [
            { ref: 'item-1', part_id: 'C496552', raw: null, qty: 5, target_distributor: 'lcsc' },
            { ref: 'item-2', part_id: null, raw: { mpn: 'RC0402FR-071KL', description: 'Resistor 1K' }, qty: 3, target_distributor: null },
          ],
        }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#cart-badge')).toHaveText('2');
    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect(page.locator('.cart-modal .cart-qty-input')).toHaveCount(2);

    page.once('dialog', (dialog) => dialog.accept());
    await page.click('.cart-modal .cart-topbar >> text=Clear cart');

    await expect(page.locator('#cart-badge')).toHaveText('0');
    await expect(page.locator('.cart-modal .cart-qty-input')).toHaveCount(0);
  });

  test('no active cart shows an empty-state instead of crashing', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: { carts: [], active_cart_id: null },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect(page.locator('.cart-modal')).toContainText(/no active cart/i);
  });
});
