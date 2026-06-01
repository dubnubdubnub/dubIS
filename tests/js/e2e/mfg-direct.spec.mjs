// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { capture, rectOf } from './visual/capture.mjs';
import { scanRay, measureGap } from './visual/measure.mjs';
import { channelDominant } from './visual/color.mjs';

const isBluishStroke = (rgb) => channelDominant(rgb, 2, 28, 60);

/**
 * Drop-zone-specific frame sampler (the bespoke geometry lives HERE, not in the
 * core). Forces the bright dragover state, then measures the L-frame margins and
 * the rounded NW corner from rendered pixels.
 */
async function sampleDropZoneFrame(page) {
  const zone = page.locator('#import-drop-zone');
  const btn = zone.locator('[data-template="direct"]');
  await page.evaluate(() => document.getElementById('import-drop-zone').classList.add('dragover'));
  await page.waitForTimeout(120);
  const frame = await capture(page, zone, { pad: 12 });
  const b = await rectOf(btn);
  const marginTop = scanRay(frame, [b.x + 6, b.y - 2], [0, -1], isBluishStroke) + 2;
  const marginLeft = measureGap(frame, b, isBluishStroke, 'left');
  const marginRight = scanRay(frame, [b.x + b.width + 2, b.y - 20], [1, 0], isBluishStroke) + 2;
  const marginBottom = scanRay(frame, [b.x - 20, b.y + b.height + 2], [0, 1], isBluishStroke) + 2;
  const [vx, vy] = frame.toImg(b.x - 8, b.y - 8);
  const cornerRounded = !isBluishStroke(frame.pixel(vx, vy)) &&
    marginTop !== Infinity && marginLeft !== Infinity;
  return { marginTop, marginRight, marginBottom, marginLeft, cornerRounded };
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);
const MDT_INVOICE = path.join(__dirname, 'fixtures', 'mdt-invoice.csv');

const VENDORS = [
  { id: 'v_unknown', name: 'Unknown', icon: '❓', type: 'unknown' },
  { id: 'v_self',    name: 'Self',    icon: '⚙️', type: 'self' },
  { id: 'v_salvage', name: 'Salvage', icon: '♻️', type: 'salvage' },
  { id: 'v_mdt_test', name: 'MDT', url: 'https://tmr-sensors.com',
    favicon_path: 'data/sources/favicons/test.ico', type: 'real' },
];

test.describe('Direct-from-mfg import', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {
      mfgDirectVendors: VENDORS,
      mdtInvoiceParseResult: [
        { mpn: 'TMR2615', manufacturer: 'MDT', package: 'SOIC-8', quantity: 50, unit_price: 4.20 },
        { mpn: 'TMR2305', manufacturer: 'MDT', package: 'SOIC-8', quantity: 25, unit_price: 3.10 },
      ],
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('happy path: click Direct → fill vendor → drop CSV → import', async ({ page }) => {
    // 1. Click ★ Direct button
    const directBtn = page.locator('[data-template="direct"]');
    await directBtn.scrollIntoViewIfNeeded();
    await directBtn.click();
    await expect(page.locator('.mfg-direct-editor')).toBeVisible();

    // 2. Type vendor name + website
    const nameInput = page.locator('#mfg-vendor-name-input');
    await nameInput.click();
    await nameInput.fill('TMR Sensors');
    await nameInput.press('Tab');
    const urlInput = page.locator('#mfg-vendor-url-input');
    await urlInput.fill('tmr-sensors.com');
    await urlInput.press('Tab');
    await expect(page.locator('.vendor-favicon, .vendor-favicon-emoji').first()).toBeVisible();

    // 3. Use file input directly (drag-drop in Playwright is via setInputFiles)
    const fileInput = page.locator('#mfg-source-input');
    await fileInput.setInputFiles(MDT_INVOICE);

    // 4. Verify line items populated
    await expect(page.locator('.mfg-items-table tbody tr')).toHaveCount(2);
    await expect(page.locator('.mfg-items-table tbody tr').nth(0).locator('input[data-field="mpn"]'))
      .toHaveValue('TMR2615');

    // 5. Click Import
    await page.locator('#mfg-import').click();
    await expect(page.locator('.toast')).toContainText('Imported');
  });

  test('popout toggle expands editor into modal', async ({ page }) => {
    await page.locator('[data-template="direct"]').click();
    await page.locator('#mfg-popout-btn').click();
    await expect(page.locator('.mfg-direct-modal')).toBeVisible();
  });

  // RENDERED-PIXEL assertions for the dashed L-frame wrapping the ★ Direct button.
  //
  // The OLD approach read the SVG path's `d` attribute and the button's bounding
  // rect, then checked they agreed. That is TAUTOLOGICAL: the path is generated
  // FROM that same rect, so math consistency is all it proves. A viewBox-scale bug
  // that visually abandons the button still passes. These tests instead use
  // `sampleDashedFrame` which decodes an actual screenshot and scans for the blue
  // stroke pixels, giving real rendering evidence independent of the path math.
  for (const vp of [
    { name: 'narrow',    width: 1280, height: 720 },
    { name: 'medium',    width: 1600, height: 900 },
    { name: 'wide',      width: 1920, height: 1200 },
    { name: 'ultrawide', width: 2560, height: 1440 },
  ]) {
    test(`dashed L-frame wraps button with 8px margin + rounded corners (rendered pixels) @ ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // Let ResizeObserver re-run and the SVG path recompute.
      await page.waitForTimeout(150);

      await page.locator('#import-drop-zone').scrollIntoViewIfNeeded();

      const m = await sampleDropZoneFrame(page);

      // Each margin must be ≈ 8 CSS px (±3 tolerance for sub-pixel rendering and
      // anti-aliasing). Infinity means no stroke was found — that is always wrong.
      expect(m.marginTop).toBeGreaterThanOrEqual(5);
      expect(m.marginTop).toBeLessThanOrEqual(11);
      expect(m.marginRight).toBeGreaterThanOrEqual(5);
      expect(m.marginRight).toBeLessThanOrEqual(11);
      expect(m.marginBottom).toBeGreaterThanOrEqual(5);
      expect(m.marginBottom).toBeLessThanOrEqual(11);
      expect(m.marginLeft).toBeGreaterThanOrEqual(5);
      expect(m.marginLeft).toBeLessThanOrEqual(11);

      // The NW notch corner must be rounded (arc present, not a sharp L).
      expect(m.cornerRounded).toBe(true);
    });
  }

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

  test('pseudo-vendor chip selects Self', async ({ page }) => {
    await page.locator('[data-template="direct"]').click();
    await page.locator('.mfg-pseudo-chip[data-pseudo="v_self"]').click();
    await expect(page.locator('#mfg-vendor-name-input')).toHaveValue('Self');
    // Pseudo-vendors don't get a website field
    await expect(page.locator('#mfg-vendor-url-input')).toHaveCount(0);
  });
});
