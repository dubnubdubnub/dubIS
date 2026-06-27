// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('mirror toggle enables and shows serve URL', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  const cb = page.locator('#mirror-enabled');
  await expect(cb).not.toBeChecked();

  await cb.check();
  await expect(cb).toBeChecked();
  await expect(page.locator('#mirror-url')).toHaveValue('https://mauler.example.ts.net');
  await expect(page.locator('#mirror-status')).toContainText('Running');
});

test('mirror toggle disables and clears URL', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await page.locator('#mirror-enabled').check();
  await expect(page.locator('#mirror-url')).toHaveValue('https://mauler.example.ts.net');

  await page.locator('#mirror-enabled').uncheck();
  await expect(page.locator('#mirror-url')).toHaveValue('');
  await expect(page.locator('#mirror-status')).toContainText('Disabled');
});
