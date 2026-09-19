// @ts-check
/* The merged ("All") inventory view: one row per part, summed across servers,
   with its provenance on the row.

   What this file is really guarding is the failure mode a merged view has and a
   single-server view does not: EVERY wrong answer here looks exactly like a
   right one. A summed row is a plausible number. A row whose description came
   from one server and whose package came from another is a plausible row. A
   view that is missing a whole machine's stock because that machine was asleep
   is a plausible inventory — the fan-out degrades rather than failing
   (server/fanout.py) and answers 200 either way. So the assertions below are
   mostly about things being SAID out loud.

   The merged payload is served by a `page.route` registered after
   installRouteMocks, which therefore wins (Playwright matches last-registered
   first). Nothing about the merge itself is under test here — that is
   tests/python/test_federation.py's job; this is about what the browser does
   with a merged answer. */

import { test, expect } from '@playwright/test';
import { waitForInventoryRows } from './helpers.mjs';
import {
  installRouteMocks,
  addPersistentPrefsRouteMock,
  installSourcesRouteMocks,
} from './route-mocks.mjs';

const BENCH = 'https://bench.example';
const SHOP = 'https://shop.example';

/** The roster the hub would have, seeded straight into the persisted prefs. */
const SERVERS = [
  { id: 'bench', name: 'bench', url: BENCH },
  { id: 'shop', name: 'shop', url: SHOP },
];

/** One part, on the bench only. */
const ONLY_BENCH = {
  section: 'Connectors', lcsc: 'C1000', mpn: 'MPN-ONE', digikey: '', pololu: '', mouser: '',
  manufacturer: 'Acme', package: 'SMD', description: 'bench only', qty: 850,
  unit_price: 0.1, ext_price: 85, primary_vendor_id: 'v_unknown', po_history: [],
  sources: [{ id: 'bench', name: 'bench', qty: 850 }],
  conflicts: [],
};

/** One part, stocked on BOTH servers, whose descriptions disagree. */
const SPLIT = {
  section: 'Connectors', lcsc: 'C2000', mpn: 'MPN-TWO', digikey: '', pololu: '', mouser: '',
  manufacturer: 'Acme', package: 'SMD', description: 'the bench spelling', qty: 1250,
  unit_price: 0.2, ext_price: 250, primary_vendor_id: 'v_unknown', po_history: [],
  sources: [
    { id: 'bench', name: 'bench', qty: 850 },
    { id: 'shop', name: 'shop', qty: 400 },
  ],
  conflicts: ['description', 'mpn'],
};

/** One part, in the shop only. */
const ONLY_SHOP = {
  section: 'Connectors', lcsc: 'C3000', mpn: 'MPN-THREE', digikey: '', pololu: '', mouser: '',
  manufacturer: 'Acme', package: 'SMD', description: 'shop only', qty: 40,
  unit_price: 0.5, ext_price: 20, primary_vendor_id: 'v_unknown', po_history: [],
  sources: [{ id: 'shop', name: 'shop', qty: 40 }],
  conflicts: [],
};

const MERGED_ROWS = [ONLY_BENCH, SPLIT, ONLY_SHOP];

/** Every source answered. */
const ALL_OK = [
  { id: 'bench', name: 'bench', ok: true, error: '', status: 200 },
  { id: 'shop', name: 'shop', ok: true, error: '', status: 200 },
];

/**
 * Open the app on a merged view.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{rows?: Array<any>, sources?: Array<any>, active?: string,
 *           servers?: Array<any>}} [opts]
 * @returns {Promise<{adjusts: Array<{key: string, source: string|null, body: any}>}>}
 *   every adjust the page issued, with the `X-Dubis-Source` it carried.
 */
