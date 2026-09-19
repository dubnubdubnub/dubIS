// @ts-check
/* The server quick-switcher: browser-style tabs under the header. A tab is a
   VIEW INSTANCE over one or more dubIS servers — the same server can be open
   twice, several tabs can be grouped into one merged view, tabs drag to reorder
   and close, and each keeps its own search/filter/sort state.

   Three things this file is careful about.

   **The header trap.** The strip is its own row BELOW `.header`, never inside
   it: `.header` is flex-wrap: wrap, so a control inside it costs a whole extra
   header row at narrow widths and pushes the Import button off an 800x600
   viewport — resize-visibility.spec.mjs is what catches that, and is not to be
   weakened. Two tests below pin the rest of the fix: however many tabs are open
   they stay on ONE line and scroll sideways, and the strip hides under the app's
   documented 1200x700 minimum exactly as the zoom control does.

   **The source header.** server/dispatch.py keeps no "current server"; every
   /v1 request names its own with `X-Dubis-Source`, which is what makes two
   windows on two different servers independent. `sourceHeaders()` below reads
   what actually went out on the wire, so a switch is asserted by what the next
   request asked for — not merely by which tab is highlighted.

   **Drag.** Playwright's `dragTo` drives real HTML5 drag-and-drop in Chromium,
   which is the same mechanism js/server-tabs.js uses, so the reorder test
   exercises the production path rather than a synthetic click. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import {
  installRouteMocks,
  addPersistentPrefsRouteMock,
  installSourcesRouteMocks,
  defaultSourceOnHub,
} from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const BENCH = 'https://bench.example';
const SHOP = 'https://shop.example';

/** Every `X-Dubis-Source` this page has sent, oldest first. */
let sent = [];

/** @param {import('@playwright/test').Page} page */
async function openPrefs(page) {
  await page.keyboard.press('Control+,');
  await expect(page.locator('#pref-server-list')).toBeVisible();
}

/** @param {import('@playwright/test').Page} page */
async function addServer(page, name, url) {
  await page.locator('#pref-server-new-name').fill(name);
  await page.locator('#pref-server-new-url').fill(url);
  await page.locator('#pref-server-add').click();
  await expect(page.locator('.server-row', {
    has: page.locator(`.server-name:text-is("${name}")`),
  })).toBeVisible();
}

/** Open a tab per server via the Preferences picker, then close the modal. */
async function openTabsFor(page, servers) {
  await openPrefs(page);
  for (const [name, url] of servers) await addServer(page, name, url);
  for (const [name] of servers) {
    await page.locator('.server-row', { has: page.locator(`.server-name:text-is("${name}")`) })
      .locator('[data-act="select"]').click();
    await expect(tab(page, name)).toHaveClass(/selected/);
  }
  await page.keyboard.press('Escape');
}

const tabs = (page) => page.locator('.server-tab');
function tab(page, label) {
  return page.locator('.server-tab', { has: page.locator(`.server-tab-name:text-is("${label}")`) });
}
const labels = (page) => page.locator('.server-tab-name');

/** A per-load sentinel: if it survives an action, the page never reloaded. */
const loadId = (page) => page.evaluate(() => window.__loadId);

test.beforeEach(async ({ page }) => {
  sent = [];
  page.on('request', (r) => {
    const h = r.headers()['x-dubis-source'];
    if (r.url().includes('/v1/') && h !== undefined) sent.push(h);
  });
  await installRouteMocks(page, MOCK_INVENTORY);
  await addPersistentPrefsRouteMock(page);
  await installSourcesRouteMocks(page, { reachable: { [BENCH]: true, [SHOP]: false } });
  await page.addInitScript(() => { window.__loadId = Math.random().toString(36); });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
});

// ── The strip itself ──────────────────────────────────────

test('the strip stays hidden until there is something to switch between', async ({ page }) => {
  // One tab and nowhere else to go is not a switcher, and the row would cost the
  // three panels its height forever in exchange for nothing.
  await expect(page.locator('#server-tabs')).toBeHidden();

  await openPrefs(page);
  await addServer(page, 'Bench', BENCH);
  await page.keyboard.press('Escape');
  await expect(page.locator('#server-tabs')).toBeVisible();
});

