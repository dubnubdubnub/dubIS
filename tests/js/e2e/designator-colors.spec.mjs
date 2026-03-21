// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaEmit, loadBomViaFileInput, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');
const BOM_CSV = fs.readFileSync(BOM_CSV_PATH, 'utf8');
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

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

// ── Designator coloring in inventory panel (BOM comparison table) ──

test.describe('Designator colors — inventory panel BOM table', () => {

  test('designators have correct color classes', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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
      expect(await span.count(), `No span found for prefix ${prefix}`).toBeGreaterThan(0);
      const cls = await span.getAttribute('class');
      console.log(`${prefix}* span class: "${cls}" (expected: "${expectedClass}")`);
      expect(cls).toContain(expectedClass);
    }
  });

  test('all ref spans have data-ref attribute matching text', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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

test.describe('Designator colors — inventory panel BOM table — with PO', () => {

  test('designators have correct color classes with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const refSpans = page.locator('#inventory-body .refs-cell [data-ref]');
    const count = await refSpans.count();
    console.log('Colored ref spans in inventory panel (BOM+PO):', count);
    expect(count).toBeGreaterThan(0);

    for (const [prefix, expectedClass] of Object.entries(EXPECTED_CLASSES)) {
      const span = refSpans.filter({ hasText: new RegExp('^' + prefix + '\\d') }).first();
      expect(await span.count(), `No span found for prefix ${prefix} (BOM+PO)`).toBeGreaterThan(0);
      const cls = await span.getAttribute('class');
      expect(cls).toContain(expectedClass);
    }
  });

  test('designators have correct color classes at narrow viewport (1200px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const refSpans = page.locator('#inventory-body .refs-cell [data-ref]');
    const count = await refSpans.count();
    console.log('Colored ref spans at 1200px:', count);
    expect(count).toBeGreaterThan(0);

    for (const [prefix, expectedClass] of Object.entries(EXPECTED_CLASSES)) {
      const span = refSpans.filter({ hasText: new RegExp('^' + prefix + '\\d') }).first();
      expect(await span.count(), `No span found for prefix ${prefix} (1200px)`).toBeGreaterThan(0);
      const cls = await span.getAttribute('class');
      expect(cls).toContain(expectedClass);
    }
  });
});

// ── Designator coloring in BOM panel (staging table) ──

test.describe('Designator colors — BOM panel staging table', () => {

  test('staging ref column shows colored display divs', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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
      expect(await span.count(), `No span found for prefix ${prefix} (BOM staging)`).toBeGreaterThan(0);
      const cls = await span.getAttribute('class');
      console.log(`BOM staging ${prefix}* class: "${cls}" (expected: "${expectedClass}")`);
      expect(cls).toContain(expectedClass);
    }
  });

  test('clicking refs display reveals input for editing', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
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
    await addMockSetup(page, MOCK_INVENTORY);
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
    await addMockSetup(page, MOCK_INVENTORY);
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
    await addMockSetup(page, MOCK_INVENTORY);
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

// ── Cross-panel hover with PO loaded ──

test.describe('Cross-panel designator hover highlighting — with PO', () => {

  test('hover highlighting works with BOM + PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    const invRefC1 = page.locator('#inventory-body [data-ref="C1"]').first();
    const bomRefC1 = page.locator('#bom-tbody [data-ref="C1"]').first();

    await expect(invRefC1).toBeVisible();
    await expect(bomRefC1).toBeVisible();

    await invRefC1.hover();
    await expect(invRefC1).toHaveClass(/ref-highlight/);
    await expect(bomRefC1).toHaveClass(/ref-highlight/);
    console.log('Hover on inv C1 with PO → both panels highlighted');
  });

  test('hover highlighting works at narrow viewport (1200px) with BOM', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    const invRefC1 = page.locator('#inventory-body [data-ref="C1"]').first();
    const bomRefC1 = page.locator('#bom-tbody [data-ref="C1"]').first();

    await expect(invRefC1).toBeVisible();
    await expect(bomRefC1).toBeVisible();

    await invRefC1.hover();
    await expect(invRefC1).toHaveClass(/ref-highlight/);
    await expect(bomRefC1).toHaveClass(/ref-highlight/);
    console.log('Hover at 1200px → both panels highlighted');
  });
});
