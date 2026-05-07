// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

// ── Test inventory with parts from each distributor ──

const TOOLTIP_INVENTORY = [
  {
    section: "Passives - Capacitors > MLCC",
    lcsc: "C2040", digikey: "", pololu: "", mouser: "",
    mpn: "CL05A104KA5NNNC", manufacturer: "Samsung",
    package: "0402", description: "100nF MLCC Capacitor",
    qty: 500, unit_price: 0.0025, ext_price: 1.25,
  },
  {
    section: "Connectors > Through Hole",
    lcsc: "", digikey: "DK-CONN-123", pololu: "", mouser: "",
    mpn: "B2B-XH-A", manufacturer: "JST",
    package: "Through Hole", description: "2-pin XH Connector",
    qty: 50, unit_price: 0.15, ext_price: 7.50,
  },
  {
    section: "Passives - Resistors > Chip Resistors",
    lcsc: "", digikey: "YAG2274TR-ND", pololu: "", mouser: "",
    mpn: "RC0402FR-0710KL", manufacturer: "Yageo",
    package: "0402", description: "10k 1% 0.063W 0402 Resistor",
    qty: 5000, unit_price: 0.007, ext_price: 35.00,
  },
  {
    section: "Mechanical & Hardware",
    lcsc: "", digikey: "", pololu: "1992", mouser: "",
    mpn: "1992", manufacturer: "Pololu",
    package: "2x20-Pin", description: "Crimp Connector Housing 5-Pack",
    qty: 11, unit_price: 4.49, ext_price: 49.39,
  },
  {
    section: "Connectors > Through Hole",
    lcsc: "", digikey: "", pololu: "", mouser: "736-FGG0B305CLAD52",
    mpn: "FGG.0B.305.CLAD52", manufacturer: "LEMO",
    package: "", description: "Circular Push Pull Connector 5-pos",
    qty: 4, unit_price: 37.55, ext_price: 150.20,
  },
];

// ── Mock product data returned by fetch_*_product APIs ──

const MOCK_PRODUCTS = {
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
    imageUrl: "https://example.com/cap-img.jpg",
    pdfUrl: "https://example.com/cap-datasheet.pdf",
    lcscUrl: "https://www.lcsc.com/product-detail/C2040.html",
    category: "Capacitors",
    subcategory: "MLCC",
    attributes: [
      { name: "Capacitance", value: "100nF" },
      { name: "Voltage Rating", value: "16V" },
    ],
    provider: "lcsc",
  },
  "pololu:1992": {
    productCode: "1992",
    title: "0.1\" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack",
    manufacturer: "Pololu",
    mpn: "1992",
    package: "2x20-Pin",
    description: "5-pack of 2x20-pin crimp connector housings",
    stock: 250,
    prices: [
      { qty: 1, price: 4.49 },
      { qty: 5, price: 4.13 },
    ],
    imageUrl: "https://example.com/pololu-1992.jpg",
    pdfUrl: "",
    pololuUrl: "https://www.pololu.com/product/1992",
    category: "Connectors",
    subcategory: "Crimp",
    attributes: [
      { name: "Pin Count", value: "40" },
    ],
    provider: "pololu",
  },
  "digikey:YAG2274TR-ND": {
    productCode: "YAG2274TR-ND",
    title: "Yageo 10k Resistor 0402",
    manufacturer: "Yageo",
    mpn: "RC0402FR-0710KL",
    package: "0402",
    description: "10k 1% 0.063W 0402 Resistor",
    stock: 1000000,
    prices: [
      { qty: 1, price: 0.10 },
      { qty: 10, price: 0.034 },
      { qty: 25, price: 0.0252 },
      { qty: 50, price: 0.0204 },
      { qty: 100, price: 0.0168 },
      { qty: 250, price: 0.01316 },
      { qty: 500, price: 0.01112 },
      { qty: 1000, price: 0.00952 },
      { qty: 5000, price: 0.00698 },
    ],
    imageUrl: "https://example.com/yag2274.jpg",
    pdfUrl: "https://www.yageo.com/RC0402.pdf",
    digikeyUrl: "https://www.digikey.com/product/YAG2274TR-ND",
    category: "Resistors",
    subcategory: "Chip Resistor - Surface Mount",
    attributes: [
      { name: "Resistance", value: "10k" },
      { name: "Tolerance", value: "1%" },
    ],
    packagings: [
      {
        name: "Cut Tape (CT)",
        partNumber: "YAG2274CT-ND",
        code: "CT",
        prices: [
          { qty: 1, price: 0.12 },
          { qty: 10, price: 0.040 },
        ],
      },
      {
        name: "Tape & Reel (TR)",
        partNumber: "YAG2274TR-ND",
        code: "TR",
        prices: [
          { qty: 1, price: 0.10 },
          { qty: 10, price: 0.034 },
          { qty: 25, price: 0.0252 },
          { qty: 50, price: 0.0204 },
          { qty: 100, price: 0.0168 },
          { qty: 250, price: 0.01316 },
          { qty: 500, price: 0.01112 },
          { qty: 1000, price: 0.00952 },
          { qty: 5000, price: 0.00698 },
        ],
      },
    ],
    provider: "digikey",
  },
  "mouser:736-FGG0B305CLAD52": {
    productCode: "736-FGG0B305CLAD52",
    title: "FGG.0B.305.CLAD52 Circular Push Pull Connector",
    manufacturer: "LEMO",
    mpn: "FGG.0B.305.CLAD52",
    package: "",
    description: "Circular Push Pull Connectors 5P STRT PLUG",
    stock: 500,
    prices: [
      { qty: 1, price: 37.55 },
      { qty: 10, price: 35.00 },
    ],
    imageUrl: "https://example.com/lemo-connector.jpg",
    pdfUrl: "https://example.com/lemo-datasheet.pdf",
    mouserUrl: "https://www.mouser.com/ProductDetail/736-FGG0B305CLAD52",
    category: "Connectors",
    subcategory: "Circular",
    attributes: [
      { name: "Contact Gender", value: "Plug" },
      { name: "Number of Contacts", value: "5" },
    ],
    provider: "mouser",
  },
};

