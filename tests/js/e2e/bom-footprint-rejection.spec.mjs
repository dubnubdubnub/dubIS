// @ts-check
import { test, expect } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { addMockSetup, waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BOM_CSV = join(__dirname, 'fixtures', 'bom-footprint.csv');

// Crafted inventory: a 1kΩ resistor in 0603 (wrong footprint for BOM) and a
// 620Ω resistor in 0402 (matches BOM row R2/R5 via the new plain-number parse).
const INVENTORY = [
  {
    section: 'Passives - Resistors > Chip Resistors',
    lcsc: 'C22936', mpn: '0603WAF100KT5E',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'UNI-ROYAL', package: '0603',
    description: '1kΩ ±1% 100mW 0603 Thick Film Resistor',
    qty: 50, unit_price: 0.001, ext_price: 0.05,
  },
  {
    section: 'Passives - Resistors > Chip Resistors',
    lcsc: 'C0402-620', mpn: 'RC0402FR-07620RL',
    digikey: '', pololu: '', mouser: '',
    manufacturer: 'Yageo', package: '0402',
    description: '620Ω ±1% 62.5mW 0402 Thick Film Resistor',
    qty: 100, unit_price: 0.0005, ext_price: 0.05,
  },
];

test.describe('BOM footprint hard-rejection and resistor plain-number match', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);
  });

  test('0603 candidate is not promoted to matched, appears as near-miss in remainder list', async ({ page }) => {
    const nearMissRow = page.locator('.inv-part-row.inv-row-near-miss').first();
    await expect(nearMissRow).toBeVisible();

    const badge = nearMissRow.locator('.near-miss-badge');
    await expect(badge).toBeVisible();
    const title = await badge.getAttribute('title');
    expect(title).toContain('R1');
    expect(title).toContain('0402');
    expect(title).toContain('0603');
    expect(title).toContain('override');
  });

  test('R1 1k 0402 BOM row ends up missing (no false 0603 match)', async ({ page }) => {
    const r1Row = page.locator('tr[data-part-key]').filter({ has: page.locator('.refs-cell', { hasText: /\bR1\b/ }) }).first();
    await expect(r1Row).toBeVisible();
    await expect(r1Row).toHaveClass(/row-red/);
  });

  test('R2 / R5 620 0402 BOM row matches the 620Ω 0402 inventory part', async ({ page }) => {
    const r2Row = page.locator('tr[data-part-key]').filter({ has: page.locator('.refs-cell', { hasText: /\bR2\b/ }) }).first();
    await expect(r2Row).toBeVisible();
    await expect(r2Row).not.toHaveClass(/row-red/);
    await expect(r2Row.locator('.mono', { hasText: 'Value' })).toBeVisible();
  });

  test('clicking the near-miss badge enters linking mode', async ({ page }) => {
    const nearMissRow = page.locator('.inv-part-row.inv-row-near-miss').first();
    await nearMissRow.locator('.near-miss-badge').click();
    await expect(nearMissRow).toHaveClass(/linking-source/);
  });
});
