// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

test.describe('Inventory row alignment across sections', () => {

  test('part rows in flat and subcategory sections share the same left edge', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    // Expand all sections so rows from both flat and hierarchy sections are visible
    // Collect the left offset of the first child element in every .inv-part-row
    const result = await page.evaluate(() => {
      const rows = document.querySelectorAll('.inv-part-row');
      if (rows.length === 0) return { count: 0, offsets: [], issues: [] };

      const offsets = [];
      for (const row of rows) {
        const rect = row.getBoundingClientRect();
        // Check if row is in a subsection (hierarchy) or directly in a section (flat)
        const inSubsection = !!row.closest('.inv-subsection');
        offsets.push({
          left: Math.round(rect.left),
          inSubsection,
        });
      }

      // All rows should have the same left offset
      const leftValues = new Set(offsets.map(o => o.left));
      const issues = [];
      if (leftValues.size > 1) {
        const flatLefts = offsets.filter(o => !o.inSubsection).map(o => o.left);
        const subLefts = offsets.filter(o => o.inSubsection).map(o => o.left);
        const flatLeft = flatLefts.length > 0 ? flatLefts[0] : null;
        const subLeft = subLefts.length > 0 ? subLefts[0] : null;
        issues.push(
          `Misaligned: flat section rows at x=${flatLeft}, subcategory rows at x=${subLeft} (delta=${subLeft - flatLeft}px)`
        );
      }

      return {
        count: offsets.length,
        uniqueLefts: [...leftValues],
        flatCount: offsets.filter(o => !o.inSubsection).length,
        subCount: offsets.filter(o => o.inSubsection).length,
        issues,
      };
    });

    console.log(`Row alignment: ${result.count} rows (${result.flatCount} flat, ${result.subCount} subsection), unique lefts: [${result.uniqueLefts}]`);
    expect(result.count).toBeGreaterThan(0);
    expect(result.subCount, 'need rows in subsections to test alignment').toBeGreaterThan(0);
    expect(result.flatCount, 'need rows in flat sections to test alignment').toBeGreaterThan(0);
    expect(result.issues.length, result.issues.join('; ')).toBe(0);
  });

  test('part row content columns align across all sections', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    // Check that the first content child (part-ids or part-id) starts at the same x
    // across flat and subsection rows
    const result = await page.evaluate(() => {
      const rows = document.querySelectorAll('.inv-part-row');
      if (rows.length === 0) return { count: 0, issues: [] };

      const positions = [];
      for (const row of rows) {
        const firstChild = row.firstElementChild;
        if (!firstChild) continue;
        const rect = firstChild.getBoundingClientRect();
        const inSubsection = !!row.closest('.inv-subsection');
        positions.push({ left: Math.round(rect.left), inSubsection });
      }

      const flatLefts = [...new Set(positions.filter(p => !p.inSubsection).map(p => p.left))];
      const subLefts = [...new Set(positions.filter(p => p.inSubsection).map(p => p.left))];

      const issues = [];
      if (flatLefts.length > 0 && subLefts.length > 0) {
        // Allow 1px tolerance for rounding
        for (const fl of flatLefts) {
          for (const sl of subLefts) {
            if (Math.abs(fl - sl) > 1) {
              issues.push(`Content misaligned: flat row content at x=${fl}, subsection content at x=${sl} (delta=${sl - fl}px)`);
            }
          }
        }
      }

      return {
        count: positions.length,
        flatLefts,
        subLefts,
        issues,
      };
    });

    console.log(`Content alignment: flat lefts=[${result.flatLefts}], sub lefts=[${result.subLefts}]`);
    expect(result.count).toBeGreaterThan(0);
    expect(result.issues.length, result.issues.join('; ')).toBe(0);
  });
});
