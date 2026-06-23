// @ts-check
// Regression suite: an inventory mutation must NEVER reset the scroll position.
// The inventory list is rebuilt on every INVENTORY_UPDATED; render() captures and
// restores #inventory-body.scrollTop so a save/adjust doesn't jump the user back
// up the list. This was reported for the ⚠ price modal with a BOM loaded, but the
// failure mode (the BOM-comparison rebuild dropping scrollTop) is general, so we
// cover both view modes and every mutation entry point.
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBom } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

// 60 rows in a valid section so the body overflows. The LAST row is price-less
// (renders the ⚠ price-warning button) and single-sourced with a fetch mock.
const PRICELESS_LCSC = 'C20159';
const INVENTORY = [];
for (let i = 0; i < 60; i++) {
  const last = i === 59;
  INVENTORY.push({
    section: 'Passives - Capacitors > MLCC',
    lcsc: 'C20' + (i + 100), digikey: '', pololu: '', mouser: '',
    mpn: 'ZAP-' + i, manufacturer: 'Samsung', package: '0402', description: 'cap ' + i,
    qty: 100, unit_price: last ? 0 : 0.01, ext_price: last ? 0 : 1.0,
  });
}
const MOCK_PRODUCTS = {
  ['lcsc:' + PRICELESS_LCSC]: {
    productCode: PRICELESS_LCSC, title: 'cap', manufacturer: 'Samsung', mpn: 'ZAP-59',
    package: '0402', description: 'cap 59', stock: 1000,
    prices: [{ qty: 1, price: 0.02 }, { qty: 100, price: 0.005 }], provider: 'lcsc',
  },
};

const body = (page) => page.locator('#inventory-body');
const rowOf = (page, lcsc) => page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');
const scrollTop = (page) => body(page).evaluate((el) => el.scrollTop);

async function setup(page, { withBom }) {
  await addMockSetup(page, INVENTORY, { productMocks: MOCK_PRODUCTS, lastPoQty: { [PRICELESS_LCSC]: 100 } });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  if (withBom) {
    await loadBom(page, BOM_CSV);   // inventory panel → BOM-comparison render path
    await page.waitForTimeout(150);
  }
}

/** Scroll the body to the bottom and assert a non-trivial position. */
async function scrollToBottom(page) {
  await body(page).focus();
  await page.keyboard.press('End');
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(100);
}

/** Assert scroll is essentially unchanged after a mutation settles. */
async function expectPreserved(page, before) {
  await page.waitForTimeout(400);   // allow async re-renders (vendor/PO refresh) to settle
  const after = await scrollTop(page);
  expect(Math.abs(after - before)).toBeLessThanOrEqual(2);
}

for (const mode of [{ name: 'normal mode', withBom: false }, { name: 'BOM mode', withBom: true }]) {
  test.describe('inventory scroll preserved across mutations — ' + mode.name, () => {
    test.beforeEach(async ({ page }) => { await setup(page, { withBom: mode.withBom }); });

    test('saving a fetched price from the ⚠ price modal', async ({ page }) => {
      await scrollToBottom(page);
      const warn = rowOf(page, PRICELESS_LCSC).locator('.price-warn-btn');
      await warn.scrollIntoViewIfNeeded();
      await warn.click();
      await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

      await page.locator('#price-fetch-price').click();
      await expect.poll(() => page.locator('#price-unit').inputValue()).not.toBe('');

      const before = await scrollTop(page);
      expect(before).toBeGreaterThan(100);
      await page.locator('#price-apply').click();
      await expect(page.locator('#price-modal')).toHaveClass(/hidden/);
      await expectPreserved(page, before);
    });

    test('typing a price in the ⚠ price modal and saving', async ({ page }) => {
      await scrollToBottom(page);
      const warn = rowOf(page, PRICELESS_LCSC).locator('.price-warn-btn');
      await warn.scrollIntoViewIfNeeded();
      await warn.click();
      await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

      await page.locator('#price-unit').fill('0.0123');
      const before = await scrollTop(page);
      expect(before).toBeGreaterThan(100);
      await page.locator('#price-apply').click();
      await expect(page.locator('#price-modal')).toHaveClass(/hidden/);
      await expectPreserved(page, before);
    });

    test('a qty adjustment from the Adjust modal', async ({ page }) => {
      await scrollToBottom(page);
      const row = rowOf(page, PRICELESS_LCSC);
      await row.locator('.adj-btn').click();
      await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

      const before = await scrollTop(page);
      expect(before).toBeGreaterThan(100);
      await page.locator('#adj-type').selectOption('set');
      await page.locator('#adj-qty').fill('42');
      await page.locator('#adj-apply').click();
      await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
      await expectPreserved(page, before);
    });

    test('a price change from the Adjust modal', async ({ page }) => {
      await scrollToBottom(page);
      const row = rowOf(page, PRICELESS_LCSC);
      await row.locator('.adj-btn').click();
      await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

      await page.locator('#adj-unit-price').fill('0.077');
      const before = await scrollTop(page);
      expect(before).toBeGreaterThan(100);
      await page.locator('#adj-qty').fill('100');
      await page.locator('#adj-apply').click();
      await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
      await expectPreserved(page, before);
    });
  });
}

// A mid-list scroll position (not the bottom) must also survive a mutation —
// guards against a fix that only "works" because the bottom clamps.
test('mid-list scroll position is preserved (BOM mode, Adjust)', async ({ page }) => {
  await setup(page, { withBom: true });
  await body(page).focus();
  await page.keyboard.press('PageDown');
  await page.keyboard.press('PageDown');
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(100);

  // Adjust the first row currently in view at this position.
  const row = page.locator('.inv-part-row').filter({ has: page.locator('.adj-btn') }).first();
  await row.locator('.adj-btn').scrollIntoViewIfNeeded();
  const before = await scrollTop(page);
  await row.locator('.adj-btn').click();
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill('9');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  await expectPreserved(page, before);
});
