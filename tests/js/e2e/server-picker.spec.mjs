// @ts-check
/* The server picker in Preferences: the roster persists, each row's dot
   reflects a real cross-origin /v1/health probe, and selecting a row moves the
   selection without applying it until a restart.

   The probe is a genuine cross-origin fetch, so the fulfilled responses below
   carry `Access-Control-Allow-Origin` exactly as server/routes/meta.py does.
   Note what this suite CANNOT prove, though: Playwright fulfills a route below
   the browser's CORS check, so a fulfilled response is readable with or
   without that header and removing it here would not turn a dot red. That the
   header is actually served is pinned in
   tests/python/server/test_health_cors.py instead. */

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks, addPersistentPrefsRouteMock } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const ALIVE = 'https://alive.example';
const DEAD = 'https://dead.example';
const FOREIGN = 'https://foreign.example';
// The sessionStorage key addPersistentPrefsRouteMock persists prefs under.
const PREFS_STORAGE_KEY = '__test_prefs_inv_view';

/**
 * Stand in for three candidate servers: one healthy dubIS, one that refuses
 * the connection, and one that answers 200 with something that is not dubIS.
 * Registered after installRouteMocks so these win over its `**\/v1/health`
 * catch-all (Playwright matches last-registered first).
 * @param {import('@playwright/test').Page} page
 */
async function installServerProbes(page) {
  await page.route(`${ALIVE}/v1/health*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({ ok: true }),
    });
  });
  await page.route(`${FOREIGN}/v1/health*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({ server: 'nginx' }),
    });
  });
  await page.route(`${DEAD}/v1/health*`, async (route) => {
    await route.abort('connectionrefused');
  });
}

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
}

function row(page, name) {
  return page.locator('.server-row', { has: page.locator(`.server-name:text-is("${name}")`) });
}

test.beforeEach(async ({ page }) => {
  await installRouteMocks(page, MOCK_INVENTORY);
  await addPersistentPrefsRouteMock(page);
  await installServerProbes(page);
  await page.goto('/index.html');
  await waitForInventoryRows(page);
});

test('the local row is always present, selected by default, and undeletable', async ({ page }) => {
  await openPrefs(page);
  const rows = page.locator('.server-row');
  await expect(rows).toHaveCount(1);
  const local = rows.first();
  await expect(local.locator('.server-name')).toHaveText('Local');
  await expect(local.locator('.server-badge')).toHaveText('selected');
  // No Edit/× on Local: it is the fallback, so removing it would leave the
  // picker with no way back to a working configuration.
  await expect(local.locator('[data-act="remove"]')).toHaveCount(0);
  await expect(local.locator('[data-act="edit"]')).toHaveCount(0);
  // Served from loopback, so a local server demonstrably answered this page.
  await expect(local.locator('.server-dot')).toHaveClass(/active/);
});

test('a probe turns the dot green, red, or yellow by what actually answered', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await addServer(page, 'Bench', DEAD);
  await addServer(page, 'Router', FOREIGN);

  await expect(row(page, 'Cluster').locator('.server-dot')).toHaveClass(/live/);
  await expect(row(page, 'Bench').locator('.server-dot')).toHaveClass(/down/);
  // 200, but not dubIS's health payload — selecting it would restart into a
  // broken app, so it must not read the same as a working server.
  await expect(row(page, 'Router').locator('.server-dot')).toHaveClass(/foreign/);

  await expect(row(page, 'Cluster').locator('.server-detail')).toHaveText(/ms$/);
  await expect(row(page, 'Bench').locator('.server-detail')).toHaveText('unreachable');
  await expect(row(page, 'Router').locator('.server-detail')).toHaveText('not dubIS');
});

test('as many servers as you want, each persisting across a reopen', async ({ page }) => {
  await openPrefs(page);
  for (let i = 1; i <= 6; i++) {
    await addServer(page, `Server ${i}`, `${ALIVE.replace('//', `//s${i}.`)}`);
  }
  await expect(page.locator('.server-row')).toHaveCount(7); // 6 + Local

  await page.keyboard.press('Escape');
  await openPrefs(page);
  await expect(page.locator('.server-row')).toHaveCount(7);
  await expect(row(page, 'Server 6')).toBeVisible();

  // The roster is a scroll region, so a long list cannot push the restart
  // button out of the modal.
  const list = page.locator('#pref-server-list');
  const box = await list.boundingBox();
  const maxH = await list.evaluate((el) =>
    parseFloat(getComputedStyle(el).getPropertyValue('max-height')));
  expect(box.height).toBeLessThanOrEqual(maxH + 1);
  await expect(page.locator('#pref-restart')).toBeVisible();
});

test('a URL without a scheme is refused and nothing is added', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Bad', 'alive.example');
  await expect(page.locator('.server-row')).toHaveCount(1);
  await expect(page.locator('#toast')).toContainText(/http/);
});

test('a duplicate URL is refused, naming the entry that already has it', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  // Trailing slash: normalization has to run before the comparison, or this
  // becomes a second row selecting the very same server.
  await addServer(page, 'Cluster again', `${ALIVE}/`);
  await expect(page.locator('.server-row')).toHaveCount(2);
  await expect(page.locator('#toast')).toContainText('Cluster');
});

