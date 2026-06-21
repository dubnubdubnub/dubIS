// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('focused panel body scrolls with PageDown and Home/End', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  const body = page.locator('#inventory-body');
  await body.evaluate((el) => el.focus());
  const before = await body.evaluate((el) => el.scrollTop);
  await page.keyboard.press('PageDown');
  const after = await body.evaluate((el) => el.scrollTop);
  expect(after).toBeGreaterThan(before);

  await page.keyboard.press('End');
  const atEnd = await body.evaluate((el) => el.scrollTop);
  await page.keyboard.press('Home');
  const atHome = await body.evaluate((el) => el.scrollTop);
  expect(atEnd).toBeGreaterThan(atHome);
  expect(atHome).toBe(0);
});
