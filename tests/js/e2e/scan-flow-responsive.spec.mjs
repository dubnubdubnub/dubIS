// @ts-check
//
// E2E — responsive scan flow: Reading shell, multi-image grouping, template
// switch + vendor prefill (Task 9).
//
// Three tests, all using the same addMockSetup harness as ocr-overlay.spec.mjs
// and import-two-zone.spec.mjs. Realistic interactions only: real setInputFiles,
// .selectOption(), .click() — NO dispatchEvent, NO {force:true}, NO synthetic
// events (project hard rule).
//
// Integration concerns addressed here:
//
// 1. SHELL VISIBILITY: ocr_overlay_b64 normally resolves immediately in the
//    mock, so the scan-shell-overlay is closed before Playwright can assert it.
//    The shell test installs a second addInitScript (last writer wins for
//    window.pywebview) that wraps ocr_overlay_b64 with a ~150 ms delay so the
//    shell is visibly present before the OCR result lands.
//
// 2. MULTI-IMAGE: setInputFiles with three buffers makes three sequential
//    ocr_overlay_b64 calls. The mock is not a one-shot — it returns a valid
//    result on every invocation. Three calls → three photo records → grouping
//    editor with three thumbnails.
//
// 3. TEMPLATE SWITCH: selectOption('#ocr-template-select','lcsc') fires the
//    native change event → maybePrefillVendor('lcsc') → vendorPicker
//    .onVendorNameBlur('LCSC') → update_vendor (already mocked in helpers.mjs
//    to echo back {id, name, ...}) → rerender → #ocr-vendor-name-input gets
//    "LCSC"; the grid header column changes to "LCSC#".

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Tiny valid 1×1 PNG (base64). Same inline constant used throughout the E2E suite.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown', url: '', favicon_path: '' },
  { id: 'v_self',    name: 'Self',    icon: '⚙️', type: 'self',    url: '', favicon_path: '' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage', url: '', favicon_path: '' },
];

// A minimal one-page OCR result. One word token so the overlay renders a
// complete grid without needing any additional setup.
const WORDS = [
  { text: 'C12624', x: 10, y: 10, w: 30, h: 12, conf: 0.97, line_id: 0 },
];
const OCR_RESULT = {
  template: 'generic',
  pages: [{ image_b64: PNG_1X1_B64, width: 100, height: 100, words: WORDS, lines: [] }],
  prefill_rows: [
    { mpn: '', manufacturer: '', description: '', package: '0603', quantity: 100, unit_price: 0.01 },
  ],
};

// ---------------------------------------------------------------------------
// Test 1 — one image → shell IMMEDIATELY → then OCR overlay; shell gone
// ---------------------------------------------------------------------------
test('one image: Reading shell appears immediately, then OCR overlay replaces it',
  async ({ page }) => {
    // Standard mock setup — we override ocr_overlay_b64 below with a delayed
    // version so the shell is observable before OCR finishes.
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT,
    });

    // Override ocr_overlay_b64 with a delayed resolver so #scan-shell-overlay
    // stays visible long enough for Playwright to assert it. This second
    // addInitScript runs after the first (last-writer-wins for window.pywebview
    // because both scripts run in the same context, with the second patching the
    // already-installed object).
    await page.addInitScript(({ result }) => {
      // Unconditionally ensure window.pywebview.api exists and install the
      // delayed mock. No conditional guard — a guarded override that silently
      // does nothing would make the shell-visibility assertion tautological.
      // addMockSetup's initScript runs first (Playwright executes addInitScript
      // scripts in registration order), so pywebview is already populated; but
      // we defensively initialise the chain anyway to guarantee the assignment
      // always takes effect regardless of future harness changes.
      window.pywebview = window.pywebview || {};
      window.pywebview.api = window.pywebview.api || {};
      window.pywebview.api.ocr_overlay_b64 = async (b64, name, template) => {
        await new Promise(r => setTimeout(r, 200));
        return { ...result, template: template || result.template };
      };
    }, { result: OCR_RESULT });

    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Hand one PNG buffer to the OCR file input (same as ocr-overlay.spec.mjs).
    await page.locator('#import-ocr-input').setInputFiles({
      name: 'po-scan.png',
      mimeType: 'image/png',
      buffer: Buffer.from(PNG_1X1_B64, 'base64'),
    });

    // The Reading… shell must appear immediately (before OCR resolves).
    await expect(page.locator('#scan-shell-overlay')).toBeVisible({ timeout: 3000 });

    // After OCR resolves (~200ms), the overlay opens and the shell closes.
    await expect(page.locator('#ocr-overlay')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('#scan-shell-overlay')).toHaveCount(0);
  });

// ---------------------------------------------------------------------------
// Test 2 — three images → grouping editor with three thumbnails
// ---------------------------------------------------------------------------
test('three images: grouping editor opens with three scan-thumb tiles',
  async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Supply three distinct PNG buffers — all the same tiny 1×1 PNG but with
    // different names so each becomes its own photo record. The mock returns a
    // valid OCR result for every call (not a one-shot).
    await page.locator('#import-ocr-input').setInputFiles([
      { name: 'scan-a.png', mimeType: 'image/png', buffer: Buffer.from(PNG_1X1_B64, 'base64') },
      { name: 'scan-b.png', mimeType: 'image/png', buffer: Buffer.from(PNG_1X1_B64, 'base64') },
      { name: 'scan-c.png', mimeType: 'image/png', buffer: Buffer.from(PNG_1X1_B64, 'base64') },
    ]);

    // Three photos → routeScanResult routes to the grouping editor.
    await expect(page.locator('#scan-grouping-overlay')).toBeVisible({ timeout: 10000 });
    // Each photo gets its own .scan-thumb in the editor.
    await expect(page.locator('.scan-thumb')).toHaveCount(3);
  });

// ---------------------------------------------------------------------------
// Test 3 — template switch → LCSC# column header + vendor name prefill
// ---------------------------------------------------------------------------
test('switching template to lcsc updates the dist-PN column header and prefills LCSC vendor',
  async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      ocrOverlayResult: OCR_RESULT,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Open the OCR overlay with one image (generic template → no dist-PN column).
    await page.locator('#import-ocr-input').setInputFiles({
      name: 'po-scan.png',
      mimeType: 'image/png',
      buffer: Buffer.from(PNG_1X1_B64, 'base64'),
    });
    await expect(page.locator('#ocr-overlay')).toBeVisible({ timeout: 5000 });

    // Switch the in-overlay template selector to 'lcsc'.
    await page.locator('#ocr-template-select').selectOption('lcsc');

    // The grid header should now show "LCSC#" (the distributor PN column label).
    await expect(page.locator('.ocr-grid th', { hasText: 'LCSC#' })).toBeVisible({ timeout: 3000 });

    // maybePrefillVendor called update_vendor('','LCSC','') → rerender sets the
    // vendor name input. Wait for the async update_vendor round-trip.
    await expect(page.locator('#ocr-vendor-name-input')).toHaveValue(/LCSC/i, { timeout: 3000 });
  });
