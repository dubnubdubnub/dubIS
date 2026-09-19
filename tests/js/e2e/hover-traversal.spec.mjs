// @ts-check
/**
 * hover-traversal.spec.mjs — "moving the cursor to the popup makes the popup
 * disappear, it is unusable".
 *
 * Both hover affordances anchor their popup a few px away from the trigger, so
 * the pointer has to cross a gap that belongs to neither element. The generic
 * Copy popover used to hide *synchronously* on the trigger's `mouseout`, and
 * `relatedTarget` in that gap is some unrelated row — so the popover was gone
 * before the pointer arrived, every time.
 *
 * The reason it was never caught: `locator.hover()` followed by
 * `locator.click()` TELEPORTS the pointer. It never visits the gap. Every
 * assertion here therefore moves the mouse through real intermediate positions
 * (`{ steps: n }`), which is the only way to reproduce the bug.
 *
 * Each traversal is also run at a non-100% UI zoom. The corridor test that keeps
 * the popup alive is arithmetic over rects and pointer coordinates, and those
 * live in two different px spaces under `html { zoom: z }` (see js/ui-zoom.js) —
 * mixing them yields a corridor that only lines up at 100%.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import { setZoom } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/** The LCSC part the fixture's first row carries, plus a product payload for it. */
const LCSC_CODE = 'C429942';
const MOCK_PRODUCTS = {
  [`lcsc:${LCSC_CODE}`]: {
    productCode: LCSC_CODE,
    title: 'DF40C-30DP-0.4V(51) Connector',
    manufacturer: 'HRS (Hirose)',
    mpn: 'DF40C-30DP-0.4V(51)',
    package: 'SMD,P=0.4mm',
    description: 'Board to Board Connector Header 30 position 0.4mm Pitch',
    stock: 15000,
    prices: [{ qty: 1, price: 0.4123 }, { qty: 10, price: 0.3856 }],
    imageUrl: '',
    pdfUrl: '',
    lcscUrl: 'https://www.lcsc.com/product-detail/C429942.html',
    category: 'Connectors',
    subcategory: 'Board to Board Connectors',
    attributes: [{ name: 'Pitch', value: '0.4mm' }],
    provider: 'lcsc',
  },
};

/**
 * Walk the pointer from the middle of `from` into `to` through real intermediate
 * positions, so it genuinely passes through the gap between them.
 *
 * Both boxes come from `boundingBox()`, which reports post-zoom px — the same
 * space `page.mouse` takes — so this stays correct at any zoom.
 */
async function traverse(page, from, to) {
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  // Two legs: straight down/up into the popup's near edge, then on to the target
  // point inside it. Each leg is stepped, so every px of gap is visited.
  const nearX = Math.min(Math.max(from.x + from.width / 2, to.x + 6), to.x + to.width - 6);
  const nearY = to.y < from.y ? to.y + to.height - 4 : to.y + 4;
  await page.mouse.move(nearX, nearY, { steps: 12 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 });
}

async function boot(page, opts = {}) {
  await installRouteMocks(page, MOCK_INVENTORY, { productMocks: MOCK_PRODUCTS, ...opts });
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(300);
}

/** Somewhere inert: the header's own padding bears no hoverable text. */
async function leaveToUnrelated(page) {
  await page.mouse.move(1, 1, { steps: 10 });
}

