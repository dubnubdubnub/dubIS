// @ts-check
/* Pixel-truth guard for the preferences publish: saving a threshold slider must
   repaint the grid, on its own, with nothing else fetching.

   The bug this exists for was invisible to every unit test that existed before
   it. `preferencesSignal.set(preferences)` passed the object the writer had just
   mutated in place, js/signals.js's identity guard called that "no change", and
   the grid's subscriber — the one thing that would have re-rendered — never ran.
   A user moved a category slider, pressed Save, saw nothing, and switched server
   tabs, at which point the grid updated "by itself".

   So this asserts the two halves of the symptom together: the row's colour moves
   after Save, and the page never reloaded or re-fetched to make it happen. */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

/** The first rendered part row's identity and the colour baked into its qty span. */
function firstRowColour(page) {
  return page.evaluate(() => {
    const row = document.querySelector('#inventory-body .inv-part-row');
    if (!row) throw new Error('no inventory row rendered');
    const qty = /** @type {HTMLElement} */ (row.querySelector('.part-qty'));
    if (!qty) throw new Error('row has no .part-qty');
    return { partId: row.dataset.partId, color: getComputedStyle(qty).color };
  });
}

/** Set every category slider in the open Preferences modal to `value` and Save. */
async function saveAllThresholds(page, value) {
  const inputs = page.locator('#prefs-sliders .prefs-input');
  const count = await inputs.count();
  expect(count, 'Preferences renders a slider per category').toBeGreaterThan(0);
  for (let i = 0; i < count; i++) await inputs.nth(i).fill(String(value));
  await page.click('#prefs-save');
  await expect(page.locator('#prefs-modal')).toBeHidden();
}

test('saving a threshold repaints the grid without a reload or a refetch', async ({ page }) => {
  const routeState = await installRouteMocks(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  // A marker on the live window: if the grid only looked fixed because the page
  // reloaded, this is gone. (A tab switch would also have "fixed" it, and a tab
  // switch is a refetch — hence the HTTP census below.)
  await page.evaluate(() => { /** @type {any} */ (window).__no_reload = 'alive'; });

  const before = await firstRowColour(page);

  // A ceiling nothing in stock approaches ⇒ every row ramps to the red end.
  await page.keyboard.press('Control+,');
  await expect(page.locator('#prefs-modal')).toBeVisible();
  const hitsBeforeSave = routeState.getHttpHits();
  await saveAllThresholds(page, 999999);

  const afterRaise = await firstRowColour(page);
  expect(afterRaise.partId, 'the same row, not a different one that happens to be red')
    .toBe(before.partId);
  await expect
    .poll(() => firstRowColour(page).then(r => r.color))
    .toBe('rgb(248, 81, 73)');

  // Exactly one round trip for that save: the POST of preferences.json. A second
  // one would mean the repaint rode in on a refetch, which is the accidental
  // path this branch had to break (tab switch → scheduleInventoryRefresh →
  // rebuild_inventory → INVENTORY_UPDATED → render).
  expect(routeState.getHttpHits() - hitsBeforeSave,
    'a preferences save must cost its own POST and nothing else').toBe(1);

  // And back the other way: threshold 0 means "fully green" by definition
  // (stockValueColor), so the same row must leave red. One of the two moves is a
  // real transition whichever colour the fixture started on.
  await page.keyboard.press('Control+,');
  await expect(page.locator('#prefs-modal')).toBeVisible();
  await saveAllThresholds(page, 0);

  const afterZero = await firstRowColour(page);
  expect(afterZero.color).toBe('rgb(63, 185, 80)');
  expect(afterZero.color).not.toBe(afterRaise.color);

  expect(await page.evaluate(() => /** @type {any} */ (window).__no_reload)).toBe('alive');
});