async function openMerged(page, opts = {}) {
  const rows = opts.rows || MERGED_ROWS;
  const sources = opts.sources || ALL_OK;
  const active = opts.active || 'merged';
  const servers = opts.servers || SERVERS;

  await installRouteMocks(page, rows);
  await addPersistentPrefsRouteMock(page);
  await installSourcesRouteMocks(page, {
    active,
    reachable: { [BENCH]: true, [SHOP]: true },
  });

  // Seed the persisted preferences directly: the roster is what makes the hub's
  // sources nameable, and a merged view is only reachable once there is more
  // than one server to merge.
  await page.addInitScript((seed) => {
    window.sessionStorage.setItem('__test_prefs_inv_view', JSON.stringify({
      thresholds: {}, servers: seed.servers, active_source: seed.active, server_url: '',
    }));
  }, { servers, active });

  /** @type {Array<{key: string, source: string|null, body: any}>} */
  const adjusts = [];
  // Registered BEFORE the parts route below but after installRouteMocks, so it
  // still beats the catch-all; the two patterns do not overlap (`**/v1/parts`
  // matches that path exactly).
  await page.route('**/v1/parts/*/adjust', async (route) => {
    const req = route.request();
    const key = decodeURIComponent(req.url().split('/v1/parts/')[1].split('/')[0]);
    adjusts.push({
      key,
      source: req.headers()['x-dubis-source'] ?? null,
      body: JSON.parse(req.postData() || '{}'),
    });
    await route.fulfill({ json: { ok: true, detail: { part_key: key } } });
  });

  // The merged answer: rows PLUS the per-source status, which is the half
  // `api()`'s unwrap would throw away.
  await page.route('**/v1/parts', async (route) => {
    await route.fulfill({ json: { inventory: rows, sources } });
  });

  await page.goto('/index.html');
  await waitForInventoryRows(page);
  return { adjusts };
}

/** @param {import('@playwright/test').Page} page */
function rowOf(page, lcsc) {
  return page.locator(`.inv-part-row[data-part-id="${lcsc}"]`);
}

// ── The badge ─────────────────────────────────────────────

test('a single-source row names its server; a split row counts them', async ({ page }) => {
  await openMerged(page);

  // Named, because with one server that IS the whole answer — a count would
  // make the reader click to learn nothing.
  await expect(rowOf(page, 'C1000').locator('.inv-source-badge')).toHaveText('bench');
  await expect(rowOf(page, 'C3000').locator('.inv-source-badge')).toHaveText('shop');

  // Counted, and expandable: the split quantities are what someone reading a
  // summed row actually wants, and they do not fit in the Group cell.
  const split = rowOf(page, 'C2000').locator('.inv-source-badge');
  await expect(split).toContainText('2 servers');
  await expect(split).toHaveAttribute('aria-expanded', 'false');

  // The badge sits in the existing Group cell beside the section chip — a badge,
  // not a sixth column that five hand-maintained lists would have to agree on.
  await expect(rowOf(page, 'C1000').locator('.inv-row-group-cell .inv-source-badge')).toBeVisible();
});

test('the summed qty is the sum of the breakdown', async ({ page }) => {
  await openMerged(page);
  await expect(rowOf(page, 'C2000').locator('.part-qty')).toHaveText('1250');
});

// ── The breakdown ─────────────────────────────────────────

test('the split row expands into a per-source breakdown', async ({ page }) => {
  await openMerged(page);
  await expect(page.locator('.inv-source-breakdown')).toHaveCount(0);

  await rowOf(page, 'C2000').locator('.inv-source-badge').click();

  const breakdown = page.locator('.inv-source-breakdown[data-part-key="C2000"]');
  await expect(breakdown).toBeVisible();
  await expect(breakdown.locator('.inv-source-line')).toHaveCount(2);
  await expect(breakdown).toContainText('bench');
  await expect(breakdown).toContainText('850');
  await expect(breakdown).toContainText('shop');
  await expect(breakdown).toContainText('400');
  await expect(rowOf(page, 'C2000').locator('.inv-source-badge'))
    .toHaveAttribute('aria-expanded', 'true');

  // It is a SIBLING of the row, not a child: .inv-part-row is overflow:hidden
  // with a max-height, so a nested breakdown would be clipped to nothing.
  const isSibling = await page.evaluate(() => {
    const row = document.querySelector('.inv-part-row[data-part-id="C2000"]');
    return row.nextElementSibling?.classList.contains('inv-source-breakdown');
  });
  expect(isSibling).toBe(true);

  // And it closes again.
  await rowOf(page, 'C2000').locator('.inv-source-badge').click();
  await expect(page.locator('.inv-source-breakdown')).toHaveCount(0);
});

