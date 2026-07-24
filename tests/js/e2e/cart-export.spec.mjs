// tests/js/e2e/cart-export.spec.mjs
// @ts-check
// Task B9: cart modal export controls — LCSC/DigiKey CSV download, copy-paste
// clipboard, and an unresolved-lines warning toast. exportCart(cartId,
// distributor, fmt) -> {content, unresolved, filename} (js/cart/cart-store.js);
// this spec exercises the client-side download/clipboard glue in
// js/cart/cart-export.js + the top-bar buttons in js/cart/cart-modal.js.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

// C496552 (lcsc-only) exists in the fixture (see cart-split-consolidate.spec.mjs) —
// one resolvable line is enough to exercise the export flow.
const CARTS_SEED = {
  carts: [{
    id: 'cart-1',
    name: 'My Cart',
    items: [
      { ref: 'item-1', part_id: 'C496552', raw: null, qty: 5, target_distributor: null },
    ],
  }],
  active_cart_id: 'cart-1',
};

test.describe('Cart export', () => {
  test('export LCSC CSV downloads a file', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      (async () => {
        await page.click('.cart-topbar .cart-export');
        await page.click('.cart-export-lcsc-csv');
      })(),
    ]);
    expect(download.suggestedFilename()).toMatch(/lcsc\.csv$/);
  });

  test('export DigiKey CSV downloads a file', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      (async () => {
        await page.click('.cart-topbar .cart-export');
        await page.click('.cart-export-digikey-csv');
      })(),
    ]);
    expect(download.suggestedFilename()).toMatch(/digikey\.csv$/);
  });

  // Clipboard writes require the browser context's permission — granted here
  // (mirrors tests/js/e2e/text-popover.spec.mjs's existing clipboard spec) so
  // the assertion reads the REAL clipboard content, not just "a toast fired".
  test('copy LCSC paste format writes to the clipboard and shows a confirmation toast', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    await page.click('.cart-topbar .cart-export');
    await page.click('.cart-export-lcsc-paste');

    await expect(page.locator('.toast, .toast-message').last()).toContainText(/copied/i);
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.trim()).toBe('C496552\t5');
  });

  test('unresolved lines surface a warning toast on export', async ({ page }) => {
    // digikey export against an lcsc-only part is unresolved per the mock below.
    await installRouteMocks(page, MOCK_INVENTORY, { carts: JSON.parse(JSON.stringify(CARTS_SEED)) });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      (async () => {
        await page.click('.cart-topbar .cart-export');
        await page.click('.cart-export-digikey-csv');
      })(),
    ]);
    expect(download.suggestedFilename()).toMatch(/digikey\.csv$/);
    await expect(page.locator('.toast, .toast-message').last()).toContainText(/unresolved|could not/i);
  });
});
