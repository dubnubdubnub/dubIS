// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadPurchaseOrder } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

// Add pololu field to existing items (empty for non-Pololu parts)
// and append a Pololu-only test item
const POLOLU_ITEM = {
  section: 'Connectors > Through Hole',
  lcsc: '',
  digikey: '',
  pololu: '1992',
  mpn: '',
  manufacturer: 'PCX',
  package: '2x20-Pin',
  description: '0.1" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack',
  qty: 11,
  unit_price: 4.49,
  ext_price: 49.39,
};

const MOCK_INVENTORY = [
  ...BASE_INVENTORY.map(item => ({ ...item, pololu: item.pololu || '' })),
  POLOLU_ITEM,
];

// A mock product response matching what PololuClient returns
const MOCK_POLOLU_PRODUCT = {
  productCode: '1992',
  title: '0.1" (2.54mm) Crimp Connector Housing: 2x20-Pin 5-Pack',
  manufacturer: 'PCX',
  mpn: '1992',
  package: '',
  description: 'Crimp connector housing for custom cables',
  stock: 222,
  prices: [
    { qty: 1, price: 4.49 },
    { qty: 5, price: 4.13 },
    { qty: 25, price: 3.80 },
    { qty: 100, price: 3.50 },
  ],
  imageUrl: 'https://a.pololu-files.com/picture/0J5817.600x480.jpg',
  pdfUrl: '',
  pololuUrl: 'https://www.pololu.com/product/1992',
  category: 'Crimp Connector Housings',
  subcategory: 'Cables and Wire',
  attributes: [],
  provider: 'pololu',
};

/** Enhanced mock setup that includes fetch_pololu_product */
function addPololuMockSetup(page, inventory) {
  return page.addInitScript((inv) => {
    window._pololuFetchCalls = [];
    window.pywebview = {
      api: {
        load_inventory: async () => inv.inventory,
        rebuild_inventory: async () => inv.inventory,
        adjust_part: async () => inv.inventory,
        update_part_price: async () => inv.inventory,
        update_part_fields: async () => inv.inventory,
        load_preferences: async () => ({ thresholds: {} }),
        save_preferences: async () => true,
        get_digikey_login_status: async () => ({ logged_in: false }),
        check_digikey_session: async () => ({ logged_in: false }),
        start_digikey_login: async () => null,
        sync_digikey_cookies: async () => ({ logged_in: false }),
        logout_digikey: async () => null,
        import_csv: async () => inv.inventory,
        remove_last_adjustments: async () => inv.inventory,
        set_bom_dirty: async () => null,
        detect_columns: async () => ({}),
        import_purchases: async () => inv.inventory,
        remove_last_purchases: async () => inv.inventory,
        open_file_dialog: async () => null,
        save_file_dialog: async () => null,
        load_file: async () => null,
        confirm_close: async () => null,
        consume_bom: async () => inv.inventory,
        fetch_pololu_product: async (sku) => {
          window._pololuFetchCalls.push(sku);
          return inv.pololuProduct;
        },
        fetch_lcsc_product: async () => null,
        fetch_digikey_product: async () => null,
      },
    };
  }, { inventory, pololuProduct: MOCK_POLOLU_PRODUCT });
}

// ── Pololu part ID display in inventory panel ──

test.describe('Pololu part ID display', () => {

  test('Pololu part shows icon and SKU in inventory row', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Find the Pololu part ID span
    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    await expect(pololuSpan).toBeVisible();
    await expect(pololuSpan).toContainText('1992');

    // Verify the vendor icon is present
    const icon = pololuSpan.locator('.vendor-icon');
    await expect(icon).toBeVisible();
    const src = await icon.getAttribute('src');
    expect(src).toContain('pololu-icon');
  });

  test('Pololu part uses correct brand color', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    const color = await pololuSpan.evaluate(el => getComputedStyle(el).color);
    // #1e2f94 = rgb(30, 47, 148)
    expect(color).toBe('rgb(30, 47, 148)');
  });

  test('Pololu-only part does not show NO DIST. PN warning', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // The row with pololu=1992 should NOT have a "no-dist-warn" button
    const pololuRow = page.locator('.inv-part-row').filter({
      has: page.locator('[data-pololu="1992"]'),
    });
    await expect(pololuRow).toBeVisible();
    await expect(pololuRow.locator('.no-dist-warn')).toHaveCount(0);
  });
});

// ── Pololu hover preview tooltip ──