for (const zoom of [100, 150, 80]) {
  test.describe(`generic Copy popover, pointer traversal @ ${zoom}%`, () => {

    test.beforeEach(async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write']);
      await boot(page);
      if (zoom !== 100) await setZoom(page, zoom);
    });

    test('survives the pointer moving from the value into the popover, and Copy works', async ({ page }) => {
      const cell = page.locator('.inv-part-row:visible .part-mpn:visible').first();
      const cellText = (await cell.innerText()).trim();
      expect(cellText.length).toBeGreaterThan(0);

      await cell.hover();
      const popover = page.locator('.text-popover:not(.hidden)');
      await expect(popover).toBeVisible({ timeout: 3000 });  // > the 350ms show delay

      const triggerBox = await cell.boundingBox();
      const popBox = await popover.boundingBox();
      expect(triggerBox).not.toBeNull();
      expect(popBox).not.toBeNull();
      if (!triggerBox || !popBox) return;

      // There IS a gap to cross — otherwise this test would prove nothing.
      const gap = popBox.y > triggerBox.y
        ? popBox.y - (triggerBox.y + triggerBox.height)
        : triggerBox.y - (popBox.y + popBox.height);
      expect(gap, `@${zoom}%: expected a visible gap between the value and its popover`)
        .toBeGreaterThan(0);

      await traverse(page, triggerBox, popBox);

      await expect(popover,
        `@${zoom}%: the popover vanished while the pointer travelled into it — `
        + 'the trigger→popover corridor is not keeping it alive')
        .toBeVisible();

      // And the Copy button inside is genuinely clickable, not just present.
      const btn = popover.locator('.text-popover-copy');
      await expect(btn).toBeVisible();
      await btn.click();
      await expect(btn).toHaveText(/Copied|Failed/);
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      expect(clip.trim()).toBe(cellText);
    });

    test('still hides when the pointer leaves for somewhere unrelated', async ({ page }) => {
      const cell = page.locator('.inv-part-row:visible .part-mpn:visible').first();
      await cell.hover();
      await expect(page.locator('.text-popover:not(.hidden)')).toBeVisible({ timeout: 3000 });

      await leaveToUnrelated(page);
      await expect(page.locator('.text-popover'),
        `@${zoom}%: the grace period must not become "never hides"`)
        .toHaveClass(/hidden/, { timeout: 3000 });
    });

    test('a pointer that pauses in the gap does not lose the popover', async ({ page }) => {
      // The delay alone cannot save a slow hand; the corridor has to.
      const cell = page.locator('.inv-part-row:visible .part-mpn:visible').first();
      await cell.hover();
      const popover = page.locator('.text-popover:not(.hidden)');
      await expect(popover).toBeVisible({ timeout: 3000 });

      const t = await cell.boundingBox();
      const p = await popover.boundingBox();
      if (!t || !p) return;
      const midGapY = p.y > t.y
        ? (t.y + t.height + p.y) / 2
        : (p.y + p.height + t.y) / 2;
      await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2);
      await page.mouse.move(t.x + t.width / 2, midGapY, { steps: 6 });
      // Dwell in the gap for well over the hide delay.
      await page.waitForTimeout(900);
      await expect(popover,
        `@${zoom}%: pausing in the gap dismissed the popover — the hide path is `
        + 'trusting its timer instead of re-checking the corridor')
        .toBeVisible();
    });
  });

  test.describe(`part-number tooltip, pointer traversal @ ${zoom}%`, () => {

    test.beforeEach(async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write']);
      await boot(page);
      if (zoom !== 100) await setZoom(page, zoom);
    });

    test('survives the pointer moving from the part number into the tooltip', async ({ page }) => {
      const pn = page.locator(`#inventory-body [data-lcsc="${LCSC_CODE}"]`).first();
      await pn.scrollIntoViewIfNeeded();
      await pn.hover();
      const tooltip = page.locator('.part-preview:not(.hidden)');
      await expect(tooltip).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });
      // Let the async history sections land and re-anchor the card first, so the
      // traversal starts from the geometry the user would actually see.
      await page.waitForTimeout(400);

      const triggerBox = await pn.boundingBox();
      const tipBox = await tooltip.boundingBox();
      if (!triggerBox || !tipBox) return;

      await traverse(page, triggerBox, tipBox);
      await expect(tooltip,
        `@${zoom}%: the part tooltip vanished while the pointer travelled into it`)
        .toBeVisible();
    });

    test('the tooltip Copy button copies the part number', async ({ page }) => {
      const pn = page.locator(`#inventory-body [data-lcsc="${LCSC_CODE}"]`).first();
      await pn.scrollIntoViewIfNeeded();
      await pn.hover();
      const tooltip = page.locator('.part-preview:not(.hidden)');
      await expect(tooltip).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });
      await page.waitForTimeout(400);

      const triggerBox = await pn.boundingBox();
      const tipBox = await tooltip.boundingBox();
      if (!triggerBox || !tipBox) return;
      await traverse(page, triggerBox, tipBox);

      const btn = tooltip.locator('.part-preview-copy-field').first();
      await expect(btn,
        'the part tooltip must offer a Copy control for the part number — the generic '
        + 'Copy popover is suppressed on part-number cells, so this is the only path')
        .toBeVisible();
      await btn.click();
      await expect(btn).toHaveText(/Copied|Failed/);
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      expect(clip.trim()).toBe(LCSC_CODE);
    });

    test('still hides when the pointer leaves for somewhere unrelated', async ({ page }) => {
      const pn = page.locator(`#inventory-body [data-lcsc="${LCSC_CODE}"]`).first();
      await pn.scrollIntoViewIfNeeded();
      await pn.hover();
      await expect(page.locator('.part-preview:not(.hidden)')).toBeVisible({ timeout: 5000 });

      await leaveToUnrelated(page);
      await expect(page.locator('.part-preview'),
        `@${zoom}%: the tooltip must still dismiss when the pointer goes elsewhere`)
        .toHaveClass(/hidden/, { timeout: 3000 });
    });
  });
}

test.describe('the two affordances do not fight over the same hover', () => {
  test('the generic Copy popover never opens on top of the part tooltip', async ({ page }) => {
    // .text-popover is z-index 10000 and .part-preview is 500, so a popover
    // opened over the tooltip covers it AND steals its hover — dismissing the
    // card the user was reading. It must stay suppressed inside .part-preview.
    await boot(page);
    const pn = page.locator(`#inventory-body [data-lcsc="${LCSC_CODE}"]`).first();
    await pn.hover();
    await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });
    await page.waitForTimeout(400);

    const title = page.locator('.part-preview-title');
    const box = await title.boundingBox();
    if (!box) return;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 10 });
    // Well past the popover's 350ms show delay.
    await page.waitForTimeout(900);

    await expect(page.locator('.text-popover'),
      'hovering the part tooltip\'s own text must not open the generic Copy popover')
      .toHaveClass(/hidden/);
    await expect(page.locator('.part-preview:not(.hidden)'),
      'and the tooltip must still be up').toBeVisible();
  });
});
