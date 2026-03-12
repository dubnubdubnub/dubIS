// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');
const BOM_CSV = fs.readFileSync(BOM_CSV_PATH, 'utf8');

/** Expected CSS class per designator prefix */
const EXPECTED_CLASSES = {
  R: 'ref-r',
  C: 'ref-c',
  D: 'ref-d',
  U: 'ref-ic',
  L: 'ref-l',
  Q: 'ref-ic',
  Y: 'ref-osc',
};

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
        get_digikey_login_status: async () => ({ logged_in: false }),
        check_digikey_session: async () => ({ logged_in: false }),
        start_digikey_login: async () => null,
        sync_digikey_cookies: async () => ({ logged_in: false }),
        logout_digikey: async () => null,
        import_csv: async () => inventory,
        remove_last_adjustments: async () => inventory,
        set_bom_dirty: async () => null,
      },
    };
  }, MOCK_INVENTORY);
}

async function waitForInventoryRows(page) {
  await page.waitForSelector('.inv-part-row', { timeout: 10_000 });
}

/**
 * Load BOM via evaluate (populates inventory panel only — no staging table).
 * Used for inventory-panel-only tests.
 */
async function loadBomViaEmit(page, bomCsv) {
  await page.evaluate((csv) => {
    const result = processBOM(csv, 'test-bom.csv');
    if (!result) throw new Error('processBOM returned null');
    const { headers, cols, aggregated } = result;
    App.bomHeaders = headers;
    App.bomCols = cols;
    const results = matchBOM(aggregated, App.inventory, App.links.manualLinks, App.links.confirmedMatches);
    App.bomResults = results;
    App.bomFileName = 'test-bom.csv';
    const rows = results.map(r => {
      let status;
      if (r.bom.dnp) status = 'dnp';
      else if (!r.inv) status = 'missing';
      else if (r.matchType === 'value' || r.matchType === 'fuzzy') status = 'possible';
      else if (r.bom.qty <= r.inv.qty) status = 'ok';
      else status = 'short';
      const altQty = (r.alts || []).reduce((s, a) => s + a.qty, 0);
      const combinedQty = (r.inv ? r.inv.qty : 0) + altQty;
      const isShort = status === 'short';
      return {
        ...r,
        effectiveQty: r.bom.qty,
        effectiveStatus: status,
        altQty,
        combinedQty,
        coveredByAlts: isShort && combinedQty >= r.bom.qty,
      };
    });
    EventBus.emit(Events.BOM_LOADED, { rows, fileName: 'test-bom.csv', multiplier: 1 });
  }, bomCsv);
}

/**
 * Load BOM via file input (populates BOTH bom-panel staging table and inventory panel).
 * This triggers the full bom-panel loadBomText flow.
 */
async function loadBomViaFileInput(page, csvFilePath) {
  const fileInput = page.locator('#bom-file-input');
  await fileInput.setInputFiles(csvFilePath);
  // Wait for the BOM staging table to be populated
  await page.waitForSelector('#bom-tbody tr', { timeout: 10_000 });
}

// ── Designator coloring in inventory panel (BOM comparison table) ──

test.describe('Designator colors — inventory panel BOM table', () => {

  test('designators have correct color classes', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    // Inventory panel's BOM comparison table should have colored ref spans
    const refSpans = page.locator('#inventory-body .refs-cell [data-ref]');
    const count = await refSpans.count();
    console.log('Colored ref spans in inventory panel:', count);
    expect(count).toBeGreaterThan(0);

    // Check a sample of designators have the right color classes
    for (const [prefix, expectedClass] of Object.entries(EXPECTED_CLASSES)) {
      const span = refSpans.filter({ hasText: new RegExp('^' + prefix + '\\d') }).first();
      if (await span.count() > 0) {
        const cls = await span.getAttribute('class');
        console.log(`${prefix}* span class: "${cls}" (expected: "${expectedClass}")`);
        expect(cls).toContain(expectedClass);
      }
    }
  });

  test('all ref spans have data-ref attribute matching text', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const refSpans = page.locator('#inventory-body .refs-cell [data-ref]');
    const count = await refSpans.count();
    expect(count).toBeGreaterThan(0);

    // Verify each span's data-ref matches its text content
    for (let i = 0; i < Math.min(count, 20); i++) {
      const span = refSpans.nth(i);
      const dataRef = await span.getAttribute('data-ref');
      const text = await span.textContent();
      expect(dataRef).toBe(text);
    }
  });
});

