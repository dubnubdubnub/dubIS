// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV = fs.readFileSync(
  path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8'
);

function addMockSetup(page) {
  return page.addInitScript((inventory) => {
    window.pywebview = {
      api: {
        load_inventory: async () => inventory,
        rebuild_inventory: async () => inventory,
        adjust_part: async () => inventory,
        update_part_price: async () => inventory,
        load_preferences: async () => ({ thresholds: {} }),
        save_preferences: async () => true,
        check_digikey_session: async () => ({ logged_in: false }),
        start_digikey_login: async () => null,
        sync_digikey_cookies: async () => ({ logged_in: false }),
        logout_digikey: async () => null,
        import_csv: async () => inventory,
        remove_last_adjustments: async () => inventory,
      },
    };
  }, MOCK_INVENTORY);
}

async function waitForInventoryRows(page) {
  await page.waitForSelector('.inv-part-row', { timeout: 10_000 });
}

async function loadBom(page, bomCsv) {
  await page.evaluate((csv) => {
    const result = processBOM(csv, 'test-bom.csv');
    if (!result) throw new Error('processBOM returned null');
    const { aggregated, bomHeaders, bomCols } = result;
    const results = matchBOM(aggregated, App.inventory, App.links.manualLinks, App.links.confirmedMatches);
    App.bomResults = results;
    App.bomHeaders = bomHeaders;
    App.bomCols = bomCols;
    App.bomFileName = 'test-bom.csv';
    const rows = results.map(r => ({
      ...r,
      effectiveQty: r.bom.qty,
      effectiveStatus: r.status,
    }));
    EventBus.emit(Events.BOM_LOADED, { rows, fileName: 'test-bom.csv', multiplier: 1 });
  }, bomCsv);
}

test.describe('Sticky button column — BOM comparison mode', () => {

  test('button column stays within viewport when table scrolls horizontally', async ({ page }) => {
    await addMockSetup(page);
    // Narrow viewport so the BOM table overflows horizontally
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const btnCells = page.locator('td.btn-group');
    const count = await btnCells.count();
    expect(count).toBeGreaterThan(0);

    // Get the inventory panel's right edge
    const panelBox = await page.locator('#panel-inventory').boundingBox();
    expect(panelBox).not.toBeNull();

    // Every btn-group cell should be within the panel's visible area
    for (let i = 0; i < Math.min(count, 5); i++) {
      const cellBox = await btnCells.nth(i).boundingBox();
      if (!cellBox) continue; // off-screen vertically, skip
      expect(cellBox.x + cellBox.width,
        `btn-group cell[${i}] right edge should be within panel`
      ).toBeLessThanOrEqual(panelBox.x + panelBox.width + 1);
    }
  });

  test('button column header is sticky', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const th = page.locator('th.btn-group-hdr');
    await expect(th).toHaveCount(1);

    const style = await th.evaluate(el => {
      const cs = window.getComputedStyle(el);
      return { position: cs.position, right: cs.right };
    });
    expect(style.position).toBe('sticky');
    expect(style.right).toBe('0px');
  });

  test('button cell background matches row tint (not transparent)', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Check that the ::before pseudo-element provides an opaque base
    const firstBtnCell = page.locator('td.btn-group').first();
    const beforeBg = await firstBtnCell.evaluate(el => {
      return window.getComputedStyle(el, '::before').backgroundColor;
    });
    // ::before should have the opaque --bg-base color, not transparent
    expect(beforeBg).not.toBe('rgba(0, 0, 0, 0)');
    expect(beforeBg).not.toBe('transparent');

    // The cell itself should inherit from the row (not be the raw --bg-base)
    const cellBg = await firstBtnCell.evaluate(el => {
      return window.getComputedStyle(el).backgroundColor;
    });
    const rowBg = await firstBtnCell.evaluate(el => {
      return window.getComputedStyle(el.closest('tr')).backgroundColor;
    });
    expect(cellBg).toBe(rowBg);
  });

  test('buttons remain visible after scrolling table horizontally', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const tableWrap = page.locator('#inventory-body .table-wrap');

    // Scroll the table all the way to the left (default) — buttons should be visible
    const btnBefore = await page.locator('td.btn-group').first().boundingBox();
    expect(btnBefore).not.toBeNull();

    // Scroll table-wrap to the right to push content left
    await tableWrap.evaluate(el => { el.scrollLeft = 200; });
    await page.waitForTimeout(100);

    // Buttons should still be within the panel bounds
    const panelBox = await page.locator('#panel-inventory').boundingBox();
    const btnAfter = await page.locator('td.btn-group').first().boundingBox();
    expect(btnAfter).not.toBeNull();
    expect(btnAfter.x + btnAfter.width,
      'btn-group should remain visible after horizontal scroll'
    ).toBeLessThanOrEqual(panelBox.x + panelBox.width + 1);
  });
});