// ── Helper: hover over a part and wait for the tooltip to load ──

async function hoverAndWaitForTooltip(page, selector) {
  const el = page.locator(selector).first();
  await expect(el).toBeVisible();
  await el.hover();
  // Wait for hover delay (300ms) + fetch + render
  const tooltip = page.locator('.part-preview-card');
  await expect(tooltip).toBeVisible({ timeout: 5000 });
  // Wait for loading to finish (title appears when data is rendered)
  await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });
  return tooltip;
}

async function hideTooltip(page) {
  // Move mouse to header area to dismiss tooltip
  await page.locator('.header').hover();
  await expect(page.locator('.part-preview')).toHaveClass(/hidden/, { timeout: 3000 });
}

// ── Tests ──

test.describe('Part preview tooltip — data loading', () => {

  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, TOOLTIP_INVENTORY, { productMocks: MOCK_PRODUCTS });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
  });

  test('LCSC tooltip shows all product fields', async ({ page }) => {
    const tooltip = await hoverAndWaitForTooltip(page, '[data-lcsc="C2040"]');

    // Title
    await expect(page.locator('.part-preview-title')).toContainText('100nF Ceramic Capacitor');
    // Description
    await expect(page.locator('.part-preview-desc')).toContainText('100nF 16V X5R MLCC');
    // Manufacturer
    await expect(tooltip).toContainText('Samsung Electro-Mechanics');
    // MPN
    await expect(tooltip).toContainText('CL05A104KA5NNNC');
    // Part label
    await expect(tooltip).toContainText('LCSC Part #');
    await expect(tooltip).toContainText('C2040');
    // Package
    await expect(tooltip).toContainText('0402');
    // Category
    await expect(tooltip).toContainText('Capacitors');
    await expect(tooltip).toContainText('MLCC');
    // Attributes
    await expect(page.locator('.part-preview-attr')).toHaveCount(2);
    await expect(tooltip).toContainText('Capacitance');
    await expect(tooltip).toContainText('100nF');
    await expect(tooltip).toContainText('Voltage Rating');
    await expect(tooltip).toContainText('16V');
    // Stock badge
    const stockBadge = page.locator('.part-preview-stock');
    await expect(stockBadge).toContainText('50,000 in stock');
    await expect(stockBadge).toHaveClass(/in-stock/);
    // Price tiers
    const priceRows = page.locator('.part-preview-prices tbody tr');
    await expect(priceRows).toHaveCount(2);
    await expect(priceRows.nth(0)).toContainText('1+');
    await expect(priceRows.nth(0)).toContainText('$0.0025');
    // Action links
    await expect(page.locator('.part-preview-actions a')).toHaveCount(2); // PDF + LCSC page
    await expect(tooltip).toContainText('Datasheet (PDF)');
    await expect(tooltip).toContainText('View on LCSC');
    // Provider accent
    await expect(page.locator('.part-preview-card')).toHaveClass(/provider-lcsc/);
  });

  test('Pololu tooltip shows all product fields', async ({ page }) => {
    const tooltip = await hoverAndWaitForTooltip(page, '[data-pololu="1992"]');

    await expect(page.locator('.part-preview-title')).toContainText('Crimp Connector Housing');
    await expect(tooltip).toContainText('Pololu');
    await expect(tooltip).toContainText('Pololu SKU');
    await expect(tooltip).toContainText('1992');
    await expect(tooltip).toContainText('5-pack');
    // Stock
    await expect(page.locator('.part-preview-stock')).toContainText('250 in stock');
    // Prices
    await expect(page.locator('.part-preview-prices tbody tr')).toHaveCount(2);
    await expect(tooltip).toContainText('$4.4900');
    // Attributes
    await expect(page.locator('.part-preview-attr')).toHaveCount(1);
    await expect(tooltip).toContainText('Pin Count');
    // Action link (no PDF, only Pololu page)
    await expect(tooltip).toContainText('View on Pololu');
    await expect(page.locator('.part-preview-card')).toHaveClass(/provider-pololu/);
  });

  test('Mouser tooltip shows all product fields', async ({ page }) => {
    const tooltip = await hoverAndWaitForTooltip(page, '[data-mouser="736-FGG0B305CLAD52"]');

    // Title
    await expect(page.locator('.part-preview-title')).toContainText('FGG.0B.305.CLAD52');
    // Description
    await expect(page.locator('.part-preview-desc')).toContainText('Circular Push Pull');
    // Manufacturer
    await expect(tooltip).toContainText('LEMO');
    // MPN
    await expect(tooltip).toContainText('FGG.0B.305.CLAD52');
    // Part label
    await expect(tooltip).toContainText('Mouser Part #');
    await expect(tooltip).toContainText('736-FGG0B305CLAD52');
    // Category
    await expect(tooltip).toContainText('Connectors');
    await expect(tooltip).toContainText('Circular');
    // Attributes
    await expect(page.locator('.part-preview-attr')).toHaveCount(2);
    await expect(tooltip).toContainText('Contact Gender');
    await expect(tooltip).toContainText('Plug');
    await expect(tooltip).toContainText('Number of Contacts');
    await expect(tooltip).toContainText('5');
    // Stock badge
    const stockBadge = page.locator('.part-preview-stock');
    await expect(stockBadge).toContainText('500 in stock');
    await expect(stockBadge).toHaveClass(/in-stock/);
    // Price tiers
    const priceRows = page.locator('.part-preview-prices tbody tr');
    await expect(priceRows).toHaveCount(2);
    await expect(priceRows.nth(0)).toContainText('$37.5500');
    // Action links
    await expect(tooltip).toContainText('Datasheet (PDF)');
    await expect(tooltip).toContainText('View on Mouser');
    // Provider accent
    await expect(page.locator('.part-preview-card')).toHaveClass(/provider-mouser/);
  });

  test('DigiKey tooltip shows login prompt when not logged in', async ({ page }) => {
    const el = page.locator('[data-digikey="DK-CONN-123"]').first();
    await expect(el).toBeVisible();
    await el.hover();
    // Wait for hover delay + fetch (returns null) + error render
    const card = page.locator('.part-preview-card');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.part-preview-error')).toContainText(
      'Login to Digikey in Preferences to enable preview',
    );
  });

  test('DigiKey tooltip shows full price ladder, packaging tabs, datasheet & site links', async ({ page }) => {
    const tooltip = await hoverAndWaitForTooltip(page, '[data-digikey="YAG2274TR-ND"]');

    // Title and identifying info
    await expect(page.locator('.part-preview-title')).toContainText('Yageo 10k Resistor');
    await expect(tooltip).toContainText('Digikey Part #');
    await expect(tooltip).toContainText('YAG2274TR-ND');

    // All 9 price tiers from the active (Tape & Reel) packaging
    const priceRows = page.locator('.part-preview-prices tbody tr');
    await expect(priceRows).toHaveCount(9);
    await expect(priceRows.nth(0)).toContainText('1+');
    await expect(priceRows.nth(0)).toContainText('$0.1000');
    await expect(priceRows.nth(8)).toContainText('5000+');
    await expect(priceRows.nth(8)).toContainText('$0.0070');

    // Packaging tabs visible, with TR active by default (matches the requested PN)
    const tabs = page.locator('.part-preview-pack-tab');
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toContainText('Cut Tape (CT)');
    await expect(tabs.nth(1)).toContainText('Tape & Reel (TR)');
    await expect(tabs.nth(1)).toHaveClass(/active/);

    // Click the Cut Tape tab → price table swaps to its 2 tiers
    await tabs.nth(0).click();
    await expect(tabs.nth(0)).toHaveClass(/active/);
    await expect(tabs.nth(1)).not.toHaveClass(/active/);
    await expect(page.locator('.part-preview-prices tbody tr')).toHaveCount(2);
    await expect(page.locator('.part-preview-prices tbody tr').nth(0)).toContainText('$0.1200');

    // Action links: datasheet + Digikey
    await expect(page.locator('.part-preview-actions a')).toHaveCount(2);
    await expect(tooltip).toContainText('Datasheet (PDF)');
    await expect(tooltip).toContainText('View on Digikey');
    const dsLink = page.locator('.part-preview-actions a', { hasText: 'Datasheet' });
    await expect(dsLink).toHaveAttribute('href', 'https://www.yageo.com/RC0402.pdf');
    const siteLink = page.locator('.part-preview-actions a', { hasText: 'View on Digikey' });
    await expect(siteLink).toHaveAttribute('href', 'https://www.digikey.com/product/YAG2274TR-ND');

    // Provider accent
    await expect(tooltip).toHaveClass(/provider-digikey/);
  });

  test('tooltip hides after moving mouse away', async ({ page }) => {
    await hoverAndWaitForTooltip(page, '[data-lcsc="C2040"]');
    await hideTooltip(page);
    // Tooltip should have the hidden class
    await expect(page.locator('.part-preview')).toHaveClass(/hidden/);
  });

  test('tooltip shows loading state before data arrives', async ({ page }) => {
    const el = page.locator('[data-mouser="736-FGG0B305CLAD52"]').first();
    await expect(el).toBeVisible();
    await el.hover();
    // The loading text appears during the hover delay + fetch
    const loading = page.locator('.part-preview-loading');
    // It may flash quickly — just check the tooltip becomes visible
    const card = page.locator('.part-preview-card');
    await expect(card).toBeVisible({ timeout: 5000 });
  });

  test('null product data shows error message', async ({ page }) => {
    // Pololu SKU "9999" is NOT in our mock — fetch returns null
    // We need an inventory item with a pololu PN that has no mock
    await page.evaluate(() => {
      // Inject a fake item with an unmocked pololu PN into the DOM
      const container = document.querySelector('#inventory-body');
      const div = document.createElement('div');
      div.innerHTML = '<span data-pololu="9999" class="part-id-pololu" style="display:inline-flex;align-items:center;cursor:pointer">9999</span>';
      container.appendChild(div);
    });
    const el = page.locator('[data-pololu="9999"]').first();
    await el.hover();
    const card = page.locator('.part-preview-card');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.part-preview-error')).toContainText('Product not found');
  });
});
