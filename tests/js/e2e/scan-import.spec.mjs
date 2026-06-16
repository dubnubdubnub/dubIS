// @ts-check
//
// Desktop modal + end-to-end import E2E (phone-scan PO feature, Task 4 test #2).
//
// Uses the existing mocked-pywebview harness (addMockSetup) — the same one
// mfg-direct.spec.mjs uses — so the assertions match the established style.
// All USER actions are real interactions (real .click()/.fill()/.selectOption()):
//   - pick a scan template in the image/PDF zone, click "Scan with phone"
//   - assert the QR modal + rendered canvas + URL btn
//   - simulate ONLY the backend→frontend push by calling the REAL
//     window._scanReceived(payload) entry point (this IS the documented
//     backend contract; everything the user does stays a real interaction)
//   - assert the modal closes and the staging editor populates (incl. dist PN)
//   - pick a vendor in the editor, drive the EXISTING Import button and assert
//     the create-PO call received the scanned photo bytes (via the recorder)
//   - exercise the "Choose a file instead" fallback → the #import-ocr-input

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Tiny valid 1x1 PNG, base64 — stands in for the phone-captured PO photo.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';

const SCAN_PAYLOAD = {
  template: 'lcsc',
  filename: 'po.png',
  image_b64: PNG_1X1_B64,
  line_items: [
    { mpn: 'RC0402FR-0710KL', manufacturer: 'Yageo', package: '0402',
      quantity: 100, unit_price: 0.01, distributor: 'LCSC', distributor_pn: 'C25744' },
    { mpn: 'CL05A104KA5NNNC', manufacturer: 'Samsung', package: '0402',
      quantity: 50, unit_price: 0.02, distributor: 'LCSC', distributor_pn: 'C15525' },
  ],
};

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown', url: '', favicon_path: '' },
  { id: 'v_self', name: 'Self', icon: '⚙️', type: 'self', url: '', favicon_path: '' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage', url: '', favicon_path: '' },
];

// A multi-photo scan push: two photos, each its own PO by default. Each photo
// carries a page (for the overlay) and one prefilled, importable row.
function multiPhoto(idx, mpn, distpn) {
  return {
    index: idx, filename: `p${idx}.png`, image_b64: PNG_1X1_B64,
    pages: [{ image_b64: PNG_1X1_B64, width: 100, height: 100, words: [], lines: [] }],
    prefill_rows: [{ mpn, manufacturer: 'Acme', package: '0402', quantity: 100,
      unit_price: 0.01, distributor: 'LCSC', distributor_pn: distpn }],
  };
}
const MULTI_PHOTO = {
  template: 'lcsc',
  image_b64: PNG_1X1_B64,
  filename: 'p0.png',
  groups: [[0], [1]],
  photos: [multiPhoto(0, 'PARTA', 'C1'), multiPhoto(1, 'PARTB', 'C2')],
};

