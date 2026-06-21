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

    // Focus the first grid cell (single tab stop established by refresh()).
    // This may be a section header or a part-row cell depending on inventory layout.
    const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
    await firstCell.focus();
    await expect(firstCell).toBeFocused();

    // ArrowDown moves to a different row.
    const startTag = await firstCell.evaluate((el) => el.tagName + el.className);
    await page.keyboard.press('ArrowDown');
    const afterDownTag = await page.evaluate(() => document.activeElement?.tagName + document.activeElement?.className);
    expect(afterDownTag).not.toBe(startTag);
  });

  test('only one tab stop exists in the inventory grid', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    const count = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(count).toBe(1);
  });

  test('section header is keyboard-reachable and Enter toggles collapse', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Find a section header — it should be among the grid's roving cells.
    const header = page.locator('#inventory-body .inv-section-header, #inventory-body .inv-parent-header, #inventory-body .inv-subsection-header').first();
    await expect(header).toBeVisible();

    // Navigate via arrow keys from the grid's tab stop to reach a header.
    // The tab stop starts on the first roving cell; arrow down/up to land on a header.
    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    // The first roving cell in #inventory-body is a section header (headers come
    // first in DOM order). Confirm it IS the header we expect.
    const isHeader = await tabStop.evaluate((el) =>
      el.matches('.inv-section-header, .inv-parent-header, .inv-subsection-header'));
    expect(isHeader).toBe(true);

    // Count part rows before collapsing.
    const rowsBefore = await page.locator('#inventory-body .inv-part-row').count();
    expect(rowsBefore).toBeGreaterThan(0);

    // Press Enter on the focused header — triggers the click handler → collapse.
    await page.keyboard.press('Enter');

    // After collapse, part rows under this section should be hidden or removed.
    // The inventory re-renders on collapse, so wait for the row count to drop.
    await page.waitForFunction(
      (before) => document.querySelectorAll('#inventory-body .inv-part-row').length < before,
      rowsBefore,
      { timeout: 5000 },
    );
    const rowsAfter = await page.locator('#inventory-body .inv-part-row').count();
    expect(rowsAfter).toBeLessThan(rowsBefore);
  });
});
