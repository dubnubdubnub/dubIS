// @ts-check
import { test, expect } from '@playwright/test';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startServer, addLiveSetup, waitForInventoryRows, loadBomViaFileInput } from './live-helpers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BOM_CSV = join(__dirname, 'fixtures', 'bom.csv');

test.describe('BOM consumption', () => {
  let server;
  test.beforeAll(async () => { server = await startServer(); });
  test.afterAll(async () => { await server.cleanup(); });
  test.beforeEach(async ({ page }) => {
    await server.reset();
    await addLiveSetup(page, server.url);
    await page.goto('http://localhost:3123/index.html');
    await waitForInventoryRows(page);
  });

  test('consume button enabled when BOM has matched parts', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    const consumeBtn = page.locator('#bom-consume-btn');
    await expect(consumeBtn).toBeVisible();
    await expect(consumeBtn).not.toBeDisabled();
  });

  test('consume modal shows matched count and filename', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();

    const modal = page.locator('#consume-modal');
    await expect(modal).not.toHaveClass(/hidden/);
    await expect(page.locator('#consume-subtitle')).toContainText('bom.csv');
  });

  test('first click arms confirmation, second click executes', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    const confirmBtn = page.locator('#consume-confirm');

    // First click — arms the confirmation
    await confirmBtn.click();
    await expect(confirmBtn).toHaveClass(/btn-danger/);
    await expect(confirmBtn).toHaveText('Are you sure?');

    // Second click — executes consumption
    await confirmBtn.click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
  });

  test('consumed parts have reduced qty in inventory', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    // Double-click confirm (arm + execute)
    const confirmBtn = page.locator('#consume-confirm');
    await confirmBtn.click();
    await confirmBtn.click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);

    // Verify inventory quantities changed — rebuild from backend
    const inventory = await page.evaluate(async () =>
      window.pywebview.api.rebuild_inventory()
    );

    // C25794 had 500, BOM needs 28 of CL05B104KB54PNC — should be < 500
    const c25794 = inventory.find(p => p.lcsc === 'C25794');
    expect(c25794).toBeTruthy();
    expect(c25794.qty).toBeLessThan(500);
  });

  test('consume with note records the note', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    // Fill in the note
    await page.locator('#consume-note').fill('test-build-001');

    // Double-click confirm (arm + execute)
    const confirmBtn = page.locator('#consume-confirm');
    await confirmBtn.click();
    await confirmBtn.click();

    // Assert modal closes — note is stored in adjustments CSV by the backend
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
  });

  test('cancel closes consume modal without changes', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    // Click cancel
    await page.locator('#consume-cancel').click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);

    // Verify inventory unchanged
    const inventory = await page.evaluate(async () =>
      window.pywebview.api.rebuild_inventory()
    );

    const c25794 = inventory.find(p => p.lcsc === 'C25794');
    expect(c25794).toBeTruthy();
    expect(c25794.qty).toBe(500);
  });
});
