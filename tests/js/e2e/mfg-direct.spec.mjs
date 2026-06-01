// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { capture, rectOf } from './visual/capture.mjs';
import { scanRay } from './visual/measure.mjs';
import { channelDominant } from './visual/color.mjs';

const isBluishStroke = (rgb) => channelDominant(rgb, 2, 28, 60);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Tiny valid 1x1 PNG (base64) — stands in for a scanned PO photo.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';

// A flat (no-page) scan push lands two line items in the standalone editor —
// the same editor formerly reached via the removed ★ Direct button. We use this
// REAL backend-push entry point (window._scanReceived) to exercise the editor's
// vendor pick / favicon / line-item / import / popout UI that still exists.
const SCAN_PAYLOAD = {
  template: 'lcsc',
  filename: 'po.png',
  image_b64: PNG_1X1_B64,
  line_items: [
    { mpn: 'TMR2615', manufacturer: 'MDT', package: 'SOIC-8',
      quantity: 50, unit_price: 4.20, distributor: 'LCSC', distributor_pn: 'C1' },
    { mpn: 'TMR2305', manufacturer: 'MDT', package: 'SOIC-8',
      quantity: 25, unit_price: 3.10, distributor: 'LCSC', distributor_pn: 'C2' },
  ],
};

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown' },
  { id: 'v_self',    name: 'Self',    icon: '⚙️', type: 'self' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage' },
  { id: 'v_mdt_test', name: 'MDT', url: 'https://tmr-sensors.com',
    favicon_path: 'data/sources/favicons/test.ico', type: 'real' },
];

/**
 * Open the standalone Direct editor via the realistic scan path: pick a template
 * in the image/PDF zone, click "Scan with phone", then deliver the backend push.
 * Leaves two line items staged in the editor for the caller to assert on.
 */
async function openEditorViaScan(page) {
  await page.locator('#import-ocr-zone').scrollIntoViewIfNeeded();
  await page.locator('#import-ocr-template').selectOption('lcsc');
  await page.locator('#import-scan-btn').click();
  await expect(page.locator('#mfg-scan-overlay')).toBeVisible();
  // Real backend→frontend contract: the phone uploads a flat (no-page) payload.
  await page.evaluate((payload) => window._scanReceived(payload), SCAN_PAYLOAD);
  await expect(page.locator('#mfg-scan-overlay')).toHaveCount(0);
  await expect(page.locator('.mfg-direct-editor')).toBeVisible();
}

test.describe('Direct-from-mfg import', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('scan push → fill vendor → import the staged rows', async ({ page }) => {
    await openEditorViaScan(page);

    // Staged line items are present.
    await expect(page.locator('.mfg-items-table tbody tr')).toHaveCount(2);
    await expect(page.locator('.mfg-items-table tbody tr').nth(0).locator('input[data-field="mpn"]'))
      .toHaveValue('TMR2615');

    // Type vendor name + website → favicon resolves.
    const nameInput = page.locator('#mfg-vendor-name-input');
    await nameInput.click();
    await nameInput.fill('TMR Sensors');
    await nameInput.press('Tab');
    const urlInput = page.locator('#mfg-vendor-url-input');
    await urlInput.fill('tmr-sensors.com');
    await urlInput.press('Tab');
    await expect(page.locator('.vendor-favicon, .vendor-favicon-emoji').first()).toBeVisible();

    // Import.
    await page.locator('#mfg-import').click();
    await expect(page.locator('.toast')).toContainText('Imported');
  });

  test('popout toggle expands editor into modal', async ({ page }) => {
    await openEditorViaScan(page);
    await page.locator('#mfg-popout-btn').click();
    await expect(page.locator('.mfg-direct-modal')).toBeVisible();
  });

  test('pseudo-vendor chip selects Self', async ({ page }) => {
    await openEditorViaScan(page);
    await page.locator('.mfg-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#mfg-vendor-name-input')).toHaveValue('Self');
    // Pseudo-vendors don't get a website field
    await expect(page.locator('#mfg-vendor-url-input')).toHaveCount(0);
  });

  test('drop-zone resting state: dashed frame present (muted), distinct from blue dragover', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.waitForTimeout(150);
    const zone = page.locator('#import-drop-zone');
    await zone.scrollIntoViewIfNeeded();
    const frame = await capture(page, zone, { pad: 12 });
    const z = await rectOf(zone);
    const bg = frame.pixel(...frame.toImg(z.x + z.width / 2, z.y + z.height / 2));
    const notBg = (rgb) => !!rgb &&
      (Math.abs(rgb[0] - bg[0]) + Math.abs(rgb[1] - bg[1]) + Math.abs(rgb[2] - bg[2])) > 40;
    // A stroke pixel exists at/just inside the top border (within ~6px scanning downward).
    const d = scanRay(frame, [z.x + z.width / 2, z.y - 2], [0, 1], notBg, { maxSearch: 6 });
    expect(d).toBeLessThan(Infinity);

    // The resting stroke must be the muted border color, NOT the bright-blue
    // dragover stroke — sample where the scan found it and assert not-bluish.
    const [tx, ty] = frame.toImg(z.x + z.width / 2, z.y - 2 + d);
    // search a tiny neighborhood for the actual stroke pixel we detected
    let strokePixel = null;
    for (let oy = -2; oy <= 2 && !strokePixel; oy++) {
      for (let ox = -3; ox <= 3 && !strokePixel; ox++) {
        const p = frame.pixel(tx + ox, ty + oy);
        if (notBg(p)) strokePixel = p;
      }
    }
    expect(strokePixel, 'should have located the resting stroke pixel').not.toBeNull();
    expect(isBluishStroke(strokePixel), 'resting stroke must be muted, not the blue dragover color').toBe(false);
  });

  test('Direct filter pill replaces Other', async ({ page }) => {
    const direct = page.locator('[data-distributor="direct"]');
    await expect(direct).toBeVisible();
    await expect(page.locator('[data-distributor="other"]')).toHaveCount(0);
  });

  test('vendor caret expands sub-pill panel', async ({ page }) => {
    const caret = page.locator('.dist-vendor-caret');
    await caret.click();
    await expect(page.locator('#vendor-subpills-panel')).toBeVisible();
  });
});
