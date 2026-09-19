// @ts-check
/* Column resizing, end to end.

   The thing that silently breaks here is alignment: the inventory grid is not a
   <table>, so its header cells and its row cells are two independent sets of
   boxes that only agree because they read the same CSS custom property. Every
   drag assertion below therefore checks the header AND the row cell, never one
   alone.

   The second thing that silently breaks is the coordinate space. `clientX` is
   post-zoom while a written width is authored px, so a drag that mixes them
   looks perfect at 100% and is off by the zoom factor everywhere else — the
   zoom test at the bottom pins the pointer-tracking ratio at 150%. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBom } from './helpers.mjs';
import { installRouteMocks, addPersistentPrefsRouteMock } from './route-mocks.mjs';
import { setZoom } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

/* A DigiKey part number long enough that the default 100px Part # column clips
   it — the user's actual complaint ("make the PN row bigger to see the full
   PN"). Placed first so it is in the first rendered section. */
const LONG_PN = 'RMCF0402FT10K0CT-ND-0123456789';
const MOCK_INVENTORY = [
  {
    section: 'Connectors', lcsc: '', digikey: LONG_PN, mpn: 'LONG-PN-PART-0402-XYZ',
    manufacturer: 'Stackpole', package: '0402', description: 'Long part number probe',
    qty: 42, unit_price: 0.0123, ext_price: 0.52, pololu: '',
  },
  ...BASE_INVENTORY,
];

const WIDE = { width: 1700, height: 900 };

/** Two frames: one to apply, one for dependent layout to settle. */
const settle = (page) => page.evaluate(() => new Promise(r => requestAnimationFrame(
  () => requestAnimationFrame(() => r(undefined)))));

/**
 * Boot with the inventory panel given the full window (the two side panels
 * collapsed through their own toggles).
 *
 * Why it matters for these assertions: every inventory column is
 * `flex-shrink: 1`, so in a space-starved panel the *rendered* widths are
 * proportionally smaller than the widths written to the tokens, and a drag
 * cannot track the pointer 1:1 no matter how correct the arithmetic is. The
 * tests below are about the resize mechanism, so they run in a panel with
 * slack — and assert that precondition explicitly (expectUnshrunk) instead of
 * silently measuring a shrunken grid.
 */
async function boot(page, {
  persistPrefs = false, preferences, viewport = WIDE, widen = true,
} = {}) {
  const opts = preferences ? { preferences } : {};
  await installRouteMocks(page, MOCK_INVENTORY, opts);
  // Registered after installRouteMocks so it wins over the generic /v1
  // catch-all (Playwright matches last-registered first).
  if (persistPrefs) await addPersistentPrefsRouteMock(page);
  await page.setViewportSize(viewport);
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  if (widen) {
    await page.click('#panel-toggle-import');
    await page.click('#panel-toggle-bom');
    await settle(page);
  }
}

/** Assert the grid has slack, i.e. rendered widths equal the written tokens. */
async function expectUnshrunk(page) {
  const { hdr, tok } = await page.evaluate(() => {
    const body = document.getElementById('inventory-body');
    return {
      hdr: document.querySelector('.inv-col-header .inv-col-partid').offsetWidth,
      tok: parseFloat(getComputedStyle(body).getPropertyValue('--inv-col-pn-w')),
    };
  });
  expect(hdr, 'precondition: the panel must have slack, or flex-shrink hides the '
    + 'width being written (rendered ' + hdr + ' vs token ' + tok + ')').toBe(tok);
}

/** A width token as it resolves for the grid container (authored px). */
function token(page, prop) {
  return page.evaluate((p) => {
    const body = document.getElementById('inventory-body');
    return parseFloat(getComputedStyle(body).getPropertyValue(p));
  }, prop);
}

/** offsetWidth (authored px) of the header cell and the row cell of one column. */
function pair(page, headerSel, rowSel) {
  return page.evaluate(({ h, r }) => {
    const hdr = document.querySelector('.inv-col-header ' + h);
    const cell = document.querySelector('.inv-part-row ' + r);
    return {
      hdr: hdr ? hdr.offsetWidth : -1,
      row: cell ? cell.offsetWidth : -1,
    };
  }, { h: headerSel, r: rowSel });
}

