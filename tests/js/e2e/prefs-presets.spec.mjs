// @ts-check
/* prefs-presets.spec.mjs — the threshold tab's preset picker, in the real
   modal: choosing a bundle re-renders the rows at the bundle's numbers,
   re-clicking the active bundle unpins back to the shipped defaults (the
   cart's segmented-control gesture), moving one slider marks exactly that row
   overridden, and Save persists ONLY the preset id plus the moved category —
   never the resolved table. The resolution order itself is pinned on both
   sides by tests/fixtures/thresholds/legacy-defaults.json; this is the wire
   between that logic and the picker. */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/** The number input of one category's row. */
function rowInput(page, section) {
  return page.locator(
    `#prefs-sliders .prefs-row:has(.prefs-slider[data-section="${section}"]) .prefs-input`);
}
function resetOf(page, section) {
  return page.locator(
    `#prefs-sliders .prefs-row:has(.prefs-slider[data-section="${section}"]) .prefs-reset`);
}

test('preset picker: choose, unpin, per-category override, sparse save', async ({ page }) => {
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await expect(page.locator('#prefs-modal')).toBeVisible();

  // Three segments — the shipped sentinel plus the two bundles — with the
  // shipped one active on a clean preferences file.
  const presets = page.locator('#prefs-presets .prefs-preset');
  await expect(presets).toHaveCount(3);
  await expect(presets.first()).toHaveAttribute('aria-pressed', 'true');

  // Choosing a bundle re-renders every row at what the category now answers:
  // ic-heavy moves the tuned IC name from the base to $200.
  await page.locator('.prefs-preset[data-preset="ic-heavy"]').click();
  await expect(page.locator('.prefs-preset[data-preset="ic-heavy"]'))
    .toHaveAttribute('aria-pressed', 'true');
  await expect(rowInput(page, 'ICs - Microcontrollers')).toHaveValue('200');

  // Re-clicking the active bundle unpins back to the shipped defaults — the
  // handleRowPresetChange gesture from js/cart/cart-modal.js.
  await page.locator('.prefs-preset[data-preset="ic-heavy"]').click();
  await expect(page.locator('.prefs-preset[data-preset="ic-heavy"]'))
    .toHaveAttribute('aria-pressed', 'false');
  await expect(presets.first()).toHaveAttribute('aria-pressed', 'true');
  await expect(rowInput(page, 'ICs - Microcontrollers')).toHaveValue('50');

  // Moving one slider marks exactly that row overridden — the reset control
  // is the marker, and it holds its space when inert (visibility, not
  // display), so the slider-edge alignment guard stays honest.
  await expect(resetOf(page, 'Diodes')).toHaveClass(/is-inert/);
  await expect(resetOf(page, 'Connectors')).toHaveClass(/is-inert/);
  await rowInput(page, 'Diodes').fill('17');
  await expect(resetOf(page, 'Diodes')).not.toHaveClass(/is-inert/);
  await expect(resetOf(page, 'Connectors')).toHaveClass(/is-inert/);

  // Reset snaps the row back to the inherited value, and the marker follows.
  await resetOf(page, 'Diodes').click();
  await expect(rowInput(page, 'Diodes')).toHaveValue('10');
  await expect(resetOf(page, 'Diodes')).toHaveClass(/is-inert/);

  // Save persists the preset id plus ONLY the categories that differ from
  // what the preset/default/derivation answers — the resolved table never
  // travels into preferences.json.
  await rowInput(page, 'Diodes').fill('17');
  await page.click('#prefs-save');
  await expect(page.locator('#prefs-modal')).toBeHidden();

  const calls = await page.evaluate(() => window.__apiCalls['save_preferences'] || []);
  expect(calls.length, 'one whole-bag save per Save click').toBe(1);
  // The router records the PARSED body (rawBody methods arrive as the object
  // the page put on the wire).
  const prefs = /** @type {{threshold_preset: string, thresholds: Record<string, number>}} */ (
    /** @type {unknown} */ (calls[0][0]));
  expect(prefs.threshold_preset).toBe('shipped');
  expect(prefs.thresholds, 'the delta only — not the resolved table').toEqual({ Diodes: 17 });
});
