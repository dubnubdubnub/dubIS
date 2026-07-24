// tests/js/e2e/cart-manage.spec.mjs
// @ts-check
// Task B7: cart management controls in the cart modal's top bar — switch
// active cart, create, rename, delete. Extends the same modal/top bar built
// by js/cart/cart-modal.js (Task B6's "Clear cart" button lives there too).
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

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

test.describe('Cart modal management', () => {
  test('create, switch, rename, delete', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect(page.locator('#cart-switcher option')).toHaveCount(1);

    // New — creates a second cart, prefilled name, and makes it active.
    await page.click('.cart-topbar .cart-new');
    await expect(page.locator('#cart-switcher option')).toHaveCount(2);
    const today = new Date().toISOString().slice(0, 10);
    await expect(page.locator('#cart-switcher option:checked')).toHaveText(new RegExp(today));

    // Rename the (now active, newly-created) cart via window.prompt.
    page.once('dialog', (d) => d.accept('Renamed Cart'));
    await page.click('.cart-topbar .cart-rename');
    await expect(page.locator('#cart-switcher option:checked')).toHaveText(/Renamed Cart/);

    // Switch back to the original cart.
    await page.selectOption('#cart-switcher', 'cart-1');
    await expect(page.locator('.cart-modal')).toContainText('My Cart');

    // Delete the original cart — active should fall back to the remaining one.
    page.once('dialog', (d) => d.accept());
    await page.click('.cart-topbar .cart-delete');
    await expect(page.locator('#cart-switcher option')).toHaveCount(1);
    await expect(page.locator('#cart-switcher option:checked')).toHaveText(/Renamed Cart/);
  });

  test('deleting the last cart falls back to the empty state', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    page.once('dialog', (d) => d.accept());
    await page.click('.cart-topbar .cart-delete');
    await expect(page.locator('.cart-modal')).toContainText(/no active cart/i);
    await expect(page.locator('#cart-switcher option')).toHaveCount(0);
  });
});
