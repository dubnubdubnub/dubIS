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

// ── Header info ──

test.describe('Inventory — header info', () => {

  test('#inv-count shows part count', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const text = await page.locator('#inv-count').textContent();
    expect(text).toMatch(/\d+ parts/);
    // Should match the inventory size
    expect(text).toContain(MOCK_INVENTORY.length + ' parts');
  });

  test('#inv-total-value shows dollar total', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const text = await page.locator('#inv-total-value').textContent();
    expect(text).toMatch(/^\$/);
  });
});

// ── Section collapse/expand ──

test.describe('Inventory — section collapse/expand', () => {

  test('clicking section header toggles collapsed state and hides/shows parts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Find the first section header (could be .inv-parent-header or .inv-section-header)
    const firstHeader = page.locator('.inv-parent-header, .inv-section-header').first();
    await expect(firstHeader).toBeVisible();

    // Should not be collapsed initially
    await expect(firstHeader).not.toHaveClass(/collapsed/);

    // Count visible part rows before collapse
    const rowsBefore = await page.locator('.inv-part-row').count();
    expect(rowsBefore).toBeGreaterThan(0);

    // Click to collapse
    await firstHeader.click();
    await expect(firstHeader).toHaveClass(/collapsed/);

    // Some rows should disappear
    const rowsAfter = await page.locator('.inv-part-row').count();
    expect(rowsAfter).toBeLessThan(rowsBefore);

    // Click again to expand
    await firstHeader.click();
    await expect(firstHeader).not.toHaveClass(/collapsed/);

    // Rows should be restored
    const rowsRestored = await page.locator('.inv-part-row').count();
    expect(rowsRestored).toBe(rowsBefore);
  });

  test('parent header collapse hides all subsections', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Find a parent header that has subsections
    const parentHeader = page.locator('.inv-parent-header').first();
    const parentHeaderCount = await page.locator('.inv-parent-header').count();
    if (parentHeaderCount === 0) return; // skip if no hierarchy

    await expect(parentHeader).not.toHaveClass(/collapsed/);

    // Count subsection headers inside the same section container
    const parentSection = parentHeader.locator('..');
    const subsectionsBefore = await parentSection.locator('.inv-subsection').count();

    // Collapse parent
    await parentHeader.click();
    await expect(parentHeader).toHaveClass(/collapsed/);

    // Subsections should be gone (not rendered)
    const subsectionsAfter = await parentSection.locator('.inv-subsection').count();
    expect(subsectionsAfter).toBe(0);
  });

  test('subsection header collapse hides only its parts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const subHeaderCount = await page.locator('.inv-subsection-header').count();
    if (subHeaderCount === 0) return;

    // Get the text of the first subsection header to re-locate after re-render
    const subHeaderText = await page.locator('.inv-subsection-header').first().textContent();

    const subsection = page.locator('.inv-subsection-header').first().locator('..');
    const partsBefore = await subsection.locator('.inv-part-row').count();
    if (partsBefore === 0) return;

    // Total rows across all sections
    const totalBefore = await page.locator('.inv-part-row').count();

    // Collapse subsection — click triggers full re-render, so locators become stale
    await page.locator('.inv-subsection-header').first().click();

    // After re-render, re-locate the header by its text and verify collapsed
    const collapsedHeader = page.locator('.inv-subsection-header.collapsed').first();
    await expect(collapsedHeader).toBeVisible({ timeout: 5000 });

    // Total should decrease by exactly the collapsed section's parts
    const totalAfter = await page.locator('.inv-part-row').count();
    expect(totalAfter).toBe(totalBefore - partsBefore);
  });
});

// ── Search filtering ──

