// @ts-check
//
// OCR scanning-skeleton E2E (Phase 1 — drop zone).
//
// When an image is dropped into the import OCR zone, the review modal opens
// immediately in a "scanning" skeleton state (the dropped image under an
// animated scan-line sweep) while OCR runs, then transitions in place to the
// real token/grid review once the (mock-delayed) OCR returns.
//
// REALISTIC interactions ONLY: a real file chooser via setInputFiles. No
// dispatchEvent, no {force:true}. The OCR mock is delayed (ocrOverlayDelayMs) so
// the skeleton is observable before it resolves.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';

const WORDS = [
  { text: 'C12624', x: 10, y: 10, w: 30, h: 12, conf: 0.97, line_id: 0 },
  { text: 'KT-0603G', x: 12, y: 55, w: 40, h: 12, conf: 0.95, line_id: 1 },
];
const PREFILL_ROWS = [
  { distributor_pn: '', mpn: '', manufacturer: '', description: '', package: '0603', quantity: 100, unit_price: 0.01 },
];
const OCR_RESULT = {
  template: 'lcsc',
  pages: [{ image_b64: PNG_1X1_B64, width: 100, height: 100, words: WORDS, lines: [] }],
  prefill_rows: PREFILL_ROWS,
};

async function dropImage(page) {
  const ocrZone = page.locator('#import-ocr-zone');
  await ocrZone.scrollIntoViewIfNeeded();
  await page.locator('#import-ocr-template').selectOption('lcsc');
  await page.locator('#import-ocr-input').setInputFiles({
    name: 'po-scan.png',
    mimeType: 'image/png',
    buffer: Buffer.from(PNG_1X1_B64, 'base64'),
  });
}

test.describe('OCR scanning skeleton (drop zone)', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      ocrOverlayResult: OCR_RESULT,
      ocrOverlayDelayMs: 700,  // hold OCR so the skeleton is observable
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('skeleton appears with the dropped image and scan sweep, then resolves', async ({ page }) => {
    await dropImage(page);

    const modal = page.locator('#ocr-overlay .ocr-overlay-modal.ocr-loading');
    await expect(modal).toBeVisible();
    await expect(page.locator('#ocr-overlay .ocr-scan-sweep')).toBeVisible();
    await expect(page.locator('#ocr-overlay .ocr-skel-img')).toHaveAttribute(
      'src', new RegExp(PNG_1X1_B64.slice(0, 24)));
    await expect(page.locator('#ocr-overlay .ocr-skel-row').first()).toBeVisible();
    await expect(page.locator('#ocr-overlay .ocr-token')).toHaveCount(0);

    await expect(page.locator('#ocr-overlay .ocr-token')).toHaveCount(2);
    await expect(page.locator('#ocr-overlay .ocr-overlay-modal.ocr-loading')).toHaveCount(0);
    await expect(page.locator('#ocr-overlay .ocr-img-wrap img')).toBeVisible();
  });

  test('Cancel during scanning closes the skeleton', async ({ page }) => {
    await dropImage(page);
    await expect(page.locator('#ocr-overlay .ocr-overlay-modal.ocr-loading')).toBeVisible();
    await page.locator('#ocr-overlay #ocr-cancel').click();
    await expect(page.locator('#ocr-overlay')).toHaveCount(0);
    await page.waitForTimeout(900);
    await expect(page.locator('#ocr-overlay')).toHaveCount(0);
  });
});
