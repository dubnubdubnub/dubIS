// @ts-check
/* Collapsing the PO (import), BOM and console regions: the panel goes to zero
   width, the inventory grid takes the space, the toggle stays clickable, and the
   dragged width comes back on reopen. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks, addPersistentPrefsRouteMock } from './route-mocks.mjs';
import { setZoom, expectNoClipping, expectNoPageScrollbars } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

async function boot(page, { persistPrefs = false, prefs = undefined } = {}) {
  await installRouteMocks(page, MOCK_INVENTORY, prefs ? { preferences: prefs } : {});
  if (persistPrefs) await addPersistentPrefsRouteMock(page);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
}

const widthOf = (page, id) => page.evaluate(
  (i) => document.getElementById(i).getBoundingClientRect().width, id);

test.describe('Panel collapse — toggling', () => {
  test('both toggle pills are mounted on the dividers', async ({ page }) => {
    await boot(page);
    await expect(page.locator('#panel-toggle-import')).toBeVisible();
    await expect(page.locator('#panel-toggle-bom')).toBeVisible();
    await expect(page.locator('#console-toggle')).toBeVisible();

    // Each pill must sit on a resize handle — that is the "T" junction between
    // panels the control is specified to live at.
    const onHandle = await page.evaluate(() => ({
      import: !!document.getElementById('panel-toggle-import').closest('.resize-handle-h'),
      bom: !!document.getElementById('panel-toggle-bom').closest('.resize-handle-h'),
    }));
    expect(onHandle.import).toBe(true);
    expect(onHandle.bom).toBe(true);
  });

  test('collapsing the import panel takes it to zero width and grows the grid',
    async ({ page }) => {
      await boot(page);
      const gridBefore = await widthOf(page, 'panel-inventory');
      const importBefore = await widthOf(page, 'panel-import');
      expect(importBefore).toBeGreaterThan(100);

      await page.click('#panel-toggle-import');

      expect(await widthOf(page, 'panel-import'),
        'collapsed panel should be zero width despite its px min-width').toBe(0);
      expect(await widthOf(page, 'panel-inventory'),
        'the inventory grid should absorb the freed space').toBeGreaterThan(gridBefore);
    });

  test('collapsing the BOM panel takes it to zero width and grows the grid',
    async ({ page }) => {
      await boot(page);
      const gridBefore = await widthOf(page, 'panel-inventory');
      await page.click('#panel-toggle-bom');

      expect(await widthOf(page, 'panel-bom')).toBe(0);
      expect(await widthOf(page, 'panel-inventory')).toBeGreaterThan(gridBefore);
    });

  test('the pill stays clickable at zero panel width and reopens the panel',
    async ({ page }) => {
      await boot(page);
      const before = await widthOf(page, 'panel-import');

      await page.click('#panel-toggle-import');
      expect(await widthOf(page, 'panel-import')).toBe(0);

      // The whole point of the inflated hit box: the only way back must remain
      // hittable once the panel behind it is gone. A real click, no force.
      await expect(page.locator('#panel-toggle-import')).toBeVisible();
      await page.click('#panel-toggle-import');

      expect(await widthOf(page, 'panel-import')).toBeCloseTo(before, 0);
    });

  test('collapsing both side panels leaves the grid the full width', async ({ page }) => {
    await boot(page);
    await page.click('#panel-toggle-import');
    await page.click('#panel-toggle-bom');

    const geom = await page.evaluate(() => ({
      grid: document.getElementById('panel-inventory').getBoundingClientRect().width,
      panels: document.querySelector('.panels').getBoundingClientRect().width,
    }));
    // The two 5px handles remain, so allow a small margin.
    expect(geom.grid).toBeGreaterThan(geom.panels - 20);
    await expectNoPageScrollbars(page, 'both panels collapsed');
  });

  test('aria state reflects the collapse', async ({ page }) => {
    await boot(page);
    const pill = page.locator('#panel-toggle-import');
    await expect(pill).toHaveAttribute('aria-expanded', 'true');
    await expect(pill).toHaveAttribute('aria-controls', 'panel-import');

    await page.click('#panel-toggle-import');
    await expect(pill).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#panel-import')).toHaveAttribute('aria-hidden', 'true');
    await expect(pill).toHaveAttribute('aria-label', 'Show Purchase Import');

    await page.click('#panel-toggle-import');
    await expect(pill).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#panel-import')).not.toHaveAttribute('aria-hidden', 'true');
    await expect(pill).toHaveAttribute('aria-label', 'Hide Purchase Import');
  });

  test('the pill is keyboard reachable and activates on Enter and Space',
    async ({ page }) => {
      await boot(page);
      const pill = page.locator('#panel-toggle-bom');
      await pill.focus();
      await expect(pill).toBeFocused();

      await page.keyboard.press('Enter');
      expect(await widthOf(page, 'panel-bom')).toBe(0);

      await page.keyboard.press('Space');
      expect(await widthOf(page, 'panel-bom')).toBeGreaterThan(100);
    });

  test('clicking the pill does not start a divider drag', async ({ page }) => {
    await boot(page);
    const importBefore = await widthOf(page, 'panel-import');
    // Collapse and reopen; a stray drag would leave a different width behind.
    await page.click('#panel-toggle-import');
    await page.click('#panel-toggle-import');
    expect(await widthOf(page, 'panel-import')).toBeCloseTo(importBefore, 0);
    await expect(page.locator('.resize-handle-h').first()).not.toHaveClass(/active/);
  });
});

test.describe('Panel collapse — width restoration', () => {
  test('reopening restores a dragged width, not the stylesheet default',
    async ({ page }) => {
      await boot(page);
      const handle = page.locator('.resize-handle-h').first();
      const box = await handle.boundingBox();
      expect(box).not.toBeNull();
      if (!box) return;

      // Drag the divider right to widen the import panel well past its default.
      await page.mouse.move(box.x + box.width / 2, box.y + 60);
      await page.mouse.down();
      await page.mouse.move(box.x + 160, box.y + 60, { steps: 10 });
      await page.mouse.up();

      const dragged = await widthOf(page, 'panel-import');
      expect(dragged, 'the drag should have widened the panel').toBeGreaterThan(300);

      await page.click('#panel-toggle-import');
      expect(await widthOf(page, 'panel-import')).toBe(0);
      await page.click('#panel-toggle-import');

      expect(await widthOf(page, 'panel-import'),
        'a collapse round-trip must not cost the user their dragged width')
        .toBeCloseTo(dragged, 0);
    });
});

test.describe('Panel collapse — console section', () => {
  test('the console toggle hides the log body without collapsing the panel',
    async ({ page }) => {
      await boot(page);
      await expect(page.locator('#console-entries')).toBeVisible();
      const importBefore = await widthOf(page, 'panel-import');

      await page.click('#console-toggle');

      await expect(page.locator('#console-entries')).toBeHidden();
      expect(await widthOf(page, 'panel-import'),
        'collapsing the log must not collapse its containing panel')
        .toBeCloseTo(importBefore, 0);
      await expect(page.locator('#console-toggle')).toHaveAttribute('aria-expanded', 'false');
    });

  test('collapsing the console hides the orphaned resize handle above it',
    async ({ page }) => {
      await boot(page);
      const handleVisible = () => page.evaluate(() => {
        const log = document.getElementById('console-log');
        const prev = log.previousElementSibling;
        if (!prev || !prev.classList.contains('resize-handle-v')) return null;
        return getComputedStyle(prev).display !== 'none';
      });
      expect(await handleVisible()).toBe(true);

      await page.click('#console-toggle');
      expect(await handleVisible(),
        'a hidden console must not leave a drag handle that resizes nothing')
        .toBe(false);

      await page.click('#console-toggle');
      expect(await handleVisible()).toBe(true);
    });
});

test.describe('Panel collapse — persistence', () => {
  test('collapse state survives a reload', async ({ page }) => {
    await boot(page, { persistPrefs: true });
    await page.click('#panel-toggle-bom');
    expect(await widthOf(page, 'panel-bom')).toBe(0);

    await page.waitForFunction(() => {
      const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
      return !!raw && JSON.parse(raw).panels_collapsed?.bom === true;
    }, null, { timeout: 5000 });

    await page.reload();
    await waitForInventoryRows(page);

    expect(await widthOf(page, 'panel-bom'), 'the BOM panel should still be collapsed').toBe(0);
    await expect(page.locator('#panel-toggle-bom')).toHaveAttribute('aria-expanded', 'false');
  });

  test('a malformed stored state opens everything rather than breaking',
    async ({ page }) => {
      await boot(page, { prefs: { thresholds: {}, panels_collapsed: 'nonsense' } });
      expect(await widthOf(page, 'panel-import')).toBeGreaterThan(100);
      expect(await widthOf(page, 'panel-bom')).toBeGreaterThan(100);
      await expect(page.locator('#console-entries')).toBeVisible();
    });

  test('an unknown region key in stored state is ignored', async ({ page }) => {
    await boot(page, {
      prefs: { thresholds: {}, panels_collapsed: { bogus: true, bom: true } },
    });
    expect(await widthOf(page, 'panel-bom')).toBe(0);
    expect(await widthOf(page, 'panel-import')).toBeGreaterThan(100);
  });
});

test.describe('Panel collapse — cross-feature with zoom', () => {
  for (const zoom of [50, 200]) {
    test(`both panels collapsed at ${zoom}% still lays out cleanly`, async ({ page }) => {
      await boot(page);
      await setZoom(page, zoom);
      await page.click('#panel-toggle-import');
      await page.click('#panel-toggle-bom');

      const label = `both collapsed @ ${zoom}%`;
      await expectNoPageScrollbars(page, label);
      await expectNoClipping(page, '#panel-inventory .panel-header', `${label} — grid header`);

      // The pills must still be reachable to get the panels back.
      await expect(page.locator('#panel-toggle-import')).toBeVisible();
      await page.click('#panel-toggle-import');
      expect(await widthOf(page, 'panel-import')).toBeGreaterThan(0);
    });
  }
});
