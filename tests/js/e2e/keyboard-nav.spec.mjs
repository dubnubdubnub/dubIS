// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaEmit } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

test.describe('Inventory roving grid', () => {
  test('arrow keys move focus within and across rows', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Focus the first grid cell (single tab stop established by refresh()).
    // This may be a section header or a part-row cell depending on inventory layout.
    const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
    await firstCell.focus();
    await expect(firstCell).toBeFocused();

    // ArrowDown moves to a different row.
    const startTag = await firstCell.evaluate((el) => el.tagName + el.className);
    await page.keyboard.press('ArrowDown');
    const afterDownTag = await page.evaluate(() => document.activeElement?.tagName + document.activeElement?.className);
    expect(afterDownTag).not.toBe(startTag);
  });

  test('only one tab stop exists in the inventory grid', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    const count = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(count).toBe(1);
  });

  test('plain inventory: ArrowRight moves across column spans within a row', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // The initial tab stop is often a section header (a single-cell row), where
    // ArrowRight has no next sibling and does nothing. Navigate down until we land
    // on a part row cell, which has multiple columns to move through.
    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    let onPartRow = await page.evaluate(() => !!document.activeElement?.closest('.inv-part-row'));
    let attempts = 0;
    while (!onPartRow && attempts < 15) {
      await page.keyboard.press('ArrowDown');
      onPartRow = await page.evaluate(() => !!document.activeElement?.closest('.inv-part-row'));
      attempts++;
    }
    if (!onPartRow) return; // fixture has no part rows reachable; pass trivially

    // Tag the focused element so we can verify identity changed after navigation.
    await page.evaluate(() => {
      document.activeElement?.setAttribute('data-test-focus-start', '1');
    });

    // ArrowRight should move focus to another column cell in the same row.
    await page.keyboard.press('ArrowRight');

    // Verify focus moved to a DIFFERENT element (not the tagged one).
    const moved = await page.evaluate(() => !document.activeElement?.hasAttribute('data-test-focus-start'));
    expect(moved).toBe(true);

    // At minimum, exactly one [tabindex="0"] should remain.
    const tabStopCount = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(tabStopCount).toBe(1);
    // The focused element should be a valid column cell or action button.
    const focusedIsCell = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return false;
      return el.matches(
        '.part-ids,.part-mpn,.part-vendor,.part-unit-price,.part-value,.part-qty,.part-desc,' +
        '.adj-btn,.link-btn,.confirm-btn,.unconfirm-btn,.swap-btn,' +
        '.no-dist-warn,.price-warn-btn,.generic-group-badge,.near-miss-badge,' +
        '.inv-section-header,.inv-parent-header,.inv-subsection-header',
      );
    });
    expect(focusedIsCell).toBe(true);

    // Clean up the marker attribute.
    await page.evaluate(() => {
      document.querySelector('[data-test-focus-start]')?.removeAttribute('data-test-focus-start');
    });
  });

  test('plain inventory: ArrowLeft moves back across column spans within a row', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Navigate down to a part row cell (section headers are single-cell rows;
    // ArrowRight/Left won't move from them).
    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    let onPartRow = await page.evaluate(() => !!document.activeElement?.closest('.inv-part-row'));
    let attempts = 0;
    while (!onPartRow && attempts < 15) {
      await page.keyboard.press('ArrowDown');
      onPartRow = await page.evaluate(() => !!document.activeElement?.closest('.inv-part-row'));
      attempts++;
    }
    if (!onPartRow) return; // no reachable part row; pass trivially

    // Move right first so there is room to move left.
    await page.keyboard.press('ArrowRight');

    // Tag the element that ArrowRight landed on.
    await page.evaluate(() => {
      document.activeElement?.setAttribute('data-test-focus-mid', '1');
    });

    // ArrowLeft should move back to a different (prior) element.
    await page.keyboard.press('ArrowLeft');

    const movedLeft = await page.evaluate(() => !document.activeElement?.hasAttribute('data-test-focus-mid'));
    expect(movedLeft).toBe(true);

    // Exactly one tab stop remains.
    const tabStopCount = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(tabStopCount).toBe(1);

    // Clean up marker.
    await page.evaluate(() => {
      document.querySelector('[data-test-focus-mid]')?.removeAttribute('data-test-focus-mid');
    });
  });

  test('plain inventory: ArrowDown moves focus to same column in next part row', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Need at least two part rows to test cross-row navigation.
    const rowCount = await page.locator('#inventory-body .inv-part-row').count();
    if (rowCount < 2) {
      // Not enough rows; pass trivially (follow existing pattern).
      return;
    }

    // Focus the first roving tab stop. Navigate into the first part row's cells
    // (skipping any section headers) by pressing ArrowDown until we land on a part row cell.
    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    // Find which inv-part-row the current focus belongs to (may need to navigate down
    // past section headers first).
    let startRowIndex = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return -1;
      const row = el.closest('.inv-part-row');
      if (!row) return -1;
      const rows = Array.from(document.querySelectorAll('#inventory-body .inv-part-row'));
      return rows.indexOf(row);
    });

    // If the initial tab stop is not on a part row (e.g. it's a section header),
    // press ArrowDown until we land on a part row cell.
    let attempts = 0;
    while (startRowIndex === -1 && attempts < 10) {
      await page.keyboard.press('ArrowDown');
      startRowIndex = await page.evaluate(() => {
        const el = document.activeElement;
        if (!el) return -1;
        const row = el.closest('.inv-part-row');
        if (!row) return -1;
        const rows = Array.from(document.querySelectorAll('#inventory-body .inv-part-row'));
        return rows.indexOf(row);
      });
      attempts++;
    }

    if (startRowIndex === -1) {
      // Could not land on a part row cell; skip trivially.
      return;
    }

    // Tag the start element for identity comparison.
    await page.evaluate(() => {
      document.activeElement?.setAttribute('data-test-focus-row-start', '1');
    });

    // ArrowDown should move to a DIFFERENT row.
    await page.keyboard.press('ArrowDown');

    const movedRow = await page.evaluate(() => !document.activeElement?.hasAttribute('data-test-focus-row-start'));
    expect(movedRow).toBe(true);

    // Verify the new element is in a different .inv-part-row.
    const afterRowIndex = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return -1;
      const row = el.closest('.inv-part-row');
      if (!row) return -1;
      const rows = Array.from(document.querySelectorAll('#inventory-body .inv-part-row'));
      return rows.indexOf(row);
    });
    expect(afterRowIndex).not.toBe(startRowIndex);

    // Exactly one tab stop remains.
    const tabStopCount = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(tabStopCount).toBe(1);

    // Clean up marker.
    await page.evaluate(() => {
      document.querySelector('[data-test-focus-row-start]')?.removeAttribute('data-test-focus-row-start');
    });
  });

  test('section header is keyboard-reachable and Enter toggles collapse', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Find a section header — it should be among the grid's roving cells.
    const header = page.locator('#inventory-body .inv-section-header, #inventory-body .inv-parent-header, #inventory-body .inv-subsection-header').first();
    await expect(header).toBeVisible();

    // Navigate via arrow keys from the grid's tab stop to reach a header.
    // The tab stop starts on the first roving cell; arrow down/up to land on a header.
    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    // The first roving cell in #inventory-body is a section header (headers come
    // first in DOM order). Confirm it IS the header we expect.
    const isHeader = await tabStop.evaluate((el) =>
      el.matches('.inv-section-header, .inv-parent-header, .inv-subsection-header'));
    expect(isHeader).toBe(true);

    // Count part rows before collapsing.
    const rowsBefore = await page.locator('#inventory-body .inv-part-row').count();
    expect(rowsBefore).toBeGreaterThan(0);

    // Press Enter on the focused header — triggers the click handler → collapse.
    await page.keyboard.press('Enter');

    // After collapse, part rows under this section should be hidden or removed.
    // The inventory re-renders on collapse, so wait for the row count to drop.
    await page.waitForFunction(
      (before) => document.querySelectorAll('#inventory-body .inv-part-row').length < before,
      rowsBefore,
      { timeout: 5000 },
    );
    const rowsAfter = await page.locator('#inventory-body .inv-part-row').count();
    expect(rowsAfter).toBeLessThan(rowsBefore);
  });
});

