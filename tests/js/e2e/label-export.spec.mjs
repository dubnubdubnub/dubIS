// @ts-check
/*
 * End-to-end coverage for the Epson "Labels" export feature.
 *
 * Flow exercised (all via realistic interactions — real clicks + real typing,
 * NO force/dispatchEvent hacks, per project E2E policy):
 *   1. Enter label mode → toolbar appears, row action buttons become checkboxes.
 *   2. Tick a row checkbox → selected-count increments.
 *   3. Select a PO from the picker → count grows by the PO's part count.
 *   4. Choose 12 mm tape.
 *   5. Create Labels → preview modal opens, 3 editable cells per row, grouped
 *      by distributor, each row shows an mm estimate.
 *   6. Edit a cell (real typing) → the row's mm estimate updates, and a long
 *      value pushes the row across the over-budget threshold → red badge.
 *   7. Export (save_file_dialog stubbed to a fake path) → the captured CSV
 *      reflects the EDITED value; success toast shows; modal closes.
 *   8. Done → label mode exits, normal action buttons return.
 *
 * E2E HARNESS NOTES
 * -----------------
 * The functional Playwright project serves the static app (no Python backend)
 * and stubs `window.pywebview.api` via `addMockSetup` (helpers.mjs). The base
 * stub returns `null` for `save_file_dialog` (which the app treats as "user
 * cancelled") and `[]` for `list_purchase_orders`. This spec layers a SECOND
 * `addInitScript` on top that:
 *   - overrides `save_file_dialog` to record each (csv, name) call on
 *     `window.__savedFiles` and return a fake `{ path }` — so no native dialog
 *     can open and hang the run, and the test can assert on the CSV bytes.
 *   - overrides `list_purchase_orders` / `get_po_with_items` to supply one PO.
 * Inventory items are given a matching `po_history` so `selectPo` resolves them.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const PO_ID = 'PO-2026-001';

// Tag the first three inventory items with a po_history so the PO picker can
// resolve them; everything else stays untouched.
const MOCK_INVENTORY = BASE_INVENTORY.map((item, i) =>
  i < 3 ? { ...item, po_history: [PO_ID] } : item,
);

/** Layer the save_file_dialog recorder + PO data over the base api stub. */
function addLabelExportStubs(page) {
  return page.addInitScript(({ poId, inv }) => {
    const poItems = inv.filter((it) => (it.po_history || []).includes(poId));
    window.__savedFiles = [];
    // Wait until the base helpers stub has installed window.pywebview.api,
    // then patch the two methods we need without clobbering the rest.
    const patch = () => {
      if (!window.pywebview || !window.pywebview.api) return false;
      const api = window.pywebview.api;
      api.save_file_dialog = async (csv, name) => {
        const fakePath = '/fake/exports/' + name;
        window.__savedFiles.push({ name, csv, path: fakePath });
        return { path: fakePath };
      };
      api.list_purchase_orders = async () => [
        { po_id: poId, purchase_date: '2026-05-01', vendor_id: 'v_lcsc' },
      ];
      api.get_po_with_items = async () => ({
        po_id: poId,
        line_items: poItems.map((it) => ({
          mpn: it.mpn,
          manufacturer: it.manufacturer,
          package: it.package,
          quantity: it.qty,
        })),
      });
      return true;
    };
    if (!patch()) {
      // addInitScript ordering should make the base stub already present, but
      // guard against ordering changes with a short polling fallback.
      const t = setInterval(() => { if (patch()) clearInterval(t); }, 5);
      setTimeout(() => clearInterval(t), 2000);
    }
  }, { poId: PO_ID, inv: MOCK_INVENTORY });
}

