// @ts-check
/* prefs-presets.spec.mjs — the threshold tab's preset picker, in the real
   modal: choosing a bundle re-renders the rows at the bundle's numbers,
   re-clicking the active bundle unpins back to the shipped defaults (the
   cart's segmented-control gesture), moving one slider marks exactly that row
   overridden, and Save persists ONLY the preset id plus the moved category —
   never the resolved table. The resolution order itself is pinned on both
   sides by tests/fixtures/thresholds/legacy-defaults.json; this is the wire
   between that logic and the picker. The last two tests are geometry: the
   action row must be reachable at the app's documented minimum window and at
   the suite's narrowest one, and an overridden row must show both numbers
   there — measured without scrollIntoView, which proves nothing (see
   isReachableByUserScroll in helpers.mjs). */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, isReachableByUserScroll } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/** The number input of one category's row. */
function rowInput(page, section) {
  return page.locator(
    `#prefs-sliders .prefs-row:has(.prefs-slider[data-section="${section}"]) .prefs-input`);
}
function resetOf(page, section) {
  return page.locator(
    `#prefs-sliders .prefs-row:has(.prefs-slider[data-section="${section}"]) .prefs-reset`);
}
function inheritedOf(page, section) {
  return page.locator(
    `#prefs-sliders .prefs-row:has(.prefs-slider[data-section="${section}"]) .prefs-inherited`);
}

test('preset picker: choose, unpin, per-category override, sparse save', async ({ page }) => {
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await expect(page.locator('#prefs-modal')).toBeVisible();

  // Three segments — the shipped sentinel plus the two bundles — with the
  // shipped one active on a clean preferences file.
  const presets = page.locator('#prefs-presets .prefs-preset');
  await expect(presets).toHaveCount(3);
  await expect(presets.first()).toHaveAttribute('aria-pressed', 'true');

  // Choosing a bundle re-renders every row at what the category now answers:
  // ic-heavy moves the tuned IC name from the base to $200.
  await page.locator('.prefs-preset[data-preset="ic-heavy"]').click();
  await expect(page.locator('.prefs-preset[data-preset="ic-heavy"]'))
    .toHaveAttribute('aria-pressed', 'true');
  await expect(rowInput(page, 'ICs - Microcontrollers')).toHaveValue('200');

  // Re-clicking the active bundle unpins back to the shipped defaults — the
  // handleRowPresetChange gesture from js/cart/cart-modal.js.
  await page.locator('.prefs-preset[data-preset="ic-heavy"]').click();
  await expect(page.locator('.prefs-preset[data-preset="ic-heavy"]'))
    .toHaveAttribute('aria-pressed', 'false');
  await expect(presets.first()).toHaveAttribute('aria-pressed', 'true');
  await expect(rowInput(page, 'ICs - Microcontrollers')).toHaveValue('50');

  // Moving one slider marks exactly that row overridden — the reset control
  // is the marker, and it holds its space when inert (visibility, not
  // display), so the slider-edge alignment guard stays honest.
  await expect(resetOf(page, 'Diodes')).toHaveClass(/is-inert/);
  await expect(resetOf(page, 'Connectors')).toHaveClass(/is-inert/);
  await rowInput(page, 'Diodes').fill('17');
  await expect(resetOf(page, 'Diodes')).not.toHaveClass(/is-inert/);
  await expect(resetOf(page, 'Connectors')).toHaveClass(/is-inert/);

  // Both numbers are visible on an overridden row: the effective value in
  // the input, the inherited one in the "← $N" label hint — no hover needed.
  await expect(rowInput(page, 'Diodes')).toHaveValue('17');
  await expect(inheritedOf(page, 'Diodes')).toBeVisible();
  await expect(inheritedOf(page, 'Diodes')).toContainText('$10');
  // A row that is not overridden shows one number: its hint stays hidden.
  await expect(inheritedOf(page, 'Connectors')).toBeHidden();

  // Reset snaps the row back to the inherited value, and the marker follows.
  await resetOf(page, 'Diodes').click();
  await expect(rowInput(page, 'Diodes')).toHaveValue('10');
  await expect(resetOf(page, 'Diodes')).toHaveClass(/is-inert/);
  await expect(inheritedOf(page, 'Diodes')).toBeHidden();

  // Save persists the preset id plus ONLY the categories that differ from
  // what the preset/default/derivation answers — the resolved table never
  // travels into preferences.json.
  await rowInput(page, 'Diodes').fill('17');
  await page.click('#prefs-save');
  await expect(page.locator('#prefs-modal')).toBeHidden();

  const calls = await page.evaluate(() => window.__apiCalls['save_preferences'] || []);
  expect(calls.length, 'one whole-bag save per Save click').toBe(1);
  // The router records the PARSED body (rawBody methods arrive as the object
  // the page put on the wire).
  const prefs = /** @type {{threshold_preset: string, thresholds: Record<string, number>}} */ (
    /** @type {unknown} */ (calls[0][0]));
  expect(prefs.threshold_preset).toBe('shipped');
  expect(prefs.thresholds, 'the delta only — not the resolved table').toEqual({ Diodes: 17 });
});

/** The modal box's computed overflow-y — the scroll container every
 *  below-the-fold control in the preferences dialog depends on. */
