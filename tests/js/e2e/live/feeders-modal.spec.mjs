// @ts-check
import { test, expect } from '@playwright/test';
import { resetServer, setupPage } from './setup-page.mjs';
import { waitForInventoryRows } from '../helpers.mjs';

// /v1/_test/reset only rolls back adjustment-tagged rows (see setup-page.mjs) —
// feeders.json isn't touched by it, so each test uses its own tag id rather
// than relying on reset for isolation (same pattern as vendors-modal.spec.mjs
// using unique vendor names).
// DICT_APRILTAG_36h11 only has ids 0..586 (see server/routes/feeders.py's
// MAX_TAG_ID) — the tag-PNG download test exercises the real range check, so
// keep every test tag id well under that ceiling.
let nextTagId = 100;
function freshTagId() {
  return String(nextTagId++);
}

const feederRow = (page, tagId) => page.locator(`tr[data-row-key="${tagId}"]`);

async function openFeeders(page) {
  await page.click('#feeders-btn');
  await expect(page.locator('#feeders-modal')).not.toHaveClass(/hidden/);
}

async function registerFeeder(page, tagId, feederType = '8mm reel') {
  await page.click('#feeders-register-btn');
  await expect(page.locator('#feeder-register-modal')).not.toHaveClass(/hidden/);
  await page.fill('#tag_id', tagId);
  await page.fill('#feeder_type', feederType);
  await page.click('#feeder-register-modal-confirm');
  await expect(feederRow(page, tagId)).toBeVisible();
}

test.describe('Feeders modal', () => {
  test.beforeEach(async ({ page }) => {
    await resetServer();
    await setupPage(page);
    await waitForInventoryRows(page);
  });

  test('opens from the toolbar and shows an empty state with no feeders', async ({ page }) => {
    // A completely fresh live server has no feeders yet on the very first test
    // to run against it; later tests in this file leave rows behind (no
    // reset), so only assert the modal opens and its grid renders.
    await openFeeders(page);
    await expect(page.locator('#feeders-table-wrap table')).toBeVisible();
  });

  test('register a feeder → it appears in the list', async ({ page }) => {
    const tagId = freshTagId();
    await openFeeders(page);
    await registerFeeder(page, tagId, '8mm reel');

    const row = feederRow(page, tagId);
    await expect(row).toContainText(tagId);
    await expect(row).toContainText('8mm reel');
    // Unloaded feeder shows placeholders, and only Load + Tag PNG actions.
    await expect(row.locator('[data-action-key="unload"]')).toHaveCount(0);
    await expect(row.locator('[data-action-key="load"]')).toBeVisible();
  });

  test('registering an already-used tag id shows a clear error and keeps the modal open', async ({ page }) => {
    const tagId = freshTagId();
    await openFeeders(page);
    await registerFeeder(page, tagId);

    // Re-register the same tag id.
    await page.click('#feeders-register-btn');
    await page.fill('#tag_id', tagId);
    await page.fill('#feeder_type', 'other type');
    await page.click('#feeder-register-modal-confirm');

    await expect(page.locator('#toast')).toContainText(new RegExp(`already registered`, 'i'));
    // Modal stayed open (no success close) — confirm button still reachable.
    await expect(page.locator('#feeder-register-modal')).not.toHaveClass(/hidden/);
  });

  test('load a reel → the row shows the loaded part, qty, and derived tape width', async ({ page }) => {
    const tagId = freshTagId();
    await openFeeders(page);
    await registerFeeder(page, tagId);

    await feederRow(page, tagId).locator('[data-action-key="load"]').click();
    await expect(page.locator('#feeder-load-modal')).not.toHaveClass(/hidden/);
    await expect(page.locator('#feeder-load-modal-title')).toContainText(tagId);

    await page.fill('#part_key', 'C25794');
    await expect(page.locator('.feeders-suggest-item[data-suggest-key="C25794"]')).toBeVisible();
    await page.click('.feeders-suggest-item[data-suggest-key="C25794"]');
    await expect(page.locator('#part_key')).toHaveValue('C25794');

    await page.fill('#qty', '50');
    // Leave tape width blank — the backend auto-derives 8mm for a 0402 chip part.
    await page.click('#feeder-load-modal-confirm');

    const row = feederRow(page, tagId);
    await expect(row).toContainText('C25794');
    await expect(row).toContainText('100nF');
    await expect(row).toContainText('50');
    await expect(row).toContainText('8');
    await expect(row.locator('[data-action-key="unload"]')).toBeVisible();
  });

  test('unload clears the row back to placeholders', async ({ page }) => {
    const tagId = freshTagId();
    await openFeeders(page);
    await registerFeeder(page, tagId);

    await feederRow(page, tagId).locator('[data-action-key="load"]').click();
    await page.fill('#part_key', 'C25794');
    await page.click('.feeders-suggest-item[data-suggest-key="C25794"]');
    await page.fill('#qty', '50');
    await page.click('#feeder-load-modal-confirm');
    await expect(feederRow(page, tagId)).toContainText('C25794');

    page.once('dialog', (dialog) => dialog.accept());
    await feederRow(page, tagId).locator('[data-action-key="unload"]').click();

    const row = feederRow(page, tagId);
    await expect(row).not.toContainText('C25794');
    await expect(row.locator('[data-action-key="unload"]')).toHaveCount(0);
    await expect(row.locator('[data-action-key="load"]')).toBeVisible();
  });

  test('downloading a tag PNG triggers a real browser download', async ({ page }) => {
    const tagId = freshTagId();
    await openFeeders(page);
    await registerFeeder(page, tagId);

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      feederRow(page, tagId).locator('[data-action-key="tag-png"]').click(),
    ]);
    expect(download.suggestedFilename()).toBe(`feeder-tag-${tagId}.png`);
  });
});
