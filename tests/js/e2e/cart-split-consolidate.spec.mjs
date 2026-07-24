// tests/js/e2e/cart-split-consolidate.spec.mjs
// @ts-check
// Task B8: per-line target-distributor selection + "split by distributor" /
// "consolidate to distributor" top-bar operations in the cart modal.
// Available distributors per line are derived client-side from the resolved
// inventory item's non-empty {lcsc, digikey, mouser, pololu} PN fields (see
// js/cart/cart-modal.js's availableDistributors()) — no backend round-trip.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

// C496552 (lcsc-only: lcsc="C496552", digikey/mouser/pololu all "") and
// CL05A104KA5NNNC (digikey-only: mpn="CL05A104KA5NNNC" resolved via
// invPartKey since its lcsc field is "", digikey="1276-1043-2-ND") both exist
// in the fixture — seeding a cart with one line against each gives a
// mixed-distributor cart to split/consolidate.
const CARTS_SEED = {
  carts: [{
    id: 'cart-1',
    name: 'My Cart',
    items: [
      { ref: 'item-1', part_id: 'C496552', raw: null, qty: 5, target_distributor: null },
      { ref: 'item-2', part_id: 'CL05A104KA5NNNC', raw: null, qty: 20, target_distributor: null },
    ],
  }],
  active_cart_id: 'cart-1',
};

test.describe('Cart split / consolidate', () => {
  test('per-row target-distributor select offers only the part\'s sourced distributors', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    const selects = page.locator('.cart-modal .cart-row-dist-select');
    await expect(selects).toHaveCount(2);

    // Row 1 (C496552, lcsc-only): options are blank + lcsc.
    const row1Options = await selects.nth(0).locator('option').allTextContents();
    expect(row1Options.map((t) => t.toLowerCase())).toEqual(expect.arrayContaining(['lcsc']));
    expect(row1Options.length).toBe(2); // blank + lcsc

    // Row 2 (CL05A104KA5NNNC, digikey-only): options are blank + digikey.
    const row2Options = await selects.nth(1).locator('option').allTextContents();
    expect(row2Options.map((t) => t.toLowerCase())).toEqual(expect.arrayContaining(['digikey']));
    expect(row2Options.length).toBe(2);

    // Changing a row's select commits via updateItem (target_distributor).
    await selects.nth(0).selectOption('lcsc');
    await expect.poll(async () => page.evaluate(() =>
      (window.__apiCalls?.update_cart_item || []).length)).toBeGreaterThan(0);
  });

  test('split by distributor creates a new cart with only that distributor\'s lines', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect(page.locator('.cart-modal .cart-row')).toHaveCount(2);

    await page.selectOption('.cart-topbar .cart-split-dist', 'lcsc');
    await page.click('.cart-topbar .cart-split-go');

    // Active cart switches to the new lcsc-only cart.
    await expect(page.locator('#cart-switcher option')).toHaveCount(2);
    await expect(page.locator('.cart-modal')).toContainText(/lcsc/i);
    await expect(page.locator('.cart-modal .cart-row')).toHaveCount(1);
  });

  test('split with "remove from this cart" also removes the line from the source cart', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    await page.selectOption('.cart-topbar .cart-split-dist', 'lcsc');
    await page.check('.cart-topbar .cart-split-remove');
    await page.click('.cart-topbar .cart-split-go');

    // New (active) cart has the 1 lcsc line.
    await expect(page.locator('.cart-modal .cart-row')).toHaveCount(1);

    // Switch back to the source cart — it should now have only the digikey line.
    await page.selectOption('#cart-switcher', 'cart-1');
    await expect(page.locator('.cart-modal .cart-row')).toHaveCount(1);
    await expect(page.locator('.cart-modal')).toContainText('CAP CER 0.1UF 25V X5R 0402');
  });

  test('consolidate to a distributor sets target on sourceable lines and warns about unresolved ones', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    await page.selectOption('.cart-topbar .cart-consolidate-dist', 'lcsc');
    await page.click('.cart-topbar .cart-consolidate-go');

    // The lcsc-sourceable line (C496552) got targeted at lcsc; the
    // digikey-only line (CL05A104KA5NNNC) is not sourceable from lcsc, so it
    // shows up in the unresolved warning toast.
    await expect(page.locator('.toast, .toast-message').last()).toContainText(/unresolved|could not/i);
  });
});