test.describe('Epson label export', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await addLabelExportStubs(page);
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('select parts → create labels → edit → export', async ({ page }) => {
    // ── 1. Enter label mode ──────────────────────────────────────────────
    // Before: a row shows a normal Adjust button, no checkbox.
    const firstRow = page.locator('.inv-part-row').first();
    await expect(firstRow.locator('.adj-btn')).toBeVisible();
    await expect(firstRow.locator('.label-select-checkbox')).toHaveCount(0);

    // The PO picker now lives in the Purchase Import panel and is always
    // visible (dimmed) as a PO history — its rows render on load, before
    // label mode is ever entered, and it carries no pop-out class yet.
    const picker = page.locator('#panel-import #label-po-picker');
    await expect(picker).toBeVisible();
    await expect(picker).not.toHaveClass(/is-label-active/);
    await expect(page.locator('.label-po-row').first()).toBeVisible();

    await page.locator('#label-mode-btn').click();

    // Toolbar appears, and the picker pops out (gains the active class).
    await expect(page.locator('#label-toolbar')).toBeVisible();
    await expect(picker).toHaveClass(/is-label-active/);
    // The row's right-edge action buttons are replaced by a real checkbox.
    const firstCheckbox = firstRow.locator('.label-select-checkbox');
    await expect(firstCheckbox).toBeVisible();
    await expect(firstRow.locator('.adj-btn')).toHaveCount(0);
    await expect(firstRow.locator('.link-btn')).toHaveCount(0);

    await expect(page.locator('#label-selected-count')).toHaveText('0 selected');

    // ── 2. Tick a row checkbox ───────────────────────────────────────────
    // Pick a checkbox for a row NOT in the PO (index >= 3) so the PO step
    // adds a clean, predictable delta on top.
    const standaloneCheckbox = page.locator('.inv-part-row').nth(5)
      .locator('.label-select-checkbox');
    await standaloneCheckbox.check();
    await expect(standaloneCheckbox).toBeChecked();
    await expect(page.locator('#label-selected-count')).toHaveText('1 selected');

    // ── 3. Select a PO from the picker ───────────────────────────────────
    // The PO list is already rendered (it loads on page init). Three inventory
    // items carry po_history === [PO_ID], so selecting the PO should add 3 more.
    const poRow = page.locator('.label-po-row').first();
    await expect(poRow).toBeVisible();
    await poRow.locator('.label-po-select').click();
    await expect(page.locator('#label-selected-count')).toHaveText('4 selected');

    // ── 4. Choose 12 mm tape ─────────────────────────────────────────────
    await page.locator('#label-tape-toggle input[value="12mm"]').check();

    // ── 5. Create Labels → preview modal ─────────────────────────────────
    await page.locator('#label-create-btn').click();
    const modal = page.locator('#label-export-modal');
    await expect(modal).toBeVisible();

    // Grouped by distributor (at least one group), each row with 3 editable
    // cells (12 mm = three stacked lines) plus an mm value.
    await expect(modal.locator('.label-export-group').first()).toBeVisible();
    const previewRows = modal.locator('.label-export-row');
    await expect(previewRows).toHaveCount(4); // 1 manual + 3 from the PO
    const targetRow = previewRows.first();
    await expect(targetRow.locator('.label-edit-cell')).toHaveCount(3);
    const mmCell = targetRow.locator('.label-mm');
    await expect(mmCell).toContainText('mm');
    const mmBefore = await mmCell.textContent();

    // No red over-budget badge yet for this freshly-built row (true before-state
    // so the post-edit badge assertion is a real transition, not just end-state).
    await expect(targetRow.locator('.label-badge.label-badge-red')).toHaveCount(0);

    // ── 6. Edit a cell by REAL typing → mm changes + badge appears ───────
    // Type a long string that deterministically exceeds the 12 mm budget
    // (budget 40 mm; ~80 default-width chars ≈ 40.8 mm).
    const longText = 'X'.repeat(80);
    const editCell = targetRow.locator('.label-edit-cell').first();
    await editCell.click();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Delete');
    await page.keyboard.type(longText);

    // mm estimate updated and a red over-budget badge appeared.
    await expect(mmCell).not.toHaveText(/** @type {string} */(mmBefore));
    await expect(targetRow.locator('.label-badge.label-badge-red')).toBeVisible();

    // ── 7. Export → captured CSV reflects the EDITED value ───────────────
    await page.locator('#label-export-do').click();

    // Modal closes + success toast.
    await expect(modal).toBeHidden();
    await expect(page.locator('#toast')).toHaveClass(/show/);
    await expect(page.locator('#toast')).toContainText('Exported');

    // save_file_dialog was invoked and the CSV contains the long edited value.
    const saved = await page.evaluate(() => window.__savedFiles);
    expect(saved.length).toBeGreaterThan(0);
    const allCsv = saved.map((s) => s.csv).join('\n');
    expect(allCsv).toContain(longText);
    // Filenames follow labels_<tape>_<vendor>.csv.
    expect(saved.some((s) => /^labels_12mm_/.test(s.name))).toBe(true);

    // ── 8. Done → exit label mode, action buttons return ─────────────────
    // Exporting closes the modal but stays in label mode, so the toolbar (and
    // its Done button) are still present.
    await expect(page.locator('#label-toolbar')).toBeVisible();
    await page.locator('#label-done-btn').click();

    await expect(page.locator('#label-toolbar')).toBeHidden();
    const rowAfter = page.locator('.inv-part-row').first();
    await expect(rowAfter.locator('.adj-btn')).toBeVisible();
    await expect(rowAfter.locator('.label-select-checkbox')).toHaveCount(0);
  });

  test('Select PO from the dimmed picker auto-enters label mode', async ({ page }) => {
    // Not in label mode yet: toolbar hidden, rows show normal action buttons,
    // and the picker sits dimmed in the import panel as a browsable history.
    await expect(page.locator('#label-toolbar')).toBeHidden();
    const picker = page.locator('#panel-import #label-po-picker');
    await expect(picker).toBeVisible();
    await expect(picker).not.toHaveClass(/is-label-active/);

    const firstRow = page.locator('.inv-part-row').first();
    await expect(firstRow.locator('.adj-btn')).toBeVisible();
    await expect(firstRow.locator('.label-select-checkbox')).toHaveCount(0);

    // Clicking "Select PO" straight from the dimmed history activates the mode.
    await page.locator('.label-po-row').first().locator('.label-po-select').click();

    // Label mode is now on: toolbar visible, picker popped out, the PO's 3 parts
    // selected, and row action buttons replaced by checkboxes.
    await expect(page.locator('#label-toolbar')).toBeVisible();
    await expect(picker).toHaveClass(/is-label-active/);
    await expect(page.locator('#label-selected-count')).toHaveText('3 selected');
    await expect(firstRow.locator('.label-select-checkbox')).toBeVisible();
    await expect(firstRow.locator('.adj-btn')).toHaveCount(0);
  });
});
