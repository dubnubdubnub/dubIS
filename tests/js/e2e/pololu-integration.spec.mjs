// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

// Small focused inventory with a Pololu part + a few others for search filtering
const POLOLU_INVENTORY = [
  {
    section: "Passives - Capacitors > MLCC",
    lcsc: "C2040", digikey: "", pololu: "", mouser: "",
    mpn: "CL05A104KA5NNNC", manufacturer: "Samsung",
    package: "0402", description: "100nF MLCC Capacitor",
    qty: 500, unit_price: 0.0025, ext_price: 1.25,
  },
  {
    section: "Connectors > Through Hole",
    lcsc: "", digikey: "", pololu: "1992", mouser: "",
    mpn: "", manufacturer: "PCX",
    package: "2x20-Pin", description: '0.1" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack',
    qty: 11, unit_price: 4.49, ext_price: 49.39,
  },
];

const MOCK_PRODUCTS = {
  "pololu:1992": {
    productCode: "1992",
    title: '0.1" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack',
    manufacturer: "PCX",
    mpn: "1992",
    package: "",
    description: "Crimp connector housing for custom cables",
    stock: 222,
    prices: [
      { qty: 1, price: 4.49 },
      { qty: 5, price: 4.13 },
      { qty: 25, price: 3.80 },
      { qty: 100, price: 3.50 },
    ],
    imageUrl: "https://a.pololu-files.com/picture/0J5817.600x480.jpg",
    pdfUrl: "",
    pololuUrl: "https://www.pololu.com/product/1992",
    category: "Crimp Connector Housings",
    subcategory: "Cables and Wire",
    attributes: [],
    provider: "pololu",
  },
};

test.describe('Pololu integration', () => {

  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, POLOLU_INVENTORY, { productMocks: MOCK_PRODUCTS });
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  // ── Part ID display ──

  test('Pololu part shows icon and SKU in inventory row', async ({ page }) => {
    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    await expect(pololuSpan).toBeVisible();
    await expect(pololuSpan).toContainText('1992');

    const icon = pololuSpan.locator('.vendor-icon');
    await expect(icon).toBeVisible();
    const src = await icon.getAttribute('src');
    expect(src).toContain('pololu-icon');
  });

  test('Pololu part uses correct brand color', async ({ page }) => {
    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    const color = await pololuSpan.evaluate(el => getComputedStyle(el).color);
    // #1e2f94 = rgb(30, 47, 148)
    expect(color).toBe('rgb(30, 47, 148)');
  });

  test('Pololu-only part does not show NO DIST. PN warning', async ({ page }) => {
    const pololuRow = page.locator('.inv-part-row').filter({
      has: page.locator('[data-pololu="1992"]'),
    });
    await expect(pololuRow).toBeVisible();
    await expect(pololuRow.locator('.no-dist-warn')).toHaveCount(0);
  });

  // ── Hover preview ──

  test('hovering Pololu SKU shows product tooltip with all data', async ({ page }) => {
    const pololuSpan = page.locator('[data-pololu="1992"]').first();
    await pololuSpan.hover();

    const tooltip = page.locator('.part-preview:not(.hidden)');
    await expect(tooltip).toBeVisible({ timeout: 5000 });
    // Wait for data to load
    await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });

    const card = tooltip.locator('.part-preview-card');
    await expect(card).toContainText('Crimp Connector Housing');
    await expect(card).toContainText('PCX');
    await expect(card).toContainText('222 in stock');
    await expect(card).toHaveClass(/provider-pololu/);

    // Price tiers
    await expect(card).toContainText('$4.4900');
    await expect(card).toContainText('$3.5000');

    // Pololu SKU label and link
    await expect(tooltip.locator('.part-preview-info')).toContainText('Pololu SKU');
    const link = tooltip.locator('a.part-preview-link', { hasText: 'View on Pololu' });
    await expect(link).toBeVisible();
    expect(await link.getAttribute('href')).toBe('https://www.pololu.com/product/1992');
  });

  // ── Import panel ──

  test('Pololu PO template button exists', async ({ page }) => {
    const pololuBtn = page.locator('.new-po-btn[data-template="pololu"]');
    await expect(pololuBtn).toBeVisible();
    await expect(pololuBtn).toHaveText('Pololu');
  });

  // ── Adjust modal ──

  test('adjust modal shows Pololu field with value', async ({ page }) => {
    const pololuRow = page.locator('.inv-part-row').filter({
      has: page.locator('[data-pololu="1992"]'),
    });
    await pololuRow.locator('.adj-btn').click();

    const modal = page.locator('#adjust-modal:not(.hidden)');
    await expect(modal).toBeVisible();

    const pololuInput = modal.locator('.modal-field-input[data-field="pololu"]');
    await expect(pololuInput).toBeVisible();
    expect(await pololuInput.inputValue()).toBe('1992');
  });

  // ── Search ──

  test('searching by Pololu SKU finds the part', async ({ page }) => {
    const searchInput = page.locator('#inv-search');
    await searchInput.fill('1992');
    await page.waitForTimeout(300);

    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    await expect(pololuSpan).toBeVisible();

    // Other parts should be filtered out
    const rows = page.locator('.inv-part-row');
    const count = await rows.count();
    expect(count).toBeLessThan(POLOLU_INVENTORY.length);
  });
});
