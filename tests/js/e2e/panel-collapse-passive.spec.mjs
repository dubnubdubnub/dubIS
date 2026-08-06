// @ts-check
/* Passive interactions must NEVER reopen a collapsed panel.
 *
 * The companion to panel-collapse-reopen.spec.mjs: that file proves the panels
 * come back when their content genuinely changes, this one proves they stay shut
 * when nothing did. A user who collapses the Purchase Import panel and then
 * hovers a part number to read its tooltip must keep their layout.
 *
 * Guards the CLASS, not just the reported trigger. The reported instance was an
 * LCSC part-number hover, but the shape is general: a read-only gesture kicks off
 * a background write (`record_fetched_prices` — the tooltip banks the prices it
 * just fetched), the server publishes `inventory.updated`, every client
 * re-fetches vendors + POs, and an unconditional PO_CHANGED made
 * panel-collapse.js conclude the user had changed a purchase order. Any hover
 * over any distributor's part number takes the same path, so all four are
 * exercised here.
 *
 * The one thing the harness substitutes is the server's decision to publish: the
 * `/v1/events` mock holds the stream open (exactly like a real idle SSE
 * connection) until the test releases one `inventory.updated` frame, standing in
 * for the push the hover's own mutation causes in the running app. Everything
 * downstream of that frame — sse.js, scheduleInventoryRefresh, the vendor/PO
 * re-fetch, the EventBus, panel-collapse.js — is the real code.
 */

import { test, expect } from '@playwright/test';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const INVENTORY = [
  {
    section: 'Passives - Capacitors > MLCC',
    lcsc: 'C2040', digikey: '', pololu: '', mouser: '',
    mpn: 'CL05A104KA5NNNC', manufacturer: 'Samsung', package: '0402',
    description: '100nF MLCC Capacitor', qty: 500, unit_price: 0.0025, ext_price: 1.25,
  },
  {
    section: 'Passives - Resistors > Chip Resistors',
    lcsc: '', digikey: 'YAG2274TR-ND', pololu: '', mouser: '',
    mpn: 'RC0402FR-0710KL', manufacturer: 'Yageo', package: '0402',
    description: '10k 1% 0402 Resistor', qty: 5000, unit_price: 0.007, ext_price: 35.0,
  },
  {
    section: 'Mechanical & Hardware',
    lcsc: '', digikey: '', pololu: '1992', mouser: '',
    mpn: '1992', manufacturer: 'Pololu', package: '2x20-Pin',
    description: 'Crimp Connector Housing 5-Pack', qty: 11, unit_price: 4.49, ext_price: 49.39,
  },
  {
    section: 'Connectors > Through Hole',
    lcsc: '', digikey: '', pololu: '', mouser: '736-FGG0B305CLAD52',
    mpn: 'FGG.0B.305.CLAD52', manufacturer: 'LEMO', package: '',
    description: 'Circular Push Pull Connector 5-pos', qty: 4, unit_price: 37.55, ext_price: 150.2,
  },
];

/** Every product carries prices — that is what makes the tooltip write them back. */
const product = (code, provider, urlKey) => ({
  productCode: code, title: 'Preview of ' + code, manufacturer: 'ACME', mpn: code,
  package: '0402', description: 'A part', stock: 1234,
  prices: [{ qty: 1, price: 0.01 }, { qty: 100, price: 0.005 }],
  imageUrl: '', pdfUrl: '', [urlKey]: 'https://example.com/' + code,
  category: 'Passives', attributes: [], provider,
});

const PRODUCTS = {
  'lcsc:C2040': product('C2040', 'lcsc', 'lcscUrl'),
  'digikey:YAG2274TR-ND': product('YAG2274TR-ND', 'digikey', 'digikeyUrl'),
  'pololu:1992': product('1992', 'pololu', 'pololuUrl'),
  'mouser:736-FGG0B305CLAD52': product('736-FGG0B305CLAD52', 'mouser', 'mouserUrl'),
};

/* A PO exists, so the post-refresh list is non-empty but IDENTICAL — the
   realistic shape of the bug. An empty list would reproduce it too, but a real
   user has purchase orders. */
const PURCHASE_ORDERS = [
  { po_id: 'po_1', vendor_id: 'v_unknown', purchase_date: '2026-05-31', order_number: 'PO-1' },
];

const HOVER_TARGETS = [
  { name: 'LCSC', selector: '[data-lcsc="C2040"]' },
  { name: 'DigiKey', selector: '[data-digikey="YAG2274TR-ND"]' },
  { name: 'Pololu', selector: '[data-pololu="1992"]' },
  { name: 'Mouser', selector: '[data-mouser="736-FGG0B305CLAD52"]' },
];

const widthOf = (page, id) => page.evaluate(
  (i) => document.getElementById(i).getBoundingClientRect().width, id);

