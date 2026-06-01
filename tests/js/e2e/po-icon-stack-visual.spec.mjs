// @ts-check
/**
 * Visual truth for the inline PO icon cascade in the inventory grid
 * (renderFanStack in js/inventory/favicon-stack.js).
 *
 * Asserts the RENDERED result, not the inline-style string (see
 * docs/visual-testing.md): the most-recent PO icon is layered on top at the
 * stack's top-left, older POs cascade down-right and remain visible at the
 * bottom-right, and the cascade does not blow out the row height.
 */
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';
import { capture, rectOf } from './visual/capture.mjs';
import { detectClipping } from './visual/measure.mjs';

// Three vendors with distinct emoji icons. The .fan-icon box (border +
// background) is painted regardless of whether the glyph renders, so the
// assertions are cross-platform safe.
const VENDORS = [
  { id: 'v_old', name: 'OldCo',  type: 'real', icon: '🔴', url: '', favicon_path: '' },
  { id: 'v_mid', name: 'MidCo',  type: 'real', icon: '🟢', url: '', favicon_path: '' },
  { id: 'v_new', name: 'NewCo',  type: 'real', icon: '🔵', url: '', favicon_path: '' },
];

// po_history is chronological oldest→newest, so po_new is the most recent.
const PURCHASE_ORDERS = [
  { po_id: 'po_old', vendor_id: 'v_old', purchase_date: '2025-01-01' },
  { po_id: 'po_mid', vendor_id: 'v_mid', purchase_date: '2025-03-01' },
  { po_id: 'po_new', vendor_id: 'v_new', purchase_date: '2025-06-01' },
];

const INVENTORY = [
  {
    section: 'Connectors', lcsc: 'C1', mpn: 'MULTI-PO-PART', manufacturer: 'X',
    package: '0603', description: 'part bought across three POs',
    qty: 100, unit_price: 0.01, ext_price: 1.0,
    digikey: '', pololu: '', mouser: '',
    po_history: ['po_old', 'po_mid', 'po_new'],
    primary_vendor_id: '',
  },
];

const STACK_OFFSET = 6;

/** Index of the .fan-icon under a viewport CSS-px point, or -1. */
function iconIndexAt(page, x, y) {
  return page.evaluate(({ x, y }) => {
    const el = document.elementFromPoint(x, y);
    const icon = el && el.closest('.fan-icon');
    if (!icon) return -1;
    return Array.from(icon.parentElement.querySelectorAll('.fan-icon')).indexOf(icon);
  }, { x, y });
}

test('most-recent PO is on top; older POs cascade down-right and stay visible', async ({ page }) => {
  await addMockSetup(page, INVENTORY, { mfgDirectVendors: VENDORS, purchaseOrders: PURCHASE_ORDERS });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  // The cascade depends on POs/vendors, which load after INVENTORY_LOADED.
  await page.waitForSelector('.inv-part-row .favicon-fan-stack .fan-icon', { timeout: 10_000 });

  const stack = page.locator('.inv-part-row .favicon-fan-stack').first();
  const icons = stack.locator('.fan-icon');
  await expect(icons, 'one icon per PO, capped at 3 most recent').toHaveCount(3);

  // Front icon (most recent, index 0) is fully visible — not clipped/occluded.
  const front = icons.nth(0);
  const clip = await detectClipping(page, front);
  expect(clip.occluded, `front icon occluded: ${clip.reason}`).toBe(false);
  expect(clip.clipped, `front icon clipped: ${clip.reason}`).toBe(false);

  // Cascade geometry: each older icon is offset ~6px right AND down.
  const rects = [];
  for (let i = 0; i < 3; i++) rects.push(await rectOf(icons.nth(i)));
  for (let i = 1; i < 3; i++) {
    expect(Math.abs((rects[i].x - rects[i - 1].x) - STACK_OFFSET),
      `icon ${i} horizontal offset wrong`).toBeLessThanOrEqual(1.5);
    expect(Math.abs((rects[i].y - rects[i - 1].y) - STACK_OFFSET),
      `icon ${i} vertical offset wrong`).toBeLessThanOrEqual(1.5);
  }

  const r = await rectOf(stack);

  // Top-left of the stack hit-tests to the most-recent icon (index 0, on top).
  expect(await iconIndexAt(page, r.x + 3, r.y + 3),
    'top-left should hit the most-recent icon').toBe(0);

  // In the overlap zone the most-recent icon is layered ON TOP of the next.
  expect(await iconIndexAt(page, r.x + STACK_OFFSET + 2, r.y + STACK_OFFSET + 2),
    'most-recent icon should cover the older one in the overlap region').toBe(0);

  // The bottom-right sliver belongs to an OLDER icon — it peeks out, visible.
  expect(await iconIndexAt(page, r.x + r.width - 2, r.y + r.height - 2),
    'bottom-right should hit an older icon peeking out').toBeGreaterThan(0);

  // The cascade must not blow out the row height.
  const rowRect = await rectOf(page.locator('.inv-part-row').first());
  expect(rowRect.height, `row grew too tall (${rowRect.height}px)`).toBeLessThanOrEqual(40);
});

test('the older icon is actually painted in the bottom-right sliver', async ({ page }) => {
  await addMockSetup(page, INVENTORY, { mfgDirectVendors: VENDORS, purchaseOrders: PURCHASE_ORDERS });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForSelector('.inv-part-row .favicon-fan-stack .fan-icon', { timeout: 10_000 });

  const stack = page.locator('.inv-part-row .favicon-fan-stack').first();
  const r = await rectOf(stack);
  const frame = await capture(page, stack, { pad: 6 });

  // Reference: a point well to the right of the stack = bare row background.
  const [refX, refY] = frame.toImg(r.x + r.width + 4, r.y + r.height / 2);
  const ref = frame.pixel(refX, refY);
  expect(ref, 'reference pixel out of frame').not.toBeNull();

  // Scan the bottom-right sliver (the part of the oldest icon NOT covered by
  // the ones in front) for any pixel that differs from the row background —
  // i.e. the older icon's box/border is genuinely painted there.
  const channelDelta = (a, b) => Math.max(
    Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]), Math.abs(a[3] - b[3]),
  );
  let painted = false;
  for (let dx = 1; dx <= STACK_OFFSET && !painted; dx++) {
    for (let dy = 1; dy <= STACK_OFFSET && !painted; dy++) {
      const [px, py] = frame.toImg(r.x + r.width - dx, r.y + r.height - dy);
      const p = frame.pixel(px, py);
      if (p && channelDelta(p, /** @type {number[]} */(ref)) > 24) painted = true;
    }
  }
  expect(painted, 'bottom-right sliver is blank — older icon not visible behind the front one').toBe(true);
});
