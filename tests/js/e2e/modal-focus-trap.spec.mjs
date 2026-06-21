// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('Enter key confirms the preferences modal (not in a textarea)', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  // Open the preferences modal via its trigger button.
  await page.locator('#prefs-btn').click();
  await expect(page.locator('#prefs-modal')).toBeVisible();

  // Focus is inside the modal — move to a non-textarea field so Enter fires confirm.
  const firstInput = page.locator('#prefs-modal input[type="number"]').first();
  await firstInput.focus();

  // Press Enter: the Modal factory should click #prefs-save, closing the modal.
  await page.keyboard.press('Enter');
  await expect(page.locator('#prefs-modal')).toBeHidden();
});

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
