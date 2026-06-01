// @ts-check
//
// OCR-overlay PO review modal E2E (Task 8).
//
// Drives the Direct-from-mfg import flow through the side-by-side OCR overlay
// using the SAME mocked-pywebview harness (addMockSetup) as mfg-direct.spec.mjs
// and scan-import.spec.mjs. The backend ocr_overlay_b64 method is mocked (in
// helpers.mjs) to return a small page+token+prefill fixture.
//
// REALISTIC interactions ONLY: real file chooser (setInputFiles), real
// .click()/.dblclick()/.fill()/.press(), and a real mouse drag via
// page.mouse.down/move/up. No dispatchEvent, no {force:true}, no synthetic events.
// The only non-UI entry point used is the documented file-input that the user's
// real "Choose a file" action targets (same as mfg-direct.spec.mjs).

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Tiny valid 1x1 PNG (base64). Stretched to the scan pane's width by the
// renderer (img { width:100% }), so its 1:1 aspect yields a square token canvas.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';
// A distinct tiny PNG (2x2, red) for page 2 so the <img src> visibly changes.
const PNG_2X2_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP8z8DwnwEJMA5' +
  'GAQAd5gMBlPzu4QAAAABJRU5ErkJggg==';

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown', url: '', favicon_path: '' },
  { id: 'v_self', name: 'Self', icon: '⚙️', type: 'self', url: '', favicon_path: '' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage', url: '', favicon_path: '' },
];

// Two well-separated, generously sized word tokens so real clicks land cleanly.
// Coordinates are in the page's own width/height units; the renderer converts to
// % of the rendered image so positions scale with the responsive square image.
const WORDS = [
  { text: 'C12624', x: 10, y: 10, w: 30, h: 12, conf: 0.97, line_id: 0 },
  { text: 'KT-0603G', x: 12, y: 55, w: 40, h: 12, conf: 0.95, line_id: 1 },
];

// 'lcsc' template → grid columns: distributor_pn, mpn, manufacturer,
// description, package, quantity, unit_price. Leave fields blank to fill.
const PREFILL_ROWS = [
  { distributor_pn: '', mpn: '', manufacturer: '', description: '', package: '0603', quantity: 100, unit_price: 0.01 },
  { distributor_pn: '', mpn: '', manufacturer: 'Generic', description: '', package: '0402', quantity: 50, unit_price: 0.02 },
];

const OCR_RESULT_1PAGE = {
  template: 'lcsc',
  pages: [{ image_b64: PNG_1X1_B64, width: 100, height: 100, words: WORDS, lines: [] }],
  prefill_rows: PREFILL_ROWS,
};

const OCR_RESULT_2PAGE = {
  template: 'lcsc',
  pages: [
    { image_b64: PNG_1X1_B64, width: 100, height: 100, words: WORDS, lines: [] },
    { image_b64: PNG_2X2_B64, width: 100, height: 100, words: [WORDS[0]], lines: [] },
  ],
  prefill_rows: PREFILL_ROWS,
};

/** Open the Direct-from-mfg flow and drop an image, landing in the OCR overlay. */
async function openOverlay(page) {
  const directBtn = page.locator('[data-template="direct"]');
  await directBtn.scrollIntoViewIfNeeded();
  await directBtn.click();
  await expect(page.locator('.mfg-direct-editor')).toBeVisible();

  // Pick the lcsc template so the grid renders the LCSC# column.
  await page.locator('#mfg-scan-template').selectOption('lcsc');

  // Real file chooser: hand a PNG buffer to the existing source input. Its
  // onchange fires handleSourceFile → ocrOverlayB64 → openOverlay.
  await page.locator('#mfg-source-input').setInputFiles({
    name: 'po-scan.png',
    mimeType: 'image/png',
    buffer: Buffer.from(PNG_1X1_B64, 'base64'),
  });

  await expect(page.locator('#ocr-overlay')).toBeVisible();
}

