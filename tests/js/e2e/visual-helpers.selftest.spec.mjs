// @ts-check
/**
 * SELF-TEST for the visual helpers in `visual-helpers.mjs`.
 *
 * This proves the SEMANTIC pixel sampler (`sampleDashedFrame`) actually measures
 * rendered pixels — not the SVG path string. On the correct code, the dashed
 * L-frame wraps the ★ Direct button with a constant 8px margin (FRAME_MARGIN)
 * and a rounded NW concave corner; the sampler must report all four margins
 * ≈ 8px and cornerRounded === true.
 *
 * It is designed to FAIL loudly if the frame is mis-rendered (e.g. a viewBox
 * scale bug), which a property-based path-vs-rect test cannot catch.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { sampleDashedFrame } from './visual-helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const EXPECTED_MARGIN = 8;  // FRAME_MARGIN in import-panel.js
const MARGIN_TOL = 3;       // ± px tolerance for anti-aliasing / dash gaps

async function loadApp(page, w, h) {
  await addMockSetup(page, MOCK_INVENTORY, {});
  await page.setViewportSize({ width: w, height: h });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(300); // fonts + ResizeObserver settle
}

test.describe('visual-helpers self-test — sampleDashedFrame', () => {
  test('Test A: margins ≈ 8px and corner rounded @medium', async ({ page }) => {
    await loadApp(page, 1600, 900);

    const m = await sampleDashedFrame(page, {});

    // Surface measurements in the report for easy debugging on failure.
    test.info().annotations.push({ type: 'measured', description: JSON.stringify(m) });

    expect(m.marginRight, `marginRight=${m.marginRight}`).toBeCloseTo(EXPECTED_MARGIN, 0);
    expect(Math.abs(m.marginRight - EXPECTED_MARGIN)).toBeLessThanOrEqual(MARGIN_TOL);
    expect(Math.abs(m.marginBottom - EXPECTED_MARGIN)).toBeLessThanOrEqual(MARGIN_TOL);
    expect(Math.abs(m.marginTop - EXPECTED_MARGIN)).toBeLessThanOrEqual(MARGIN_TOL);
    expect(Math.abs(m.marginLeft - EXPECTED_MARGIN)).toBeLessThanOrEqual(MARGIN_TOL);
    expect(m.cornerRounded, 'NW concave corner should be rounded').toBe(true);
  });

  test('Test B: detection-sanity guard — right & bottom margins in [4,14]', async ({ page }) => {
    await loadApp(page, 1600, 900);

    const m = await sampleDashedFrame(page, {});
    test.info().annotations.push({ type: 'measured', description: JSON.stringify(m) });

    // A loose guard that proves the sampler is detecting a real, finite stroke
    // near the button (not Infinity, not absurdly far). If this fails the whole
    // detection approach is broken regardless of exact margins.
    expect(m.marginRight).toBeGreaterThanOrEqual(4);
    expect(m.marginRight).toBeLessThanOrEqual(14);
    expect(m.marginBottom).toBeGreaterThanOrEqual(4);
    expect(m.marginBottom).toBeLessThanOrEqual(14);
  });
});