test('every /v1 request names this window’s own source', async ({ page }) => {
  // The hub holds no current-server state: a request that names none is served
  // from the saved default, which is another window's setting. This is the
  // contract in server/dispatch.py, and it is only closable from here.
  await openTabsFor(page, [['Bench', BENCH]]);
  expect(sent.length).toBeGreaterThan(0);
  expect(sent.every((v) => v && v.length)).toBe(true);
  // And after a switch, the very next request asks for the new source.
  const before = sent.length;
  await tab(page, 'Local').click();
  await expect(tab(page, 'Local')).toHaveClass(/selected/);
  await expect.poll(() => sent.length).toBeGreaterThan(before);
  expect(sent[sent.length - 1]).toBe('local');
});

// ── Opening tabs ──────────────────────────────────────────

test('+ opens another tab on the same server, so one server can be open twice', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();
  await expect(tabs(page)).toHaveCount(2);

  const before = await loadId(page);
  await page.locator('.server-tab-add').click();
  await expect(tabs(page)).toHaveCount(3);
  // Two tabs, same label, same server — which is exactly the point: a tab id is
  // not a source id.
  await expect(labels(page)).toHaveText(['Local', 'Bench', 'Bench']);
  expect(await loadId(page)).toBe(before);
  // The new one opens next to the tab it came from and takes the front.
  await expect(tabs(page).nth(2)).toHaveClass(/selected/);
});

test('each tab keeps its own search, filter and sort state', async ({ page }) => {
  // The whole reason to open one server twice. Switching captures the outgoing
  // tab's view and applies the incoming one.
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();

  const search = page.locator('#inv-search');
  await search.fill('capacitor');
  await page.locator('.server-tab-add').click();      // second Bench tab, clean view
  await expect(search).toHaveValue('');

  await search.fill('resistor');
  await tabs(page).nth(1).click();                    // back to the first Bench tab
  await expect(search).toHaveValue('capacitor');

  await tabs(page).nth(2).click();                    // and forward again
  await expect(search).toHaveValue('resistor');
});

test('a tab’s view survives a reload', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();
  await page.locator('#inv-search').fill('inductor');
  // Switch away and back so the view is captured onto the tab.
  await tab(page, 'Local').click();
  await tabs(page).nth(1).click();
  await expect(page.locator('#inv-search')).toHaveValue('inductor');

  await page.reload();
  await waitForInventoryRows(page);
  await expect(tabs(page).nth(1)).toHaveClass(/selected/);
  await expect(page.locator('#inv-search')).toHaveValue('inductor');
});

// ── Grouping ──────────────────────────────────────────────

test('ctrl-click several tabs, then group them into one merged view', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await expect(labels(page)).toHaveText(['Local', 'Bench', 'Shop']);

  await tab(page, 'Bench').click();
  await tab(page, 'Shop').click({ modifiers: ['ControlOrMeta'] });
  await expect(tab(page, 'Bench')).toHaveClass(/multi/);
  await expect(tab(page, 'Shop')).toHaveClass(/multi/);
  // The Group button only exists while a multi-selection does — it is the one
  // control that says what the selection is for.
  const group = page.locator('.server-tab-group');
  await expect(group).toHaveText('Group 2');

  const before = await loadId(page);
  await group.click();

  // A rearrangement, not a duplication: the two tabs become one.
  await expect(labels(page)).toHaveText(['Local', 'Bench + Shop']);
  await expect(tab(page, 'Bench + Shop')).toHaveClass(/selected/);
  expect(await loadId(page)).toBe(before);

  // And the merged view is exactly those two servers, named as a set.
  await expect.poll(() => sent[sent.length - 1]).toMatch(/^[^,]+,[^,]+$/);
  expect(await defaultSourceOnHub(page)).toBe(sent[sent.length - 1]);
});

