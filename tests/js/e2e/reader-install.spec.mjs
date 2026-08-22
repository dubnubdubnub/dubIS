// @ts-check
//
// Picture/PDF reader E2E: the in-zone notice that points at the setting, and the
// setting itself — mode select, Install with a polled progress bar, Uninstall
// behind a confirm. Realistic interactions only (real .click(), real keyboard;
// no dispatchEvent, no force).
//
// Replaces tesseract-install.spec.mjs. That affordance was Windows-only: off
// Windows the click was a no-op that toasted a winget command (see problem 2 in
// docs/plans/2026-08-21-cross-platform-reader-design.md). Its three
// notice-visibility cases are ported here unchanged in intent, because
// `ocr_engine_available` still decides whether the notice shows.
//
// The reader methods live on the pywebview client shell, not /v1 (the local
// reader installs on the *client* machine, and in remote-backend mode there is
// no local /v1). They are stubbed in helpers.mjs's addMockSetup, and the install
// is stepped by the spec via window.__reader.advance() rather than by wall clock,
// so no assertion races the panel's poll timer.

import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks, assertHttpExercised } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

const READER_DIR = '/fake/dubis-data/reader';
const GIB_4_7 = 5046586573;

const NOT_INSTALLED = {
  installed: false, path: READER_DIR, bytes_total: 0, file_count: 0,
  server_running: false, endpoint: '', job_id: '',
};

const INSTALLED = {
  installed: true, path: READER_DIR, bytes_total: GIB_4_7, file_count: 3,
  server_running: true, endpoint: 'http://127.0.0.1:8081', job_id: '',
};

/** The phases the panel has to render differently, in backend key spelling. */
const DETECT = {
  phase: 'detect', message: 'Checking memory', bytes_done: 0, bytes_total: null,
  pct: null, indeterminate: true, done: false, error: null, tier: '',
  install_dir: READER_DIR,
};
const WEIGHTS_HALF = {
  phase: 'weights', message: 'Downloading the model', bytes_done: GIB_4_7 / 2,
  bytes_total: GIB_4_7, pct: 50.0, indeterminate: false, done: false,
  error: null, tier: 'qwen2.5-vl-7b', install_dir: READER_DIR,
};
const START_PHASE = {
  phase: 'start', message: 'Waiting for /health', bytes_done: GIB_4_7,
  bytes_total: GIB_4_7, pct: 100.0, indeterminate: true, done: false,
  error: null, tier: 'qwen2.5-vl-7b', install_dir: READER_DIR,
};
const DONE = {
  phase: 'done', message: 'Ready', bytes_done: GIB_4_7, bytes_total: GIB_4_7,
  pct: 100.0, indeterminate: false, done: true, error: null,
  tier: 'qwen2.5-vl-7b', endpoint: 'http://127.0.0.1:8081', install_dir: READER_DIR,
};
const FAILED = {
  phase: 'error', message: 'Download failed', bytes_done: 1024,
  bytes_total: GIB_4_7, pct: null, indeterminate: false, done: true,
  error: 'sha256 mismatch for Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf',
  tier: 'qwen2.5-vl-7b', install_dir: READER_DIR,
};

/** Boot the app with the given mock options and open Preferences. */
async function openPrefs(page, options) {
  const routeState = await installRouteMocks(page, MOCK_INVENTORY, options);
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.keyboard.press('Control+,');
  await expect(page.locator('#reader-prefs')).toBeVisible();
  return routeState;
}

/** Step the scripted install to its next status. */
const advance = (page) => page.evaluate(() => window.__reader.advance());

/** How many status polls the panel has made so far. */
const pollCount = (page) => page.evaluate(() => window.__reader.polls);

/** How many times start_reader_install has been called. */
const startCount = (page) =>
  page.evaluate(() => (window.__apiCalls.start_reader_install || []).length);

// ── The in-zone notice ──────────────────────────────────────────────────────