test.describe('OCR overlay PO review modal', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT_1PAGE,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('open: image renders with the expected word tokens', async ({ page }) => {
    await openOverlay(page);
    await expect(page.locator('#ocr-overlay .ocr-img-wrap img')).toBeVisible();
    await expect(page.locator('#ocr-overlay .ocr-token')).toHaveCount(2);
    // Tokens carry their text in title/textContent.
    await expect(page.locator('.ocr-token[data-token="0:w:0"]')).toHaveAttribute('title', 'C12624');
    await expect(page.locator('.ocr-token[data-token="0:w:1"]')).toHaveAttribute('title', 'KT-0603G');
  });

  test('word → cell fill: click a token then a cell', async ({ page }) => {
    await openOverlay(page);
    // Click the first token, then the LCSC# cell of row 0.
    await page.locator('.ocr-token[data-token="0:w:0"]').click();
    await page.locator('.ocr-cell[data-row="0"][data-field="distributor_pn"]').click();
    await expect(page.locator('.ocr-cell[data-row="0"][data-field="distributor_pn"]'))
      .toHaveText('C12624');
  });

  test('cell → word fill (reverse): click a cell then a token', async ({ page }) => {
    await openOverlay(page);
    // Reverse order: target cell first, then the source token.
    await page.locator('.ocr-cell[data-row="0"][data-field="mpn"]').click();
    await page.locator('.ocr-token[data-token="0:w:1"]').click();
    await expect(page.locator('.ocr-cell[data-row="0"][data-field="mpn"]'))
      .toHaveText('KT-0603G');
  });

  test('double-click edit: type a value inline and commit', async ({ page }) => {
    await openOverlay(page);
    const cell = page.locator('.ocr-cell[data-row="1"][data-field="manufacturer"]');
    await cell.dblclick();
    const input = cell.locator('input.ocr-cell-edit');
    await expect(input).toBeVisible();
    await input.fill('Yageo');
    await input.press('Enter');
    await expect(cell).toHaveText('Yageo');
  });

  test('drag selects multiple tokens into one cell', async ({ page }) => {
    await openOverlay(page);
    const wrap = page.locator('#ocr-overlay .ocr-img-wrap');
    const box = await wrap.boundingBox();
    if (!box) throw new Error('img-wrap has no bounding box');
    // Drag a rubber-band rectangle covering both tokens (top-left to bottom-right
    // of the token region) using a REAL mouse drag.
    await page.mouse.move(box.x + 2, box.y + 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.8, { steps: 8 });
    await page.mouse.up();
    // Now assign the combined selection into the description cell.
    await page.locator('.ocr-cell[data-row="0"][data-field="description"]').click();
    const text = await page.locator('.ocr-cell[data-row="0"][data-field="description"]').textContent();
    // Both token texts combined in reading order (top then left): "C12624 KT-0603G".
    expect(text).toContain('C12624');
    expect(text).toContain('KT-0603G');
  });

  test('confirm → import carries the corrected rows', async ({ page }) => {
    await openOverlay(page);

    // Fill some cells with real interactions.
    await page.locator('.ocr-token[data-token="0:w:0"]').click();
    await page.locator('.ocr-cell[data-row="0"][data-field="distributor_pn"]').click();
    await expect(page.locator('.ocr-cell[data-row="0"][data-field="distributor_pn"]'))
      .toHaveText('C12624');

    await page.locator('.ocr-cell[data-row="0"][data-field="mpn"]').click();
    await page.locator('.ocr-token[data-token="0:w:1"]').click();
    await expect(page.locator('.ocr-cell[data-row="0"][data-field="mpn"]'))
      .toHaveText('KT-0603G');

    // Row 1 needs an MPN too (validateLineItems requires one per row); fill it
    // via a real inline edit.
    const mpn1 = page.locator('.ocr-cell[data-row="1"][data-field="mpn"]');
    await mpn1.dblclick();
    await mpn1.locator('input.ocr-cell-edit').fill('RC0402');
    await mpn1.locator('input.ocr-cell-edit').press('Enter');
    await expect(mpn1).toHaveText('RC0402');

    // Inline-edit a quantity so an edited value also flows to the import.
    const qty = page.locator('.ocr-cell[data-row="1"][data-field="quantity"]');
    await qty.dblclick();
    await qty.locator('input.ocr-cell-edit').fill('77');
    await qty.locator('input.ocr-cell-edit').press('Enter');
    await expect(qty).toHaveText('77');

    // Set a vendor via the overlay footer's pseudo-vendor chip (real click).
    await page.locator('#ocr-vendor-mount .ocr-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#ocr-vendor-name-input')).toHaveValue('Self');

    // Confirm → importPO → create_purchase_order_with_items.
    await page.locator('#ocr-confirm').click();
    await expect(page.locator('#ocr-overlay')).toHaveCount(0);
    await expect(page.locator('.toast')).toContainText('Imported');

    const call = await page.evaluate(() => {
      const calls = window.__apiCalls.create_purchase_order_with_items || [];
      return calls[calls.length - 1] || null;
    });
    expect(call).not.toBeNull();
    expect(call.vendorId).toBe('v_self');
    const items = JSON.parse(call.itemsJson);
    // The corrected/edited values are present in the serialized items payload.
    expect(items[0].distributor_pn).toBe('C12624');
    expect(items[0].mpn).toBe('KT-0603G');
    expect(items[1].mpn).toBe('RC0402');
    // quantity is carried straight from the inline edit (string-or-number).
    expect(String(items[1].quantity)).toBe('77');
  });

  test('multi-page: next button switches the page image', async ({ page }) => {
    // beforeEach installed the 1-page mock + navigated. Install a 2-page mock as
    // a later init script (last writer wins for window.pywebview) and reload so
    // this test sees the multi-page payload from ocr_overlay_b64.
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT_2PAGE,
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await openOverlay(page);

    // Page 1 shows the 1x1 image and 2 tokens; nav controls present.
    await expect(page.locator('#ocr-overlay')).toContainText('Page 1 / 2');
    const img = page.locator('#ocr-overlay .ocr-img-wrap img');
    const src1 = await img.getAttribute('src');
    expect(src1).toContain(PNG_1X1_B64);
    await expect(page.locator('#ocr-prev')).toBeDisabled();

    // Real click Next → page 2 image (distinct src) + single token.
    await page.locator('#ocr-next').click();
    await expect(page.locator('#ocr-overlay')).toContainText('Page 2 / 2');
    const src2 = await page.locator('#ocr-overlay .ocr-img-wrap img').getAttribute('src');
    expect(src2).toContain(PNG_2X2_B64);
    expect(src2).not.toBe(src1);
    await expect(page.locator('#ocr-overlay .ocr-token')).toHaveCount(1);
    await expect(page.locator('#ocr-next')).toBeDisabled();
  });
});