/**
 * Drag one column's divider by `dx` viewport px (the space page.mouse and
 * clientX share — deliberately NOT authored px, so the zoom test can prove the
 * conversion happens inside the app).
 */
async function dragHandle(page, tableId, colId, dx) {
  const handle = page.locator(
    `[data-col-resize-table="${tableId}"][data-col-resize="${colId}"]`).first();
  await expect(handle).toBeAttached();
  const box = await handle.boundingBox();
  if (!box) throw new Error(`no box for handle ${tableId}.${colId}`);
  const y = box.y + box.height / 2;
  const x = box.x + box.width / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  // Several steps: a single jump can be coalesced, and a real drag is many moves.
  await page.mouse.move(x + dx, y, { steps: 10 });
  await page.mouse.up();
  await page.evaluate(() => new Promise(r => requestAnimationFrame(
    () => requestAnimationFrame(() => r(undefined)))));
}

/** The persisted column_widths blob, as written through PUT /v1/preferences. */
function storedWidths(page) {
  return page.evaluate(() => {
    const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
    if (!raw) return null;
    const prefs = JSON.parse(raw);
    return prefs.column_widths === undefined ? null : prefs.column_widths;
  });
}

test.describe('Column resizing — the inventory grid (flex rows)', () => {
  test('a divider with a resize cursor sits at every resizable column edge',
    async ({ page }) => {
      await boot(page);
      for (const col of ['partid', 'mpn', 'vendor', 'unit', 'value', 'qty']) {
        const handle = page.locator(
          `.inv-col-header [data-col-resize-table="inv"][data-col-resize="${col}"]`);
        await expect(handle).toHaveCount(1);
        expect(await handle.evaluate(el => getComputedStyle(el).cursor),
          `${col} handle should advertise a resize cursor`).toBe('col-resize');
      }
      // The non-resizable columns must NOT sprout a handle: the group toggle,
      // the flexible Description column, and the ↺ button.
      for (const cls of ['.inv-col-group', '.inv-col-desc', '.inv-col-reset']) {
        await expect(page.locator('.inv-col-header ' + cls + ' [data-col-resize]'))
          .toHaveCount(0);
      }
    });

  test('dragging the Part # divider widens the header cell and the row cell together',
    async ({ page }) => {
      await boot(page);
      await expectUnshrunk(page);
      const before = await pair(page, '.inv-col-partid', '.part-ids');
      expect(before.hdr).toBe(before.row);          // aligned to begin with
      expect(await token(page, '--inv-col-pn-w')).toBe(before.hdr);

      await dragHandle(page, 'inv', 'partid', 80);

      const after = await pair(page, '.inv-col-partid', '.part-ids');
      expect(after.hdr, 'header cell should have grown by the drag distance')
        .toBeCloseTo(before.hdr + 80, 0);
      expect(after.row, 'the row cell must move with its header, not stay behind')
        .toBe(after.hdr);
      expect(await token(page, '--inv-col-pn-w')).toBeCloseTo(before.hdr + 80, 0);
    });

  test('the header and row cells stay left-aligned for every column after a drag',
    async ({ page }) => {
      await boot(page);
      await dragHandle(page, 'inv', 'partid', 90);

      // Same measurement inv-col-alignment.spec.mjs makes, re-run in the
      // resized state: a drag must not shear the grid.
      const diffs = await page.evaluate(() => {
        const pairs = [
          ['inv-col-partid', '.part-ids'], ['inv-col-mpn', '.part-mpn'],
          ['inv-col-vendor', '.part-vendor'], ['inv-col-unit', '.part-unit-price'],
          ['inv-col-value', '.part-value'], ['inv-col-qty', '.part-qty'],
        ];
        const row = document.querySelector('.inv-part-row');
        return pairs.map(([hdrCls, rowSel]) => {
          const hdr = document.querySelector('.inv-col-header .' + hdrCls);
          const cell = row && row.querySelector(rowSel);
          if (!hdr || !cell) return { col: hdrCls, diff: 0 };
          return {
            col: hdrCls,
            diff: Math.round(cell.getBoundingClientRect().left
              - hdr.getBoundingClientRect().left),
          };
        });
      });
      for (const d of diffs) {
        expect(Math.abs(d.diff), `${d.col} misaligned by ${d.diff}px after resize`)
          .toBeLessThanOrEqual(2);
      }
    });

  test('a clipped long part number becomes fully visible once the column is widened',
    async ({ page }) => {
      await boot(page);
      await expectUnshrunk(page);
      const overflow = () => page.evaluate((pn) => {
        const el = document.querySelector(`.part-id-digikey[data-digikey="${pn}"]`)
          || Array.from(document.querySelectorAll('.part-id-digikey'))
            .find(e => e.textContent.trim() === pn);
        if (!el) return null;
        return { over: el.scrollWidth - el.clientWidth, text: el.textContent.trim() };
      }, LONG_PN);

      const before = await overflow();
      expect(before, 'the long-PN probe row should be rendered').not.toBeNull();
      expect(before.over,
        'precondition: the default 100px Part # column clips this PN').toBeGreaterThan(0);

      await dragHandle(page, 'inv', 'partid', 160);

      const after = await overflow();
      expect(after.over,
        `PN "${after.text}" is still clipped by ${after.over}px after widening`)
        .toBeLessThanOrEqual(0);
    });

  test('dragging far left stops at the column minimum instead of collapsing it',
    async ({ page }) => {
      await boot(page);
      const floor = await page.evaluate(() => parseFloat(getComputedStyle(
        document.documentElement).getPropertyValue('--inv-col-pn-min-w')));
      expect(floor).toBeGreaterThan(0);

      await dragHandle(page, 'inv', 'partid', -600);

      expect(await token(page, '--inv-col-pn-w')).toBe(floor);
      const after = await pair(page, '.inv-col-partid', '.part-ids');
      expect(after.hdr).toBe(floor);
      expect(after.row).toBe(floor);
    });

  test('each column resizes independently', async ({ page }) => {
    await boot(page);
    const mpnBefore = await token(page, '--inv-col-mfgpn-w');
    const qtyBefore = await token(page, '--inv-col-stock-w');

    await dragHandle(page, 'inv', 'mpn', 70);

    expect(await token(page, '--inv-col-mfgpn-w')).toBeCloseTo(mpnBefore + 70, 0);
    expect(await token(page, '--inv-col-stock-w'),
      'resizing MPN must not disturb Qty').toBe(qtyBefore);
  });

  test('the flexible Description column absorbs the slack — nothing overflows',
    async ({ page }) => {
      await boot(page);
      const headerWidth = () => page.evaluate(() => {
        const h = document.querySelector('.inv-col-header');
        const body = document.getElementById('inventory-body');
        return { hdr: h.offsetWidth, body: body.clientWidth,
          scroll: body.scrollWidth };
      });
      const before = await headerWidth();
      await dragHandle(page, 'inv', 'partid', 150);
      const after = await headerWidth();
      expect(after.hdr, 'the header row itself must not grow past the panel')
        .toBeLessThanOrEqual(before.body + 2);
      expect(after.scroll, 'no horizontal overflow of the grid')
        .toBeLessThanOrEqual(before.scroll + 2);
    });

  test('a drag on the divider does not also trigger the header button underneath',
    async ({ page }) => {
      await boot(page);
      // Part # is a button that cycles vendor grouping; MPN is a sort button.
      await dragHandle(page, 'inv', 'partid', 60);
      await expect(page.locator('.inv-vendor-header'),
        'the resize drag must not have cycled vendor grouping').toHaveCount(0);

      await dragHandle(page, 'inv', 'mpn', 40);
      await expect(page.locator('.inv-col-mpn .inv-col-sort-active'),
        'the resize drag must not have triggered a sort').toHaveCount(0);
    });
});