test.describe('Pololu hover preview', () => {

  test('hovering Pololu SKU shows product tooltip', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Hover over the Pololu part number
    const pololuSpan = page.locator('[data-pololu="1992"]').first();
    await pololuSpan.scrollIntoViewIfNeeded();
    await pololuSpan.hover();

    // Wait for the tooltip to appear (300ms hover delay + fetch)
    const tooltip = page.locator('.part-preview:not(.hidden)');
    await expect(tooltip).toBeVisible({ timeout: 5000 });

    // Verify tooltip content
    const card = tooltip.locator('.part-preview-card');
    await expect(card).toContainText('Crimp Connector Housing');
    await expect(card).toContainText('PCX');
    await expect(card).toContainText('222 in stock');

    // Verify Pololu brand accent border
    const borderColor = await card.evaluate(el => getComputedStyle(el).borderTopColor);
    // #1e2f94 = rgb(30, 47, 148)
    expect(borderColor).toBe('rgb(30, 47, 148)');
  });

  test('tooltip shows price tiers', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const pololuSpan = page.locator('[data-pololu="1992"]').first();
    await pololuSpan.scrollIntoViewIfNeeded();
    await pololuSpan.hover();

    const tooltip = page.locator('.part-preview:not(.hidden)');
    await expect(tooltip).toBeVisible({ timeout: 5000 });

    // All 4 price tiers should be displayed
    const priceTable = tooltip.locator('.part-preview-prices');
    await expect(priceTable).toBeVisible();
    await expect(priceTable).toContainText('$4.4900');
    await expect(priceTable).toContainText('$4.1300');
    await expect(priceTable).toContainText('$3.8000');
    await expect(priceTable).toContainText('$3.5000');
  });

  test('tooltip shows "View on Pololu" link', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const pololuSpan = page.locator('[data-pololu="1992"]').first();
    await pololuSpan.scrollIntoViewIfNeeded();
    await pololuSpan.hover();

    const tooltip = page.locator('.part-preview:not(.hidden)');
    await expect(tooltip).toBeVisible({ timeout: 5000 });

    const link = tooltip.locator('a.part-preview-link', { hasText: 'View on Pololu' });
    await expect(link).toBeVisible();
    const href = await link.getAttribute('href');
    expect(href).toBe('https://www.pololu.com/product/1992');
  });

  test('tooltip shows Pololu SKU label', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const pololuSpan = page.locator('[data-pololu="1992"]').first();
    await pololuSpan.scrollIntoViewIfNeeded();
    await pololuSpan.hover();

    const tooltip = page.locator('.part-preview:not(.hidden)');
    await expect(tooltip).toBeVisible({ timeout: 5000 });

    // Info table should show "Pololu SKU" label
    await expect(tooltip.locator('.part-preview-info')).toContainText('Pololu SKU');
    await expect(tooltip.locator('.part-preview-info')).toContainText('1992');
  });
});

// ── Pololu PO template in import panel ──

test.describe('Pololu import panel', () => {

  test('Pololu PO template button exists', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const pololuBtn = page.locator('.new-po-btn[data-template="pololu"]');
    await expect(pololuBtn).toBeVisible();
    await expect(pololuBtn).toHaveText('Pololu');
  });
});

// ── Pololu field in adjust modal ──

test.describe('Pololu adjust modal', () => {

  test('adjust modal shows Pololu field', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Click Adjust on the Pololu part row
    const pololuRow = page.locator('.inv-part-row').filter({
      has: page.locator('[data-pololu="1992"]'),
    });
    await pololuRow.locator('.adj-btn').click();

    // Modal should be visible with Pololu field
    const modal = page.locator('#adjust-modal:not(.hidden)');
    await expect(modal).toBeVisible();

    const pololuInput = modal.locator('.modal-field-input[data-field="pololu"]');
    await expect(pololuInput).toBeVisible();
    const value = await pololuInput.inputValue();
    expect(value).toBe('1992');
  });
});

// ── Search includes Pololu SKU ──

test.describe('Pololu search', () => {

  test('searching by Pololu SKU finds the part', async ({ page }) => {
    await addPololuMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Type the Pololu SKU in the search box
    const searchInput = page.locator('#inv-search');
    await searchInput.click();
    await searchInput.fill('1992');

    // Wait for debounced re-render
    await page.waitForTimeout(300);

    // Should show the Pololu part
    const pololuSpan = page.locator('.part-id-pololu[data-pololu="1992"]');
    await expect(pololuSpan).toBeVisible();

    // Other parts should be filtered out (or at least our Pololu part is visible)
    const rows = page.locator('.inv-part-row');
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThan(MOCK_INVENTORY.length);
  });
});
