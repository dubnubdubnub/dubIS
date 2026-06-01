// @ts-check
//
// Desktop modal + end-to-end import E2E (phone-scan PO feature, Task 4 test #2).
//
// Uses the existing mocked-pywebview harness (addMockSetup) — the same one
// mfg-direct.spec.mjs uses — so the assertions match the established style.
// All USER actions are real interactions (real .click()/.fill()/.selectOption()):
//   - open the Direct-from-mfg flow, pick a vendor, choose a scan template
//   - click "Scan with phone" → assert the QR modal + rendered canvas + URL btn
//   - simulate ONLY the backend→frontend push by calling the REAL
//     window._scanReceived(payload) entry point (this IS the documented
//     backend contract; everything the user does stays a real interaction)
//   - assert the modal closes and the staging table populates (incl. dist PN)
//   - drive the EXISTING Import button and assert the create-PO call received
//     the scanned photo bytes (via the harness api-call recorder)
//   - exercise the "Choose a file instead" fallback → existing #mfg-source-input

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

test.describe('Phone-scan desktop modal → end-to-end import', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, { mfgDirectVendors: VENDORS });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('scan modal renders QR + URLs; push populates staging; import carries the photo', async ({ page }) => {
    // 1. Open the Direct-from-mfg flow.
    const directBtn = page.locator('[data-template="direct"]');
    await directBtn.scrollIntoViewIfNeeded();
    await directBtn.click();
    await expect(page.locator('.mfg-direct-editor')).toBeVisible();

    // 2. Pick a vendor (real chip click) so the eventual import has a vendor id.
    await page.locator('.mfg-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#mfg-vendor-name-input')).toHaveValue('Self');

    // 3. Choose a distributor template (real select) + click Scan with phone.
    await page.locator('#mfg-scan-template').selectOption('lcsc');
    await page.locator('#mfg-scan-btn').click();

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
  });

  test('fallback "choose a file" closes the modal and opens the file picker path', async ({ page }) => {
    await page.locator('[data-template="direct"]').click();
    await expect(page.locator('.mfg-direct-editor')).toBeVisible();

    await page.locator('#mfg-scan-template').selectOption('lcsc');
    await page.locator('#mfg-scan-btn').click();
    await expect(page.locator('#mfg-scan-overlay')).toBeVisible();

    // The fallback click triggers the hidden #mfg-source-input file chooser;
    // assert that interaction by catching the native file-chooser event.
    const chooserPromise = page.waitForEvent('filechooser');
    await page.locator('#mfg-scan-fallback').click();
    const chooser = await chooserPromise;

    // Modal closed and the surfaced picker targets the existing source input.
    await expect(page.locator('#mfg-scan-overlay')).toHaveCount(0);
    expect(await chooser.element().getAttribute('id')).toBe('mfg-source-input');
  });
});
