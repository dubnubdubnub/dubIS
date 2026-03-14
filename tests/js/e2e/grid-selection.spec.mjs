// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');

/** Helper: load BOM and return the first data cell (skip delete + status cols). */
async function setupGrid(page) {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await loadBomViaFileInput(page, BOM_CSV_PATH);
}

// ── Cell selection ──

test.describe('Grid selection — basics', () => {

  test('clicking a cell shows .grid-sel overlay', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.scrollIntoViewIfNeeded();
    await cell.click();

    const sel = page.locator('.bom-table-wrap .grid-sel');
    await expect(sel).toBeVisible();
  });

  test('arrow keys move selection', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.click();

    // Move down
    await page.keyboard.press('ArrowDown');
    // The .grid-sel should still be visible (moved to next row)
    await expect(page.locator('.bom-table-wrap .grid-sel')).toBeVisible();
  });

  test('Tab moves right, Enter moves down', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.click();

    await page.keyboard.press('Tab');
    await expect(page.locator('.bom-table-wrap .grid-sel')).toBeVisible();

    await page.keyboard.press('Enter');
    await expect(page.locator('.bom-table-wrap .grid-sel')).toBeVisible();
  });
});

// ── Edit mode ──

test.describe('Grid selection — editing', () => {

  test('double-click enters edit mode with floating input', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.dblclick();

    const editInput = page.locator('.bom-table-wrap .grid-edit-input');
    await expect(editInput).toBeVisible();
  });

  test('type-to-edit: typing a character enters edit mode', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.click();

    await page.keyboard.type('X');
    const editInput = page.locator('.bom-table-wrap .grid-edit-input');
    await expect(editInput).toBeVisible();
    await expect(editInput).toHaveValue('X');
  });

  test('Escape cancels edit and reverts value', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    const originalText = await cell.textContent();

    await cell.dblclick();
    const editInput = page.locator('.bom-table-wrap .grid-edit-input');
    await editInput.fill('CHANGED');
    await page.keyboard.press('Escape');

    // Floating input should be hidden
    await expect(editInput).not.toBeVisible();
    // Cell text should revert
    await expect(cell).toHaveText(originalText || '');
  });

  test('Tab commits edit and moves to next cell', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.click();
    await page.keyboard.type('NEWVAL');
    await page.keyboard.press('Tab');

    // Edit input should be hidden (committed)
    await expect(page.locator('.bom-table-wrap .grid-edit-input')).not.toBeVisible();
    // Cell should have new value
    await expect(cell).toHaveText('NEWVAL');
  });
});

// ── Range selection ──

test.describe('Grid selection — range', () => {

  test('Shift+click creates a range overlay', async ({ page }) => {
    await setupGrid(page);
    const cell1 = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell1.click();

    const cell2 = page.locator('#bom-tbody tr:nth-child(3) td').nth(3);
    await cell2.click({ modifiers: ['Shift'] });

    const range = page.locator('.bom-table-wrap .grid-range');
    await expect(range).toBeVisible();
  });

  test('Delete clears all cells in range', async ({ page }) => {
    await setupGrid(page);
    const cell1 = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell1.click();

    const cell2 = page.locator('#bom-tbody tr:first-child td').nth(3);
    await cell2.click({ modifiers: ['Shift'] });

    await page.keyboard.press('Delete');

    // Both cells should now be empty
    await expect(cell1).toHaveText('');
    await expect(cell2).toHaveText('');
  });
});

// ── Fill handle ──

test.describe('Grid selection — fill handle', () => {

  test('fill handle is visible after selecting a cell', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    await cell.click();

    const handle = page.locator('.bom-table-wrap .grid-fill-handle');
    await expect(handle).toBeVisible();
  });
});

// ── Row classes preserved ──

test.describe('Grid selection — row styling', () => {

  test('DNP rows keep .row-dnp class after cell selection', async ({ page }) => {
    await setupGrid(page);

    // Verify DNP rows exist
    const dnpRows = page.locator('#bom-tbody tr.row-dnp');
    const count = await dnpRows.count();
    expect(count).toBeGreaterThan(0);

    // Click a cell in the first DNP row
    const dnpCell = dnpRows.first().locator('td').nth(2);
    await dnpCell.scrollIntoViewIfNeeded();
    await dnpCell.click();

    // Row should still have .row-dnp
    await expect(dnpRows.first()).toHaveClass(/row-dnp/);
  });

  test('warning rows keep .row-warn class', async ({ page }) => {
    await setupGrid(page);

    const warnRows = page.locator('#bom-tbody tr.row-warn');
    const count = await warnRows.count();
    if (count === 0) return; // Skip if no warn rows in fixture

    const warnCell = warnRows.first().locator('td').nth(2);
    await warnCell.scrollIntoViewIfNeeded();
    await warnCell.click();

    await expect(warnRows.first()).toHaveClass(/row-warn/);
  });
});

// ── Undo integration ──

test.describe('Grid selection — undo', () => {

  test('Ctrl+Z reverts cell edit (global undo, not in edit mode)', async ({ page }) => {
    await setupGrid(page);
    const cell = page.locator('#bom-tbody tr:first-child td').nth(2);
    const originalText = await cell.textContent();

    // Edit the cell
    await cell.click();
    await page.keyboard.type('UNDO_TEST');
    await page.keyboard.press('Tab');
    await expect(cell).toHaveText('UNDO_TEST');

    // Click another cell to ensure we're in select mode, not edit mode
    const otherCell = page.locator('#bom-tbody tr:nth-child(2) td').nth(2);
    await otherCell.click();

    // Undo
    await page.keyboard.press('Control+z');

    // Cell should revert (after re-render)
    await expect(cell).toHaveText(originalText || '');
  });
});
