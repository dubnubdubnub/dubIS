// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

test.describe('Search bar keyboard shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('Ctrl+F focuses the search input ready for typing', async ({ page }) => {
    const search = page.locator('#inv-search');
    await expect(search).not.toBeFocused();

    await page.keyboard.press('Control+f');
    await expect(search).toBeFocused();

    // The user can immediately type — no extra click needed.
    await page.keyboard.type('Resistor');
    await expect(search).toHaveValue('Resistor');
  });

  test('Ctrl+F selects existing text so typing replaces it', async ({ page }) => {
    const search = page.locator('#inv-search');
    await search.fill('Capacitor');

    await page.keyboard.press('Control+f');
    await expect(search).toBeFocused();

    // Existing text is selected; typing overwrites rather than appends.
    await page.keyboard.type('Resistor');
    await expect(search).toHaveValue('Resistor');
  });

  test('Escape clears the search bar and removes the filter', async ({ page }) => {
    const search = page.locator('#inv-search');
    const allRows = await page.locator('.inv-part-row').count();
    expect(allRows).toBeGreaterThan(1);

    await page.keyboard.press('Control+f');
    await page.keyboard.type('Resistor');
    await page.waitForTimeout(300); // debounce + re-render
    const filteredRows = await page.locator('.inv-part-row').count();
    expect(filteredRows).toBeLessThan(allRows);

    await page.keyboard.press('Escape');

    // Bar is cleared, blurred, and all rows are restored.
    await expect(search).toHaveValue('');
    await expect(search).not.toBeFocused();
    await page.waitForTimeout(300);
    const restoredRows = await page.locator('.inv-part-row').count();
    expect(restoredRows).toBe(allRows);
  });
});
