// tests/js/e2e/cart-plan.spec.mjs
// @ts-check
// The cart's purchase plan: board count, presets, and accepting a
// recommendation. The plan itself is computed server-side
// (domain/cart_plan.py + domain/purchase_candidates.py) and arrives over
// GET /v1/carts/{id}/plan — these specs assert the modal renders it and that
// the controls send what they claim to, NOT that the ranking is correct
// (that is tests/python/domain/test_purchase_candidates.py's job).
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

const CARTS_SEED = {
  carts: [{
    id: 'cart-1',
    name: 'Glasgow revD0',
    board_count: 25,
    items: [
      {
        ref: 'item-1', part_id: 'C496552', raw: null, qty: 5,
        target_distributor: 'lcsc', target_packaging: null,
        preset: null, per_board_qty: 8,
      },
    ],
  }],
  active_cart_id: 'cart-1',
};

/** A candidate shaped the way domain/purchase_candidates.Candidate serializes. */
function candidate(overrides = {}) {
  return {
    distributor: 'lcsc', packaging: 'Cut Tape', carrier: 'tape', is_reel: false,
    qty: 100, unit_price: 0.0041, fee: 0, spend: 0.41, break_qty: 100,
    on_break: true, surplus: 12, stock_known: true, origin: 'break',
    ...overrides,
  };
}

/** A plan with one priced line, matching CARTS_SEED's single item. */
function planWith(overrides = {}) {
  const selected = overrides.selected || candidate();
  return {
    cart_id: 'cart-1',
    board_count: 25,
    default_preset: 'min',
    reel_ceiling: 80,
    lines: [{
      ref: 'item-1',
      part_id: 'C496552',
      preset: 'min',
      board_count: 25,
      per_board_qty: 8,
      gross_qty: 200,
      covered_by_stock: 112,
      required_qty: 88,
      on_hand: 112,
      target_distributor: 'lcsc',
      target_packaging: null,
      candidates: [selected, candidate({ qty: 1000, unit_price: 0.0029, spend: 2.9 })],
      selected,
      runner_up: candidate({ qty: 1000, unit_price: 0.0029, spend: 2.9 }),
      rejections: [],
      reason: 'lowest total spend',
      over_ceiling: false,
      fell_back: '',
      ...(overrides.line || {}),
    }],
    totals: { spend: selected.spend, lines: 1, covered_by_stock: 0, unpriced: 0 },
    ...(overrides.top || {}),
  };
}

async function openCart(page, options) {
  await installRouteMocks(page, MOCK_INVENTORY, options);
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.click('#cart-btn');
  await expect(page.locator('.cart-modal')).toBeVisible();
}

