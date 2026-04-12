// @ts-check
import { test, expect } from '@playwright/test';
import { startServer, addLiveSetup, waitForInventoryRows } from './live-helpers.mjs';

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');

test.describe('Adjustment modal', () => {
  let server;
  test.beforeAll(async () => { server = await startServer(); });
  test.afterAll(async () => { await server.cleanup(); });
  test.beforeEach(async ({ page }) => {
    await server.reset();
    await addLiveSetup(page, server.url);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('opens with correct part info when clicking Adjust', async ({ page }) => {
    const row = partRow(page, 'C25794');
    await row.locator('.adj-btn').click();

    const modal = page.locator('#adjust-modal');
    await expect(modal).not.toHaveClass(/hidden/);
    await expect(page.locator('#modal-title')).toContainText('C25794');
    await expect(page.locator('#adj-qty')).toBeVisible();
    await expect(page.locator('#adj-type')).toBeVisible();
    await expect(page.locator('#adj-note')).toBeVisible();
  });

  test('cancel closes modal without changes', async ({ page }) => {
    const row = partRow(page, 'C25794');
    await row.locator('.adj-btn').click();

    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await page.locator('#adj-cancel').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await expect(row.locator('.part-qty')).toContainText('500');
  });

  test('"Set to" adjustment updates inventory qty', async ({ page }) => {
    const row = partRow(page, 'C25794');
    await row.locator('.adj-btn').click();

    await page.locator('#adj-type').selectOption('set');
    await page.locator('#adj-qty').fill('42');
    await page.locator('#adj-note').fill('test set');
    await page.locator('#adj-apply').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await expect(row.locator('.part-qty')).toContainText('42');
  });

  test('"Add" adjustment increases qty', async ({ page }) => {
    const row = partRow(page, 'C2286');
    await row.locator('.adj-btn').click();

    await page.locator('#adj-type').selectOption('add');
    await page.locator('#adj-qty').fill('25');
    await page.locator('#adj-note').fill('test add');
    await page.locator('#adj-apply').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await expect(row.locator('.part-qty')).toContainText('125');
  });

  test('"Remove" adjustment decreases qty', async ({ page }) => {
    const row = partRow(page, 'C440198');
    await row.locator('.adj-btn').click();

    await page.locator('#adj-type').selectOption('remove');
    await page.locator('#adj-qty').fill('50');
    await page.locator('#adj-note').fill('test remove');
    await page.locator('#adj-apply').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await expect(row.locator('.part-qty')).toContainText('250');
  });

  test('price edit updates unit and ext price', async ({ page }) => {
    const row = partRow(page, 'C429942');
    await row.locator('.adj-btn').click();

    await page.locator('#adj-unit-price').fill('0.50');
    await page.locator('#adj-type').selectOption('set');
    await page.locator('#adj-qty').fill('30');
    await page.locator('#adj-note').fill('test price');
    await page.locator('#adj-apply').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    // 30 * 0.50 = 15.00
    await expect(row.locator('.part-value')).toContainText('15.00');
  });

  test('field edit updates part metadata', async ({ page }) => {
    const row = partRow(page, 'C2286');
    await row.locator('.adj-btn').click();

    await page.locator('.modal-field-input[data-field="mpn"]').fill('NEW-MPN-123');
    await page.locator('#adj-type').selectOption('set');
    await page.locator('#adj-qty').fill('100');
    await page.locator('#adj-note').fill('test field');
    await page.locator('#adj-apply').click();

    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await expect(row.locator('.part-mpn')).toContainText('NEW-MPN-123');
  });

  test('Escape key closes modal', async ({ page }) => {
    const row = partRow(page, 'C25794');
    await row.locator('.adj-btn').click();

    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await page.keyboard.press('Escape');
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  });
});
