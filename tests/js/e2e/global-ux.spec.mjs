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

// ── Undo/Redo buttons ──

test.describe('Global UX — undo/redo buttons', () => {

  test('both undo and redo are disabled on load', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#global-undo')).toBeDisabled();
    await expect(page.locator('#global-redo')).toBeDisabled();
  });

  test('after adjustment, undo becomes enabled; after undo, redo becomes enabled', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Perform an adjustment
    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await page.locator('#adj-qty').fill('99');
    await page.locator('#adj-apply').click();
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);

    // Wait for state update
    await page.waitForTimeout(300);

    // Undo should be enabled, redo should be disabled
    await expect(page.locator('#global-undo')).toBeEnabled();
    await expect(page.locator('#global-redo')).toBeDisabled();

    // Click undo
    await page.locator('#global-undo').click();
    await page.waitForTimeout(300);

    // Now undo should be disabled, redo should be enabled
    await expect(page.locator('#global-undo')).toBeDisabled();
    await expect(page.locator('#global-redo')).toBeEnabled();
  });
});

// ── Keyboard shortcuts ──

test.describe('Global UX — keyboard shortcuts', () => {

  test('Ctrl+Z triggers undo, Ctrl+Shift+Z triggers redo', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Perform an adjustment
    await page.locator('.adj-btn').first().click();
    await page.locator('#adj-qty').fill('99');
    await page.locator('#adj-apply').click();
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
    await page.waitForTimeout(300);

    await expect(page.locator('#global-undo')).toBeEnabled();

    // Ctrl+Z should undo
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(300);

    await expect(page.locator('#global-undo')).toBeDisabled();
    await expect(page.locator('#global-redo')).toBeEnabled();

    // Ctrl+Shift+Z should redo
    await page.keyboard.press('Control+Shift+Z');
    await page.waitForTimeout(300);

    await expect(page.locator('#global-undo')).toBeEnabled();
    await expect(page.locator('#global-redo')).toBeDisabled();
  });
});

// ── Preferences modal ──

test.describe('Global UX — preferences modal', () => {

  test('prefs button opens modal with sliders and inputs', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#prefs-modal')).toHaveClass(/hidden/);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    // Should have sliders and inputs
    const sliders = page.locator('.prefs-slider');
    const inputs = page.locator('.prefs-input');
    expect(await sliders.count()).toBeGreaterThan(0);
    expect(await inputs.count()).toBeGreaterThan(0);
  });

  test('slider input syncs with number input', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    const slider = page.locator('.prefs-slider').first();
    const input = page.locator('.prefs-input').first();

    // Change the number input — slider should update
    await input.fill('100');
    await input.dispatchEvent('input');

    const sliderVal = await slider.inputValue();
    expect(sliderVal).toBe('100');
  });

  test('cancel closes preferences modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    await page.locator('#prefs-cancel').click();
    await expect(page.locator('#prefs-modal')).toHaveClass(/hidden/);
  });

  test('save closes preferences modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    await page.locator('#prefs-save').click();
    await expect(page.locator('#prefs-modal')).toHaveClass(/hidden/);
  });

  test('#dk-status shows login status text', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    // Wait for async status check
    await page.waitForTimeout(500);

    const statusText = await page.locator('#dk-status').textContent();
    expect(statusText.length).toBeGreaterThan(0);
    // Mock returns { logged_in: false }, so should show "Not logged in"
    expect(statusText).toMatch(/Not logged in|Checking/);
  });
});

// ── Toast notifications ──

test.describe('Global UX — toast notifications', () => {

  test('toast appears after an action', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Perform an adjustment to trigger a toast
    await page.locator('.adj-btn').first().click();
    await page.locator('#adj-qty').fill('10');
    await page.locator('#adj-apply').click();

    // Toast should have content and .show class
    const toast = page.locator('#toast');
    await expect(toast).toHaveClass(/show/);
    const text = await toast.textContent();
    expect(text.length).toBeGreaterThan(0);
  });
});

// ── Console log ──

test.describe('Global UX — console log', () => {

  test('console entries appear after app init', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Wait for log entries to be generated during init
    await page.waitForTimeout(500);

    const entries = page.locator('#console-entries > *');
    const count = await entries.count();
    expect(count).toBeGreaterThan(0);
  });

  test('clear button empties console entries', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(500);

    // Verify there are entries first
    const entriesBefore = await page.locator('#console-entries > *').count();
    expect(entriesBefore).toBeGreaterThan(0);

    await page.locator('#console-clear').click();

    const entriesAfter = await page.locator('#console-entries > *').count();
    expect(entriesAfter).toBe(0);
  });
});

// ── Escape closes all modals ──

test.describe('Global UX — Escape closes modals', () => {

  test('Escape closes adjust modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  });

  test('Escape closes preferences modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#prefs-modal')).toHaveClass(/hidden/);
  });

  test('Escape closes consume modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
  });

  test('Escape closes price modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const priceBtn = page.locator('.price-warn-btn').first();
    if (await priceBtn.count() === 0) return;

    await priceBtn.click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#price-modal')).toHaveClass(/hidden/);
  });
});
