// @ts-check
// Regression: a manual BOM link (linking a missing row to an existing inventory
// part) is an unsaved BOM change, so Python must be told via set_bom_dirty(true).
// If it isn't, api._bom_dirty stays False and the close-confirm ("Save & Close")
// modal never appears on quit — the app just exits, silently dropping the link.
import { test, expect } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BOM_CSV = join(__dirname, 'fixtures', 'bom-footprint.csv');

// A 0603 resistor that is a footprint near-miss for the BOM's 0402 R1 — clicking
// its near-miss badge enters linking mode so we can complete a manual link.
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

test.describe('Manual BOM link marks the BOM dirty', () => {
  test('linking a missing row to an inventory part calls set_bom_dirty(true)', async ({ page }) => {
    await installRouteMocks(page, INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV);

    // Enter linking mode from the near-miss inventory candidate.
    const nearMissRow = page.locator('.inv-part-row.inv-row-near-miss').first();
    await nearMissRow.locator('.near-miss-badge').click();
    await expect(nearMissRow).toHaveClass(/linking-source/);

    // Complete the link: click the highlighted link target on the missing BOM row.
    const linkTarget = page.locator('#bom-tbody tr.link-target').first();
    await expect(linkTarget).toBeVisible();
    await linkTarget.locator('td.status').click();

    // The manual link must have told Python the BOM is now dirty.
    const calls = await page.evaluate(() => window.__apiCalls?.set_bom_dirty || []);
    expect(calls.some(args => args[0] === true)).toBe(true);
  });
});
