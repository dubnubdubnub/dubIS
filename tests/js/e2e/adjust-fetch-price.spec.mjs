// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

// ── Test inventory: single-source part, multi-source part, price-less part ──

const INVENTORY = [
  {
    section: "Passives - Capacitors > MLCC",
    lcsc: "C2040", digikey: "", pololu: "", mouser: "",
    mpn: "CL05A104KA5NNNC", manufacturer: "Samsung",
    package: "0402", description: "100nF MLCC Capacitor",
    qty: 200, unit_price: 0.0025, ext_price: 0.50,
  },
  {
    section: "Connectors > Through Hole",
    lcsc: "C555", digikey: "", pololu: "", mouser: "M-555",
    mpn: "XYZ-555", manufacturer: "Acme",
    package: "SOT-23", description: "Dual-sourced widget",
    qty: 30, unit_price: 5.00, ext_price: 150.00,
  },
  {
    // qty > 0 with no unit price → renders the ⚠ price-warning button, which
    // opens the Price modal (the warning-sign entry point for Fetch price).
    section: "Passives - Resistors > Chip Resistors",
    lcsc: "C9999", digikey: "", pololu: "", mouser: "",
    mpn: "RES-9999", manufacturer: "Yageo",
    package: "0402", description: "Priceless resistor",
    qty: 50, unit_price: 0, ext_price: 0,
  },
];

// ── Mock product data returned by fetch_*_product APIs ──

const MOCK_PRODUCTS = {
  // Single-source LCSC part. Two tiers; qty-100 tier (0.001) should be auto-picked
  // because lastPoQty["C2040"] === 100 (proves it is NOT defaulting to first tier).
  "lcsc:C2040": {
    productCode: "C2040",
    title: "100nF Ceramic Capacitor",
    manufacturer: "Samsung Electro-Mechanics",
    mpn: "CL05A104KA5NNNC",
    package: "0402",
    description: "100nF 16V X5R MLCC Capacitor",
    stock: 50000,
    prices: [
      { qty: 1, price: 0.0025 },
      { qty: 100, price: 0.001 },
    ],
    provider: "lcsc",
  },
  // Multi-source part — LCSC variant (distinct prices from Mouser).
  "lcsc:C555": {
    productCode: "C555",
    title: "Widget (LCSC listing)",
    manufacturer: "Acme",
    mpn: "XYZ-555",
    package: "SOT-23",
    description: "Dual-sourced widget — LCSC",
    stock: 1000,
    prices: [
      { qty: 1, price: 5.55 },
      { qty: 10, price: 4.44 },
    ],
    provider: "lcsc",
  },
  // Multi-source part — Mouser variant (distinct prices from LCSC).
  "mouser:M-555": {
    productCode: "M-555",
    title: "Widget (Mouser listing)",
    manufacturer: "Acme",
    mpn: "XYZ-555",
    package: "SOT-23",
    description: "Dual-sourced widget — Mouser",
    stock: 800,
    prices: [
      { qty: 1, price: 9.99 },
      { qty: 10, price: 8.00 },
    ],
    provider: "mouser",
  },
  // Price-less single-source part used to exercise the Price modal.
  "lcsc:C9999": {
    productCode: "C9999",
    title: "10k Resistor",
    manufacturer: "Yageo",
    mpn: "RES-9999",
    package: "0402",
    description: "10k 1% 0402 Resistor",
    stock: 100000,
    prices: [
      { qty: 1, price: 0.02 },
      { qty: 100, price: 0.005 },
    ],
    provider: "lcsc",
  },
};

// lastPoQty drives which tier is auto-selected.
//  - C2040 → 100 → picks the qty-100 tier (0.001)
//  - C555  → 10  → picks the qty-10 tier (8.00 mouser / 4.44 lcsc)
//  - C9999 → 100 → picks the qty-100 tier (0.005)
const LAST_PO_QTY = { C2040: 100, C555: 10, C9999: 100 };

const partRow = (page, lcsc) =>
  page.locator('.inv-part-row:has([data-lcsc="' + lcsc + '"])');

async function readNumber(locator) {
  return Number(await locator.inputValue());
}