test('only a multi-source row offers the expander', async ({ page }) => {
  await openMerged(page);
  // The single-source badge is a label, not a control.
  await expect(rowOf(page, 'C1000').locator('button.inv-source-badge')).toHaveCount(0);
  await expect(rowOf(page, 'C2000').locator('button.inv-source-badge')).toHaveCount(1);
});

// ── Conflicts ─────────────────────────────────────────────

test('a field two servers disagreed on is marked, not silently resolved', async ({ page }) => {
  await openMerged(page);

  const chip = rowOf(page, 'C2000').locator('.inv-conflict-chip');
  await expect(chip).toBeVisible();
  // The tooltip admits which value is on screen. The merge already picked one
  // (first non-empty in source order); the UI's job is to say that it did.
  await expect(chip).toHaveAttribute('title', /disagree on description, mpn/);
  await expect(chip).toHaveAttribute('title', /bench/);

  // Traceable to the CELL, not just to the row. (The description cell is only
  // rendered when the panel is wide enough — `hideDescs` — so the MPN is the
  // one that can be asserted at any viewport.)
  await expect(rowOf(page, 'C2000').locator('.part-mpn')).toHaveAttribute('data-conflict', '1');
  await expect(rowOf(page, 'C1000').locator('.part-mpn')).not.toHaveAttribute('data-conflict', '1');

  // A clean row says nothing.
  await expect(rowOf(page, 'C1000').locator('.inv-conflict-chip')).toHaveCount(0);
});

// ── A view that is missing a server ───────────────────────

test('nothing claims the view is partial while every source answered', async ({ page }) => {
  await openMerged(page);
  await expect(page.locator('#inv-source-banner')).toBeHidden();
});

test('a source that did not answer is named, because the totals are incomplete', async ({ page }) => {
  // The response is still a 200 full of plausible numbers — 850 instead of 1250
  // — and this banner is the only thing on screen that says otherwise.
  await openMerged(page, {
    rows: [{ ...SPLIT, qty: 850, sources: [{ id: 'bench', name: 'bench', qty: 850 }] }],
    sources: [
      { id: 'bench', name: 'bench', ok: true, error: '', status: 200 },
      { id: 'shop', name: 'shop', ok: false, error: 'ConnectError: refused', status: null },
    ],
  });

  const banner = page.locator('#inv-source-banner');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('shop');
  await expect(banner).toContainText('incomplete');
  await expect(banner).toHaveAttribute('title', /ConnectError/);
});

// ── Writes route to the owning server ─────────────────────

test('an adjustment on a single-source row goes to the server holding the stock', async ({ page }) => {
  const { adjusts } = await openMerged(page);

  await rowOf(page, 'C3000').locator('.adj-btn').click();
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
  // One server, so there is nothing to ask — it says which one and gets on
  // with it.
  await expect(page.locator('#adjust-modal .adj-source-select')).toHaveCount(0);
  await expect(page.locator('#modal-detail-table')).toContainText('shop');

  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill('41');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);

  await expect.poll(() => adjusts.length).toBe(1);
  expect(adjusts[0].key).toBe('C3000');
  expect(adjusts[0].source).toBe('shop');
});

