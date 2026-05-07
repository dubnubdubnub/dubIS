// @ts-check
import { test, expect } from '@playwright/test';
import { resetServer, setupPage } from './setup-page.mjs';
import { waitForInventoryRows } from '../helpers.mjs';

test.describe('Mouser API key in preferences modal', () => {
  test.beforeEach(async ({ page }) => {
    await resetServer();
    await setupPage(page);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    // Ensure no leftover key from a prior test run.
    await page.evaluate(async () => {
      await window.pywebview.api.clear_mouser_api_key();
    });
  });

  test('save → status flips to configured, clear → status flips back', async ({ page }) => {
    // Open prefs modal via the gear in the title row (id=prefs-btn).
    await page.click('#prefs-btn');
    const modal = page.locator('#prefs-modal');
    await expect(modal).not.toHaveClass(/hidden/);

    const status = page.locator('#mouser-status');
    const keyInput = page.locator('#mouser-api-key');
    const saveBtn = page.locator('#mouser-save');
    const clearBtn = page.locator('#mouser-clear');

    // Initial state: no key configured.
    await expect(status).toHaveText(/No API key/);
    await expect(clearBtn).toHaveClass(/hidden/);

    // Save a key.
    await keyInput.fill('test-api-key-12345');
    await saveBtn.click();

    // Status flips to configured; Clear button shows.
    await expect(status).toHaveText(/API key saved/);
    await expect(clearBtn).not.toHaveClass(/hidden/);
    // The input clears (so the saved key isn't visible) and placeholder hints
    // that the user can replace it.
    await expect(keyInput).toHaveValue('');
    await expect(keyInput).toHaveAttribute('placeholder', /Replace key/);

    // Backend confirms it persisted.
    const status1 = await page.evaluate(async () =>
      window.pywebview.api.get_mouser_api_key_status());
    expect(status1.configured).toBe(true);

    // Clear it.
    await clearBtn.click();
    await expect(status).toHaveText(/No API key/);
    await expect(clearBtn).toHaveClass(/hidden/);

    const status2 = await page.evaluate(async () =>
      window.pywebview.api.get_mouser_api_key_status());
    expect(status2.configured).toBe(false);
  });

  test('Save with empty input shows toast, does not flip status', async ({ page }) => {
    await page.click('#prefs-btn');
    const status = page.locator('#mouser-status');
    await expect(status).toHaveText(/No API key/);

    await page.click('#mouser-save');
    // Status stays unchanged; backend still has no key.
    await expect(status).toHaveText(/No API key/);
    const result = await page.evaluate(async () =>
      window.pywebview.api.get_mouser_api_key_status());
    expect(result.configured).toBe(false);
  });

  test('Enter key in input triggers save', async ({ page }) => {
    await page.click('#prefs-btn');
    const keyInput = page.locator('#mouser-api-key');
    await keyInput.fill('enter-key-test');
    await keyInput.press('Enter');

    await expect(page.locator('#mouser-status')).toHaveText(/API key saved/);
    const result = await page.evaluate(async () =>
      window.pywebview.api.get_mouser_api_key_status());
    expect(result.configured).toBe(true);
  });
});