test.describe('import-zone notice', () => {
  test('no reader: the notice points at the setting, not at winget', async ({ page }) => {
    const routeState = await installRouteMocks(page, MOCK_INVENTORY, { ocrEngineAvailable: false });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const notice = page.locator('#ocr-engine-missing');
    await expect(notice).toBeVisible();
    await expect(page.locator('#open-reader-prefs-btn')).toBeVisible();
    await expect(notice).toContainText('Picture/PDF reader');
    // The retired framing must be gone from the notice entirely.
    await expect(notice).not.toContainText(/tesseract/i);
    await expect(notice).not.toContainText(/winget/i);
    await expect(notice.locator('code')).toHaveCount(0);
    // Canary: proves the real HTTP envelope ({"available": false}) was exercised
    // through ocr_engine_available's unwrap, not a bridge fallback.
    await assertHttpExercised(routeState);
  });

  test('reader present: no notice renders', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { ocrEngineAvailable: true });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#import-ocr-zone')).toBeVisible();
    await expect(page.locator('#ocr-engine-missing')).toHaveCount(0);
  });

  test('inconclusive check: no false notice renders', async ({ page }) => {
    // Regression carried over from the tesseract affordance: the check runs during
    // startup and api() swallows a failure to undefined. The notice must only
    // render on a *definitive* absence, or it reappears on every launch.
    await installRouteMocks(page, MOCK_INVENTORY, { ocrEngineCheckThrows: true });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await expect(page.locator('#import-ocr-zone')).toBeVisible();
    await expect(page.locator('#ocr-engine-missing')).toHaveCount(0);
  });

  test('the notice button opens Preferences at the reader block', async ({ page }) => {
    await installRouteMocks(page, MOCK_INVENTORY, { ocrEngineAvailable: false });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#open-reader-prefs-btn').click();
    await expect(page.locator('#prefs-modal')).toBeVisible();
    const block = page.locator('#reader-prefs');
    await expect(block).toBeVisible();
    await expect(block.locator('#reader-install-btn')).toBeVisible();
  });
});

// ── The setting itself ─────────────────────────────────────────────────────

test.describe('reader mode preference', () => {
  test('offers exactly the four modes and persists a change with no restart', async ({ page }) => {
    await openPrefs(page, {});

    const select = page.locator('#pref-reader-mode');
    await expect(select.locator('option')).toHaveCount(4);
    expect(await select.locator('option').evaluateAll(os => os.map(o => o.getAttribute('value'))))
      .toEqual(['off', 'local', 'remote', 'auto']);
    await expect(select).toHaveValue('off');

    await select.selectOption('auto');
    // The reader is chosen per import, so nothing here may ask for a restart —
    // that is the Server block's contract, not this one's.
    await expect(page.locator('#reader-prefs')).not.toContainText(/restart to apply/i);
    await expect(page.locator('#reader-prefs [id*="restart"]')).toHaveCount(0);

    // Close and reopen: the choice came back from the store.
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+,');
    await expect(page.locator('#pref-reader-mode')).toHaveValue('auto');
  });

  test('the address field only applies to remote and auto', async ({ page }) => {
    await openPrefs(page, {});
    const url = page.locator('#pref-reader-url');
    const select = page.locator('#pref-reader-mode');

    await expect(url).toBeDisabled();               // off
    await select.selectOption('local');
    await expect(url).toBeDisabled();
    await select.selectOption('remote');
    await expect(url).toBeEnabled();
    await select.selectOption('auto');
    await expect(url).toBeEnabled();
  });

  test('an address with no scheme is refused rather than left looking saved', async ({ page }) => {
    await openPrefs(page, {});
    await page.locator('#pref-reader-mode').selectOption('remote');
    const url = page.locator('#pref-reader-url');

    // Tab, not Enter: the address commits on `change` (blur or Enter), and
    // Enter inside the Preferences modal is Modal()'s submit shortcut — it would
    // save-and-close the whole dialog before the field's own handler mattered.
    await url.fill('y740:8080');
    await url.press('Tab');
    await expect(page.locator('.toast')).toContainText('http://');
    await expect(url).toHaveValue('');

    await url.fill('http://y740:8080');
    await url.press('Tab');
    await expect(url).toHaveValue('http://y740:8080');
    // Blank hands remote mode back to fleet discovery, so it must stay typable.
    await url.fill('');
    await url.press('Tab');
    await expect(url).toHaveValue('');
  });
});

// ── Install ────────────────────────────────────────────────────────────────

