// tests/js/e2e/command-palette.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

test.describe('Command palette (Ctrl+K)', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('Ctrl+K opens the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-overlay')).toBeVisible();
    // Search input should be present and focused
    await expect(page.locator('.cp-search')).toBeVisible();
    await expect(page.locator('.cp-search')).toBeFocused();
  });

  test('palette shows commands on open', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-item').first()).toBeVisible();
    // At least the global commands should be listed
    const count = await page.locator('.cp-item').count();
    expect(count).toBeGreaterThanOrEqual(5);
  });

  test('typing filters the command list', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.locator('.cp-search').fill('pref');
    // Only "Open Preferences" should remain
    await expect(page.locator('.cp-item')).toHaveCount(1);
    await expect(page.locator('.cp-item').first()).toContainText('Preferences');
  });

  test('Enter after filtering runs the matched command and closes palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.locator('.cp-search').fill('pref');
    // Wait for filter to apply — only "Open Preferences" should remain
    await expect(page.locator('.cp-item')).toHaveCount(1);
    // Navigate to the item with Down arrow (makes it active), then Enter to run it
    await page.locator('.cp-search').press('ArrowDown');
    await page.locator('.cp-search').press('Enter');
    // Palette should close
    await expect(page.locator('.cp-overlay')).not.toBeVisible();
    // Preferences modal should open (the command ran successfully)
    await expect(page.locator('#prefs-modal')).toBeVisible();
  });

  test('Escape closes the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-overlay')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.cp-overlay')).not.toBeVisible();
  });

  test('pressing Ctrl+K again while open closes the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-overlay')).toBeVisible();
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-overlay')).not.toBeVisible();
  });

  test('Up/Down navigation moves active item', async ({ page }) => {
    await page.keyboard.press('Control+k');
    // Initially no active item; Down makes first item active
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('.cp-item.cp-active').first()).toBeVisible();
    // Down again moves to second item
    await page.keyboard.press('ArrowDown');
    const activeItems = page.locator('.cp-item.cp-active');
    await expect(activeItems).toHaveCount(1);
  });

  test('clicking an item runs it and closes palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.locator('.cp-search').fill('shortcuts');
    await expect(page.locator('.cp-item')).toHaveCount(1);
    // Click the "Show Keyboard Shortcuts" item
    await page.locator('.cp-item').first().click();
    // Palette closes
    await expect(page.locator('.cp-overlay')).not.toBeVisible();
    // Help modal opens
    await expect(page.locator('#help-modal')).toBeVisible();
  });

  test('clicking outside the dialog closes the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cp-overlay')).toBeVisible();
    // Click directly on the overlay backdrop (outside the dialog)
    await page.locator('.cp-overlay').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('.cp-overlay')).not.toBeVisible();
  });

  test('help overlay includes Ctrl+K entry', async ({ page }) => {
    await page.keyboard.press('?');
    await expect(page.locator('#help-modal')).toBeVisible();
    await expect(page.locator('#help-body')).toContainText('Ctrl+K');
    await expect(page.locator('#help-body')).toContainText('Command palette');
  });
});
