// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');

// ── File loading ──

test.describe('BOM panel — file loading', () => {

  test('loading BOM shows results, staging rows, headers, and summary', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    // #bom-results should be visible (no .hidden)
    await expect(page.locator('#bom-results')).not.toHaveClass(/hidden/);

    // #bom-tbody should have rows
    const rowCount = await page.locator('#bom-tbody tr').count();
    expect(rowCount).toBeGreaterThan(0);

    // #bom-thead should have a header row
    const headerCount = await page.locator('#bom-thead tr').count();
    expect(headerCount).toBe(1);

    // Summary chips should appear in #bom-summary
    const chips = page.locator('#bom-summary .chip');
    const chipCount = await chips.count();
    expect(chipCount).toBeGreaterThan(0);

    // Check specific chip colors exist
    await expect(page.locator('#bom-summary .chip.blue')).toHaveCount(1);
  });
});

// ── Staging table ──

test.describe('BOM panel — staging table', () => {

  test('editing a cell marks save button as dirty', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    // Save button should not be dirty initially
    await expect(page.locator('#bom-save-btn')).not.toHaveClass(/dirty/);

    // Edit a visible cell — skip refs column (its input is hidden behind a display div)
    // Use a visible input (not the refs column which has style="display: none")
    const visibleInputs = page.locator('#bom-tbody tr:first-child td input:visible');
    const firstInput = visibleInputs.first();
    await firstInput.scrollIntoViewIfNeeded();
    await firstInput.click();
    await firstInput.fill('EDITED');
    await firstInput.press('Tab');

    // Save button should now be dirty
    await expect(page.locator('#bom-save-btn')).toHaveClass(/dirty/);
  });

  test('clicking × removes row and staging title count decreases', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    const countBefore = await page.locator('#bom-tbody tr').count();

    // Get staging title text before
    const titleBefore = await page.locator('#bom-staging-title').textContent();
    const matchBefore = titleBefore.match(/(\d+) rows/);
    const numBefore = matchBefore ? parseInt(matchBefore[1]) : 0;

    // Click delete on first row
    await page.locator('#bom-tbody tr:first-child .row-delete').click();

    const countAfter = await page.locator('#bom-tbody tr').count();
    expect(countAfter).toBe(countBefore - 1);

    // Staging title should show decreased count
    const titleAfter = await page.locator('#bom-staging-title').textContent();
    const matchAfter = titleAfter.match(/(\d+) rows/);
    const numAfter = matchAfter ? parseInt(matchAfter[1]) : 0;
    expect(numAfter).toBe(numBefore - 1);
  });

  test('DNP rows get .row-dnp class', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    // bom.csv has DNP entries (FID, H, TP rows)
    const dnpRows = page.locator('#bom-tbody tr.row-dnp');
    const dnpCount = await dnpRows.count();
    expect(dnpCount).toBeGreaterThan(0);
  });
});

// ── Multiplier ──

test.describe('BOM panel — multiplier', () => {

  test('multiplier defaults to 1 and changing it updates price info', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    // Multiplier should default to 1
    await expect(page.locator('#bom-qty-mult')).toHaveValue('1');

    const priceBefore = await page.locator('#bom-price-info').textContent();

    // Change multiplier to 3 — click, clear, type (fires input event naturally)
    const multInput = page.locator('#bom-qty-mult');
    await multInput.click();
    await multInput.fill('3');
    await multInput.press('Tab');

    // Wait for re-render
    await page.waitForTimeout(200);

    const priceAfter = await page.locator('#bom-price-info').textContent();
    // With multiplier > 1 and some prices, we should see "total" in price info
    if (priceBefore) {
      expect(priceAfter).not.toBe(priceBefore);
    }
  });
});

// ── Clear BOM ──

test.describe('BOM panel — clear', () => {

  test('clear resets panel to initial state', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    // Verify BOM is loaded
    await expect(page.locator('#bom-results')).not.toHaveClass(/hidden/);

    await page.locator('#bom-clear-btn').click();

    // Results should be hidden
    await expect(page.locator('#bom-results')).toHaveClass(/hidden/);

    // Tbody should be empty
    await expect(page.locator('#bom-tbody')).toBeEmpty();

    // Drop zone should lose .loaded
    await expect(page.locator('#bom-drop-zone')).not.toHaveClass(/loaded/);
  });
});

// ── Consume modal ──

test.describe('BOM panel — consume modal', () => {

  test('consume button opens modal with correct subtitle', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    await page.locator('#bom-consume-btn').click();

    // Modal should be visible
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    // Subtitle should mention parts and filename
    const subtitle = await page.locator('#consume-subtitle').textContent();
    expect(subtitle).toContain('matched parts');
    expect(subtitle).toContain('bom.csv');
  });

  test('first confirm click arms to "Are you sure?", second executes', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    const confirmBtn = page.locator('#consume-confirm');

    // Initially should show "Consume" with btn-apply class
    await expect(confirmBtn).toHaveText('Consume');
    await expect(confirmBtn).toHaveClass(/btn-apply/);

    // First click arms it
    await confirmBtn.click();
    await expect(confirmBtn).toHaveText('Are you sure?');
    await expect(confirmBtn).toHaveClass(/btn-danger/);
    await expect(confirmBtn).not.toHaveClass(/btn-apply/);

    // Second click executes — modal closes, toast shown
    await confirmBtn.click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
    await expect(page.locator('#toast')).toHaveClass(/show/);
  });

  test('cancel button closes modal and resets confirm state', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    // Arm the confirm button
    await page.locator('#consume-confirm').click();
    await expect(page.locator('#consume-confirm')).toHaveText('Are you sure?');

    // Cancel should close and reset
    await page.locator('#consume-cancel').click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);

    // Re-open — confirm should be reset to "Consume"
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-confirm')).toHaveText('Consume');
    await expect(page.locator('#consume-confirm')).toHaveClass(/btn-apply/);
  });

  test('Escape key closes consume modal', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadBomViaFileInput(page, BOM_CSV_PATH);

    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);

    await page.keyboard.press('Escape');
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
  });
});