test.describe('Column resizing — persistence and reset', () => {
  test('a dragged width is written to preferences and survives a reload',
    async ({ page }) => {
      await boot(page, { persistPrefs: true });
      await expectUnshrunk(page);
      const before = await token(page, '--inv-col-pn-w');
      await dragHandle(page, 'inv', 'partid', 100);
      const widened = await token(page, '--inv-col-pn-w');
      expect(widened).toBeCloseTo(before + 100, 0);

      await page.waitForFunction(() => {
        const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
        if (!raw) return false;
        const cw = JSON.parse(raw).column_widths;
        return !!cw && !!cw.inv && typeof cw.inv.partid === 'number';
      }, null, { timeout: 5000 });
      expect((await storedWidths(page)).inv.partid).toBe(widened);

      await page.reload();
      await waitForInventoryRows(page);

      expect(await token(page, '--inv-col-pn-w'),
        'the stored width should be re-applied on load').toBe(widened);
      const after = await pair(page, '.inv-col-partid', '.part-ids');
      expect(after.hdr).toBe(widened);
      expect(after.row, 'rows must come back at the stored width too').toBe(widened);
    });

  test('the ↺ control restores every column to its default width', async ({ page }) => {
    await boot(page, { persistPrefs: true });
    await expectUnshrunk(page);
    const pnDefault = await token(page, '--inv-col-pn-w');
    const qtyDefault = await token(page, '--inv-col-stock-w');

    await dragHandle(page, 'inv', 'partid', 120);
    await dragHandle(page, 'inv', 'qty', 40);
    expect(await token(page, '--inv-col-pn-w')).toBeCloseTo(pnDefault + 120, 0);
    await page.waitForFunction(() => {
      const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
      return !!raw && !!(JSON.parse(raw).column_widths || {}).inv;
    }, null, { timeout: 5000 });

    await page.locator('.inv-col-cell[data-col="reset"]').click();

    expect(await token(page, '--inv-col-pn-w')).toBe(pnDefault);
    expect(await token(page, '--inv-col-stock-w')).toBe(qtyDefault);
    const after = await pair(page, '.inv-col-partid', '.part-ids');
    expect(after.hdr).toBe(pnDefault);
    expect(after.row).toBe(pnDefault);

    await page.waitForFunction(() => {
      const raw = window.sessionStorage.getItem('__test_prefs_inv_view');
      if (!raw) return false;
      const cw = JSON.parse(raw).column_widths;
      return !cw || !cw.inv;
    }, null, { timeout: 5000 });
  });

  test('double-clicking one divider resets only that column', async ({ page }) => {
    await boot(page, { persistPrefs: true });
    const pnDefault = await token(page, '--inv-col-pn-w');
    const mpnDefault = await token(page, '--inv-col-mfgpn-w');

    await dragHandle(page, 'inv', 'partid', 120);
    await dragHandle(page, 'inv', 'mpn', 60);

    await page.locator('[data-col-resize-table="inv"][data-col-resize="partid"]')
      .dblclick();

    expect(await token(page, '--inv-col-pn-w')).toBe(pnDefault);
    expect(await token(page, '--inv-col-mfgpn-w'),
      'the neighbouring column keeps its custom width').toBeCloseTo(mpnDefault + 60, 0);
  });

  test('a corrupt stored value falls back to the defaults instead of breaking the grid',
    async ({ page }) => {
      await boot(page, {
        preferences: { thresholds: {}, column_widths: 'not-an-object' },
      });
      await expectUnshrunk(page);
      const rootDefault = await page.evaluate(() => parseFloat(getComputedStyle(
        document.documentElement).getPropertyValue('--inv-col-pn-w')));
      expect(await token(page, '--inv-col-pn-w')).toBe(rootDefault);
      const p = await pair(page, '.inv-col-partid', '.part-ids');
      expect(p.hdr).toBe(rootDefault);
      expect(p.row).toBe(rootDefault);
    });

  test('a stored width outside the legal range is clamped on load', async ({ page }) => {
    await boot(page, {
      preferences: { thresholds: {}, column_widths: { inv: { partid: 5 } } },
    });
    const floor = await page.evaluate(() => parseFloat(getComputedStyle(
      document.documentElement).getPropertyValue('--inv-col-pn-min-w')));
    expect(await token(page, '--inv-col-pn-w')).toBe(floor);
  });
});

