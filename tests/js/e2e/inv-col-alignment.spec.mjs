// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures/inventory.json'), 'utf8')
);

/**
 * Measure left-edge x-positions of header column cells and the matching row
 * cells. Returns an array of { col, hdr_left, row_left, diff }.
 */
async function measureAlignment(page) {
  return page.evaluate(() => {
    const pairs = [
      ['inv-col-group',  '.inv-row-group-cell'],
      ['inv-col-partid', '.part-ids'],
      ['inv-col-mpn',    '.part-mpn'],
      ['inv-col-unit',   '.part-unit-price'],
      ['inv-col-value',  '.part-value'],
      ['inv-col-qty',    '.part-qty'],
    ];
    const out = [];
    const row = document.querySelector('.inv-part-row');
    for (const [hdrCls, rowSel] of pairs) {
      const hdr = document.querySelector('.inv-col-header .' + hdrCls);
      const cell = row && row.querySelector(rowSel);
      if (!hdr || !cell) continue;
      const h = hdr.getBoundingClientRect();
      const r = cell.getBoundingClientRect();
      out.push({
        col: hdrCls,
        hdr_left: Math.round(h.left),
        row_left: Math.round(r.left),
        diff: Math.round(r.left - h.left),
      });
    }
    return out;
  });
}

test.describe('Inventory column header alignment with row cells', () => {
  for (const vp of [
    { name: 'narrow', width: 1400, height: 800 },   // panel ~520px, descriptions hidden
    { name: 'wide',   width: 2400, height: 1200 },  // panel ~900px, descriptions visible
  ]) {
    test(`columns align at ${vp.name} viewport (${vp.width}x${vp.height})`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto('/index.html');
      await waitForInventoryRows(page);

      const results = await measureAlignment(page);
      // Each column's row cell must start at the same x as its header cell
      // (allow 2px slack for sub-pixel rounding).
      for (const r of results) {
        expect(Math.abs(r.diff), `${r.col} misaligned by ${r.diff}px (hdr=${r.hdr_left}, row=${r.row_left})`).toBeLessThanOrEqual(2);
      }
    });
  }
});
