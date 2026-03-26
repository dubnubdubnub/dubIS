// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);

/** Inject mock product-fetch APIs so the tooltip can render content. */
async function addProductMocks(page) {
  await page.addInitScript(() => {
    const MOCK_LCSC_PRODUCT = {
      productCode: 'C429942',
      title: 'DF40C-30DP-0.4V(51) Connector',
      manufacturer: 'HRS (Hirose)',
      mpn: 'DF40C-30DP-0.4V(51)',
      package: 'SMD,P=0.4mm',
      description: 'Board to Board Connector Header 30 position 0.4mm Pitch Surface Mount',
      stock: 15000,
      prices: [
        { qty: 1, price: 0.4123 },
        { qty: 10, price: 0.3856 },
        { qty: 100, price: 0.2856 },
      ],
      imageUrl: '',
      pdfUrl: '',
      lcscUrl: 'https://www.lcsc.com/product-detail/C429942.html',
      category: 'Connectors',
      subcategory: 'Board to Board Connectors',
      attributes: [
        { name: 'Pitch', value: '0.4mm' },
        { name: 'Positions', value: '30' },
      ],
      provider: 'lcsc',
    };
    window.pywebview.api.fetch_lcsc_product = async (code) => {
      if (code === 'C429942') return MOCK_LCSC_PRODUCT;
      return null;
    };
    window.pywebview.api.fetch_digikey_product = async () => null;
    window.pywebview.api.fetch_pololu_product = async () => null;
    window.pywebview.api.get_digikey_login_status = async () => ({ logged_in: false });
  });
}

/** Open the page and hover over the LCSC trigger to show the tooltip. */
async function showTooltip(page) {
  await addMockSetup(page, MOCK_INVENTORY);
  await addProductMocks(page);
  await page.setViewportSize({ width: 1920, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(300);

  const trigger = page.locator('[data-lcsc="C429942"]').first();
  await trigger.hover();
  await page.locator('.part-preview-title').waitFor({ state: 'visible', timeout: 5000 });
}

/** Click-drag across the tooltip title to select text. */
async function selectTitleText(page) {
  const titleBox = await page.locator('.part-preview-title').boundingBox();
  const startX = titleBox.x + 5;
  const y = titleBox.y + titleBox.height / 2;
  const endX = titleBox.x + titleBox.width - 5;

  await page.mouse.move(startX, y);
  await page.mouse.down();
  await page.mouse.move(endX, y, { steps: 10 });
  await page.mouse.up();
}

test.describe('Tooltip text selection', () => {

  test('tooltip shows product data when hovering LCSC part number', async ({ page }) => {
    await showTooltip(page);

    const tooltip = page.locator('.part-preview');
    await expect(tooltip).not.toHaveClass(/hidden/);
    await expect(page.locator('.part-preview-title')).toContainText('DF40C-30DP');
  });

  test('tooltip card has user-select: text computed style', async ({ page }) => {
    await showTooltip(page);

    const userSelect = await page.locator('.part-preview-card').evaluate(el => {
      return window.getComputedStyle(el).userSelect;
    });
    expect(userSelect).toBe('text');
  });

  test('text inside tooltip can be selected by click-and-drag', async ({ page }) => {
    await showTooltip(page);
    await selectTitleText(page);

    const selectedText = await page.evaluate(() => {
      const sel = window.getSelection();
      return sel ? sel.toString().trim() : '';
    });
    expect(selectedText.length).toBeGreaterThan(0);
    expect(selectedText).toContain('DF40C');
  });

  test('tooltip remains visible during active text selection', async ({ page }) => {
    await showTooltip(page);

    const titleBox = await page.locator('.part-preview-title').boundingBox();
    await page.mouse.move(titleBox.x + 5, titleBox.y + titleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(titleBox.x + titleBox.width - 5, titleBox.y + titleBox.height / 2, { steps: 10 });

    // Mouse still held down — tooltip must be visible
    await expect(page.locator('.part-preview')).not.toHaveClass(/hidden/);

    await page.mouse.up();

    // After release with selection present, tooltip stays visible
    await page.waitForTimeout(300);
    await expect(page.locator('.part-preview')).not.toHaveClass(/hidden/);
  });

  test('tooltip stays visible with active selection after mouse leaves', async ({ page }) => {
    await showTooltip(page);
    await selectTitleText(page);

    // Confirm selection exists
    const hasSelection = await page.evaluate(() => {
      const sel = window.getSelection();
      return sel && !sel.isCollapsed && sel.toString().trim().length > 0;
    });
    expect(hasSelection).toBe(true);

    // Move mouse outside the tooltip
    await page.mouse.move(10, 10);
    await page.waitForTimeout(400);

    // Tooltip should still be visible because of active selection
    await expect(page.locator('.part-preview')).not.toHaveClass(/hidden/);
  });

  test('drag selection starting inside tooltip and ending outside keeps tooltip visible', async ({ page }) => {
    await showTooltip(page);

    const titleBox = await page.locator('.part-preview-title').boundingBox();
    const tooltipBox = await page.locator('.part-preview').boundingBox();

    // Start inside, drag well outside
    await page.mouse.move(titleBox.x + 5, titleBox.y + titleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      tooltipBox.x + tooltipBox.width + 100,
      tooltipBox.y + tooltipBox.height + 100,
      { steps: 20 }
    );

    // Mouse held down outside — tooltip still visible
    await expect(page.locator('.part-preview')).not.toHaveClass(/hidden/);

    await page.mouse.up();

    // Selection started inside tooltip, so hasSelectionInTooltip() should be true
    await page.waitForTimeout(300);
    await expect(page.locator('.part-preview')).not.toHaveClass(/hidden/);
  });

  test('tooltip hides after selection is cleared and mouse is outside', async ({ page }) => {
    await showTooltip(page);
    await selectTitleText(page);

    // Click outside to clear selection and trigger hide
    await page.mouse.click(10, 10);
    await page.waitForTimeout(400);

    await expect(page.locator('.part-preview')).toHaveClass(/hidden/);
  });
});