/**
 * Mark the current PO array, so the test can wait for the exact moment
 * `setPurchaseOrders` runs again rather than racing it.
 *
 * Counting `list_purchase_orders` CALLS is not enough: the call is recorded when
 * the request is intercepted, while the reopen (if any) happens once the response
 * has been applied. Polling the array's identity closes that window — the setter
 * always re-assigns, whether or not it announces a change, and PO_CHANGED is
 * emitted synchronously inside it. So the instant this flips, any reopen this
 * refresh was going to cause has already happened.
 */
async function markPOs(page) {
  await page.evaluate(() => { window.__poRefBefore = window.store.purchaseOrders; });
  return () => page.evaluate(() => window.store.purchaseOrders !== window.__poRefBefore);
}

/**
 * Boot with a held-open SSE stream. Returns `pushInventoryUpdated()`, which
 * releases a single `inventory.updated` frame — the push a real server makes
 * after any inventory mutation.
 */
async function boot(page) {
  await installRouteMocks(page, INVENTORY, {
    productMocks: PRODUCTS,
    purchaseOrders: PURCHASE_ORDERS,
  });

  let release;
  const held = new Promise((resolve) => { release = resolve; });
  // Registered after installRouteMocks so it wins its own /v1/events mock
  // (Playwright matches routes last-registered-first).
  await page.route('**/v1/events', async (route) => {
    await held;
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      // `retry: 60000` keeps EventSource from reconnecting (and re-pushing)
      // inside the test after this stream ends.
      body: 'retry: 60000\nevent: inventory.updated\ndata: {"reason":"prices"}\n\n',
    });
  });

  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  return () => { release(); };
}

/** Collapse a region through its real toggle, then assert it is closed. */
async function collapse(page, region) {
  const id = region === 'console' ? 'console-toggle' : `panel-toggle-${region}`;
  await page.click('#' + id);
  if (region === 'console') {
    await expect(page.locator('#console-entries')).toBeHidden();
  } else {
    expect(await widthOf(page, `panel-${region}`)).toBe(0);
  }
}

test.describe('Passive interactions leave a collapsed panel collapsed', () => {
  for (const target of HOVER_TARGETS) {
    test(`hovering a ${target.name} part number does not reopen the import panel`,
      async ({ page }) => {
        const pushInventoryUpdated = await boot(page);
        await collapse(page, 'import');
        await collapse(page, 'bom');

        // A real hover — the tooltip's own fetch + price write-back follows.
        await page.locator(target.selector).first().hover();
        await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });

        // Prove the passive gesture really did issue a mutation: without this
        // the rest of the test could pass for the wrong reason.
        await expect.poll(
          () => page.evaluate(() => (window.__apiCalls?.record_fetched_prices || []).length),
          { message: 'the tooltip must have banked its fetched prices — otherwise this '
            + 'test is not exercising the chain it claims to' })
          .toBeGreaterThan(0);

        // ...and the push that mutation causes server-side.
        const posReapplied = await markPOs(page);
        pushInventoryUpdated();
        await expect.poll(posReapplied,
          { message: 'the SSE push must have triggered the vendor/PO re-fetch' })
          .toBe(true);

        // Nothing about reading a tooltip changed a PO, a BOM, or a link.
        expect(await widthOf(page, 'panel-import'),
          'a hover must not reopen the panel the user collapsed').toBe(0);
        expect(await widthOf(page, 'panel-bom'),
          'a hover must not reopen the BOM panel either').toBe(0);
        await expect(page.locator('#panel-toggle-import'))
          .toHaveAttribute('aria-expanded', 'false');
      });
  }

  test('the collapse survives a page reload — no reopen was persisted', async ({ page }) => {
    // handleTrigger() persists whatever it opens, so a spurious reopen would
    // outlive the session and quietly discard the user's layout for good.
    const pushInventoryUpdated = await boot(page);
    await collapse(page, 'import');

    await page.locator('[data-lcsc="C2040"]').first().hover();
    await expect(page.locator('.part-preview-title')).toBeVisible({ timeout: 5000 });
    const posReapplied = await markPOs(page);
    pushInventoryUpdated();
    await expect.poll(posReapplied).toBe(true);

    const saved = await page.evaluate(
      () => (window.__apiCalls?.save_preferences || []).at(-1));
    expect(JSON.stringify(saved || ''),
      'the last preference write must not have flipped import back open')
      .not.toContain('"import":false');
  });

  test('a genuine PO change still reopens it — the fix did not deafen the panel',
    async ({ page }) => {
      // The positive control for the whole file: silence on no-op refreshes must
      // not have cost the reopen that makes collapsing safe in the first place.
      const pushInventoryUpdated = await boot(page);
      await collapse(page, 'import');

      pushInventoryUpdated();
      await page.evaluate(() => window.EventBus.emit(window.Events.PO_CHANGED, [
        { po_id: 'po_2', vendor_id: 'v_unknown' },
      ]));

      await expect.poll(() => widthOf(page, 'panel-import'),
        { message: 'a real PO change must still bring the panel back' })
        .toBeGreaterThan(100);
    });
});
