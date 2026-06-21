// tests/js/e2e/shortcuts.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Global shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('Ctrl+, opens preferences', async ({ page }) => {
    await page.keyboard.press('Control+,');
    await expect(page.locator('#prefs-modal')).toBeVisible();
  });

  test('Ctrl+2 focuses the inventory panel', async ({ page }) => {
    await page.keyboard.press('Control+2');
    const inside = await page.evaluate(() => !!document.getElementById('inventory-body')?.contains(document.activeElement) || document.activeElement?.id === 'inventory-body');
    expect(inside).toBe(true);
  });

  test('Ctrl+1/2/3 move focus between panels', async ({ page }) => {
    await page.keyboard.press('Control+1');
    const p1 = await page.evaluate(() => document.activeElement?.closest('.panel')?.id);
    await page.keyboard.press('Control+3');
    const p3 = await page.evaluate(() => document.activeElement?.closest('.panel')?.id);
    expect(p1).toBe('panel-import');
    expect(p3).toBe('panel-bom');
  });

  test('? opens the shortcut help overlay', async ({ page }) => {
    await page.keyboard.press('?');
    await expect(page.locator('#help-modal')).toBeVisible();
    await expect(page.locator('#help-body')).toContainText('Redo');
    await page.keyboard.press('Escape');
    await expect(page.locator('#help-modal')).toBeHidden();
  });
});