test('shift-click selects a range of tabs', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await tab(page, 'Local').click();
  await tab(page, 'Shop').click({ modifiers: ['Shift'] });
  await expect(page.locator('.server-tab.multi')).toHaveCount(3);
  await expect(page.locator('.server-tab-group')).toHaveText('Group 3');
});

test('a group over every server is the old All tab, and says "merged" on the wire', async ({ page }) => {
  // "All" is not a concept any more — it is the degenerate case of a group, and
  // it still serialises to the hub's own token for "all of them" so the wire
  // format did not have to fork.
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await tab(page, 'Local').click();
  await tab(page, 'Shop').click({ modifiers: ['Shift'] });
  await page.locator('.server-tab-group').click();

  await expect(labels(page)).toHaveText(['All']);
  await expect.poll(() => defaultSourceOnHub(page)).toBe('merged');
  expect(sent[sent.length - 1]).toBe('merged');
});

test('a group warns when one of its servers is unreachable', async ({ page }) => {
  // The fan-out degrades rather than failing, so this dot is the only warning
  // that the merged view is showing less than it promises.
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);  // Shop is down
  await tab(page, 'Bench').click();
  await tab(page, 'Shop').click({ modifiers: ['ControlOrMeta'] });
  await page.locator('.server-tab-group').click();
  await expect(tab(page, 'Bench + Shop').locator('.server-tab-dot')).toHaveClass(/partial/);
});

// ── Reordering ────────────────────────────────────────────

test('drag reorders the tabs, and the order survives a reload', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await expect(labels(page)).toHaveText(['Local', 'Bench', 'Shop']);

  // Real HTML5 drag-and-drop, the same mechanism the app listens for.
  await tab(page, 'Shop').dragTo(tab(page, 'Local'));
  await expect(labels(page)).toHaveText(['Shop', 'Local', 'Bench']);

  await page.reload();
  await waitForInventoryRows(page);
  await expect(labels(page)).toHaveText(['Shop', 'Local', 'Bench']);
});

test('dragging a tab does not change which server wins a merge', async ({ page }) => {
  // The hub merges a set in ROSTER order, not the order the header names, so
  // reorder is safe by construction — this pins that the frontend does not try
  // to smuggle order through the header.
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await tab(page, 'Bench').click();
  await tab(page, 'Shop').click({ modifiers: ['ControlOrMeta'] });
  await page.locator('.server-tab-group').click();
  const before = sent[sent.length - 1];

  await tab(page, 'Bench + Shop').dragTo(tab(page, 'Local'));
  await tab(page, 'Local').click();
  await tab(page, 'Bench + Shop').click();
  await expect.poll(() => sent[sent.length - 1]).toBe(before);
});

// ── Closing ───────────────────────────────────────────────

test('a tab closes, and the last one cannot', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();
  await expect(tabs(page)).toHaveCount(2);

  await tab(page, 'Bench').locator('.server-tab-close').click();
  await expect(labels(page)).toHaveText(['Local']);
  // Closing the tab in front moves the grid onto its neighbour rather than
  // leaving the hub serving a tab that is gone.
  await expect(tab(page, 'Local')).toHaveClass(/selected/);
  await expect.poll(() => sent[sent.length - 1]).toBe('local');

  // One tab left: the grid is always showing something, so there is always a tab
  // saying what. The affordance is gone entirely rather than erroring on click.
  await expect(page.locator('.server-tab-close')).toHaveCount(0);
});

test('closing a background tab leaves the front one alone', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await tab(page, 'Shop').click();
  await tab(page, 'Bench').locator('.server-tab-close').click();
  await expect(labels(page)).toHaveText(['Local', 'Shop']);
  await expect(tab(page, 'Shop')).toHaveClass(/selected/);
});

test('removing a server closes the tabs that showed it', async ({ page }) => {
  page.on('dialog', (d) => d.accept());
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();
  await openPrefs(page);
  await page.locator('.server-row', { has: page.locator('.server-name:text-is("Bench")') })
    .locator('[data-act="remove"]').click();
  await page.keyboard.press('Escape');

  // A tab whose only source is gone cannot switch to anything, so it goes too.
  await expect(page.locator('#server-tabs')).toBeHidden();
  await expect.poll(() => defaultSourceOnHub(page)).toBe('local');
});