test.describe('Cart purchase plan', () => {
  test('renders the requirement, the recommendation and the total', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });

    // 25 boards x 8 placements = 200, less 112 on hand = 88 to buy.
    await expect(page.locator('.cart-modal .cart-plan-need')).toContainText('88');
    await expect(page.locator('.cart-modal .cart-plan-buy')).toContainText('100 · Cut Tape');
    // Sub-cent unit prices must not render as $0.00.
    await expect(page.locator('.cart-modal .cart-plan-unit')).toContainText('$0.00410');
    await expect(page.locator('.cart-modal .cart-plan-totals')).toContainText('$0.41');
  });

  test('the requirement carries its own derivation', async ({ page }) => {
    // The whole reason board_count is stored rather than folded into the
    // quantity: the row can say where its number came from.
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    await expect(page.locator('.cart-modal .cart-plan-need span'))
      .toHaveAttribute('title', '25 boards × 8 placements = 200, less 112 on hand');
  });

  test('the board count round-trips through the server', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    const input = page.locator('#cart-boards-input');
    await expect(input).toHaveValue('25');

    await input.fill('50');
    await input.blur();
    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.set_cart_board_count || []).map((c) => c[1]),
    ))).toContain(50);
  });

  test('a non-integer board count is refused, not rounded', async ({ page }) => {
    // This number multiplies every per-board quantity in the cart, so quietly
    // substituting one changes what gets ordered.
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    const input = page.locator('#cart-boards-input');
    await input.fill('2.5');
    await input.blur();
    await expect(input).toHaveClass(/invalid/);
    expect(await page.evaluate(
      () => (window.__apiCalls?.set_cart_board_count || []).length,
    )).toBe(0);
  });

  test('the cart-wide rule is sent to the plan endpoint', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    await page.click('.cart-modal .cart-topbar .cart-seg button[data-preset="reel"]');
    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.plan_cart || []).map((c) => c[1]),
    ))).toContain('reel');
  });

  test('the reel ceiling preference reaches the plan endpoint', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    // Third positional arg of plan_cart(cart_id, preset, reel_ceiling).
    const ceilings = await page.evaluate(
      () => (window.__apiCalls?.plan_cart || []).map((c) => c[2]),
    );
    expect(ceilings.some((c) => c === 80)).toBe(true);
  });

  test('pinning a row preset sends it, and clicking it again releases it', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    const rowSeg = page.locator('.cart-modal tbody .cart-seg').first();

    await rowSeg.locator('button[data-preset="reel"]').click();
    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.update_cart_item || []).map((c) => c[5]),
    ))).toContain('reel');

    // Same control releases the pin — no separate affordance to discover.
    await rowSeg.locator('button[data-preset="reel"]').click();
    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.update_cart_item || []).map((c) => c[5]),
    ))).toContain('');
  });

  test('a row following the cart default is marked inherited, a pinned one is not', async ({ page }) => {
    // "Pinned to Min" and "following a cart that happens to be Min" are
    // different states; conflating them makes pinning invisible.
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    await expect(page.locator('.cart-modal tbody .cart-seg').first()).toHaveClass(/is-inherited/);

    const pinned = JSON.parse(JSON.stringify(CARTS_SEED));
    pinned.carts[0].items[0].preset = 'reel';
    await openCart(page, { carts: pinned, cartPlan: planWith() });
    await expect(page.locator('.cart-modal tbody .cart-seg').first()).not.toHaveClass(/is-inherited/);
  });

  test('accepting a recommendation writes the quantity and packaging', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    // Stored qty is 5, the plan says 100 — so it offers a button, not a tick.
    const accept = page.locator('.cart-modal .cart-plan-accept').first();
    await expect(accept).toContainText('100 · Cut Tape');
    await accept.click();

    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.update_cart_item || []).map((c) => [c[2], c[4]]),
    ))).toContainEqual([100, 'Cut Tape']);
  });

  test('a line already matching the plan shows a tick, not a button', async ({ page }) => {
    const matched = JSON.parse(JSON.stringify(CARTS_SEED));
    matched.carts[0].items[0].qty = 100;
    matched.carts[0].items[0].target_packaging = 'Cut Tape';
    await openCart(page, { carts: matched, cartPlan: planWith() });
    await expect(page.locator('.cart-modal .cart-plan-agrees')).toContainText('100 · Cut Tape');
    await expect(page.locator('.cart-modal .cart-plan-accept')).toHaveCount(0);
  });

  test('typing a quantity pins the row to Custom', async ({ page }) => {
    // Typing a quantity IS choosing a custom one; leaving the row claiming
    // "Min" would make every future re-plan disagree with it silently.
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    const qtyInput = page.locator('.cart-modal .cart-qty-input').first();
    await qtyInput.fill('700');
    await qtyInput.blur();

    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.update_cart_item || []).map((c) => c[5]),
    ))).toContain('custom');
  });

  test('a custom row gets a packaging picker drawn from its own candidates', async ({ page }) => {
    const custom = JSON.parse(JSON.stringify(CARTS_SEED));
    custom.carts[0].items[0].preset = 'custom';
    custom.carts[0].items[0].qty = 700;
    await openCart(page, { carts: custom, cartPlan: planWith() });

    const select = page.locator('.cart-modal .cart-row-pkg-select').first();
    await expect(select).toBeVisible();
    // Only packagings the part actually has priced ladders for.
    await expect(select.locator('option')).toContainText(['—', 'Cut Tape']);
  });

  test('a line covered by stock is dimmed and costs nothing', async ({ page }) => {
    await openCart(page, {
      carts: CARTS_SEED,
      cartPlan: planWith({
        line: { required_qty: 0, covered_by_stock: 200, selected: null, runner_up: null,
                candidates: [], reason: 'covered by stock on hand' },
        top: { totals: { spend: 0, lines: 1, covered_by_stock: 1, unpriced: 0 } },
      }),
    });
    await expect(page.locator('.cart-modal tbody tr').first()).toHaveClass(/cart-row-covered/);
    await expect(page.locator('.cart-modal .cart-plan-totals')).toContainText('1 covered by stock');
  });

  test('a line nothing could price is called out rather than left blank', async ({ page }) => {
    // A blank row reads as free.
    await openCart(page, {
      carts: CARTS_SEED,
      cartPlan: planWith({
        line: {
          selected: null, runner_up: null, candidates: [],
          rejections: [{ distributor: 'lcsc', packaging: 'Tape & Reel', qty: 88,
                         reason: 'not_multiple', detail: 'sold in multiples of 5,000',
                         nearest_legal: 5000 }],
          reason: '',
        },
        top: { totals: { spend: 0, lines: 1, covered_by_stock: 0, unpriced: 1 } },
      }),
    });
    await expect(page.locator('.cart-modal tbody tr').first()).toHaveClass(/cart-row-unpriced/);
    await expect(page.locator('.cart-modal .cart-plan-note'))
      .toContainText('sold in multiples of 5,000 — nearest is 5,000');
    await expect(page.locator('.cart-modal .cart-plan-totals')).toContainText('1 unpriced');
  });

  test('a reel over the ceiling is shown and flagged, not hidden', async ({ page }) => {
    await openCart(page, {
      carts: CARTS_SEED,
      cartPlan: planWith({
        selected: candidate({ packaging: 'Tape & Reel', is_reel: true, qty: 5000, spend: 90 }),
        line: { over_ceiling: true, reason: 'cheapest reel available; exceeds the ceiling' },
      }),
    });
    await expect(page.locator('.cart-modal .cart-plan-note')).toContainText('over your reel ceiling');
    await expect(page.locator('.cart-modal .cart-plan-buy')).toContainText('5,000 · Tape & Reel');
  });

  test('Apply plan writes every disagreeing line', async ({ page }) => {
    await openCart(page, { carts: CARTS_SEED, cartPlan: planWith() });
    await page.click('.cart-modal .cart-plan-apply-all');
    await expect.poll(async () => (await page.evaluate(
      () => (window.__apiCalls?.update_cart_item || []).map((c) => c[2]),
    ))).toContain(100);
  });

  test('the plan endpoint is never asked to mutate', async ({ page }) => {
    // Re-planning after a price refresh must not rewrite a committed decision,
    // so the route is a GET and nothing else.
    const verbs = [];
    await installRouteMocks(page, MOCK_INVENTORY, { carts: CARTS_SEED, cartPlan: planWith() });
    page.on('request', (req) => {
      if (req.url().includes('/plan')) verbs.push(req.method());
    });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await expect.poll(() => verbs.length).toBeGreaterThan(0);
    expect([...new Set(verbs)]).toEqual(['GET']);
  });
});