function modalOverflowY(page) {
  return page.evaluate(() =>
    getComputedStyle(document.querySelector('#prefs-modal .prefs-modal-wide')).overflowY);
}

/**
 * Save/Cancel measured honestly. `scroll: false` is the raw rect — nothing
 * scrolled, nothing focused — which is the only measurement that can fail for
 * a layout reason; see isReachableByUserScroll in helpers.mjs for why a
 * scrollIntoView-then-measure probe (what this spec used to do) proves nothing.
 */
async function actionsReach(page, { scroll }) {
  const save = await isReachableByUserScroll(page, '#prefs-save', { scroll });
  const cancel = await isReachableByUserScroll(page, '#prefs-cancel', { scroll });
  console.log(`Prefs actions @${save.vw}x${save.vh} scroll=${scroll}: ${save.reason} | ${cancel.reason}`);
  return { save, cancel };
}

/** The modal is the scroll container for whatever exceeds its 80vh cap. If
 *  that ever becomes hidden, everything below the cap — Save included — is
 *  unreachable, and no scrollIntoView-based probe can tell. */
async function expectModalIsTheScrollContainer(page) {
  const overflowY = await modalOverflowY(page);
  expect(['auto', 'scroll', 'overlay'], `.prefs-modal-wide overflow-y=${overflowY}: not a scroll container`).toContain(overflowY);
}

test('Save/Cancel are in the viewport unscrolled at the documented minimum (1200x700)', async ({ page }) => {
  // 1200x700 is the app's documented minimum window (css/components/ui-zoom.css).
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: 1200, height: 700 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.click('#prefs-btn');
  await page.waitForSelector('#prefs-modal:not(.hidden)', { timeout: 5000 });
  await expectModalIsTheScrollContainer(page);

  const { save, cancel } = await actionsReach(page, { scroll: false });
  expect(save.inViewport, `Save outside the viewport with nothing scrolled: ${save.reason}`).toBe(true);
  expect(cancel.inViewport, `Cancel outside the viewport with nothing scrolled: ${cancel.reason}`).toBe(true);
});

test('at the narrowest size (800x600) a wheel over the modal reveals Save/Cancel, and an overridden row shows both numbers', async ({ page }) => {
  // 800x600 is the narrowest size the e2e suite uses (resize-visibility.spec).
  await installRouteMocks(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: 800, height: 600 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.click('#prefs-btn');
  await page.waitForSelector('#prefs-modal:not(.hidden)', { timeout: 5000 });
  await expectModalIsTheScrollContainer(page);

  // At 800x600 the modal's content exceeds its 80vh cap, so the action row
  // starts BELOW the fold (measured ~597-626 in a 600 viewport). What has to
  // hold is that a mouse gets you there: the modal box itself is the scroll
  // container, so this is a real wheel gesture over the modal — not
  // scrollIntoView, which would also scroll the overflow:hidden boxes no user
  // can move and would therefore pass even with the modal unscrollable.
  await actionsReach(page, { scroll: false });
  const box = await page.locator('#prefs-modal .prefs-modal-wide').boundingBox();
  // A point inside the modal but outside the nested .prefs-sliders pane, whose
  // own wheel would be eaten before it reaches the modal.
  await page.mouse.move(box.x + box.width / 2, box.y + 12);
  await page.mouse.wheel(0, 400);

  // A wheel scroll lands on the compositor, so the rect settles a frame or two
  // later — poll it instead of measuring the dispatch synchronously.
  await expect
    .poll(() => isReachableByUserScroll(page, '#prefs-save', { scroll: false }).then((r) => r.inViewport),
      { message: 'a wheel over the modal never brought Save into the viewport' })
    .toBe(true);

  const { save, cancel } = await actionsReach(page, { scroll: false });
  expect(save.inViewport, `Save outside the viewport after a wheel over the modal: ${save.reason}`).toBe(true);
  expect(cancel.inViewport, `Cancel outside the viewport after a wheel over the modal: ${cancel.reason}`).toBe(true);
  expect(save.clippedBy, `Save clipped by a non-scrollable ancestor: ${save.clippedBy}`).toEqual([]);

  // Both numbers on an overridden row at the narrowest size: the effective
  // value in the input and the inherited one in the "← $N" hint, in the
  // viewport at the same time. The row lives in the nested .prefs-sliders
  // pane, which is a genuine scroll box, so scrolling to it is legitimate —
  // the probe scrolls only ancestors a user could scroll themselves.
  await rowInput(page, 'Diodes').fill('17');
  await expect(rowInput(page, 'Diodes')).toHaveValue('17');
  await expect(inheritedOf(page, 'Diodes')).toBeVisible();
  await expect(inheritedOf(page, 'Diodes')).toContainText('$10');

  const row = '#prefs-sliders .prefs-row:has(.prefs-slider[data-section="Diodes"])';
  const hint = await isReachableByUserScroll(page, `${row} .prefs-inherited`);
  const value = await isReachableByUserScroll(page, `${row} .prefs-input`);
  console.log(`Overridden row @${hint.vw}x${hint.vh}: ${hint.reason} | ${value.reason}`);
  expect(hint.inViewport, `Inherited hint outside the viewport: ${hint.reason}`).toBe(true);
  expect(value.inViewport, `Effective value outside the viewport: ${value.reason}`).toBe(true);
});
