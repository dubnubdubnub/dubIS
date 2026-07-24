// @ts-check
/* Auto-reopen: a collapsed region whose content changes must come back, because a
   panel that silently mutates behind a collapse is a trap — the user cannot see
   the PO they just imported. One test per row of REOPEN_TRIGGERS in
   js/panel-collapse-logic.js, plus the negative case that routine info logging
   must NOT reopen anything. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaEmit, loadPurchaseOrder } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

const widthOf = (page, id) => page.evaluate(
  (i) => document.getElementById(i).getBoundingClientRect().width, id);

async function boot(page) {
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
}

/** Collapse a region through its real toggle, then assert it is closed. */
async function collapse(page, region) {
  const id = region === 'console' ? 'console-toggle' : `panel-toggle-${region}`;
  await page.click('#' + id);
  if (region === 'console') {
    await expect(page.locator('#console-entries')).toBeHidden();
  } else {
    expect(await widthOf(page, `panel-${region}`)).toBe(0);
  }
}

test.describe('Auto-reopen — the import panel', () => {
  test('importing a purchase order reopens it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'import');

    await loadPurchaseOrder(page, PO_CSV_PATH);

    await expect.poll(() => widthOf(page, 'panel-import'),
      { message: 'a PO import must reopen the panel that shows it' })
      .toBeGreaterThan(100);
  });

  test('entering Print Labels mode reopens it, since it needs the PO picker',
    async ({ page }) => {
      await boot(page);
      await collapse(page, 'import');

      await page.click('#label-mode-btn');

      await expect.poll(() => widthOf(page, 'panel-import'),
        { message: 'label mode needs the PO picker, so the panel must reopen' })
        .toBeGreaterThan(100);
    });
});

test.describe('Auto-reopen — the BOM panel', () => {
  test('loading a BOM reopens it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'bom');

    await loadBomViaEmit(page, BOM_CSV);

    await expect.poll(() => widthOf(page, 'panel-bom'),
      { message: 'loading a BOM must reopen the BOM panel' })
      .toBeGreaterThan(100);
  });

  test('clearing a BOM reopens it', async ({ page }) => {
    await boot(page);
    await loadBomViaEmit(page, BOM_CSV);
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.BOM_CLEARED));

    await expect.poll(() => widthOf(page, 'panel-bom')).toBeGreaterThan(100);
  });

  test('a link change reopens it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.LINKS_CHANGED));

    await expect.poll(() => widthOf(page, 'panel-bom'),
      { message: 'a manual link changes BOM state, so the panel must reopen' })
      .toBeGreaterThan(100);
  });

  test('a confirm change reopens it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.CONFIRMED_CHANGED));

    await expect.poll(() => widthOf(page, 'panel-bom')).toBeGreaterThan(100);
  });

  test('entering linking mode reopens it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.LINKING_MODE));

    await expect.poll(() => widthOf(page, 'panel-bom')).toBeGreaterThan(100);
  });
});

test.describe('Auto-reopen — the console', () => {
  test('a warning reopens the log and its containing panel', async ({ page }) => {
    await boot(page);
    await collapse(page, 'console');
    await collapse(page, 'import');

    await page.evaluate(() => window.AppLog.warn('probe warning'));

    // The log lives inside the import panel, so reopening it alone would be
    // invisible — both must come back.
    await expect(page.locator('#console-entries')).toBeVisible();
    await expect.poll(() => widthOf(page, 'panel-import'),
      { message: 'the console lives inside the import panel, which must reopen too' })
      .toBeGreaterThan(100);
    await expect(page.locator('#console-entries')).toContainText('probe warning');
  });

  test('an error reopens the log', async ({ page }) => {
    await boot(page);
    await collapse(page, 'console');

    await page.evaluate(() => window.AppLog.error('probe error'));

    await expect(page.locator('#console-entries')).toBeVisible();
    await expect(page.locator('#console-entries')).toContainText('probe error');
  });

  test('routine info logging does NOT reopen the log, but flags it', async ({ page }) => {
    await boot(page);
    await collapse(page, 'console');

    await page.evaluate(() => {
      for (let i = 0; i < 5; i++) window.AppLog.info('routine line ' + i);
    });

    // Reopening on every info line would make collapsing the console useless.
    await expect(page.locator('#console-entries')).toBeHidden();
    await expect(page.locator('#console-toggle')).toHaveClass(/has-activity/);
    await expect(page.locator('#console-toggle')).toHaveAttribute('aria-expanded', 'false');
  });

  test('the activity flag clears when the log is reopened', async ({ page }) => {
    await boot(page);
    await collapse(page, 'console');
    await page.evaluate(() => window.AppLog.info('routine'));
    await expect(page.locator('#console-toggle')).toHaveClass(/has-activity/);

    await page.click('#console-toggle');

    await expect(page.locator('#console-entries')).toBeVisible();
    await expect(page.locator('#console-toggle')).not.toHaveClass(/has-activity/);
  });
});

test.describe('Auto-reopen — hygiene', () => {
  test('an event for an already-open panel changes nothing', async ({ page }) => {
    await boot(page);
    const before = await widthOf(page, 'panel-bom');

    await page.evaluate(() => {
      for (let i = 0; i < 20; i++) window.EventBus.emit(window.Events.LINKS_CHANGED);
    });

    // Idempotent: no width churn, and no preference write storm.
    expect(await widthOf(page, 'panel-bom')).toBeCloseTo(before, 0);
    await expect(page.locator('#panel-toggle-bom')).toHaveAttribute('aria-expanded', 'true');
  });

  test('reopening one region leaves the other collapsed', async ({ page }) => {
    await boot(page);
    await collapse(page, 'import');
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.LINKS_CHANGED));

    await expect.poll(() => widthOf(page, 'panel-bom')).toBeGreaterThan(100);
    expect(await widthOf(page, 'panel-import'),
      'a BOM event must not reopen the unrelated import panel').toBe(0);
  });

  test('a reopen is announced in the log', async ({ page }) => {
    await boot(page);
    await collapse(page, 'bom');

    await page.evaluate(() => window.EventBus.emit(window.Events.LINKS_CHANGED));

    await expect(page.locator('#console-entries')).toContainText('Reopened bom');
  });
});
