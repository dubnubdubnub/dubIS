// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Inventory roving grid', () => {
  test('arrow keys move focus within and across rows', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Focus the first grid cell directly (single tab stop established by refresh()).
    const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
    await firstCell.focus();
    await expect(firstCell).toBeFocused();

    // ArrowRight stays in the same row, moves to the next focusable cell.
    const r0 = await firstCell.evaluate((el) => el.closest('.inv-part-row')?.dataset.partId);
    await page.keyboard.press('ArrowRight');
    const afterRight = await page.evaluate(() => ({
      pid: document.activeElement?.closest('.inv-part-row')?.dataset.partId,
    }));
    expect(afterRight.pid).toBe(r0);

    // ArrowDown moves to a different row.
    await page.keyboard.press('ArrowDown');
    const afterDown = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
    expect(afterDown).not.toBe(r0);
  });

  test('only one tab stop exists in the inventory grid', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    const count = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(count).toBe(1);
  });
});
