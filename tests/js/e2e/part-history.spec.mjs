// @ts-check
/**
 * E2E tests for the per-part adjustment History section in the part-preview tooltip.
 *
 * The tooltip appears after a real hover over the [data-lcsc] chip in an inventory row.
 * get_part_history is mocked via helpers.mjs's partHistory option.
 */
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const INVENTORY = [
  {
    section: 'Passives - Capacitors > MLCC',
    lcsc: 'C2040', digikey: '', pololu: '', mouser: '',
    mpn: 'CL05A104KA5NNNC', manufacturer: 'Samsung',
    package: '0402', description: '100nF MLCC Capacitor',
    qty: 200, unit_price: 0.0025, ext_price: 0.50,
    primary_vendor_id: '', po_history: [],
  },
  {
    section: 'Passives - Resistors > Chip Resistors',
    lcsc: 'C17024', digikey: '', pololu: '', mouser: '',
    mpn: 'RC0402FR-0710KL', manufacturer: 'Yageo',
    package: '0402', description: '10k 1% Resistor',
    qty: 500, unit_price: 0.005, ext_price: 2.50,
    primary_vendor_id: '', po_history: [],
  },
];

const PART_HISTORY = {
  C2040: [
    { timestamp: '2024-03-01T09:00:00', kind: 'add', qty_delta: 100, source: 'import', note: 'initial stock' },
    { timestamp: '2024-04-15T14:30:00', kind: 'consume', qty_delta: -50, source: 'openpnp', note: 'board run' },
    { timestamp: '2024-05-10T11:00:00', kind: 'set', qty_delta: 200, source: 'manual', note: '' },
  ],
};

// Product mocks — the tooltip only fetches history after a successful product load.
const PRODUCT_MOCKS = {
  'lcsc:C2040': {
    productCode: 'C2040',
    title: '100nF MLCC Capacitor',
    manufacturer: 'Samsung',
    mpn: 'CL05A104KA5NNNC',
    package: '0402',
    description: '100nF 16V X5R',
    stock: 50000,
    prices: [{ qty: 1, price: 0.0025 }],
    provider: 'lcsc',
  },
  'lcsc:C17024': {
    productCode: 'C17024',
    title: '10k 1% Resistor',
    manufacturer: 'Yageo',
    mpn: 'RC0402FR-0710KL',
    package: '0402',
    description: '10k 1% 0402',
    stock: 100000,
    prices: [{ qty: 1, price: 0.005 }],
    provider: 'lcsc',
  },
};

test.describe('Part preview — History section', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY, { partHistory: PART_HISTORY, productMocks: PRODUCT_MOCKS });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    // Brief settle so the app is fully ready before hovering.
    await page.waitForTimeout(300);
  });

  test('History section renders mocked entries on hover over LCSC chip', async ({ page }) => {
    // Locate the LCSC chip for C2040 — a [data-lcsc] span inside the part row.
    const lcscChip = page.locator('[data-lcsc="C2040"]').first();
    await expect(lcscChip).toBeVisible();

    // Hover to trigger the tooltip (part-preview.js schedules show after 300ms delay).
    await lcscChip.hover();

    // Wait for the tooltip card to appear and the product title to render
    // (confirms the product fetch succeeded, which is required before history is fetched).
    const tooltipCard = page.locator('.part-preview-card');
    await expect(tooltipCard).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });

    // The History section is appended by appendPartHistory() after get_part_history resolves.
    const historySection = page.locator('.part-preview-adj-history');
    await expect(historySection).toBeVisible({ timeout: 5000 });

    // Header label
    await expect(historySection.locator('.part-preview-history-title')).toHaveText('History');

    // Three mocked entries should appear (all three fit within the 8-row display cap).
    const rows = historySection.locator('table tbody tr');
    await expect(rows).toHaveCount(3);

    // Verify each row's date, source, and delta text in document order
    // (entries are returned sorted ascending by timestamp from the backend).
    await expect(rows.nth(0)).toContainText('2024-03-01');
    await expect(rows.nth(0)).toContainText('import');
    await expect(rows.nth(0)).toContainText('+100');

    await expect(rows.nth(1)).toContainText('2024-04-15');
    await expect(rows.nth(1)).toContainText('openpnp');
    await expect(rows.nth(1)).toContainText('-50');

    await expect(rows.nth(2)).toContainText('2024-05-10');
    await expect(rows.nth(2)).toContainText('manual');
    // "set" kind renders as "→200"
    await expect(rows.nth(2)).toContainText('→200');
  });

  test('History section shows no-history message when part has no adjustments', async ({ page }) => {
    // C17024 has no partHistory entries (empty array returned by mock).
    const lcscChip = page.locator('[data-lcsc="C17024"]').first();
    await expect(lcscChip).toBeVisible();

    await lcscChip.hover();

    // Wait for the product to render.
    const tooltipCard = page.locator('.part-preview-card');
    await expect(tooltipCard).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });

    // History section still appears (with the "no adjustments" placeholder).
    const historySection = page.locator('.part-preview-adj-history');
    await expect(historySection).toBeVisible({ timeout: 5000 });

    await expect(historySection.locator('.part-preview-no-history')).toHaveText('No adjustments recorded');
  });
});
