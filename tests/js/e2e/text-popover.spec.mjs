// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

test.describe('Text popover, double-click-select, auto-copy', () => {

  test('hover over a leaf text cell shows a popover with the full text and Copy works', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const cell = page.locator('.inv-part-row .part-mpn').first();
    const cellText = (await cell.innerText()).trim();
    expect(cellText.length).toBeGreaterThan(0);

    await cell.hover();
    const popover = page.locator('.text-popover:not(.hidden)');
    await expect(popover).toBeVisible({ timeout: 2000 }); // > 350ms show delay
    await expect(popover.locator('.text-popover-text')).toHaveText(cellText);

    await popover.locator('.text-popover-copy').click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.trim()).toBe(cellText);
  });

  test('double-clicking a plain value selects the whole value', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const cell = page.locator('.inv-part-row .part-mpn').first();
    const cellText = (await cell.innerText()).trim();
    await cell.dblclick();

    const selected = (await page.evaluate(() => window.getSelection().toString())).trim();
    expect(selected).toBe(cellText);
  });

  test('auto-copy preference copies selection when enabled, not when disabled', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const cell = page.locator('.inv-part-row .part-mpn').first();
    const cellText = (await cell.innerText()).trim();

    // Disabled by default: double-click selects but must NOT auto-copy.
    await page.evaluate(() => navigator.clipboard.writeText('SENTINEL'));
    await cell.dblclick();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('SENTINEL');

    // Enable via the prefs modal (realistic click), then re-select.
    await page.locator('#prefs-btn').click();
    await page.locator('#pref-auto-copy').check();
    await page.locator('#prefs-cancel').click(); // change persists on toggle, not on Save

    await cell.dblclick();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.trim()).toBe(cellText);
  });

  test('double-clicking a qty/price cell edits inline and does NOT select-whole', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const qtyCell = page.locator('.inv-part-row .part-qty').first();
    const qtyText = (await qtyCell.innerText()).trim();
    await qtyCell.dblclick();

    // Inline edit won: the cell became a focused editable input carrying the qty.
    const input = page.locator('.inv-inline-input');
    await expect(input).toBeVisible();
    await expect(input).toHaveValue(qtyText);

    // Proof the global "select whole text" dblclick handler did NOT also fire on this
    // cell: it would have called getSelection().removeAllRanges() + reselected the cell
    // contents, which blurs the input. The input staying focused shows inline-edit's
    // stopPropagation kept our handler out. (A raw getSelection() check can't be used —
    // the browser's native double-click word-selection of "30" is indistinguishable from
    // our select-whole for a single-token cell, so it would false-positive either way.)
    await expect(input).toBeFocused();
  });

  test('hovering an interactive button does NOT show the text popover', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const adjBtn = page.locator('.inv-part-row .adj-btn').first();
    await expect(adjBtn).toBeVisible();
    await adjBtn.hover();
    await page.waitForTimeout(600); // > 350ms show delay, so a false-positive would have shown by now

    const popover = page.locator('.text-popover:not(.hidden)');
    await expect(popover).toBeHidden();
  });
});