test.describe('Phone-scan desktop modal → end-to-end import', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, { mfgDirectVendors: VENDORS });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('scan modal renders QR + URLs; push populates staging; import carries the photo', async ({ page }) => {
    // 1. Choose a distributor template in the image/PDF zone (real select) and
    //    click "Scan with phone" → startPhoneScan opens the QR modal.
    const ocrZone = page.locator('#import-ocr-zone');
    await ocrZone.scrollIntoViewIfNeeded();
    await page.locator('#import-ocr-template').selectOption('lcsc');
    await page.locator('#import-scan-btn').click();

    // 4. Modal appears with a rendered QR canvas and at least one URL button.
    await expect(page.locator('#mfg-scan-overlay')).toBeVisible();
    await expect(page.locator('.mfg-scan-modal')).toBeVisible();
    const urlBtns = page.locator('.mfg-scan-url-btn');
    expect(await urlBtns.count()).toBeGreaterThan(0);
    // The QR canvas must have been drawn (non-zero dimensions => render ran).
    const canvasDrawn = await page.locator('#mfg-scan-qr-canvas').evaluate(
      (c) => c instanceof HTMLCanvasElement && c.width > 0 && c.height > 0);
    expect(canvasDrawn).toBe(true);

    // 5. Simulate the backend push (the real window._scanReceived contract).
    await page.evaluate((payload) => window._scanReceived(payload), SCAN_PAYLOAD);

    // 6. Modal closes; staging table populates with both rows.
    await expect(page.locator('#mfg-scan-overlay')).toHaveCount(0);
    const rows = page.locator('.mfg-items-table tbody tr');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator('.mfg-cell[data-field="mpn"]')).toHaveValue('RC0402FR-0710KL');
    await expect(rows.nth(1).locator('.mfg-cell[data-field="mpn"]')).toHaveValue('CL05A104KA5NNNC');

    // 7. Distributor PN flowed into the staging cell.
    await expect(rows.nth(0).locator('.mfg-cell-distpn')).toHaveValue('C25744');
    await expect(rows.nth(1).locator('.mfg-cell-distpn')).toHaveValue('C15525');

    // 7b. Pick a vendor in the editor (real chip click) so the import has a vendor id.
    await page.locator('.mfg-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#mfg-vendor-name-input')).toHaveValue('Self');

    // 8. Drive the EXISTING Import button (real click).
    await page.locator('#mfg-import').click();
    await expect(page.locator('.toast')).toContainText('Imported');

    // 9. The create-PO call received the scanned photo bytes + filename.
    const call = await page.evaluate(() => {
      const calls = window.__apiCalls.create_purchase_order_with_items || [];
      return calls[calls.length - 1] || null;
    });
    expect(call).not.toBeNull();
    expect(call.fileB64).toBe(PNG_1X1_B64);
    expect(call.fileName).toBe('po.png');
    // Distributor PNs are carried in the serialized items payload.
    const items = JSON.parse(call.itemsJson);
    expect(items.map((i) => i.distributor_pn)).toEqual(['C25744', 'C15525']);

    // 10. After import the panel re-renders, but the OCR-template dropdown keeps
    //     the user's choice (lcsc) instead of snapping back to "generic".
    await expect(page.locator('#import-ocr-template')).toHaveValue('lcsc');
  });

  test('_scanReceiving gives instant feedback before OCR completes', async ({ page }) => {
    // Open the scan modal so the "reading" hint swap has somewhere to land.
    await page.locator('#import-ocr-template').selectOption('lcsc');
    await page.locator('#import-scan-btn').click();
    await expect(page.locator('#mfg-scan-overlay')).toBeVisible();

    // The real backend fires this the moment the photo lands (before OCR).
    await page.evaluate(() => window._scanReceiving({ filename: 'po.png', template: 'lcsc' }));

    // Instant acknowledgement: a toast plus the modal hint flips to "reading".
    await expect(page.locator('.toast')).toContainText('Photo received');
    await expect(page.locator('#mfg-scan-overlay .mfg-scan-hint')).toContainText('reading');
  });

  test('fallback "choose a file" closes the modal and opens the file picker path', async ({ page }) => {
    await page.locator('#import-ocr-template').selectOption('lcsc');
    await page.locator('#import-scan-btn').click();
    await expect(page.locator('#mfg-scan-overlay')).toBeVisible();

    // The fallback click triggers the image/PDF zone's file chooser; assert that
    // interaction by catching the native file-chooser event.
    const chooserPromise = page.waitForEvent('filechooser');
    await page.locator('#mfg-scan-fallback').click();
    const chooser = await chooserPromise;

    // Modal closed and the surfaced picker targets the image-zone input.
    await expect(page.locator('#mfg-scan-overlay')).toHaveCount(0);
    expect(await chooser.element().getAttribute('id')).toBe('import-ocr-input');
  });

  test('multi-photo scan opens the grouping editor; group/ungroup adjusts the PO count', async ({ page }) => {
    await page.evaluate((p) => window._scanReceived(p), MULTI_PHOTO);

    // Two photos → two POs by default.
    await expect(page.locator('#scan-grouping-overlay')).toBeVisible();
    await expect(page.locator('.scan-po-group')).toHaveCount(2);

    // Select both photos and group → one PO.
    await page.locator('.scan-thumb[data-idx="0"]').click();
    await page.locator('.scan-thumb[data-idx="1"]').click();
    await page.locator('#scan-group-btn').click();
    await expect(page.locator('.scan-po-group')).toHaveCount(1);

    // Select both again and ungroup → back to two POs.
    await page.locator('.scan-thumb[data-idx="0"]').click();
    await page.locator('.scan-thumb[data-idx="1"]').click();
    await page.locator('#scan-ungroup-btn').click();
    await expect(page.locator('.scan-po-group')).toHaveCount(2);
  });

  test('separate-PO batch imports each PO via the sequential overlay queue', async ({ page }) => {
    await page.evaluate((p) => window._scanReceived(p), MULTI_PHOTO);
    await expect(page.locator('#scan-grouping-overlay')).toBeVisible();

    // Default grouping = two separate POs → review + import each in turn.
    await page.locator('#scan-group-import').click();
    await expect(page.locator('#scan-grouping-overlay')).toHaveCount(0);

    // PO 1 of 2: overlay opens; pick a vendor and confirm.
    await expect(page.locator('#ocr-overlay')).toBeVisible();
    await page.locator('#ocr-vendor-mount .ocr-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#ocr-vendor-name-input')).toHaveValue('Self');
    await page.locator('#ocr-confirm').click();

    // PO 2 of 2: the queue opens the next overlay automatically.
    await expect(page.locator('#ocr-overlay .ocr-img-wrap img')).toBeVisible();
    await page.locator('#ocr-vendor-mount .ocr-pseudo-chip[data-pseudo="v_self"]').click();
    await page.locator('#ocr-confirm').click();

    // Both POs were created via the create-PO API, then the flow ends.
    await expect.poll(() => page.evaluate(() =>
      (window.__apiCalls.create_purchase_order_with_items || []).length)).toBe(2);
    await expect(page.locator('#ocr-overlay')).toHaveCount(0);
  });
});
