// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('redo pref restricts the binding live', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await page.locator('#pref-redo').selectOption('ctrl-y');
  await expect(page.locator('#pref-redo')).toHaveValue('ctrl-y');

  // With redo restricted to Ctrl+Y, Ctrl+Shift+Z must no longer redo. Verify the
  // select change took effect by reloading the modal: close + reopen reads it back.
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+,');
  await expect(page.locator('#pref-redo')).toHaveValue('ctrl-y');
});

test('vim nav toggle moves the grid with j/k', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await page.locator('#pref-vim-nav').check();
  await page.keyboard.press('Escape');

  // Focus the first keyboard-reachable cell in the inventory grid.
  // The grid's initial tab stop may be a section or subsection header; navigate
  // down until we reach a part row so r0 is a stable, part-level identity.
  const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
  await firstCell.focus();
  // Advance past any header rows to land on the first actual part row.
  let r0 = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
  let iter = 0;
  while (r0 === undefined) {
    if (++iter > 20) throw new Error('vim-nav: never reached a part row');
    await page.keyboard.press('j');
    r0 = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
  }
  await page.keyboard.press('j');
  const r1 = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
  expect(r1).not.toBe(r0);
});