// ── Dots ──────────────────────────────────────────────────

test('a dot claims only what the hub actually probed', async ({ page }) => {
  await openTabsFor(page, [['Bench', BENCH], ['Shop', SHOP]]);
  await expect(tab(page, 'Bench').locator('.server-tab-dot')).toHaveClass(/live/);
  await expect(tab(page, 'Shop').locator('.server-tab-dot')).toHaveClass(/active|down/);
  await tab(page, 'Local').click();
  await expect(tab(page, 'Shop').locator('.server-tab-dot')).toHaveClass(/down/);
  await expect(tab(page, 'Local').locator('.server-tab-dot')).toHaveClass(/active/);
});

test('a server the hub never probed reads as unknown, not as down', async ({ page }) => {
  // A red dot is a claim that a server is down. "Nobody has asked yet" is not
  // evidence of that.
  await openTabsFor(page, [['Unprobed', 'https://unprobed.example']]);
  await tab(page, 'Local').click();
  const dot = tab(page, 'Unprobed').locator('.server-tab-dot');
  await expect(dot).toHaveClass(/unknown/);
  await expect(dot).not.toHaveClass(/down/);
});

// ── The header trap ───────────────────────────────────────

test('many tabs scroll sideways instead of wrapping into more rows', async ({ page }) => {
  // Wrapping here would reintroduce exactly the failure that putting the strip
  // in its own row exists to avoid, one element further down the page.
  await openTabsFor(page, [['Bench', BENCH]]);
  await tab(page, 'Bench').click();
  for (let i = 0; i < 10; i++) await page.locator('.server-tab-add').click();
  await expect(tabs(page)).toHaveCount(12);

  const strip = page.locator('#server-tabs');
  const box = await strip.boundingBox();
  const oneTab = await tabs(page).first().boundingBox();
  expect(box.height).toBeLessThan(oneTab.height * 2);

  const tops = await tabs(page).evaluateAll(
    (els) => [...new Set(els.map((el) => Math.round(el.getBoundingClientRect().top)))]);
  expect(tops).toHaveLength(1);

  const style = await strip.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { wrap: cs.flexWrap, overflowX: cs.overflowX };
  });
  expect(style.wrap).toBe('nowrap');
  expect(style.overflowX).toBe('auto');
});

test('below the documented minimum width the strip hides and Preferences takes over', async ({ page }) => {
  // The header trap, the other half of it. The app's documented minimum is
  // 1200x700; below that this row's height is the difference between the Import
  // button being on screen and off it, so the strip follows the same
  // max-width: 1199px precedent as the zoom control. The capability stays
  // reachable — the Preferences picker switches live, the same call.
  await openTabsFor(page, [['Bench', BENCH]]);
  await expect(page.locator('#server-tabs')).toBeVisible();

  await page.setViewportSize({ width: 800, height: 600 });
  await expect(page.locator('#server-tabs')).toBeHidden();
  await expect(page.locator('.zoom-control')).toBeHidden();

  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(page.locator('#server-tabs')).toBeVisible();
});

// ── One selection, two panels ─────────────────────────────

test('the Preferences picker and the tab strip are one selection', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Bench', BENCH);
  await page.locator('.server-row', { has: page.locator('.server-name:text-is("Bench")') })
    .locator('[data-act="select"]').click();
  await expect(page.locator('#pref-server-status')).toContainText(BENCH);
  await page.keyboard.press('Escape');
  await expect(tab(page, 'Bench')).toHaveClass(/selected/);

  // And back the other way.
  await tab(page, 'Local').click();
  await openPrefs(page);
  await expect(page.locator('.server-row', {
    has: page.locator('.server-name:text-is("Local")'),
  }).locator('.server-badge')).toHaveText('selected');
});
