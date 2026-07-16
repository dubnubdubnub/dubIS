// @ts-check
import { test, expect } from '@playwright/test';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks, assertHttpExercised } from './route-mocks.mjs';

const INVENTORY = [
  {
    section: 'Connectors', lcsc: 'C429942', mpn: 'DF40C-30DP',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'HRS', package: 'SMD', description: 'connector nano',
    qty: 30, unit_price: 0.29, ext_price: 8.57,
  },
  {
    section: 'Connectors', lcsc: 'C2040', mpn: 'USB-C-SMD',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'XKB', package: 'SMD', description: 'usb connector',
    qty: 10, unit_price: 0.50, ext_price: 5.00,
  },
  {
    section: 'Connectors', lcsc: 'C99999', mpn: 'FPC-20P',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'BOOMBIT', package: 'SMD', description: 'fpc connector',
    qty: 5, unit_price: 0.10, ext_price: 0.50,
  },
];

test.describe('Row handler mapping', () => {
  /** @type {{getHttpHits: () => number}} */
  let routeState;
  test.beforeEach(async ({ page }) => {
    routeState = await installRouteMocks(page, INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('clicking adjust on row N opens modal for row N', async ({ page }) => {
    const rows = page.locator('.inv-part-row');
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(3);

    for (let i = 0; i < 3; i++) {
      const row = rows.nth(i);
      const expectedLcsc = await row.locator('.part-id-lcsc').getAttribute('data-lcsc');

      await row.locator('.adj-btn').click();

      const modal = page.locator('#adjust-modal:not(.hidden)');
      await expect(modal).toBeVisible();

      const lcscInput = modal.locator('.modal-field-input[data-field="lcsc"]');
      await expect(lcscInput).toBeVisible();
      expect(await lcscInput.inputValue()).toBe(expectedLcsc);

      await modal.locator('#adj-cancel').click();
      await expect(modal).not.toBeVisible();
    }
    // openAdjustModal's adjFetch.configure() hits get_sourced_distributors/
    // get_last_po_quantity/get_price_summary/get_generic_group_names/
    // has_purchase_history over HTTP for every row opened above.
    await assertHttpExercised(routeState);
  });
});
