// @ts-check
//
// In-app "Install Tesseract" affordance E2E. When the OCR engine is missing,
// the image/PDF import zone shows a notice with an Install button + a copyable
// winget command fallback. Realistic interactions only (real .click(); no
// dispatchEvent, no force).
//
// - engine missing → notice + #install-tesseract-btn render in the OCR zone.
// - success path → real click installs, success toast, notice removed.
// - failure path → button re-enabled, message shown, command fallback still visible.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

test.describe('Install Tesseract button', () => {
  test('engine missing: notice + Install button render with command fallback', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, { ocrEngineAvailable: false });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#ocr-engine-missing')).toBeVisible();
    await expect(page.locator('#install-tesseract-btn')).toBeVisible();
    // The copyable winget command fallback is present.
    await expect(page.locator('#ocr-engine-missing code')).toContainText('UB-Mannheim.TesseractOCR');
  });

  test('engine present: no notice renders', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, { ocrEngineAvailable: true });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#import-ocr-zone')).toBeVisible();
    await expect(page.locator('#ocr-engine-missing')).toHaveCount(0);
  });

  test('success path: real click installs and removes the notice', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      ocrEngineAvailable: false,
      installTesseractResult: { ok: true, message: 'Tesseract installed.', available: true },
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#install-tesseract-btn')).toBeVisible();
    await page.locator('#install-tesseract-btn').click();

    // Success toast appears and the notice goes away (OCR zone usable again).
    await expect(page.locator('.toast')).toContainText('Tesseract installed');
    await expect(page.locator('#ocr-engine-missing')).toHaveCount(0);
    await expect(page.locator('#import-ocr-input')).toBeAttached();
  });

  test('failure path: button re-enabled, message shown, fallback still visible', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      ocrEngineAvailable: false,
      installTesseractResult: { ok: false, available: false, message: 'winget exited 1. boom' },
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const btn = page.locator('#install-tesseract-btn');
    await expect(btn).toBeVisible();
    await btn.click();

    // Failure message surfaced, notice + command fallback still present,
    // button re-enabled for a retry.
    await expect(page.locator('.toast')).toContainText('winget exited 1');
    await expect(page.locator('#ocr-engine-missing')).toBeVisible();
    await expect(page.locator('#ocr-engine-missing code')).toContainText('UB-Mannheim.TesseractOCR');
    await expect(btn).toBeEnabled();
    await expect(btn).toHaveText('Install Tesseract');
  });
});