test.describe('Install', () => {
  test('an indeterminate phase shows a busy bar and NO percentage', async ({ page }) => {
    await openPrefs(page, { readerInstallStatuses: [DETECT, WEIGHTS_HALF] });

    await page.locator('#reader-install-btn').click();

    const bar = page.locator('#reader-progress');
    await expect(page.locator('#reader-progress-wrap')).toBeVisible();
    await expect(bar).toHaveClass(/is-indeterminate/);
    await expect(page.locator('#reader-progress-pct')).toHaveText('');
    await expect(page.locator('#reader-status-line')).toContainText('Checking what this computer can run');

    // The bar must not be parked at the start: the CSS gives the fill a real
    // width and travels it, so a "0%" reading here would be the bug.
    const geom = await bar.evaluate((el) => {
      const fill = el.querySelector('.reader-progress-fill');
      return { track: el.clientWidth, fill: fill.getBoundingClientRect().width };
    });
    expect(geom.track).toBeGreaterThan(0);
    expect(geom.fill).toBeGreaterThan(0);
    expect(geom.fill).toBeLessThan(geom.track);
    // No numeric value is claimed for a length nobody knows.
    await expect(bar).not.toHaveAttribute('aria-valuenow', /.*/);
  });

  test('a byte-counted phase shows the bar, the percentage and the bytes', async ({ page }) => {
    await openPrefs(page, { readerInstallStatuses: [DETECT, WEIGHTS_HALF] });
    await page.locator('#reader-install-btn').click();
    await advance(page);

    const bar = page.locator('#reader-progress');
    await expect(page.locator('#reader-progress-pct')).toHaveText('50%');
    await expect(bar).not.toHaveClass(/is-indeterminate/);
    await expect(bar).toHaveAttribute('aria-valuenow', '50');
    await expect(page.locator('#reader-status-line')).toContainText('2.4 GiB of 4.7 GiB');
    await expect(page.locator('#reader-status-line')).toContainText('(50%)');

    const geom = await bar.evaluate((el) => {
      const fill = el.querySelector('.reader-progress-fill');
      return { track: el.clientWidth, fill: fill.getBoundingClientRect().width };
    });
    expect(geom.fill / geom.track).toBeGreaterThan(0.4);
    expect(geom.fill / geom.track).toBeLessThan(0.6);
  });

  test('a late indeterminate phase drops the percentage again', async ({ page }) => {
    // `start` and `verify` run after every byte is downloaded, so a byte-derived
    // percentage reads 100 while the job is still working. The bar has to go back
    // to busy rather than sit at a finished-looking 100%.
    await openPrefs(page, { readerInstallStatuses: [DETECT, WEIGHTS_HALF, START_PHASE] });
    await page.locator('#reader-install-btn').click();
    await advance(page);
    await expect(page.locator('#reader-progress-pct')).toHaveText('50%');

    await advance(page);
    await expect(page.locator('#reader-progress')).toHaveClass(/is-indeterminate/);
    await expect(page.locator('#reader-progress-pct')).toHaveText('');
    await expect(page.locator('#reader-status-line')).toContainText('Starting the reader');
  });

  test('done: polling stops, the reader reads as installed, Uninstall appears', async ({ page }) => {
    await openPrefs(page, {
      readerInstallStatuses: [DETECT, WEIGHTS_HALF, DONE],
      readerStatusAfterInstall: INSTALLED,
    });
    await page.locator('#reader-install-btn').click();
    await expect(page.locator('#reader-install-btn')).toBeDisabled();

    await advance(page);
    await advance(page);
    await expect(page.locator('#reader-status-line')).toHaveClass(/is-done/);
    await expect(page.locator('.toast')).toContainText('ready');
    await expect(page.locator('#reader-install-btn')).toBeEnabled();
    await expect(page.locator('#reader-uninstall-btn')).toBeVisible();
    await expect(page.locator('#reader-local-status')).toContainText('4.7 GiB');

    // The timer is gone: no further polls after the terminal status.
    const settled = await pollCount(page);
    await page.waitForTimeout(2500);
    expect(await pollCount(page)).toBe(settled);
  });

  test('error: the reason is shown, the bar reads failed, Install can be retried', async ({ page }) => {
    await openPrefs(page, { readerInstallStatuses: [DETECT, FAILED] });
    await page.locator('#reader-install-btn').click();
    await advance(page);

    await expect(page.locator('#reader-progress')).toHaveClass(/is-failed/);
    await expect(page.locator('#reader-status-line')).toContainText('sha256 mismatch');
    await expect(page.locator('#reader-install-btn')).toBeEnabled();

    const settled = await pollCount(page);
    await page.waitForTimeout(2500);
    expect(await pollCount(page)).toBe(settled);
  });
});