test('a split row PROMPTS for the target instead of guessing one', async ({ page }) => {
  const { adjusts } = await openMerged(page);

  await rowOf(page, 'C2000').locator('.adj-btn').click();
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);

  const chooser = page.locator('#adjust-modal .adj-source-select');
  await expect(chooser).toBeVisible();
  await expect(chooser).toHaveValue('');           // nothing preselected
  await expect(chooser.locator('option')).toHaveCount(3); // placeholder + two servers

  // Applying without choosing must send NOTHING. Picking the bigger share, or
  // the first source, would move stock on a machine the user was not thinking
  // about — and the hub refuses a merged write for exactly that reason.
  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill('900');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#toast')).toContainText('split across');
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
  expect(adjusts).toHaveLength(0);

  // Once told, it lands on exactly that server.
  await chooser.selectOption('shop');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);

  await expect.poll(() => adjusts.length).toBe(1);
  expect(adjusts[0].key).toBe('C2000');
  expect(adjusts[0].source).toBe('shop');
});

test('a single-server view writes to the window\u2019s own server, unrouted', async ({ page }) => {
  // The other half of the routing contract: nothing changes for the view the
  // app shows almost all of the time. No provenance on the rows, no per-row
  // routing on the write — just the window's own `X-Dubis-Source`, which every
  // request carries (js/api.js) so that two windows on two servers stay
  // independent.
  const plain = [{ ...ONLY_BENCH, sources: undefined, conflicts: undefined }];
  const { adjusts } = await openMerged(page, { rows: plain, sources: [], active: 'local', servers: [] });

  await expect(page.locator('.inv-source-badge')).toHaveCount(0);
  await expect(page.locator('#inv-source-banner')).toBeHidden();

  await rowOf(page, 'C1000').locator('.adj-btn').click();
  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill('7');
  await page.locator('#adj-apply').click();

  await expect.poll(() => adjusts.length).toBe(1);
  expect(adjusts[0].source).toBe('local');
});

test('a degraded view will not let a write be taken at face value', async ({ page }) => {
  // The row below names bench alone and reads 850 — byte-for-byte what a
  // genuinely bench-only part looks like. The shop is asleep, so it may hold
  // 400 more, and the row cannot tell. Adjusting it from a flat "Server: bench"
  // would apply a number the user worked out from an incomplete total to a
  // server they were never asked about, so the chooser opens anyway.
  const { adjusts } = await openMerged(page, {
    rows: [{ ...SPLIT, qty: 850, sources: [{ id: 'bench', name: 'bench', qty: 850 }] }],
    sources: [
      { id: 'bench', name: 'bench', ok: true, error: '', status: 200 },
      { id: 'shop', name: 'shop', ok: false, error: 'ConnectError: refused', status: null },
    ],
  });

  await rowOf(page, 'C2000').locator('.adj-btn').click();
  const chooser = page.locator('#adjust-modal .adj-source-select');
  await expect(chooser).toBeVisible();
  await expect(chooser).toHaveValue('');
  // And it says why it is asking, naming the server that went missing.
  await expect(page.locator('#adjust-modal .adj-source-warn')).toContainText('shop');

  await page.locator('#adj-type').selectOption('set');
  await page.locator('#adj-qty').fill('900');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#toast')).toContainText('did not answer');
  await expect(page.locator('#adjust-modal')).not.toHaveClass(/hidden/);
  expect(adjusts).toHaveLength(0);

  // An explicit choice is still allowed: the user has made the decision.
  await chooser.selectOption('bench');
  await page.locator('#adj-apply').click();
  await expect(page.locator('#adjust-modal')).toHaveClass(/hidden/);
  await expect.poll(() => adjusts.length).toBe(1);
  expect(adjusts[0].source).toBe('bench');
});

// ── Filtering ─────────────────────────────────────────────

test('search matches the name of the server holding a part', async ({ page }) => {
  await openMerged(page);
  await page.locator('#inv-search').fill('shop');
  // C3000 is in the shop; C2000 is split across both.
  await expect(page.locator('.inv-part-row')).toHaveCount(2);
  await expect(rowOf(page, 'C1000')).toHaveCount(0);
});
