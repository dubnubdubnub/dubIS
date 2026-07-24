// tests/js/e2e/cart-link-target.spec.mjs
// @ts-check
// Task B4: the header cart icon becomes a linking-mode drop target — arming
// a `.link-btn` on an inventory row (BOM loaded) marks #cart-btn with
// .link-target (purple dotted box, mirrors row .link-target styling), and
// clicking it while armed adds that part to the active cart and exits
// linking mode instead of opening the (stub) cart modal.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = path.join(__dirname, 'fixtures', 'bom-footprint.csv');

test.describe('Linking mode marks the cart as a drop target', () => {
  test('arming a link-btn marks #cart-btn; clicking it adds the armed part', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);

    await expect(page.locator('#cart-btn')).not.toHaveClass(/link-target/);

    // Arm linking mode from an inventory row's Link button (rendered because
    // a BOM is loaded).
    await page.locator('.inv-part-row .link-btn').first().click();

    await expect(page.locator('#cart-btn')).toHaveClass(/link-target/);

    await page.click('#cart-btn');

    await expect(page.locator('#cart-badge')).toHaveText('1');
    await expect(page.locator('#cart-btn')).not.toHaveClass(/link-target/);
  });

  test('clicking #cart-btn while NOT in linking mode does not add anything', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: {
        carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }],
        active_cart_id: 'cart-1',
      },
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');

    await expect(page.locator('#cart-badge')).toHaveText('0');
  });
});