// ── Closing Preferences mid-install ────────────────────────────────────────

test('closing Preferences mid-install stops polling; reopening re-attaches', async ({ page }) => {
  await openPrefs(page, {
    readerInstallStatuses: [DETECT, WEIGHTS_HALF, DONE],
    readerStatusAfterInstall: INSTALLED,
  });
  await page.locator('#reader-install-btn').click();
  await advance(page);
  await expect(page.locator('#reader-progress-pct')).toHaveText('50%');

  // Escape closes the modal. The download keeps going in the backend, but this
  // window must stop asking about it.
  await page.keyboard.press('Escape');
  await expect(page.locator('#prefs-modal')).toBeHidden();
  const atClose = await pollCount(page);
  await page.waitForTimeout(2500);
  expect(await pollCount(page), 'a closed panel must not keep polling')
    .toBe(atClose);

  // Reopen: the same job is picked back up, and no second multi-GiB download is
  // started (start_install is single-flight, but the panel must not even ask).
  await page.keyboard.press('Control+,');
  await expect(page.locator('#reader-progress-pct')).toHaveText('50%');
  await expect.poll(() => pollCount(page)).toBeGreaterThan(atClose);
  expect(await startCount(page)).toBe(1);

  const jobIds = await page.evaluate(() =>
    (window.__apiCalls.get_reader_install_status || []).map(a => a[0]));
  expect(new Set(jobIds).size, 'every poll must name the same job').toBe(1);

  await advance(page);
  await expect(page.locator('#reader-status-line')).toHaveClass(/is-done/);
});

// ── Uninstall ──────────────────────────────────────────────────────────────

test.describe('Uninstall', () => {
  test('hidden when nothing is installed on this computer', async ({ page }) => {
    await openPrefs(page, { readerStatus: NOT_INSTALLED });
    await expect(page.locator('#reader-uninstall-btn')).toBeHidden();
    await expect(page.locator('#reader-local-status')).toContainText('not installed');
  });

  test('confirms with the directory and the reclaimed bytes, and deletes nothing when declined',
    async ({ page }) => {
      await openPrefs(page, { readerStatus: INSTALLED });

      /** @type {string} */
      let message = '';
      page.once('dialog', async (d) => { message = d.message(); await d.dismiss(); });
      await page.locator('#reader-uninstall-btn').click();

      await expect.poll(() => message).not.toBe('');
      expect(message).toContain(READER_DIR);
      expect(message).toContain('4.7 GiB');
      expect(message).toContain('3 files');
      // Local-only: the operator must be told a remote reader survives this.
      expect(message).toMatch(/another machine/);
      expect(message).not.toMatch(/null|undefined|NaN/);

      const calls = await page.evaluate(() => (window.__apiCalls.uninstall_reader || []).length);
      expect(calls, 'a declined confirm must not delete anything').toBe(0);
      await expect(page.locator('#reader-uninstall-btn')).toBeVisible();
    });

  test('accepting deletes and reports the space freed', async ({ page }) => {
    await openPrefs(page, { readerStatus: INSTALLED });

    page.once('dialog', async (d) => { await d.accept(); });
    await page.locator('#reader-uninstall-btn').click();

    await expect(page.locator('.toast')).toContainText('4.7 GiB');
    await expect(page.locator('#reader-uninstall-btn')).toBeHidden();
    await expect(page.locator('#reader-local-status')).toContainText('not installed');
    // Uninstall is local-only: the mode preference is untouched, so `remote`
    // and `auto` keep working.
    await expect(page.locator('#pref-reader-mode')).toHaveValue('off');
    expect(await page.evaluate(() => (window.__apiCalls.uninstall_reader || []).length)).toBe(1);
  });

  test('remote mode survives an uninstall of the local reader', async ({ page }) => {
    await openPrefs(page, { readerStatus: INSTALLED });
    await page.locator('#pref-reader-mode').selectOption('remote');
    await page.locator('#pref-reader-url').fill('http://y740:8080');
    await page.locator('#pref-reader-url').press('Tab');

    page.once('dialog', async (d) => { await d.accept(); });
    await page.locator('#reader-uninstall-btn').click();
    await expect(page.locator('#reader-uninstall-btn')).toBeHidden();

    await expect(page.locator('#pref-reader-mode')).toHaveValue('remote');
    await expect(page.locator('#pref-reader-url')).toHaveValue('http://y740:8080');
  });
});