// ── Designator coloring in BOM panel (staging table) ──

test.describe('Designator colors — BOM panel staging table', () => {

  test('staging ref column shows colored display divs', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    // BOM staging table should have .refs-cell display divs with colored ref spans
    const refsDisplays = page.locator('#bom-tbody .refs-cell');
    const displayCount = await refsDisplays.count();
    console.log('refs-cell display divs in BOM staging:', displayCount);
    expect(displayCount).toBeGreaterThan(0);

    // Check colored spans exist inside the display divs
    const coloredSpans = page.locator('#bom-tbody .refs-cell [data-ref]');
    const spanCount = await coloredSpans.count();
    console.log('Colored ref spans in BOM staging:', spanCount);
    expect(spanCount).toBeGreaterThan(0);

    // Verify some have the right color classes
    for (const [prefix, expectedClass] of Object.entries(EXPECTED_CLASSES)) {
      const span = coloredSpans.filter({ hasText: new RegExp('^' + prefix + '\\d') }).first();
      if (await span.count() > 0) {
        const cls = await span.getAttribute('class');
        console.log(`BOM staging ${prefix}* class: "${cls}" (expected: "${expectedClass}")`);
        expect(cls).toContain(expectedClass);
      }
    }
  });

  test('clicking refs display reveals input for editing', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    const firstRefsDisplay = page.locator('#bom-tbody .refs-cell').first();
    await expect(firstRefsDisplay).toBeVisible();

    // The sibling input should be hidden initially
    const parentTd = firstRefsDisplay.locator('..');
    const input = parentTd.locator('input');
    await expect(input).toBeHidden();

    // Click the display div
    await firstRefsDisplay.click();

    // Now the input should be visible and the display hidden
    await expect(input).toBeVisible();
    await expect(firstRefsDisplay).toBeHidden();

    // Blur the input — display should reappear
    await input.blur();
    await expect(firstRefsDisplay).toBeVisible();
    await expect(input).toBeHidden();
  });
});

// ── Cross-panel hover highlighting ──

test.describe('Cross-panel designator hover highlighting', () => {

  test('hovering ref in inventory panel highlights matching refs everywhere', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    // Find a designator that appears in both panels (e.g. C1)
    const invRefC1 = page.locator('#inventory-body [data-ref="C1"]').first();
    const bomRefC1 = page.locator('#bom-tbody [data-ref="C1"]').first();

    // Both should exist
    await expect(invRefC1).toBeVisible();
    await expect(bomRefC1).toBeVisible();

    // Neither should be highlighted initially
    await expect(invRefC1).not.toHaveClass(/ref-highlight/);
    await expect(bomRefC1).not.toHaveClass(/ref-highlight/);

    // Hover over the inventory panel's C1
    await invRefC1.hover();

    // Both should now have the highlight class
    await expect(invRefC1).toHaveClass(/ref-highlight/);
    await expect(bomRefC1).toHaveClass(/ref-highlight/);
    console.log('Hover on inv C1 → both panels highlighted');
  });

  test('hovering ref in BOM panel highlights matching refs in inventory panel', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    // Find R1 in both panels
    const bomRefR1 = page.locator('#bom-tbody [data-ref="R1"]').first();
    const invRefR1 = page.locator('#inventory-body [data-ref="R1"]').first();

    await expect(bomRefR1).toBeVisible();
    await expect(invRefR1).toBeVisible();

    // Hover over BOM panel's R1
    await bomRefR1.hover();

    // Both should highlight
    await expect(bomRefR1).toHaveClass(/ref-highlight/);
    await expect(invRefR1).toHaveClass(/ref-highlight/);
    console.log('Hover on bom R1 → both panels highlighted');
  });

  test('moving mouse away clears highlights', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    const invRefC1 = page.locator('#inventory-body [data-ref="C1"]').first();
    await invRefC1.hover();
    await expect(invRefC1).toHaveClass(/ref-highlight/);

    // Move mouse to something else (the header)
    await page.locator('.header').hover();

    // Highlights should be cleared
    const highlightedCount = await page.locator('.ref-highlight').count();
    expect(highlightedCount).toBe(0);
    console.log('Highlights cleared after mouse moved away');
  });
});