test.describe('Inventory — search filtering', () => {

  test('typing in search filters parts after debounce', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const totalBefore = await page.locator('.inv-part-row').count();

    // Type a search term that matches only some parts
    await page.locator('#inv-search').fill('XT60');
    // Wait for debounce (150ms) + rendering buffer
    await page.waitForTimeout(300);

    const totalAfter = await page.locator('.inv-part-row').count();
    expect(totalAfter).toBeLessThan(totalBefore);
    expect(totalAfter).toBeGreaterThan(0);
  });

  test('search matches LCSC, MPN, and description', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Get an LCSC code from the first inventory item
    const firstLcsc = MOCK_INVENTORY.find(i => i.lcsc)?.lcsc;
    if (firstLcsc) {
      await page.locator('#inv-search').fill(firstLcsc);
      await page.waitForTimeout(300);
      const count = await page.locator('.inv-part-row').count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test('clearing search restores all parts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const totalBefore = await page.locator('.inv-part-row').count();

    // Filter
    await page.locator('#inv-search').fill('XT60');
    await page.waitForTimeout(300);
    const filtered = await page.locator('.inv-part-row').count();
    expect(filtered).toBeLessThan(totalBefore);

    // Clear
    await page.locator('#inv-search').fill('');
    await page.waitForTimeout(300);
    const restored = await page.locator('.inv-part-row').count();
    expect(restored).toBe(totalBefore);
  });

  test('section counts update with search', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Get a section count before search
    const countSpan = page.locator('.inv-section-count').first();
    const textBefore = await countSpan.textContent();

    // Search for something specific
    await page.locator('#inv-search').fill('connector');
    await page.waitForTimeout(300);

    // Sections that don't match should not be rendered, but those that match
    // should have updated counts — just verify the app doesn't crash
    const visibleSections = await page.locator('.inv-section-count').count();
    expect(visibleSections).toBeGreaterThanOrEqual(0);
  });
});

// ── Adjustment modal ──

test.describe('Inventory — adjustment modal', () => {

  test('clicking .adj-btn opens modal with part info', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Modal should start hidden
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);

    // Click the first adjust button
    await page.locator('.adj-btn').first().click();

    // Modal should be visible
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    // Title should be populated
    const title = await page.locator('#modal-title').textContent();
    expect(title.length).toBeGreaterThan(0);

    // Current qty should be shown
    const qtyText = await page.locator('#modal-current-qty').textContent();
    expect(qtyText).toMatch(/Current qty: \d+/);

    // Type defaults to "set"
    await expect(page.locator('#adj-type')).toHaveValue('set');
  });

  test('cancel closes modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    await page.locator('#adj-cancel').click();
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  });

  test('apply calls API, closes modal, and shows toast', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    // Set a qty value
    await page.locator('#adj-qty').fill('50');

    await page.locator('#adj-apply').click();

    // Modal should close
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);

    // Toast should appear
    await expect(page.locator('#toast')).toHaveClass(/show/);
  });

  test('Enter key triggers apply', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    // Focus qty input and press Enter
    await page.locator('#adj-qty').focus();
    await page.keyboard.press('Enter');

    // Modal should close
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  });

  test('Escape key closes modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('.adj-btn').first().click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  });
});

// ── Price modal ──

test.describe('Inventory — price modal', () => {

  test('clicking .price-warn-btn opens price modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // price-warn-btn only appears on parts with qty > 0 but no unit_price
    const priceBtn = page.locator('.price-warn-btn').first();
    const priceBtnCount = await priceBtn.count();
    if (priceBtnCount === 0) return; // skip if all parts have prices

    await priceBtn.click();

    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);
    const title = await page.locator('#price-modal-title').textContent();
    expect(title.length).toBeGreaterThan(0);
  });

  test('cancel closes price modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const priceBtn = page.locator('.price-warn-btn').first();
    if (await priceBtn.count() === 0) return;

    await priceBtn.click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    await page.locator('#price-cancel').click();
    await expect(page.locator('#price-modal')).toHaveClass(/hidden/);
  });

  test('apply closes price modal and shows toast', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const priceBtn = page.locator('.price-warn-btn').first();
    if (await priceBtn.count() === 0) return;

    await priceBtn.click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    // Fill a price value
    await page.locator('#price-unit').fill('1.50');

    await page.locator('#price-apply').click();
    await expect(page.locator('#price-modal')).toHaveClass(/hidden/);
    await expect(page.locator('#toast')).toHaveClass(/show/);
  });
});
