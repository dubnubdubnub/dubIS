// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('preferences modal traps focus and restores it on Escape', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  const prefsBtn = page.locator('#prefs-btn');
  await prefsBtn.focus();
  await prefsBtn.click();
  await expect(page.locator('#prefs-modal')).toBeVisible();

  // Focus is inside the modal.
  const insideAtOpen = await page.evaluate(() => !!document.getElementById('prefs-modal')?.contains(document.activeElement));
  expect(insideAtOpen).toBe(true);

  // Tab several times — focus never leaves the modal.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    const inside = await page.evaluate(() => !!document.getElementById('prefs-modal')?.contains(document.activeElement));
    expect(inside).toBe(true);
  }

  // Escape closes and restores focus to the trigger.
  await page.keyboard.press('Escape');
  await expect(page.locator('#prefs-modal')).toBeHidden();
  await expect(prefsBtn).toBeFocused();
});