test.describe('Column resizing — the BOM comparison table (a real <table>)', () => {
  test('dragging a <th> divider resizes that column', async ({ page }) => {
    await boot(page);
    await loadBom(page, BOM_CSV);
    await page.waitForSelector('.table-wrap table thead th[data-bom-col="mpn"]');

    const thWidth = () => page.evaluate(() => {
      const th = document.querySelector('thead th[data-bom-col="mpn"]');
      return th ? th.offsetWidth : -1;
    });
    const before = await thWidth();
    expect(before).toBeGreaterThan(0);

    await dragHandle(page, 'bom', 'mpn', 60);

    expect(await thWidth()).toBeCloseTo(before + 60, 0);
    // The sticky button column is the subject of the button-clipping tests and
    // must be untouched by a resize elsewhere in the row.
    await expect(page.locator('thead th.btn-group-hdr [data-col-resize]'))
      .toHaveCount(0);
  });

  test('a BOM column width survives the re-render a mutation triggers',
    async ({ page }) => {
      await boot(page);
      await loadBom(page, BOM_CSV);
      await page.waitForSelector('.table-wrap table thead th[data-bom-col="part"]');
      await dragHandle(page, 'bom', 'part', 70);
      const widened = await page.evaluate(() =>
        document.querySelector('thead th[data-bom-col="part"]').offsetWidth);

      // Any inventory refresh rebuilds the whole table, <thead> included.
      await page.evaluate(async () => {
        const { EventBus, Events } = await import('/js/event-bus.js');
        EventBus.emit(Events.INVENTORY_UPDATED, []);
      });
      await page.waitForTimeout(200);

      expect(await page.evaluate(() =>
        document.querySelector('thead th[data-bom-col="part"]').offsetWidth))
        .toBe(widened);
    });
});

