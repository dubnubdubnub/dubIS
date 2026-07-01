// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

// ── Test inventory ──
const INVENTORY = [
  { section: "Passives - Capacitors > MLCC",
    lcsc: "C2040", digikey: "", pololu: "", mouser: "",
    mpn: "CL05A104KA5NNNC", manufacturer: "Samsung",
    package: "0402", description: "100nF MLCC Capacitor",
    qty: 200, unit_price: 0.0025, ext_price: 0.50 },
  { section: "Connectors > Through Hole",
    lcsc: "C555", digikey: "", pololu: "", mouser: "M-555",
    mpn: "XYZ-555", manufacturer: "Acme",
    package: "SOT-23", description: "Dual-sourced widget",
    qty: 30, unit_price: 5.00, ext_price: 150.00 },
  // Cheapest flips with quantity: lcsc cheaper at qty 1, digikey cheaper at qty 100.
  { section: "ICs - Interface",
    lcsc: "C-FLIP", digikey: "DK-FLIP", pololu: "", mouser: "",
    mpn: "FLIP-1", manufacturer: "Flippy",
    package: "SOIC-8", description: "Flip part",
    qty: 10, unit_price: 0, ext_price: 0 },
  // One distributor errors (no Mouser mock) while LCSC succeeds.
  { section: "ICs - Amplifiers",
    lcsc: "C-OK", digikey: "", pololu: "", mouser: "M-FAIL",
    mpn: "OK-1", manufacturer: "Okay",
    package: "SOT-23", description: "Partial-fetch part",
    qty: 10, unit_price: 0, ext_price: 0 },
  // Price-less single-source part → opens the Price modal via the ⚠ button.
  { section: "Passives - Resistors > Chip Resistors",
    lcsc: "C9999", digikey: "", pololu: "", mouser: "",
    mpn: "RES-9999", manufacturer: "Yageo",
    package: "0402", description: "Priceless resistor",
    qty: 50, unit_price: 0, ext_price: 0 },
];

const MOCK_PRODUCTS = {
  "lcsc:C2040": { productCode: "C2040", prices: [{ qty: 1, price: 0.0025 }, { qty: 100, price: 0.001 }], provider: "lcsc" },
  "lcsc:C555":  { productCode: "C555",  prices: [{ qty: 1, price: 5.55 }, { qty: 10, price: 4.44 }], provider: "lcsc" },
  "mouser:M-555": { productCode: "M-555", prices: [{ qty: 1, price: 9.99 }, { qty: 10, price: 8.00 }], provider: "mouser" },
  "lcsc:C-FLIP":   { productCode: "C-FLIP",  prices: [{ qty: 1, price: 1.00 }, { qty: 100, price: 0.90 }], provider: "lcsc" },
  "digikey:DK-FLIP": { productCode: "DK-FLIP", prices: [{ qty: 1, price: 1.10 }, { qty: 100, price: 0.50 }], provider: "digikey" },
  "lcsc:C-OK":  { productCode: "C-OK", prices: [{ qty: 1, price: 2.00 }, { qty: 10, price: 1.50 }], provider: "lcsc" },
  // no mouser:M-FAIL mock → that row fails to fetch
  "lcsc:C9999": { productCode: "C9999", prices: [{ qty: 1, price: 0.02 }, { qty: 100, price: 0.005 }], provider: "lcsc" },
};

// lastPoQty drives each row's default quantity (and thus which tier is chosen).
const LAST_PO_QTY = { C2040: 100, C555: 10, "C-FLIP": 1, "C-OK": 10, C9999: 100 };

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');
const readNumber = async (loc) => Number(await loc.inputValue());

test.describe('Multi-distributor fetch price — Adjust & Price modals', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY, { productMocks: MOCK_PRODUCTS, lastPoQty: LAST_PO_QTY });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('single source: one row, auto-fetch fills unit price from PO-matched tier', async ({ page }) => {
    await partRow(page, 'C2040').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(1);
    // qty-100 tier auto-picked (lastPoQty 100) and auto-selected as cheapest.
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveCount(1);
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(0.001, 6);
    await expect.poll(async () => Number(await page.locator('#adj-ext-price').inputValue())).toBeCloseTo(0.2, 6);
  });

  test('multi source: two rows, cheapest auto-selected into unit price', async ({ page }) => {
    await partRow(page, 'C555').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(2);   // lcsc + mouser
    // At qty 10: lcsc 4.44 < mouser 8.00 → lcsc auto-selected.
    const selected = page.locator('#adj-fetch-panel .fetch-drow.selected');
    await expect(selected).toHaveCount(1);
    await expect(selected).toHaveAttribute('data-idx', '0');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(4.44, 4);

    // record_fetched_prices fired for both distributors.
    await expect.poll(async () =>
      (await page.evaluate(() => window.__apiCalls['record_fetched_prices'] || [])).length
    ).toBeGreaterThanOrEqual(2);
  });

  test('per-row qty change re-selects the cheapest distributor', async ({ page }) => {
    await partRow(page, 'C-FLIP').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await expect(page.locator('#adj-fetch-panel .fetch-drow')).toHaveCount(2);

    // At qty 1: lcsc 1.00 < digikey 1.10 → lcsc (idx 0) selected.
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(1.00, 4);

    // Bump the digikey row (idx 1) to qty 100 → digikey 0.50 now cheapest.
    const dkQty = page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"] .fetch-drow-qty');
    await dkQty.fill('100');
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(0.50, 4);
  });

  test('clicking a row pins it, overriding cheapest auto-pick', async ({ page }) => {
    await partRow(page, 'C555').locator('.adj-btn').click();
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');

    // Click the mouser row (idx 1) to pin the pricier source.
    await page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"]').click();
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(8.00, 4);

    // Editing the lcsc row's qty must NOT steal the selection back.
    await page.locator('#adj-fetch-panel .fetch-drow[data-idx="0"] .fetch-drow-qty').fill('1');
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '1');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(8.00, 4);
  });

  test('one distributor fails: its row shows unavailable, others still fetch', async ({ page }) => {
    await partRow(page, 'C-OK').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#adj-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(2);   // lcsc (ok) + mouser (fails)
    // Mouser row shows the error state.
    await expect(page.locator('#adj-fetch-panel .fetch-drow[data-idx="1"] .fetch-drow-err')).toHaveText(/unavailable/);
    // lcsc row (idx 0) is selected and drives the unit price (qty 10 → 1.50).
    await expect(page.locator('#adj-fetch-panel .fetch-drow.selected')).toHaveAttribute('data-idx', '0');
    await expect.poll(async () => Number(await page.locator('#adj-unit-price').inputValue())).toBeCloseTo(1.50, 4);
  });

  test('price modal (warning sign): panel fetches and fills unit price', async ({ page }) => {
    await partRow(page, 'C9999').locator('.price-warn-btn').click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    const rows = page.locator('#price-fetch-panel .fetch-drow');
    await expect(rows).toHaveCount(1);
    await expect(page.locator('#price-fetch-panel .fetch-drow.selected')).toHaveCount(1);
    // qty-100 tier (0.005) auto-picked; ext = 0.005 * 50 = 0.25.
    await expect.poll(async () => Number(await page.locator('#price-unit').inputValue())).toBeCloseTo(0.005, 6);
    await expect.poll(async () => Number(await page.locator('#price-ext').inputValue())).toBeCloseTo(0.25, 6);
  });
});
