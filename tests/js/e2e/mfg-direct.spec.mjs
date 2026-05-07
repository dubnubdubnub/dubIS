// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

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

    // 2. Type vendor URL
    const vendorInput = page.locator('#mfg-vendor-input');
    await vendorInput.click();
    await vendorInput.fill('tmr-sensors.com');
    await vendorInput.press('Tab');
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

  test('Direct button sits in bottom-right with dashed border around it', async ({ page }) => {
    const dropZone = page.locator('#import-drop-zone');
    const directBtn = dropZone.locator('[data-template="direct"]');
    await expect(directBtn).toBeVisible();

    const m = await page.evaluate(() => {
      const z = document.getElementById('import-drop-zone');
      const b = z.querySelector('[data-template="direct"]');
      const hint = z.querySelector('.hint');
      const zr = z.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      const hr = hint.getBoundingClientRect();
      return {
        rightGap: zr.right - br.right,
        bottomGap: zr.bottom - br.bottom,
        textToButtonGap: br.top - hr.bottom,
      };
    });
    // Button anchored to bottom-right with breathing room from the dashed
    // perimeter on right and bottom.
    expect(m.rightGap).toBeGreaterThanOrEqual(6);
    expect(m.rightGap).toBeLessThanOrEqual(20);
    expect(m.bottomGap).toBeGreaterThanOrEqual(6);
    expect(m.bottomGap).toBeLessThanOrEqual(20);
    // The drop-zone hint text must not crowd the button.
    expect(m.textToButtonGap).toBeGreaterThanOrEqual(4);
  });

  // The dashed L-shape perimeter is rendered as an SVG <path> computed from
  // the button's bounding rect with a constant 8px margin and rounded corners
  // (5 convex outer/notch corners + 1 concave corner that wraps the button's
  // NW corner). The path has 6 arc commands — one per corner.
  for (const vp of [
    { name: 'narrow', width: 1280, height: 720 },
    { name: 'medium', width: 1600, height: 900 },
    { name: 'wide',   width: 1920, height: 1200 },
    { name: 'ultrawide', width: 2560, height: 1440 },
  ]) {
    test(`L-shape perimeter has rounded corners + constant 8px margin from button at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // Force a re-layout so ResizeObserver fires.
      await page.waitForTimeout(50);

      const result = await page.evaluate(() => {
        const z = document.getElementById('import-drop-zone');
        const svg = z && z.querySelector('.drop-zone-frame');
        const path = z && z.querySelector('.drop-zone-frame-path');
        const button = z && z.querySelector('[data-template="direct"]');
        if (!z || !svg || !path || !button) return { missing: true };

        const d = path.getAttribute('d') || '';
        const arcs = (d.match(/A /g) || []).length;
        const dasharray = window.getComputedStyle(path).strokeDasharray;
        const strokeWidth = parseFloat(window.getComputedStyle(path).strokeWidth);
        const hasFrameClass = z.classList.contains('has-direct-frame');

        const zr = z.getBoundingClientRect();
        const br = button.getBoundingClientRect();
        // SVG userspace (0,0) = (zr.left, zr.top); user units = pixels
        const btnLeftU = br.left - zr.left;
        const btnTopU = br.top - zr.top;

        // Pull the F-arc parameters out of the path.
        // Path order: M, H, A(B), V, A(C), H, A(F), V, A(D), H, A(E), V, A(A), Z
        // The 3rd arc (index 2) is F — concave, sweep=0, centered at button NW.
        const arcMatches = [...d.matchAll(/A (\S+) (\S+) 0 0 (\d) (\S+) (\S+)/g)];
        const fArc = arcMatches[2];
        const fSweep = fArc ? fArc[3] : null;
        const fRadius = fArc ? Number(fArc[1]) : null;
        const fEndX = fArc ? Number(fArc[4]) : null;
        const fEndY = fArc ? Number(fArc[5]) : null;

        // Visible margin = distance from button to the inside edge of the
        // dashed stroke. The path's right edge is at x = W-1 in userspace,
        // and the stroke spans 1px on each side, so the inside edge of the
        // dashed line is at viewport x = zr.right - 2.
        const halfStroke = strokeWidth / 2;
        const visibleRight = (zr.right - 1 - halfStroke) - br.right;
        const visibleBottom = (zr.bottom - 1 - halfStroke) - br.bottom;

        return {
          missing: false,
          hasFrameClass,
          arcs,
          dasharray,
          visibleRight,
          visibleBottom,
          fSweep,
          fRadius,
          // F end is at (btnLeft - M, btnTop) in SVG userspace
          fEndDeltaX: fArc ? (btnLeftU - fEndX) : null,
          fEndDeltaY: fArc ? (btnTopU - fEndY) : null,
        };
      });

      expect(result.missing).toBe(false);
      expect(result.hasFrameClass).toBe(true);
      // Six rounded corners → six arc commands
      expect(result.arcs).toBe(6);
      // Dashed stroke
      expect(result.dasharray).not.toBe('none');
      // Constant 8px visible margin between button and dashed perimeter on
      // right and bottom (the two sides where the perimeter is the outer
      // rectangle, not the notch's inward edges).
      expect(result.visibleRight).toBeCloseTo(8, 0);
      expect(result.visibleBottom).toBeCloseTo(8, 0);
      // Concave F arc: sweep=0, radius equals the margin (8) so the perimeter
      // wraps the button's NW corner at constant distance.
      expect(result.fSweep).toBe('0');
      expect(result.fRadius).toBe(8);
      // F arc end point is exactly 8px west of the button NW corner
      expect(result.fEndDeltaX).toBeCloseTo(8, 0);
      expect(result.fEndDeltaY).toBeCloseTo(0, 0);
    });
  }

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
    await expect(page.locator('.mfg-direct-vendor-input')).toHaveValue('Self');
  });
});
