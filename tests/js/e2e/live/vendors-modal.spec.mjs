// @ts-check
import { test, expect } from '@playwright/test';
import { resetServer, setupPage } from './setup-page.mjs';
import { waitForInventoryRows } from '../helpers.mjs';

/** Open the Vendors modal and wait for the list to render. */
async function openVendors(page) {
  await page.click('#vendors-btn');
  await expect(page.locator('#vendors-modal')).not.toHaveClass(/hidden/);
  // List always has at least the 3 built-in pseudo-vendors.
  await expect(page.locator('#vendor-list .vendor-row').first()).toBeVisible();
}

/** Add a vendor with the given name (no URL → no favicon network fetch). */
async function addVendor(page, name) {
  await page.click('#vendor-add-btn');
  await page.fill('#vendor-name', name);
  await page.click('#vendor-save-btn');
  // Row appears in the list.
  await expect(
    page.locator('.vendor-row .vendor-row-name', { hasText: new RegExp(`^${name}$`) }),
  ).toBeVisible();
}

test.describe('Vendors modal', () => {
  test.beforeEach(async ({ page }) => {
    await resetServer();
    await setupPage(page);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('opens and lists the built-in vendors', async ({ page }) => {
    await openVendors(page);
    const names = page.locator('#vendor-list .vendor-row-name');
    await expect(names.filter({ hasText: 'Self' })).toBeVisible();
    await expect(names.filter({ hasText: 'Salvage' })).toBeVisible();
    await expect(names.filter({ hasText: 'Unknown' })).toBeVisible();
    // Detail pane starts empty.
    await expect(page.locator('#vendor-detail .vendors-detail-empty')).toBeVisible();
  });

  test('add → edit → persists', async ({ page }) => {
    await openVendors(page);
    await addVendor(page, 'Acme Parts');

    // Select it and rename.
    await page.click('.vendor-row:has(.vendor-row-name:text-is("Acme Parts"))');
    await expect(page.locator('#vendor-name')).toHaveValue('Acme Parts');
    await page.fill('#vendor-name', 'Acme Components');
    await page.click('#vendor-save-btn');

    await expect(
      page.locator('.vendor-row-name', { hasText: /^Acme Components$/ }),
    ).toBeVisible();

    // Backend persisted the rename.
    const vendors = await page.evaluate(async () => window.pywebview.api.list_vendors());
    expect(vendors.some(v => v.name === 'Acme Components')).toBe(true);
    expect(vendors.some(v => v.name === 'Acme Parts')).toBe(false);
  });

  test('merge folds one vendor into another', async ({ page }) => {
    await openVendors(page);
    await addVendor(page, 'Vendor A');
    await addVendor(page, 'Vendor B');

    // Select A, merge into B.
    await page.click('.vendor-row:has(.vendor-row-name:text-is("Vendor A"))');
    await page.selectOption('#vendor-merge-target', { label: 'Vendor B' });
    await page.click('#vendor-merge-btn');

    // A is gone; B remains.
    await expect(
      page.locator('.vendor-row-name', { hasText: /^Vendor A$/ }),
    ).toHaveCount(0);
    await expect(
      page.locator('.vendor-row-name', { hasText: /^Vendor B$/ }),
    ).toBeVisible();
  });

  test('two-step delete removes a vendor', async ({ page }) => {
    await openVendors(page);
    await addVendor(page, 'Temp Vendor');

    await page.click('.vendor-row:has(.vendor-row-name:text-is("Temp Vendor"))');
    const delBtn = page.locator('#vendor-delete-btn');

    // First click arms (does not delete).
    await delBtn.click();
    await expect(delBtn).toHaveText('Really delete?');
    await expect(
      page.locator('.vendor-row-name', { hasText: /^Temp Vendor$/ }),
    ).toBeVisible();

    // Second click confirms.
    await delBtn.click();
    await expect(
      page.locator('.vendor-row-name', { hasText: /^Temp Vendor$/ }),
    ).toHaveCount(0);
  });

  test('built-in vendors are not editable', async ({ page }) => {
    await openVendors(page);
    await page.click('.vendor-row:has(.vendor-row-name:text-is("Self"))');

    // Name input is disabled; no edit/merge/delete controls are offered.
    await expect(page.locator('#vendor-name')).toBeDisabled();
    await expect(page.locator('#vendor-save-btn')).toHaveCount(0);
    await expect(page.locator('#vendor-delete-btn')).toHaveCount(0);
    await expect(page.locator('#vendor-merge-target')).toHaveCount(0);
    await expect(page.locator('.vendor-pseudo-note')).toBeVisible();
  });

  test('empty name shows a toast and does not save', async ({ page }) => {
    await openVendors(page);
    const before = await page.evaluate(async () =>
      (await window.pywebview.api.list_vendors()).length);

    await page.click('#vendor-add-btn');
    await page.fill('#vendor-name', '   ');
    await page.click('#vendor-save-btn');

    await expect(page.locator('#toast')).toHaveText(/Vendor name required/);
    const after = await page.evaluate(async () =>
      (await window.pywebview.api.list_vendors()).length);
    expect(after).toBe(before);
  });
});
