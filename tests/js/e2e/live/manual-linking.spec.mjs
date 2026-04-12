// @ts-check
import { test, expect } from '@playwright/test';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startServer, addLiveSetup, waitForInventoryRows, loadBomViaFileInput } from './live-helpers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BOM_CSV = join(__dirname, 'fixtures', 'bom.csv');

test.describe('Manual linking', () => {
  let server;
  test.beforeAll(async () => { server = await startServer(); });
  test.afterAll(async () => { await server.cleanup(); });
  test.beforeEach(async ({ page }) => {
    await server.reset();
    await addLiveSetup(page, server.url);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);
  });

  test('link button visible on missing BOM rows in comparison table', async ({ page }) => {
    // After BOM load, the inventory panel shows a BOM comparison table.
    // Missing rows (row-red) should have a link button.
    const missingRow = page.locator('#inventory-body tr.row-red').first();
    await expect(missingRow).toBeVisible();
    await expect(missingRow.locator('.link-btn')).toBeVisible();
  });

  test('clicking link on missing row activates linking mode', async ({ page }) => {
    // Click the link button on the first missing BOM row in the comparison table
    const missingRow = page.locator('#inventory-body tr.row-red').first();
    await expect(missingRow).toBeVisible();
    await missingRow.locator('.link-btn').click();

    // Linking banner should appear in the BOM panel
    const banner = page.locator('#linking-banner');
    await expect(banner).toBeVisible();

    // Inventory rows in the "remaining" section should get link-target class
    const linkTargets = page.locator('.inv-part-row.link-target');
    await expect(linkTargets.first()).toBeVisible();
  });

  test('clicking inventory part creates manual link', async ({ page }) => {
    // Activate reverse linking mode from a missing BOM row
    const missingRow = page.locator('#inventory-body tr.row-red').first();
    await missingRow.locator('.link-btn').click();
    await expect(page.locator('#linking-banner')).toBeVisible();

    // Click the first link-target inventory row
    const target = page.locator('.inv-part-row.link-target').first();
    await expect(target).toBeVisible();
    await target.click();

    // Banner should disappear
    await expect(page.locator('#linking-banner')).not.toBeVisible();

    // Toast should appear with "Linked"
    await expect(page.locator('#toast')).toContainText('Linked');
  });

  test('cancel linking dismisses banner', async ({ page }) => {
    const missingRow = page.locator('#inventory-body tr.row-red').first();
    await missingRow.locator('.link-btn').click();

    await expect(page.locator('#linking-banner')).toBeVisible();

    // Click the cancel button
    await page.locator('.cancel-link-btn').click();

    // Banner should disappear
    await expect(page.locator('#linking-banner')).not.toBeVisible();

    // No link-target classes should remain
    await expect(page.locator('.inv-part-row.link-target')).toHaveCount(0);
  });

  test('Escape key cancels linking mode', async ({ page }) => {
    const missingRow = page.locator('#inventory-body tr.row-red').first();
    await missingRow.locator('.link-btn').click();

    await expect(page.locator('#linking-banner')).toBeVisible();

    // Press Escape
    await page.keyboard.press('Escape');

    // Banner should disappear
    await expect(page.locator('#linking-banner')).not.toBeVisible();
  });

  test('confirm button on matched row changes status', async ({ page }) => {
    // Look for a confirm button in the comparison table (only on "possible" matches)
    const confirmBtn = page.locator('#inventory-body .confirm-btn').first();
    const count = await page.locator('#inventory-body .confirm-btn').count();
    if (count === 0) {
      // No confirmable matches with this fixture data — skip gracefully
      test.skip();
      return;
    }

    // Get the part key before clicking (clicking triggers a full re-render)
    const parentRow = confirmBtn.locator('xpath=ancestor::tr');
    const partKey = await parentRow.getAttribute('data-part-key');
    await confirmBtn.click();

    // After re-render, find the row by its data-part-key
    const updatedRow = page.locator(`#inventory-body tr[data-part-key="${partKey}"]`);
    await expect(updatedRow).toHaveClass(/row-teal/);

    // Unconfirm button should appear
    await expect(updatedRow.locator('.unconfirm-btn')).toBeVisible();
  });

  test('unconfirm reverts confirmed status', async ({ page }) => {
    const confirmBtn = page.locator('#inventory-body .confirm-btn').first();
    const count = await page.locator('#inventory-body .confirm-btn').count();
    if (count === 0) {
      test.skip();
      return;
    }

    // Get the part key before clicking
    const parentRow = confirmBtn.locator('xpath=ancestor::tr');
    const partKey = await parentRow.getAttribute('data-part-key');

    // First confirm the match
    await confirmBtn.click();

    // After re-render, find the confirmed row by its part key
    const confirmedRow = page.locator(`#inventory-body tr[data-part-key="${partKey}"]`);
    await expect(confirmedRow).toHaveClass(/row-teal/);

    // Now unconfirm it
    await confirmedRow.locator('.unconfirm-btn').click();

    // After re-render, row should revert to row-orange (possible)
    const revertedRow = page.locator(`#inventory-body tr[data-part-key="${partKey}"]`);
    await expect(revertedRow).toHaveClass(/row-orange/);
    await expect(revertedRow.locator('.confirm-btn')).toBeVisible();
  });
});