test.describe('Fetch current price — Adjust & Price modals', () => {

  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY, {
      productMocks: MOCK_PRODUCTS,
      lastPoQty: LAST_PO_QTY,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('single-source: dropdown hidden, fetch fills unit price from PO-matched tier', async ({ page }) => {
    await partRow(page, 'C2040').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const supplier = page.locator('#adj-fetch-supplier');
    const fetchBtn = page.locator('#adj-fetch-price');
    await expect(supplier).toHaveClass(/hidden/);
    await expect(fetchBtn).toBeEnabled();

    await fetchBtn.click();

    const tiers = page.locator('#adj-fetch-tiers');
    await expect(tiers).not.toHaveClass(/hidden/);
    const rows = page.locator('#adj-fetch-tiers .fetch-tier');
    await expect(rows).toHaveCount(2);

    // The selected tier corresponds to qty 100 (matched against last PO qty 100).
    const selected = page.locator('#adj-fetch-tiers .fetch-tier.selected');
    await expect(selected).toHaveCount(1);
    await expect(selected).toHaveAttribute('data-qty', '100');

    // Unit price filled from the qty-100 tier (0.001), not the first tier (0.0025).
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(0.001, 6);

    // Ext recomputed = unit * part qty (200) = 0.20.
    expect(await readNumber(page.locator('#adj-ext-price'))).toBeCloseTo(0.2, 6);
  });

  test('multi-source: dropdown shows; selecting Mouser fetches Mouser prices', async ({ page }) => {
    await partRow(page, 'C555').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

    const supplier = page.locator('#adj-fetch-supplier');
    await expect(supplier).not.toHaveClass(/hidden/);

    // Options for both LCSC and Mouser (the part's two sources).
    const optionValues = await supplier.locator('option').evaluateAll(
      (opts) => opts.map((o) => o.value),
    );
    expect(optionValues).toEqual(['lcsc', 'mouser']);

    await supplier.selectOption('mouser');
    await page.locator('#adj-fetch-price').click();

    await expect(page.locator('#adj-fetch-tiers')).not.toHaveClass(/hidden/);

    // qty-10 Mouser tier (8.00) — not an LCSC price (4.44 / 5.55) nor the qty-1
    // Mouser price (9.99).
    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(8.0, 6);
    const selected = page.locator('#adj-fetch-tiers .fetch-tier.selected');
    expect(Number(await selected.getAttribute('data-price'))).toBeCloseTo(8.0, 4);

    // Price-history logging fired with the chosen provider and part key.
    const calls = await page.evaluate(() => window.__apiCalls['record_fetched_prices']);
    expect(Array.isArray(calls)).toBe(true);
    expect(calls.length).toBeGreaterThanOrEqual(1);
    const lastArgs = calls[calls.length - 1];
    // record_fetched_prices(pk, supplierKey, prices)
    expect(lastArgs[0]).toBe('C555');
    expect(lastArgs[1]).toBe('mouser');
  });

  test('tier-click override updates unit price and selection', async ({ page }) => {
    await partRow(page, 'C2040').locator('.adj-btn').click();
    await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
    await page.locator('#adj-fetch-price').click();

    const tiers = page.locator('#adj-fetch-tiers');
    await expect(tiers).not.toHaveClass(/hidden/);

    // Initially the qty-100 tier is selected.
    await expect(tiers.locator('.fetch-tier.selected')).toHaveAttribute('data-qty', '100');

    // Click the qty-1 tier to override.
    const qty1Row = tiers.locator('.fetch-tier[data-qty="1"]');
    await qty1Row.click();

    expect(await readNumber(page.locator('#adj-unit-price'))).toBeCloseTo(0.0025, 6);
    await expect(qty1Row).toHaveClass(/selected/);
    await expect(tiers.locator('.fetch-tier[data-qty="100"]')).not.toHaveClass(/selected/);
    await expect(tiers.locator('.fetch-tier.selected')).toHaveCount(1);
  });

  test('price modal (warning sign): fetch fills unit price from PO-matched tier', async ({ page }) => {
    // The ⚠ price-warning button on a price-less row opens the Price modal.
    await partRow(page, 'C9999').locator('.price-warn-btn').click();
    await expect(page.locator('#price-modal')).not.toHaveClass(/hidden/);

    const supplier = page.locator('#price-fetch-supplier');
    const fetchBtn = page.locator('#price-fetch-price');
    await expect(supplier).toHaveClass(/hidden/);   // single source → no dropdown
    await expect(fetchBtn).toBeEnabled();

    await fetchBtn.click();

    const tiers = page.locator('#price-fetch-tiers');
    await expect(tiers).not.toHaveClass(/hidden/);
    await expect(tiers.locator('.fetch-tier')).toHaveCount(2);
    await expect(tiers.locator('.fetch-tier.selected')).toHaveAttribute('data-qty', '100');

    // Unit price filled from the qty-100 tier (0.005), not the qty-1 tier (0.02).
    expect(await readNumber(page.locator('#price-unit'))).toBeCloseTo(0.005, 6);
    // Ext recomputed = unit * part qty (50) = 0.25.
    expect(await readNumber(page.locator('#price-ext'))).toBeCloseTo(0.25, 6);
  });
});
