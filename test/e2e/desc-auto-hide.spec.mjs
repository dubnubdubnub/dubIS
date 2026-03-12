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

/**
 * Inject pywebview mock + inventory data before any app scripts run.
 */
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

/** Wait for inventory rows to appear in the DOM */
async function waitForInventoryRows(page) {
  await page.waitForSelector('.inv-part-row', { timeout: 10_000 });
}

/** Log diagnostic dimensions */
async function logDimensions(page, label) {
  const dims = await page.evaluate(() => {
    const body = document.getElementById('inventory-body');
    return {
      windowWidth: window.innerWidth,
      windowHeight: window.innerHeight,
      invBodyWidth: body ? body.offsetWidth : -1,
      invBodyHeight: body ? body.offsetHeight : -1,
    };
  });
  console.log(`[${label}]`, dims);
  return dims;
}

/** Count .part-desc elements */
async function countDescs(page) {
  return page.locator('.part-desc').count();
}

/** Measure height of first row and first desc */
async function logRowHeights(page, label) {
  const rowCount = await page.locator('.inv-part-row').count();
  if (rowCount === 0) return;
  const firstRow = await page.locator('.inv-part-row').first().evaluate(el => ({
    width: el.offsetWidth,
    height: el.offsetHeight,
  }));
  console.log(`First .inv-part-row (${label}):`, firstRow);

  const descCount = await page.locator('.part-desc').count();
  if (descCount > 0) {
    const firstDesc = await page.locator('.part-desc').first().evaluate(el => ({
      width: el.offsetWidth,
      height: el.offsetHeight,
      text: el.textContent.slice(0, 60),
    }));
    console.log(`First .part-desc (${label}):`, firstDesc);
  }
}

/** Measure first N inventory rows */
async function measureInvRows(page, n) {
  const count = await page.locator('.inv-part-row').count();
  const limit = Math.min(n, count);
  const rows = [];
  for (let i = 0; i < limit; i++) {
    const info = await page.locator('.inv-part-row').nth(i).evaluate(el => ({
      height: el.offsetHeight,
      hasDesc: !!el.querySelector('.part-desc'),
      descHeight: el.querySelector('.part-desc') ? el.querySelector('.part-desc').offsetHeight : null,
      text: (el.querySelector('.part-mpn') || {}).textContent || '',
    }));
    rows.push(info);
  }
  return rows;
}

/** Measure first N BOM table rows with per-cell breakdown for tall rows */
async function measureBomRows(page, n) {
  const count = await page.locator('tr[data-part-key]').count();
  const limit = Math.min(n, count);
  const rows = [];
  for (let i = 0; i < limit; i++) {
    const info = await page.locator('tr[data-part-key]').nth(i).evaluate(el => {
      const cells = Array.from(el.querySelectorAll('td'));
      const cellHeights = cells.map((td, idx) => ({
        idx,
        h: td.offsetHeight,
        cls: td.className || '',
        text: td.textContent.slice(0, 40),
      }));
      return {
        height: el.offsetHeight,
        width: el.offsetWidth,
        partKey: el.dataset.partKey,
        cells: cellHeights,
      };
    });
    rows.push(info);
  }
  return rows;
}

/** Load BOM into the app by calling its global functions directly */
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
    // Compute effective rows (multiplier=1)
    const rows = results.map(r => ({
      ...r,
      effectiveQty: r.bom.qty,
      effectiveStatus: r.status,
    }));
    EventBus.emit(Events.BOM_LOADED, { rows, fileName: 'test-bom.csv', multiplier: 1 });
  }, bomCsv);
}

// ── Normal inventory mode tests ──

test.describe('Description auto-hide — normal inventory mode', () => {

  test('narrow viewport (1200px) — descriptions hidden', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'narrow-1200');
    const descCount = await countDescs(page);
    console.log('Desc count at 1200px:', descCount);
    console.log('Total .inv-part-row:', await page.locator('.inv-part-row').count());
    await logRowHeights(page, 'narrow');
    expect(descCount).toBe(0);
    expect(dims.invBodyWidth).toBeLessThan(680);
  });

  test('wide viewport (1920px) — descriptions visible', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'wide-1920');
    const descCount = await countDescs(page);
    console.log('Desc count at 1920px:', descCount);
    console.log('Total .inv-part-row:', await page.locator('.inv-part-row').count());
    expect(descCount).toBeGreaterThan(0);
    expect(dims.invBodyWidth).toBeGreaterThanOrEqual(680);
    await logRowHeights(page, 'wide');
  });

  test('resize wide → narrow — descriptions disappear', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    let descCount = await countDescs(page);
    expect(descCount).toBeGreaterThan(0);

    await page.setViewportSize({ width: 1200, height: 700 });
    await page.waitForTimeout(500);
    descCount = await countDescs(page);
    console.log('Desc count after resize to narrow:', descCount);
    expect(descCount).toBe(0);
  });

  test('resize narrow → wide — descriptions appear', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    let descCount = await countDescs(page);
    expect(descCount).toBe(0);

    await page.setViewportSize({ width: 1920, height: 900 });
    await page.waitForTimeout(500);
    descCount = await countDescs(page);
    console.log('Desc count after resize to wide:', descCount);
    expect(descCount).toBeGreaterThan(0);
  });

  test('medium viewport (~1500px) — boundary check', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1500, height: 800 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'medium-1500');
    const descCount = await countDescs(page);
    console.log('Desc count at 1500px:', descCount, ' inv-body width:', dims.invBodyWidth);
    if (dims.invBodyWidth >= 680) {
      expect(descCount).toBeGreaterThan(0);
    } else {
      expect(descCount).toBe(0);
    }
  });
});

