// @ts-check
//
// Two-zone import panel E2E. Verifies the split CSV | image/PDF/phone layout
// and that each zone routes to its own flow with realistic interactions only
// (real setInputFiles / .click() / .selectOption() — no dispatchEvent, no force).
//
// - panel shows both zones; the removed ★ Direct button is absent.
// - CSV → inline staging mapper (no OCR overlay).
// - image → OCR overlay (mocked ocr_overlay_b64).
// - "Scan with phone" → QR scan modal (mocked start_scan_session).
// - "+ add row manually" → an editable staging row.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);
const PO_LCSC = path.join(__dirname, 'fixtures', 'po-lcsc.csv');

// Tiny valid 1x1 PNG (base64) for the image-zone OCR path.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';

const WORDS = [{ text: 'C12624', x: 10, y: 10, w: 30, h: 12, conf: 0.97, line_id: 0 }];
const OCR_RESULT = {
  template: 'generic',
  pages: [{ image_b64: PNG_1X1_B64, width: 100, height: 100, words: WORDS, lines: [] }],
  prefill_rows: [
    { mpn: '', manufacturer: '', description: '', package: '0603', quantity: 100, unit_price: 0.01 },
  ],
};

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown', url: '', favicon_path: '' },
  { id: 'v_self', name: 'Self', icon: '⚙️', type: 'self', url: '', favicon_path: '' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage', url: '', favicon_path: '' },
];

test.describe('Two-zone import panel', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('panel shows both zones and NO ★ Direct button', async ({ page }) => {
    await expect(page.locator('#import-drop-zone')).toBeVisible();
    await expect(page.locator('#import-ocr-zone')).toBeVisible();
    // The ★ "Direct from mfg" button and its class are gone.
    await expect(page.locator('[data-template="direct"]')).toHaveCount(0);
    await expect(page.locator('.new-po-btn-direct')).toHaveCount(0);
  });

  test('CSV zone: dropping a CSV opens the inline staging mapper (no OCR overlay)', async ({ page }) => {
    await loadPurchaseOrder(page, PO_LCSC);
    await expect(page.locator('#import-mapper')).not.toHaveClass(/hidden/);
    await expect(page.locator('#import-mapper .import-preview table')).toBeVisible();
    // The image-zone OCR overlay must NOT have opened for a CSV.
    await expect(page.locator('#ocr-overlay')).toHaveCount(0);
  });

  test('image zone: template defaults to generic; a PNG opens the OCR overlay', async ({ page }) => {
    await expect(page.locator('#import-ocr-template')).toHaveValue('generic');

    await page.locator('#import-ocr-input').setInputFiles({
      name: 'po-scan.png',
      mimeType: 'image/png',
      buffer: Buffer.from(PNG_1X1_B64, 'base64'),
    });

    await expect(page.locator('#ocr-overlay')).toBeVisible();
    await expect(page.locator('#ocr-overlay .ocr-img-wrap img')).toBeVisible();
    // The CSV staging mapper stays hidden.
    await expect(page.locator('#import-mapper')).toHaveClass(/hidden/);
  });

  test('scan button: real click opens the QR scan modal', async ({ page }) => {
    await page.locator('#import-scan-btn').click();
    await expect(page.locator('#mfg-scan-overlay')).toBeVisible();
    await expect(page.locator('.mfg-scan-modal')).toBeVisible();
    // A scan URL button is rendered from the mocked session.
    expect(await page.locator('.mfg-scan-url-btn').count()).toBeGreaterThan(0);
  });

  test('add row manually: real click seeds an editable staging row', async ({ page }) => {
    await page.locator('#import-add-row').click();
    await expect(page.locator('#import-mapper')).not.toHaveClass(/hidden/);
    const rows = page.locator('#import-mapper .import-preview tbody tr');
    await expect(rows).toHaveCount(1);
    // The seeded row is a blank editable input row (identity-mapped generic headers).
    await expect(rows.first().locator('td input').first()).toHaveValue('');
  });
});