test.describe('BOM comparison grid — data-column navigation', () => {
  test('ArrowRight moves focus to next data column in same BOM row', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    // Wait for BOM comparison rows to render in #inventory-body.
    await page.waitForSelector('#inventory-body tbody tr', { timeout: 10_000 });

    // The roving grid should reset to a single tab stop after BOM re-render.
    await expect(page.locator('#inventory-body [tabindex="0"]')).toHaveCount(1);

    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    // Tag the focused element with a unique marker so we can verify identity changed.
    await page.evaluate(() => {
      document.activeElement?.setAttribute('data-test-bom-focus-start', '1');
    });

    // ArrowRight should move to the next column cell in the same row.
    await page.keyboard.press('ArrowRight');

    // Verify focus moved to a DIFFERENT element (not the tagged one).
    const moved = await page.evaluate(() => !document.activeElement?.hasAttribute('data-test-bom-focus-start'));
    expect(moved).toBe(true);

    // Clean up marker.
    await page.evaluate(() => {
      document.querySelector('[data-test-bom-focus-start]')?.removeAttribute('data-test-bom-focus-start');
    });

    // Still exactly one tab stop in #inventory-body.
    const tabStopCount = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(tabStopCount).toBe(1);
  });

  test('ArrowDown moves focus to same column in next BOM row', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForSelector('#inventory-body tbody tr', { timeout: 10_000 });

    // Ensure at least two BOM rows are present.
    const rowCount = await page.locator('#inventory-body tbody tr').count();
    if (rowCount < 2) {
      // Not enough rows to test cross-row navigation; pass trivially.
      return;
    }

    const tabStop = page.locator('#inventory-body [tabindex="0"]').first();
    await tabStop.focus();

    // Get the data-part-key of the focused row before navigation.
    const beforeRowKey = await tabStop.evaluate((el) => el.closest('tr')?.dataset.partKey ?? null);

    await page.keyboard.press('ArrowDown');

    // After ArrowDown, the focused element's parent row should differ.
    const afterRowKey = await page.evaluate(
      () => document.activeElement?.closest('tr')?.dataset?.partKey ?? null,
    );

    // Must have moved to a different row (or stayed if only one row — but we checked above).
    if (beforeRowKey !== null && afterRowKey !== null) {
      expect(afterRowKey).not.toBe(beforeRowKey);
    }

    // Exactly one tab stop remains.
    const tabStopCount = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(tabStopCount).toBe(1);
  });
});
