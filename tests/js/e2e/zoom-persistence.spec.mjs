// @ts-check
/* The zoom control's three input paths (slider, magnifier buttons, keyboard) must
   stay in lockstep, survive a reload, and work while a text field has focus. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks, addPersistentPrefsRouteMock } from './route-mocks.mjs';
import { setZoom } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/** The applied zoom, read off the CSS custom property the whole feature keys on. */
const appliedZoom = (page) => page.evaluate(() =>
  Number(getComputedStyle(document.documentElement).getPropertyValue('--ui-zoom')));

async function boot(page, { persistPrefs = false } = {}) {
  await installRouteMocks(page, MOCK_INVENTORY);
  if (persistPrefs) await addPersistentPrefsRouteMock(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
}

test.describe('Zoom control — the three input paths agree', () => {
  test('the header control renders at 100% on a fresh profile', async ({ page }) => {
    await boot(page);
    await expect(page.locator('#zoom-percent')).toHaveText('100%');
    expect(await appliedZoom(page)).toBeCloseTo(1, 5);
  });

  test('the magnifier buttons step one rung and update the readout', async ({ page }) => {
    await boot(page);
    await page.click('#zoom-out');
    await expect(page.locator('#zoom-percent')).toHaveText('90%');
    expect(await appliedZoom(page)).toBeCloseTo(0.9, 5);

    await page.click('#zoom-in');
    await expect(page.locator('#zoom-percent')).toHaveText('100%');

    await page.click('#zoom-in');
    await expect(page.locator('#zoom-percent')).toHaveText('110%');
    expect(await appliedZoom(page)).toBeCloseTo(1.1, 5);
  });

  test('the keyboard shortcuts move the slider and the readout together', async ({ page }) => {
    await boot(page);
    const slider = page.locator('#zoom-slider');
    const startIndex = Number(await slider.inputValue());

    await page.keyboard.press('Control+Minus');
    await expect(page.locator('#zoom-percent')).toHaveText('90%');
    expect(Number(await slider.inputValue()),
      'Ctrl+- must visibly move the slider, not just the applied zoom')
      .toBe(startIndex - 1);

    await page.keyboard.press('Control+Equal');
    await expect(page.locator('#zoom-percent')).toHaveText('100%');
    expect(Number(await slider.inputValue())).toBe(startIndex);
  });

  test('Ctrl+0 resets from any level', async ({ page }) => {
    await boot(page);
    await setZoom(page, 150);
    await expect(page.locator('#zoom-percent')).toHaveText('150%');
    await page.keyboard.press('Control+Digit0');
    await expect(page.locator('#zoom-percent')).toHaveText('100%');
    expect(await appliedZoom(page)).toBeCloseTo(1, 5);
  });

  test('clicking the percentage readout resets to 100%', async ({ page }) => {
    await boot(page);
    await setZoom(page, 50);
    await expect(page.locator('#zoom-percent')).toHaveText('50%');
    await page.click('#zoom-percent');
    await expect(page.locator('#zoom-percent')).toHaveText('100%');
  });

  test('zoom shortcuts still fire while the search box has focus', async ({ page }) => {
    await boot(page);
    const search = page.locator('#inv-search');
    await search.click();
    await search.type('100nF');
    await expect(search).toBeFocused();

    // Browser zoom works while typing, so this must too — and the keystroke must
    // not leak into the field.
    await page.keyboard.press('Control+Minus');
    await expect(page.locator('#zoom-percent')).toHaveText('90%');
    await expect(search).toHaveValue('100nF');
    await expect(search).toBeFocused();
  });

  test('dragging the slider applies a ladder value, never an arbitrary one', async ({ page }) => {
    await boot(page);
    const slider = page.locator('#zoom-slider');
    // The slider's value is an index into ZOOM_STEPS, so every reachable position
    // maps to a level the keyboard can also produce.
    await slider.fill('0');
    await slider.dispatchEvent('input');
    await expect(page.locator('#zoom-percent')).toHaveText('50%');
    expect(await appliedZoom(page)).toBeCloseTo(0.5, 5);

    await slider.fill('10');
    await slider.dispatchEvent('input');
    await expect(page.locator('#zoom-percent')).toHaveText('200%');
    expect(await appliedZoom(page)).toBeCloseTo(2, 5);
  });

  test('the magnifier buttons disable at the ends of the ladder', async ({ page }) => {
    await boot(page);
    await setZoom(page, 50);
    await expect(page.locator('#zoom-out')).toBeDisabled();
    await expect(page.locator('#zoom-in')).toBeEnabled();

    await setZoom(page, 200);
    await expect(page.locator('#zoom-in')).toBeDisabled();
    await expect(page.locator('#zoom-out')).toBeEnabled();

    await setZoom(page, 100);
    await expect(page.locator('#zoom-in')).toBeEnabled();
    await expect(page.locator('#zoom-out')).toBeEnabled();
  });

  test('Ctrl+- at the minimum stays at the minimum rather than wrapping', async ({ page }) => {
    await boot(page);
    await setZoom(page, 50);
    await page.keyboard.press('Control+Minus');
    await page.keyboard.press('Control+Minus');
    await expect(page.locator('#zoom-percent')).toHaveText('50%');
  });
});

test.describe('Zoom persistence', () => {
  test('the chosen zoom is written to preferences', async ({ page }) => {
    await boot(page, { persistPrefs: true });
    await page.click('#zoom-out');
    await expect(page.locator('#zoom-percent')).toHaveText('90%');

    await page.waitForFunction(() => {
      const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
      return !!raw && JSON.parse(raw).ui_zoom === 0.9;
    }, null, { timeout: 5000 });
  });

  test('the chosen zoom survives a reload and is applied before the grid renders',
    async ({ page }) => {
      await boot(page, { persistPrefs: true });
      await page.click('#zoom-out');
      await expect(page.locator('#zoom-percent')).toHaveText('90%');
      await page.waitForFunction(() => {
        const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
        return !!raw && JSON.parse(raw).ui_zoom === 0.9;
      }, null, { timeout: 5000 });

      await page.reload();
      await waitForInventoryRows(page);

      expect(await appliedZoom(page), 'stored zoom should be re-applied on load')
        .toBeCloseTo(0.9, 5);
      await expect(page.locator('#zoom-percent')).toHaveText('90%');
      expect(Number(await page.locator('#zoom-slider').inputValue()),
        'the slider should reflect the restored zoom, not its markup default').toBe(4);
    });

  test('a malformed stored zoom falls back to 100% instead of breaking the UI',
    async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY, {
        preferences: { thresholds: {}, ui_zoom: 'not-a-number' },
      });
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);

      expect(await appliedZoom(page)).toBeCloseTo(1, 5);
      await expect(page.locator('#zoom-percent')).toHaveText('100%');
    });

  test('an out-of-range stored zoom snaps onto the ladder', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      preferences: { thresholds: {}, ui_zoom: 99 },
    });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    expect(await appliedZoom(page)).toBeCloseTo(2, 5);
    await expect(page.locator('#zoom-percent')).toHaveText('200%');
  });
});