// ── BOM comparison mode tests ──

test.describe('Row heights — BOM comparison mode', () => {

  test('BOM loaded at wide viewport — row heights', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await logDimensions(page, 'bom-wide-before');

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const dims = await logDimensions(page, 'bom-wide-after');
    const bomRowCount = await page.locator('tr[data-part-key]').count();
    const invRowCount = await page.locator('.inv-part-row').count();
    console.log('BOM rows (tr[data-part-key]):', bomRowCount);
    console.log('Remaining inv rows (.inv-part-row):', invRowCount);

    // Log first BOM table row height
    if (bomRowCount > 0) {
      const firstBomRow = await page.locator('tr[data-part-key]').first().evaluate(el => ({
        width: el.offsetWidth,
        height: el.offsetHeight,
        partKey: el.dataset.partKey,
      }));
      console.log('First BOM row (wide):', firstBomRow);
    }
    // Log remaining inventory row heights
    await logRowHeights(page, 'bom-remaining-wide');
  });

  test('BOM loaded at narrow viewport — row heights', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    const dims = await logDimensions(page, 'bom-narrow');
    const bomRowCount = await page.locator('tr[data-part-key]').count();
    const invRowCount = await page.locator('.inv-part-row').count();
    console.log('BOM rows (narrow):', bomRowCount);
    console.log('Remaining inv rows (narrow):', invRowCount);

    if (bomRowCount > 0) {
      const firstBomRow = await page.locator('tr[data-part-key]').first().evaluate(el => ({
        width: el.offsetWidth,
        height: el.offsetHeight,
        partKey: el.dataset.partKey,
      }));
      console.log('First BOM row (narrow):', firstBomRow);
      // BOM rows should not wrap excessively — max ~40px (single line + padding + possible alt badge)
      expect(firstBomRow.height).toBeLessThanOrEqual(40);
    }
    await logRowHeights(page, 'bom-remaining-narrow');
  });

  test('BOM loaded — resize wide to narrow', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    await loadBom(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Log at wide
    const descCountWide = await countDescs(page);
    console.log('Desc count (BOM+wide):', descCountWide);
    await logRowHeights(page, 'bom-resize-wide');

    // Resize to narrow
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.waitForTimeout(500);

    const descCountNarrow = await countDescs(page);
    console.log('Desc count (BOM+narrow):', descCountNarrow);
    await logRowHeights(page, 'bom-resize-narrow');
    expect(descCountNarrow).toBe(0);
  });
});

// ── Row height survey across viewport sizes ──

const VIEWPORTS = [
  { width: 1200, height: 700, label: '1200×700' },
  { width: 1400, height: 800, label: '1400×800' },
  { width: 1600, height: 900, label: '1600×900' },
  { width: 1920, height: 1080, label: '1920×1080' },
  { width: 2560, height: 1440, label: '2560×1440' },
];

test.describe('Row height survey — without BOM', () => {
  for (const vp of VIEWPORTS) {
    test(`inventory rows at ${vp.label}`, async ({ page }) => {
      await addMockSetup(page);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await page.waitForTimeout(300);
      const dims = await logDimensions(page, `inv-${vp.label}`);
      const invRows = await measureInvRows(page, 5);
      console.log(`Inv rows at ${vp.label}:`, invRows);
    });
  }
});

test.describe('Row height survey — with BOM', () => {
  for (const vp of VIEWPORTS) {
    test(`BOM + inv rows at ${vp.label}`, async ({ page }) => {
      await addMockSetup(page);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await page.waitForTimeout(300);

      await loadBom(page, BOM_CSV);
      await page.waitForTimeout(300);

      const dims = await logDimensions(page, `bom-${vp.label}`);
      const bomRows = await measureBomRows(page, 5);
      for (const row of bomRows) {
        console.log(`BOM row ${row.partKey} (${vp.label}): h=${row.height} w=${row.width}`);
        for (const c of row.cells) {
          if (c.h > 30) console.log(`  TALL cell[${c.idx}] h=${c.h} cls="${c.cls}" text="${c.text}"`);
        }
      }
      const invRows = await measureInvRows(page, 5);
      console.log(`Remaining inv rows at ${vp.label}:`, invRows);
    });
  }
});