test.describe('Column resizing at non-100% UI zoom', () => {
  test('the dragged edge tracks the pointer 1:1 rather than by the zoom factor',
    async ({ page }) => {
      await boot(page);
      await setZoom(page, 150);
      await expectUnshrunk(page);

      const startAuthored = await token(page, '--inv-col-pn-w');
      const startRect = await page.evaluate(() => document
        .querySelector('.inv-col-header .inv-col-partid').getBoundingClientRect().width);

      // 90 viewport px at 150% zoom is 60 authored px.
      await dragHandle(page, 'inv', 'partid', 90);

      expect(await token(page, '--inv-col-pn-w'),
        'the written width is authored px, so it must grow by 90/1.5 = 60')
        .toBeCloseTo(startAuthored + 60, 0);

      const endRect = await page.evaluate(() => document
        .querySelector('.inv-col-header .inv-col-partid').getBoundingClientRect().width);
      expect(endRect - startRect,
        'on screen the edge must follow the cursor exactly — a mixed-space '
        + 'implementation would move it 135px (90 x 1.5) instead of 90px')
        .toBeCloseTo(90, 0);

      // Header and rows still agree after a zoomed drag.
      const p = await pair(page, '.inv-col-partid', '.part-ids');
      expect(p.row).toBe(p.hdr);
      expect(p.hdr).toBeCloseTo(startAuthored + 60, 0);
    });

  test('the minimum clamp still holds at a non-unit zoom', async ({ page }) => {
    await boot(page);
    await setZoom(page, 50);
    const floor = await page.evaluate(() => parseFloat(getComputedStyle(
      document.documentElement).getPropertyValue('--inv-col-pn-min-w')));

    await dragHandle(page, 'inv', 'partid', -400);

    expect(await token(page, '--inv-col-pn-w')).toBe(floor);
  });
});
