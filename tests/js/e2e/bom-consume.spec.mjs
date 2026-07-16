// @ts-check
//
// BOM consume/undo E2E on /v1 route mocks (Task 6, phase-1b panel wave 3).
// Mirrors tests/js/e2e/live/bom-consume.spec.mjs's flow but against the
// mocked HTTP transport (installRouteMocks) instead of the real backend, so
// it belongs to the functional/quality gate suites rather than the opt-in
// 'live' project. consume_bom / remove_last_adjustments are HTTP-routed here;
// the mock echoes the inventory back unchanged (see route-mocks.mjs) rather
// than computing real decrements — quantity-decrement correctness is covered
// by tests/python/test_inventory_api_adjustments.py and the live suite.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks, assertHttpExercised } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);
const BOM_CSV = path.join(__dirname, 'fixtures', 'bom.csv');
const BOM_CSV_TEXT = fs.readFileSync(BOM_CSV, 'utf8');

/** Arm (first click) then execute (second click) the consume confirmation. */
async function consumeAndExecute(page) {
  await page.locator('#bom-consume-btn').click();
  await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);
  const confirmBtn = page.locator('#consume-confirm');
  await confirmBtn.click(); // arm
  await expect(confirmBtn).toHaveText('Are you sure?');
  await confirmBtn.click(); // execute
  await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);
}

test.describe('BOM consume/undo — HTTP route mocks', () => {
  let routeState;

  test.beforeEach(async ({ page }) => {
    routeState = await installRouteMocks(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('consume button enabled once BOM has matched parts', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await expect(page.locator('#bom-consume-btn')).not.toBeDisabled({ timeout: 15_000 });
  });

  test('consume executes over HTTP: fresh inventory applied, modal closes, undo enabled', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await consumeAndExecute(page);

    // consume_bom was routed over HTTP with the un-stringified matches array
    // (LANDMINE fix) — the mutation's real envelope (`{ok, detail, inventory}`)
    // round-tripped and onInventoryUpdated() ran, enabling global undo.
    // Poll: the intercepted round trip is async and races an immediate read
    // on slow runners (win11 CI VM).
    await expect.poll(
      async () => (await page.evaluate(() => window.__apiCalls.consume_bom || [])).length,
      { timeout: 15_000 },
    ).toBe(1);
    const calls = await page.evaluate(() => window.__apiCalls.consume_bom);
    const [matches, boardQty, bomName] = calls[0];
    expect(Array.isArray(matches)).toBe(true);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches[0]).toHaveProperty('part_key');
    expect(matches[0]).toHaveProperty('bom_qty');
    expect(boardQty).toBe(1);
    expect(bomName).toBe('bom.csv');

    await expect(page.locator('#global-undo')).not.toBeDisabled({ timeout: 15_000 });
    await assertHttpExercised(routeState);
  });

  test('cancel leaves consume_bom uncalled', async ({ page }) => {
    await loadBomViaFileInput(page, BOM_CSV);
    await page.locator('#bom-consume-btn').click();
    await expect(page.locator('#consume-modal')).not.toHaveClass(/hidden/);
    await page.locator('#consume-cancel').click();
    await expect(page.locator('#consume-modal')).toHaveClass(/hidden/);

    const calls = await page.evaluate(() => window.__apiCalls.consume_bom || []);
    expect(calls).toHaveLength(0);
  });
});

// Separate describe block (no shared beforeEach): this test needs to install
// an `open_file_dialog` shim override BEFORE the FIRST navigation, so it
// drives its own setup start-to-finish instead of reusing the block above.
test.describe('BOM interleaved shim + HTTP transport flow', () => {
  test('open via dialog (shim/bridge) -> consume (HTTP) -> undo (HTTP)', async ({ page }) => {
    const routeState = await installRouteMocks(page, MOCK_INVENTORY);

    // open_file_dialog is a shim method (not in API_MAP — see route-mocks.mjs's
    // header comment) and stays on window.pywebview.api even once HTTP wins
    // the probe. Override its canned `null` (addMockSetup's default) with
    // real BOM content, mirroring label-export.spec.mjs's addSaveFileDialogRecorder
    // patch pattern: patch immediately if the bridge stub already exists,
    // else poll briefly (addInitScript ordering can vary run-to-run).
    await page.addInitScript((csv) => {
      const patch = () => {
        if (!window.pywebview || !window.pywebview.api) return false;
        window.pywebview.api.open_file_dialog = async () => ({
          content: csv, name: 'bom.csv', path: '/fake/bom.csv', directory: '/fake',
        });
        return true;
      };
      if (!patch()) {
        const t = setInterval(() => { if (patch()) clearInterval(t); }, 5);
        setTimeout(() => clearInterval(t), 2000);
      }
    }, BOM_CSV_TEXT);

    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Open BOM by clicking empty drop-zone space (not the <input>) — this is
    // the real browseBomFile() -> api('open_file_dialog', ...) code path,
    // served by the bridge shim above, NOT by the /v1 route mocks.
    await page.locator('#bom-drop-zone').click();
    await page.waitForSelector('#bom-tbody tr', { timeout: 10_000 });
    await expect(page.locator('#bom-consume-btn')).not.toBeDisabled({ timeout: 15_000 });

    // Consume -> HTTP mutation (consume_bom).
    await consumeAndExecute(page);
    await expect(page.locator('#global-undo')).not.toBeDisabled({ timeout: 15_000 });

    // Undo -> HTTP mutation (remove_last_adjustments), reusing the same
    // global undo stack the inventory panel's adjustments use.
    await page.locator('#global-undo').click();
    await expect(page.locator('#global-undo')).toBeDisabled();

    const consumeCalls = await page.evaluate(() => window.__apiCalls.consume_bom);
    const undoCalls = await page.evaluate(() => window.__apiCalls.remove_last_adjustments);
    expect(consumeCalls).toHaveLength(1);
    expect(undoCalls).toHaveLength(1);
    // Undo's `count` arg is the number of matched parts just consumed.
    const matchesConsumed = consumeCalls[0][0].length;
    expect(undoCalls[0][0]).toBe(matchesConsumed);

    await assertHttpExercised(routeState);
  });
});
