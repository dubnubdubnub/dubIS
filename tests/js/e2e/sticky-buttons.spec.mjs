// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBom, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV = fs.readFileSync(
  path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8'
);
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

test.describe('Sticky button column — BOM comparison mode', () => {

  test('button column stays within viewport when table scrolls horizontally', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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
    await addMockSetup(page, MOCK_INVENTORY);
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

  test('button cell ::before composites row tint over opaque base', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Find a tinted row (one with a row-* class)
    const tintedRow = page.locator('tr[class*="row-"]').first();
    await expect(tintedRow).toBeVisible();

    // The row should define --row-bg
    const rowBgVar = await tintedRow.evaluate(el =>
      getComputedStyle(el).getPropertyValue('--row-bg').trim()
    );
    expect(rowBgVar, '--row-bg should be set to a non-empty color value').not.toBe('');

    // The ::before on its btn-group cell should have a background-image gradient
    const btnCell = tintedRow.locator('td.btn-group');
    const beforeBgImage = await btnCell.evaluate(el =>
      getComputedStyle(el, '::before').backgroundImage
    );
    // Should be a linear-gradient containing the row tint, not "none"
    expect(beforeBgImage).toContain('linear-gradient');

    // Un-tinted rows should fall back to transparent (no visible tint)
    const untintedRowCount = await page.locator('#bom-tbody tr:not([class*="row-"])').count();
    if (untintedRowCount > 0) {
      // Only check gradient on un-tinted rows if mock data produces any
      const untintedBtnCell = page.locator('#bom-tbody tr:not([class*="row-"])').first().locator('td.btn-group');
      await expect(untintedBtnCell, 'Un-tinted row should have a btn-group cell').toHaveCount(1);
      const untintedBgImage = await untintedBtnCell.evaluate(el =>
        getComputedStyle(el, '::before').backgroundImage
      );
      // Should still have gradient but with transparent (rgba(0,0,0,0))
      expect(untintedBgImage).toContain('linear-gradient');
    } else {
      // All BOM rows are tinted (all matched) — this is valid with the current mock data
      console.log('No un-tinted BOM rows found — all rows matched (expected with current mock data)');
    }
  });

  test('buttons remain visible after scrolling table horizontally', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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

test.describe('BOM table buttons not clipped by panel overflow', () => {

  /**
   * Verify individual buttons inside td.btn-group cells are not clipped.
   * The existing cell-level tests above check the td position, but with
   * table-layout:fixed the cell can be correctly positioned while its
   * content (buttons) gets clipped by overflow:hidden if the column is
   * too narrow. This catches that scenario.
   */
  for (const vp of [
    { width: 1100, height: 700, label: '1100×700' },
    { width: 2016, height: 1244, label: '2016×1244' },
  ]) {
    test(`BOM table buttons not clipped at ${vp.label}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await page.waitForTimeout(300);
      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      const result = await page.evaluate(() => {
        const panelBody = document.getElementById('inventory-body');
        if (!panelBody) return { checked: 0, issues: ['panel body not found'] };
        const bodyRect = panelBody.getBoundingClientRect();
        const rows = panelBody.querySelectorAll('tr[data-part-key]');
        const issues = [];
        const checked = Math.min(rows.length, 10);
        for (let i = 0; i < checked; i++) {
          const buttons = rows[i].querySelectorAll('td.btn-group button');
          for (const btn of buttons) {
            if (btn.offsetWidth === 0 || btn.offsetHeight === 0) {
              issues.push(`row ${i}: ${btn.className} has zero dimensions`);
              continue;
            }
            const btnRect = btn.getBoundingClientRect();
            if (btnRect.right > bodyRect.right + 1) {
              issues.push(`row ${i}: ${btn.className} "${btn.textContent.trim()}" clipped right (btn.right=${Math.round(btnRect.right)}, panel.right=${Math.round(bodyRect.right)})`);
            }
          }
        }
        return { checked, panelWidth: Math.round(bodyRect.width), issues };
      });

      console.log(`BOM btn-group at ${vp.label}: panel=${result.panelWidth}px, checked=${result.checked}, issues=${result.issues.length}`);
      result.issues.forEach(i => console.log('  ' + i));
      expect(result.issues.length, result.issues.join('; ')).toBe(0);
    });
  }
});

test.describe('Sticky button column — BOM + PO mode', () => {

  test('button column stays within viewport with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const btnCells = page.locator('td.btn-group');
    const count = await btnCells.count();
    expect(count).toBeGreaterThan(0);

    const panelBox = await page.locator('#panel-inventory').boundingBox();
    expect(panelBox).not.toBeNull();

    for (let i = 0; i < Math.min(count, 5); i++) {
      const cellBox = await btnCells.nth(i).boundingBox();
      if (!cellBox) continue;
      expect(cellBox.x + cellBox.width,
        `btn-group cell[${i}] right edge should be within panel (BOM+PO)`
      ).toBeLessThanOrEqual(panelBox.x + panelBox.width + 1);
    }
  });

  test('buttons remain visible after horizontal scroll with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const tableWrap = page.locator('#inventory-body .table-wrap');
    const btnBefore = await page.locator('td.btn-group').first().boundingBox();
    expect(btnBefore).not.toBeNull();

    await tableWrap.evaluate(el => { el.scrollLeft = 200; });
    await page.waitForTimeout(100);

    const panelBox = await page.locator('#panel-inventory').boundingBox();
    const btnAfter = await page.locator('td.btn-group').first().boundingBox();
    expect(btnAfter).not.toBeNull();
    expect(btnAfter.x + btnAfter.width,
      'btn-group should remain visible after h-scroll (BOM+PO)'
    ).toBeLessThanOrEqual(panelBox.x + panelBox.width + 1);
  });

  test('button column header sticky at wide viewport (1920px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
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

  test('row tint compositing works with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1100, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Scope to inventory panel BOM table — import staging rows also have row-* classes
    const tintedRow = page.locator('#inventory-body tr[class*="row-"]').first();
    await expect(tintedRow).toBeVisible();

    const rowBgVar = await tintedRow.evaluate(el =>
      getComputedStyle(el).getPropertyValue('--row-bg').trim()
    );
    expect(rowBgVar, '--row-bg should be set to a non-empty color value (with PO)').not.toBe('');

    const btnCell = tintedRow.locator('td.btn-group');
    const beforeBgImage = await btnCell.evaluate(el =>
      getComputedStyle(el, '::before').backgroundImage
    );
    expect(beforeBgImage).toContain('linear-gradient');
  });
});
