// @ts-check
/* A KiCad BOM line whose Value column holds the MPN carries no electrical value,
   so the matcher cannot propose a substitute for it on its own. The app asks
   LCSC what the line's code is (js/bom/bom-spec-lookup.js) and re-matches —
   these specs drive that through the real bom-panel flow:

     - the line starts missing, and upgrades to "possible" on the RIGHT part
       (the 1kΩ, not the 100Ω that shares ten characters of its MPN) once the
       lookup lands;
     - that background upgrade does not force open a BOM panel the user
       collapsed (it is announced as BOM_REMATCHED, not BOM_LOADED);
     - a code LCSC does not know leaves the line missing without an error, and
       a failed lookup leaves it missing WITH a warning in the log.

   The lookup's response is held behind a gate so the pre-lookup state is
   observable, not a race. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const RES = 'Passives - Resistors > Chip Resistors';
const INVENTORY = [
  {
    section: RES, lcsc: 'C25076', digikey: '', pololu: '', mouser: '',
    mpn: '0402WGF1000TCE', manufacturer: 'UNI-ROYAL', package: '0402',
    description: '100Ω ±1% 62.5mW 0402 Thick Film Resistor', qty: 900, unit_price: 0.0005, ext_price: 0.45,
  },
  {
    section: RES, lcsc: 'C106235', digikey: '', pololu: '', mouser: '',
    mpn: 'RC0402FR-071KL', manufacturer: 'YAGEO', package: '0402',
    description: '1kΩ 62.5mW 50V Thick Film Resistor ±100ppm/℃ ±1% 0402 Chip Resistor - Surface Mount RoHS',
    qty: 400, unit_price: 0.0008, ext_price: 0.32,
  },
];

const PRODUCT_C11702 = {
  productCode: 'C11702', title: '0402WGF1001TCE', manufacturer: 'UNI-ROYAL', mpn: '0402WGF1001TCE',
  package: '0402', description: '1kΩ ±1% 62.5mW 0402 Thick Film Resistor', stock: 1000000,
  prices: [{ qty: 100, price: 0.0005 }], imageUrl: '', pdfUrl: '', lcscUrl: 'https://lcsc.com',
  category: 'Resistors', subcategory: 'Chip Resistor - Surface Mount',
  attributes: [{ name: 'Resistance', value: '1kΩ' }, { name: 'Tolerance', value: '±1%' }],
  provider: 'lcsc',
};

const BOM = [
  '"Value","Reference","Footprint","LCSC","DNP","Exclude from BOM"',
  '"0402WGF1001TCE","R1,R2","Resistor_SMD:R_0402_1005Metric","C11702","",""',
].join('\r\n') + '\r\n';

function bomFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dubis-bom-spec-'));
  const file = path.join(dir, 'kicad-value-is-mpn.csv');
  fs.writeFileSync(file, BOM);
  return file;
}

/** Hold LCSC answers for `code` until the returned release() is called. */
async function gateLookup(page, code, respond) {
  let release;
  const gate = new Promise((r) => { release = r; });
  let asked = 0;
  // Registered after installRouteMocks, so Playwright tries it first.
  await page.route(`**/v1/distributors/lcsc/product/${code}`, async (route) => {
    asked++;
    await gate;
    await respond(route);
  });
  return { release: () => release(), asked: () => asked };
}

const summary = (page) => page.locator('#bom-summary');
const widthOf = (page, id) => page.evaluate(
  (i) => document.getElementById(i).getBoundingClientRect().width, id);

async function boot(page) {
  await installRouteMocks(page, INVENTORY);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
}

test.describe('BOM spec lookup', () => {
  test('a Value=MPN line upgrades from missing to possible on the right part', async ({ page }) => {
    await boot(page);
    const lookup = await gateLookup(page, 'C11702', (route) => route.fulfill({ json: PRODUCT_C11702 }));

    await loadBomViaFileInput(page, bomFile());

    // Before the lookup: nothing about the line says it is 1kΩ, and the 100Ω
    // prefix neighbour must not be offered on MPN similarity alone.
    await expect(summary(page)).toContainText('1 missing');
    await expect(summary(page)).toContainText('0 possible');
    await expect.poll(lookup.asked).toBe(1);

    lookup.release();

    await expect(summary(page)).toContainText('1 possible');
    await expect(summary(page)).toContainText('0 missing');
    const row = page.locator('#inventory-body tr', { hasText: 'R1' }).first();
    await expect(row).toContainText('C106235');
    await expect(row).toContainText('Value');
    await expect(row).not.toContainText('C25076');
  });

  test('the background upgrade does not reopen a collapsed BOM panel', async ({ page }) => {
    await boot(page);
    const lookup = await gateLookup(page, 'C11702', (route) => route.fulfill({ json: PRODUCT_C11702 }));
    await loadBomViaFileInput(page, bomFile());
    await expect.poll(lookup.asked).toBe(1);

    await page.click('#panel-toggle-bom');
    expect(await widthOf(page, 'panel-bom')).toBe(0);

    lookup.release();

    // The upgrade landed (the inventory view shows it)...
    await expect(page.locator('#inventory-body tr', { hasText: 'R1' }).first()).toContainText('C106235');
    // ...and the panel the user closed stayed closed.
    expect(await widthOf(page, 'panel-bom')).toBe(0);
  });

  test('a code LCSC does not know leaves the line missing, without an error', async ({ page }) => {
    await boot(page);
    const lookup = await gateLookup(page, 'C11702', (route) => route.fulfill({
      status: 404, json: { error: 'Product not found: lcsc/C11702', code: 'product_not_found', detail: null },
    }));
    await loadBomViaFileInput(page, bomFile());
    lookup.release();
    await expect.poll(() => page.locator('#console-entries').textContent())
      .toContain('LCSC does not know C11702');
    await expect(summary(page)).toContainText('1 missing');
    // A not-found is an answer, not a failure: no api() error log or toast for
    // the lookup (api() would log "fetch_lcsc_product: ..." and toast it), and
    // no warning.
    await expect(page.locator('#console-entries')).not.toContainText('fetch_lcsc_product');
    await expect(page.locator('#console-entries')).not.toContainText('BOM spec lookup failed');
    await expect(page.locator('#toast')).not.toContainText('Product not found');
  });

  test('a failed lookup leaves the line missing and says so in the log', async ({ page }) => {
    await boot(page);
    const lookup = await gateLookup(page, 'C11702', (route) => route.fulfill({
      status: 502, json: { error: 'LCSC timed out', code: 'distributor_timeout', detail: null },
    }));
    await loadBomViaFileInput(page, bomFile());
    lookup.release();
    await expect.poll(() => page.locator('#console-entries').textContent())
      .toContain('BOM spec lookup failed');
    await expect(page.locator('#console-entries')).toContainText('LCSC timed out');
    await expect(summary(page)).toContainText('1 missing');
  });
});
