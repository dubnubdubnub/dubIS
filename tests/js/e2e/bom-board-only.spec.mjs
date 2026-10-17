// @ts-check
//
// Board features are not parts. The reported bug: on a KiCad BOM with no MPN
// column, fiducials, mounting holes, bare test pads and Tag-Connect footprints
// came through as `missing` parts — counted in UTBP and Missing, offered a
// Link button, and queued by "Add missing to cart". They are now `board-only`
// (js/bom-status.js, js/bom/board-features.js): still listed, greyed, with
// their reason, and out of every count and action.
//
// The fixture (fixtures/bom-kicad-board-features.csv) is synthetic, shaped
// like the user's KiCad export: header "Value","Reference","Footprint",
// "LCSC","DNP","Exclude from BOM"; 41 unique lines = 32 real parts (three of
// them near-miss negatives — a Keystone test point and a standoff, both with
// LCSC codes, and an LTC2050 op-amp), 2 DNP, and the 7 board features.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = path.join(__dirname, 'fixtures', 'bom-kicad-board-features.csv');
const BOARD_FEATURE_VALUES = ['TestPoint', 'MountingHole_Pad', 'Fiducial', '12V6', '5V', 'TC2070-NL-FP', 'TC2030'];

/** The number on a summary chip, e.g. chipCount(page, 'UTBP') for "32 UTBP". */
async function chipCount(page, label) {
  const chip = page.locator('#bom-summary .chip', { hasText: new RegExp(`^\\d+ ${label}$`) });
  await expect(chip).toHaveCount(1);
  return parseInt((await chip.textContent()) || '', 10);
}

test.describe('BOM board features (fiducials, mounting holes, test pads, Tag-Connect)', () => {
  test.beforeEach(async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, {
      carts: { carts: [{ id: 'cart-1', name: 'Cart 1', items: [] }], active_cart_id: 'cart-1' },
    });
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);
  });

  test('are counted as board-only, not toward UTBP or Missing', async ({ page }) => {
    expect(await chipCount(page, 'total')).toBe(41);
    expect(await chipCount(page, 'DNP')).toBe(2);
    expect(await chipCount(page, 'board-only')).toBe(7);
    expect(await chipCount(page, 'UTBP')).toBe(32);
    // Everything placed is accounted for by a status chip; the 7 features
    // are in none of them.
    const placed = await chipCount(page, 'ok') + await chipCount(page, 'short')
      + await chipCount(page, 'possible') + await chipCount(page, 'missing');
    expect(placed).toBeLessThanOrEqual(32);
  });

  test('stay visible in the staging table, greyed, with their reason', async ({ page }) => {
    const boardRows = page.locator('#bom-tbody tr.row-board-only');
    await expect(boardRows).toHaveCount(7);
    const fid = boardRows.filter({ has: page.locator('input[value="Fiducial"]') });
    await expect(fid).toHaveAttribute('title', /Fiducial .*nothing to place/);
    await expect(fid.locator('td.status')).toHaveAttribute('title', /Fiducial/);
    // Greyed by the stylesheet, not just tagged: the cell text is the muted grey.
    const color = await fid.locator('td').nth(2).evaluate((td) => getComputedStyle(td).color);
    const muted = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-gray-muted').trim());
    expect(muted).not.toBe('');
    const probe = await page.evaluate((c) => {
      const el = document.createElement('span');
      el.style.color = c;
      document.body.appendChild(el);
      const rgb = getComputedStyle(el).color;
      el.remove();
      return rgb;
    }, muted);
    expect(color).toBe(probe);
    // The near-miss negatives are real parts, not greyed.
    await expect(page.locator('#bom-tbody tr', { has: page.locator('input[value="C238122"]') }))
      .not.toHaveClass(/row-board-only/);
  });

  test('have their own filter in the inventory BOM view, and no Link button', async ({ page }) => {
    const btn = page.locator('#inventory-body .filter-btn[data-filter="boardOnly"]');
    await expect(btn).toHaveText('Board-only (7)');
    // Missing filter count leaves them out.
    await expect(page.locator('#inventory-body .filter-btn[data-filter="missing"]'))
      .toHaveText(`Missing (${await chipCount(page, 'missing')})`);

    await btn.click();
    const rows = page.locator('#inventory-body tr.row-board-only');
    await expect(rows).toHaveCount(7);
    await expect(rows.locator('.link-btn')).toHaveCount(0);
    await expect(rows.locator('.adj-btn')).toHaveCount(0);
    await expect(rows.filter({ hasText: 'Tag-Connect footprint' })).toHaveCount(2);
  });

  test('are never added by "Add missing to cart"', async ({ page }) => {
    const wanted = await chipCount(page, 'short') + await chipCount(page, 'possible') + await chipCount(page, 'missing');
    await page.click('#bom-add-to-cart');
    await expect.poll(
      async () => (await page.evaluate(() => (window.__apiCalls.add_bom_missing_to_cart || []).length)),
      { timeout: 15_000 },
    ).toBe(1);
    const args = await page.evaluate(() => window.__apiCalls.add_bom_missing_to_cart[0]);
    const entries = /** @type {Array<any>} */ (args.find((a) => Array.isArray(a)));
    expect(entries).toBeTruthy();
    expect(entries).toHaveLength(wanted);
    const rawMpns = entries.filter((e) => e.raw).map((e) => e.raw.mpn);
    // "TestPoint" appears exactly once: the Keystone test loop, a real part
    // (it has an LCSC code). The bare-pad TestPoint line is not sent.
    expect(rawMpns.filter((m) => m === 'TestPoint')).toHaveLength(1);
    for (const v of BOARD_FEATURE_VALUES.filter((v) => v !== 'TestPoint')) expect(rawMpns).not.toContain(v);
    // The DNP parts are not bought either.
    expect(rawMpns).not.toContain('10k');
    await expect(page.locator('#cart-badge')).toHaveText(String(wanted));
  });
});
