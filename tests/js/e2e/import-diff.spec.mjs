// @ts-check
/**
 * import-diff.spec.mjs — E2E tests for the reviewable import diff modal.
 *
 * Verifies:
 * - Loading a CSV, clicking Import → review modal appears with correct
 *   insert/update counts and per-row badges.
 * - Confirm imports only included rows via api("import_purchases", ...).
 * - Back button returns to staging without committing.
 * - Cancel button dismisses without committing.
 *
 * Realistic interactions only: no dispatchEvent / force:true.
 */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// inventory.json has C429942 (qty 30) and C496552 (qty 100).
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// Fixture CSV: C429942 (update — already in inventory) + C999000 (insert).
// Headers match the LCSC column-detection fixture so auto-mapping fires correctly.
const PO_DIFF_CSV = path.join(__dirname, 'fixtures', 'po-diff-test.csv');

/**
 * Set up page with import-call tracking built in to the initial mock.
 * The tracker is on window.__importPurchasesArgs before any JS runs.
 */
function addMockWithTracking(page) {
  return addMockSetup(page, MOCK_INVENTORY, {
    _trackImport: true,
  });
}

test.describe('Import diff review modal', () => {
  // ── Happy path: review modal opens with correct counts ──────────────────────

  test('Import click shows review modal with correct insert/update counts', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await loadPurchaseOrder(page, PO_DIFF_CSV);

    // Wait for mapping to settle then click Import
    const importBtn = page.locator('#do-import-btn');
    await expect(importBtn).toBeVisible();
    await importBtn.click();

    // Review modal should appear
    const overlay = page.locator('#import-diff-overlay');
    await expect(overlay).toBeVisible();

    // Header title
    await expect(page.locator('#import-diff-title')).toHaveText('Review import');

    // Summary line must mention "to insert" and "to update"
    const summary = page.locator('.import-diff-summary');
    await expect(summary).toContainText('to insert');
    await expect(summary).toContainText('to update');

    // Table should show 2 rows (one update + one insert)
    const rows = page.locator('#import-diff-overlay .data-grid tbody tr[data-row-key]');
    await expect(rows).toHaveCount(2);

    // There should be one "Update" badge and one "Insert" badge
    await expect(page.locator('.import-diff-badge--update')).toHaveCount(1);
    await expect(page.locator('.import-diff-badge--insert')).toHaveCount(1);
  });

  // ── Confirm commits the rows ─────────────────────────────────────────────────

  test('Confirm button commits included rows and closes modal', async ({ page }) => {
    // Inject tracking before page loads so it's available from the start
    await page.addInitScript(() => {
      window.__importPurchasesArgs = [];
    });
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Patch import_purchases to record args
    await page.evaluate(() => {
      const orig = window.pywebview.api.import_purchases;
      window.pywebview.api.import_purchases = async (...args) => {
        window.__importPurchasesArgs.push(args);
        return orig(...args);
      };
    });

    await loadPurchaseOrder(page, PO_DIFF_CSV);
    const importBtn = page.locator('#do-import-btn');
    await importBtn.click();

    // Wait for modal
    await expect(page.locator('#import-diff-overlay')).toBeVisible();

    // Both rows should be checked by default; confirm should be enabled
    const confirmBtn = page.locator('#import-diff-confirm-btn');
    await expect(confirmBtn).toBeVisible();
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();

    // Modal should close
    await expect(page.locator('#import-diff-overlay')).not.toBeAttached();

    // import_purchases was called
    const calls = await page.evaluate(() => window.__importPurchasesArgs);
    expect(calls.length).toBeGreaterThan(0);
    // Committed 2 rows
    const committed = JSON.parse(calls[0][0]);
    expect(committed).toHaveLength(2);
  });

  // ── Back does NOT commit ─────────────────────────────────────────────────────

  test('Back button closes modal and returns to staging without committing', async ({ page }) => {
    await page.addInitScript(() => { window.__importPurchasesArgs = []; });
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.evaluate(() => {
      const orig = window.pywebview.api.import_purchases;
      window.pywebview.api.import_purchases = async (...args) => {
        window.__importPurchasesArgs.push(args);
        return orig(...args);
      };
    });

    await loadPurchaseOrder(page, PO_DIFF_CSV);
    await page.locator('#do-import-btn').click();

    // Wait for modal
    await expect(page.locator('#import-diff-overlay')).toBeVisible();

    // Click Back
    const backBtn = page.locator('#import-diff-overlay .btn-md').filter({ hasText: 'Back' });
    await backBtn.click();

    // Modal should close
    await expect(page.locator('#import-diff-overlay')).not.toBeAttached();

    // Staging table should still be visible (user is back at staging)
    await expect(page.locator('#import-mapper')).not.toHaveClass(/hidden/);

    // import_purchases must NOT have been called
    const calls = await page.evaluate(() => window.__importPurchasesArgs);
    expect(calls.length).toBe(0);
  });

  // ── Cancel does NOT commit ───────────────────────────────────────────────────

  test('Cancel button closes modal without committing', async ({ page }) => {
    await page.addInitScript(() => { window.__importPurchasesArgs = []; });
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.evaluate(() => {
      const orig = window.pywebview.api.import_purchases;
      window.pywebview.api.import_purchases = async (...args) => {
        window.__importPurchasesArgs.push(args);
        return orig(...args);
      };
    });

    await loadPurchaseOrder(page, PO_DIFF_CSV);
    await page.locator('#do-import-btn').click();
    await expect(page.locator('#import-diff-overlay')).toBeVisible();

    // Click Cancel
    const cancelBtn = page.locator('#import-diff-overlay .btn-md').filter({ hasText: 'Cancel' });
    await cancelBtn.click();

    await expect(page.locator('#import-diff-overlay')).not.toBeAttached();

    const calls = await page.evaluate(() => window.__importPurchasesArgs);
    expect(calls.length).toBe(0);
  });

  // ── Escape key = Back ────────────────────────────────────────────────────────

  test('Escape key closes modal without committing (same as Back)', async ({ page }) => {
    await page.addInitScript(() => { window.__importPurchasesArgs = []; });
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.evaluate(() => {
      const orig = window.pywebview.api.import_purchases;
      window.pywebview.api.import_purchases = async (...args) => {
        window.__importPurchasesArgs.push(args);
        return orig(...args);
      };
    });

    await loadPurchaseOrder(page, PO_DIFF_CSV);
    await page.locator('#do-import-btn').click();
    await expect(page.locator('#import-diff-overlay')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('#import-diff-overlay')).not.toBeAttached();

    const calls = await page.evaluate(() => window.__importPurchasesArgs);
    expect(calls.length).toBe(0);
  });

  // ── Deselect row, confirm skips it ──────────────────────────────────────────

  test('Unchecking a row excludes it from the committed batch', async ({ page }) => {
    await page.addInitScript(() => { window.__importPurchasesArgs = []; });
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.evaluate(() => {
      const orig = window.pywebview.api.import_purchases;
      window.pywebview.api.import_purchases = async (...args) => {
        window.__importPurchasesArgs.push(args);
        return orig(...args);
      };
    });

    await loadPurchaseOrder(page, PO_DIFF_CSV);
    await page.locator('#do-import-btn').click();
    await expect(page.locator('#import-diff-overlay')).toBeVisible();

    // Uncheck the first checkbox
    const firstCb = page.locator('#import-diff-overlay .import-diff-cb').first();
    await expect(firstCb).toBeChecked();
    await firstCb.uncheck();
    await expect(firstCb).not.toBeChecked();

    // Confirm (button should still be enabled — second row is still checked)
    await expect(page.locator('#import-diff-confirm-btn')).toBeEnabled();
    await page.locator('#import-diff-confirm-btn').click();
    await expect(page.locator('#import-diff-overlay')).not.toBeAttached();

    // import_purchases called with only 1 row (not 2)
    const calls = await page.evaluate(() => window.__importPurchasesArgs);
    expect(calls.length).toBe(1);
    const committed = JSON.parse(calls[0][0]);
    expect(committed).toHaveLength(1);
  });
});