test('a blank name falls back to the host', async ({ page }) => {
  await openPrefs(page);
  await page.locator('#pref-server-new-url').fill(ALIVE);
  await page.locator('#pref-server-add').click();
  await expect(row(page, 'alive.example')).toBeVisible();
});

test('selecting a server moves the badge and marks the change pending', async ({ page }) => {
  // Dialogs are auto-dismissed by Playwright, so the restart offer is declined
  // — which is the path this test wants: the selection is saved, and nothing
  // is applied until the user restarts.
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await expect(page.locator('#pref-server-status')).toContainText('active');

  await row(page, 'Cluster').locator('[data-act="select"]').click();
  await expect(row(page, 'Cluster').locator('.server-badge')).toHaveText('selected');
  await expect(row(page, 'Local').locator('.server-badge')).toHaveCount(0);
  await expect(row(page, 'Local').locator('[data-act="select"]')).toBeVisible();

  // Still served from localhost, so the choice is saved but not yet in effect.
  await expect(page.locator('#pref-server-status')).toContainText('pending restart');
  await expect(page.locator('#pref-restart')).toHaveClass(/pending/);
  await expect(page.locator('#toast')).toContainText(/restart/i);
});

test('a selection survives closing and reopening the modal', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await row(page, 'Cluster').locator('[data-act="select"]').click();
  await page.keyboard.press('Escape');
  await openPrefs(page);
  await expect(row(page, 'Cluster').locator('.server-badge')).toHaveText('selected');
  await expect(page.locator('#pref-server-status')).toContainText('pending restart');
});

test('removing the selected server falls the selection back to local', async ({ page }) => {
  page.on('dialog', (d) => d.accept());
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await row(page, 'Cluster').locator('[data-act="select"]').click();
  await row(page, 'Cluster').locator('[data-act="remove"]').click();

  await expect(page.locator('.server-row')).toHaveCount(1);
  // Otherwise the app would keep connecting to an entry that is no longer in
  // the list, and the picker would show it back as an unlisted row.
  await expect(row(page, 'Local').locator('.server-badge')).toHaveText('selected');
  await expect(page.locator('#pref-server-status')).toContainText('active');
});

test('declining the remove confirmation keeps the server', async ({ page }) => {
  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  // Auto-dismissed -> cancel.
  await row(page, 'Cluster').locator('[data-act="remove"]').click();
  await expect(row(page, 'Cluster')).toBeVisible();
});

test('a server_url nobody added still shows as the selected row', async ({ page }) => {
  // DUBIS_URL outranks the preference and preferences.json can be hand-edited,
  // so the server actually in use must never be invisible in the picker — the
  // alternative is a list that shows Local as the choice while the app talks
  // to something else.
  // Seeded the way this state actually arises: a preferences file carrying a
  // server_url that no roster entry matches (a hand edit, or a DUBIS_URL
  // launch). Written into the prefs mock's store before the app loads.
  await page.addInitScript(({ key, prefs }) => {
    window.sessionStorage.setItem(key, JSON.stringify(prefs));
  }, { key: PREFS_STORAGE_KEY, prefs: { thresholds: {}, server_url: ALIVE } });
  await page.reload();
  await waitForInventoryRows(page);

  await openPrefs(page);
  const rows = page.locator('.server-row');
  await expect(rows).toHaveCount(2);
  await expect(row(page, 'alive.example').locator('.server-badge')).toHaveText('selected');
  await expect(row(page, 'Local').locator('.server-badge')).toHaveCount(0);
  // Nothing to remove or select — it is not a roster entry. The way to keep it
  // is to add it.
  await expect(row(page, 'alive.example').locator('[data-act="remove"]')).toHaveCount(0);
  await expect(row(page, 'alive.example').locator('[data-act="select"]')).toHaveCount(0);
  await expect(page.locator('#pref-server-status')).toContainText('pending restart');
});

test('the dots keep re-checking while the modal is open', async ({ page }) => {
  // The dot claims to be live, so a server that goes down while the picker is
  // open has to turn red without the user reopening anything.
  let healthy = true;
  await page.unroute(`${ALIVE}/v1/health*`);
  await page.route(`${ALIVE}/v1/health*`, async (route) => {
    if (!healthy) {
      await route.abort('connectionrefused');
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({ ok: true }),
    });
  });

  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await expect(row(page, 'Cluster').locator('.server-dot')).toHaveClass(/live/);

  healthy = false;
  await expect(row(page, 'Cluster').locator('.server-dot')).toHaveClass(/down/, { timeout: 15000 });
});

test('closing the modal stops the probing', async ({ page }) => {
  // A hidden modal has nothing to show, and this app is long-lived — a poll
  // that outlives its UI is a request every 8s forever.
  let probes = 0;
  await page.unroute(`${ALIVE}/v1/health*`);
  await page.route(`${ALIVE}/v1/health*`, async (route) => {
    probes += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({ ok: true }),
    });
  });

  await openPrefs(page);
  await addServer(page, 'Cluster', ALIVE);
  await expect(row(page, 'Cluster').locator('.server-dot')).toHaveClass(/live/);
  await page.keyboard.press('Escape');

  const afterClose = probes;
  await page.waitForTimeout(10_000);
  expect(probes).toBe(afterClose);
});
