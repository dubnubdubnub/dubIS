// @ts-check
import { test, expect } from '@playwright/test';
import { startServer, addLiveSetup, waitForInventoryRows } from './live-helpers.mjs';

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');

/**
 * Open adjust modal, set qty, and apply.
 * Waits for the modal to close and the inventory row to update.
 */
async function adjustPart(page, lcsc, qty) {
  await partRow(page, lcsc).locator('.adj-btn').click();
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill(String(qty));
  await page.locator('#adj-apply').click();
  await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  await expect(partRow(page, lcsc).locator('.part-qty')).toContainText(String(qty));
}

test.describe('Undo and redo', () => {
  let server;
  test.beforeAll(async () => { server = await startServer(); });
  test.afterAll(async () => { await server.cleanup(); });
  test.beforeEach(async ({ page }) => {
    await server.reset();
    await addLiveSetup(page, server.url);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('undo and redo buttons start disabled', async ({ page }) => {
    await expect(page.locator('#global-undo')).toBeDisabled();
    await expect(page.locator('#global-redo')).toBeDisabled();
  });

  test('adjust then undo reverts qty', async ({ page }) => {
    // C25794 starts at qty 500
    await adjustPart(page, 'C25794', 42);
    await expect(partRow(page, 'C25794').locator('.part-qty')).toContainText('42');

    // Undo button should be enabled after adjustment
    await expect(page.locator('#global-undo')).not.toBeDisabled();

    // Click undo
    await page.locator('#global-undo').click();

    // Qty should revert to 500
    await expect(partRow(page, 'C25794').locator('.part-qty')).toContainText('500');
  });

  test('undo then redo restores the adjustment', async ({ page }) => {
    // C2286 starts at qty 100
    await adjustPart(page, 'C2286', 77);

    // Undo -> verify 100
    await page.locator('#global-undo').click();
    await expect(partRow(page, 'C2286').locator('.part-qty')).toContainText('100');

    // Redo button should be enabled
    await expect(page.locator('#global-redo')).not.toBeDisabled();

    // Redo -> verify 77
    await page.locator('#global-redo').click();
    await expect(partRow(page, 'C2286').locator('.part-qty')).toContainText('77');
  });

  test('Ctrl+Z triggers undo', async ({ page }) => {
    // C440198 starts at qty 300
    await adjustPart(page, 'C440198', 10);

    // Ctrl+Z to undo
    await page.keyboard.press('Control+z');

    // Qty should revert to 300
    await expect(partRow(page, 'C440198').locator('.part-qty')).toContainText('300');
  });

  test('Ctrl+Shift+Z triggers redo', async ({ page }) => {
    // C440198 starts at qty 300
    await adjustPart(page, 'C440198', 10);

    // Ctrl+Z to undo -> 300
    await page.keyboard.press('Control+z');
    await expect(partRow(page, 'C440198').locator('.part-qty')).toContainText('300');

    // Ctrl+Shift+Z to redo -> 10
    await page.keyboard.press('Control+Shift+Z');
    await expect(partRow(page, 'C440198').locator('.part-qty')).toContainText('10');
  });

  test('multiple operations undo in stack order', async ({ page }) => {
    // C25794 starts at 500, C2286 starts at 100
    await adjustPart(page, 'C25794', 1);
    await adjustPart(page, 'C2286', 2);

    // First undo: C2286 reverts to 100, C25794 stays at 1
    await page.locator('#global-undo').click();
    await expect(partRow(page, 'C2286').locator('.part-qty')).toContainText('100');
    await expect(partRow(page, 'C25794').locator('.part-qty')).toContainText('1');

    // Second undo: C25794 reverts to 500
    await page.locator('#global-undo').click();
    await expect(partRow(page, 'C25794').locator('.part-qty')).toContainText('500');
  });
});
