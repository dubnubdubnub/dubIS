// @ts-check
import { test, expect } from '@playwright/test';

const MOCK_INVENTORY = [
  { lcsc: 'C12345', digikey: '', mpn: 'RC0402FR-0710KL', manufacturer: 'Yageo', package: '0402', description: '10kOhm 1% 1/16W', qty: 100, unit_price: 0.01, ext_price: 1.0, section: 'Passives - Resistors' },
  { lcsc: 'C22345', digikey: '', mpn: 'CL05A104KA5NNNC', manufacturer: 'Samsung', package: '0402', description: '100nF 25V X5R', qty: 200, unit_price: 0.005, ext_price: 1.0, section: 'Passives - Capacitors' },
  { lcsc: 'C32345', digikey: 'DK-001', mpn: 'STM32F405RGT6', manufacturer: 'ST', package: 'LQFP-64', description: 'ARM Cortex-M4 168MHz 1MB Flash', qty: 5, unit_price: 5.0, ext_price: 25.0, section: 'ICs - Microcontrollers' },
  { lcsc: 'C42345', digikey: '', mpn: 'BLM15AG102SN1D', manufacturer: 'Murata', package: '0402', description: 'Ferrite Bead 1kOhm 300mA', qty: 50, unit_price: 0.02, ext_price: 1.0, section: 'Passives - Inductors' },
];

/**
 * Inject pywebview mock + inventory data before any app scripts run.
 */
function addMockSetup(page) {
  return page.addInitScript((inventory) => {
    // Mock pywebview API — all methods return safe defaults
    window.pywebview = {
      api: {
        load_inventory: async () => inventory,
        rebuild_inventory: async () => inventory,
        adjust_part: async () => inventory,
        update_part_price: async () => inventory,
        load_preferences: async () => ({ thresholds: {} }),
        save_preferences: async () => true,
        check_digikey_session: async () => ({ logged_in: false }),
        start_digikey_login: async () => null,
        sync_digikey_cookies: async () => ({ logged_in: false }),
        logout_digikey: async () => null,
        import_csv: async () => inventory,
        remove_last_adjustments: async () => inventory,
      },
    };
  }, MOCK_INVENTORY);
}

/** Wait for inventory rows to appear in the DOM */
async function waitForInventoryRows(page) {
  await page.waitForSelector('.inv-part-row', { timeout: 10_000 });
}

/** Log diagnostic dimensions for debugging */
async function logDimensions(page, label) {
  const dims = await page.evaluate(() => {
    const body = document.getElementById('inventory-body');
    return {
      windowWidth: window.innerWidth,
      windowHeight: window.innerHeight,
      invBodyWidth: body ? body.offsetWidth : -1,
      invBodyHeight: body ? body.offsetHeight : -1,
    };
  });
  console.log(`[${label}]`, dims);
  return dims;
}

/** Count .part-desc elements currently in the DOM */
async function countDescs(page) {
  return page.locator('.part-desc').count();
}

test.describe('Description auto-hide based on panel width', () => {

  test('narrow viewport (1200px) — descriptions hidden', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    // Allow ResizeObserver to fire
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'narrow-1200');
    const descCount = await countDescs(page);
    console.log('Desc count at 1200px:', descCount);
    expect(descCount).toBe(0);
    expect(dims.invBodyWidth).toBeLessThan(680);
  });

  test('wide viewport (1920px) — descriptions visible', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'wide-1920');
    const descCount = await countDescs(page);
    console.log('Desc count at 1920px:', descCount);
    expect(descCount).toBeGreaterThan(0);
    expect(dims.invBodyWidth).toBeGreaterThanOrEqual(680);

    // Verify first description has non-zero rendered width
    const firstDescWidth = await page.locator('.part-desc').first().evaluate(el => el.offsetWidth);
    console.log('First .part-desc width:', firstDescWidth);
    expect(firstDescWidth).toBeGreaterThan(0);
  });

  test('resize wide → narrow — descriptions disappear', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    // Confirm descs visible at wide
    let descCount = await countDescs(page);
    expect(descCount).toBeGreaterThan(0);
    await logDimensions(page, 'resize-start-wide');

    // Shrink viewport
    await page.setViewportSize({ width: 1200, height: 700 });
    // Wait for ResizeObserver + re-render
    await page.waitForTimeout(500);
    await logDimensions(page, 'resize-end-narrow');

    descCount = await countDescs(page);
    console.log('Desc count after resize to narrow:', descCount);
    expect(descCount).toBe(0);
  });

  test('resize narrow → wide — descriptions appear', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);

    // Confirm descs hidden at narrow
    let descCount = await countDescs(page);
    expect(descCount).toBe(0);
    await logDimensions(page, 'resize-start-narrow');

    // Expand viewport
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.waitForTimeout(500);
    await logDimensions(page, 'resize-end-wide');

    descCount = await countDescs(page);
    console.log('Desc count after resize to wide:', descCount);
    expect(descCount).toBeGreaterThan(0);
  });

  test('medium viewport (~1500px) — boundary check', async ({ page }) => {
    await addMockSetup(page);
    await page.setViewportSize({ width: 1500, height: 800 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await page.waitForTimeout(300);
    const dims = await logDimensions(page, 'medium-1500');
    const descCount = await countDescs(page);
    console.log('Desc count at 1500px:', descCount, ' inv-body width:', dims.invBodyWidth);

    // At 1500px the inv-body should be around 680px — verify desc visibility matches threshold
    if (dims.invBodyWidth >= 680) {
      expect(descCount).toBeGreaterThan(0);
    } else {
      expect(descCount).toBe(0);
    }
  });
});
