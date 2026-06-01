// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { rectOf } from './visual/capture.mjs';
import { measureAlignment } from './visual/measure.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

/**
 * Column mapping: each entry pairs the header cell CSS class (child of
 * .inv-col-header) with the matching row cell selector (child of .inv-part-row).
 *
 * Discovered from inv-col-alignment.spec.mjs:
 *   Header: .inv-col-header .inv-col-<name>  (e.g. .inv-col-partid)
 *   Row:    .inv-part-row .<row-class>        (e.g. .part-ids)
 */
const COLUMN_PAIRS = [
  { name: 'group',      headerCls: 'inv-col-group',  rowSel: '.inv-row-group-cell' },
  { name: 'part-id',    headerCls: 'inv-col-partid', rowSel: '.part-ids' },
  { name: 'mpn',        headerCls: 'inv-col-mpn',    rowSel: '.part-mpn' },
  { name: 'vendor',     headerCls: 'inv-col-vendor', rowSel: '.part-vendor' },
  { name: 'unit-price', headerCls: 'inv-col-unit',   rowSel: '.part-unit-price' },
  { name: 'value',      headerCls: 'inv-col-value',  rowSel: '.part-value' },
  { name: 'qty',        headerCls: 'inv-col-qty',    rowSel: '.part-qty' },
];

for (const vp of [
  { name: 'narrow', w: 1280, h: 720 },
  { name: 'wide',   w: 1920, h: 1080 },
]) {
  test(`column headers align with row cells (rendered layout) @ ${vp.name}`, async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY, {});
    await page.setViewportSize({ width: vp.w, height: vp.h });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(150);

    const firstRow = page.locator('.inv-part-row').first();
    await expect(firstRow).toBeVisible();

    let columnCount = 0;
    const offsets = [];

    for (const { name, headerCls, rowSel } of COLUMN_PAIRS) {
      const headerCell = page.locator(`.inv-col-header .${headerCls}`).first();
      const rowCell = firstRow.locator(rowSel).first();

      // Skip columns not present in both header and first row
      const headerVisible = await headerCell.isVisible().catch(() => false);
      const rowVisible = await rowCell.isVisible().catch(() => false);
      if (!headerVisible || !rowVisible) continue;

      const headerRect = await rectOf(headerCell);
      const rowRect = await rectOf(rowCell);

      const offset = measureAlignment(headerRect, rowRect, 'left');
      offsets.push({ name, offset });
      columnCount++;

      expect(
        Math.abs(offset),
        `column "${name}" misaligned: header left=${headerRect.x.toFixed(1)}, ` +
          `row cell left=${rowRect.x.toFixed(1)}, offset=${offset.toFixed(1)}px`,
      ).toBeLessThanOrEqual(1.5);
    }

    expect(columnCount, 'no columns checked — selector typo or missing fixture').toBeGreaterThan(0);

    const maxOffset = offsets.reduce((m, o) => Math.max(m, Math.abs(o.offset)), 0);
    console.log(
      `[${vp.name}] checked ${columnCount} columns, ` +
        `max left-edge offset=${maxOffset.toFixed(2)}px ` +
        `(${offsets.map(o => `${o.name}:${o.offset.toFixed(1)}`).join(', ')})`,
    );
  });
}
