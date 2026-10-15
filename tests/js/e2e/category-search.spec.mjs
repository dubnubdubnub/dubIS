// @ts-check
/* Pixel-truth guard for "a search term that names a category shows the whole
   category", and for the Preferences toggle that owns it.

   Why an E2E test on top of the predicate units: the units prove filterByQuery
   keeps a bucket. They cannot prove the grid ever ASKS it to — the whole feature
   rides on one read of one preference at one call site, and every way to get that
   wrong (cached the value at module load, forgot the normalizeBehavior whitelist,
   classified the key as `lazy-read` instead of `repaints`) leaves every unit test
   green and the feature dead.

   So this asserts the two user-visible halves:
     1. typing a category name into the live grid shows rows whose text never
        contains the term — the actual bug, since every Diodes row reads "Diode
        100V 2A …" and searching the plural used to return nothing;
     2. flipping the toggle in Preferences, with the term still in the box,
        changes the grid on its own — no reload, no refetch. That is PR A's real
        publish reaching a new key, and it is the half a `lazy-read`
        misclassification would silently lose. */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

/**
 * The grid's row identity: `row.dataset.partId = invPartKey(item)`
 * (js/inventory/inv-row-build.js:33) — the LCSC C-number when there is one, else
 * the first other PN. Mirrored here rather than imported because a Playwright
 * spec runs outside the page's module graph, same precedent as route-mocks.mjs's
 * `mockPartKey`. One Diodes fixture row has no LCSC number at all, so getting
 * this wrong is not hypothetical.
 */
function partKey(item) {
  const lcsc = item.lcsc || '';
  if (lcsc && /^C/i.test(lcsc)) return lcsc;
  return item.mpn || item.digikey || item.pololu || item.mouser || '';
}

/** Every Diodes part key in the fixture — the answer the grid must produce. */
const DIODE_KEYS = MOCK_INVENTORY
  .filter((r) => r.section === 'Diodes')
  .map(partKey)
  .sort();

/** Part keys currently rendered in the inventory grid. */
function visibleKeys(page) {
  return page.evaluate(() => Array.from(
    document.querySelectorAll('#inventory-body .inv-part-row'),
    (row) => /** @type {HTMLElement} */ (row).dataset.partId,
  ));
}

/**
 * The text `filterByQuery` searches (js/inventory/inventory-logic.js), mirrored
 * here the way tests/js/e2e/filter-chips.spec.mjs already mirrors it: a
 * Playwright spec runs outside the page's module graph. It is used only as a SET
 * MEMBERSHIP test below — never to recompute an expected ordering — so a drift
 * between the two fails loudly rather than quietly agreeing.
 */
function haystack(item) {
  return [item.lcsc, item.mpn, item.description, item.manufacturer, item.package,
    item.digikey, item.pololu, item.mouser].join(' ').toLowerCase();
}

/** The "(N)" a section header claims, by section name. */
function sectionCount(page, name) {
  return page.evaluate((sectionName) => {
    const headers = Array.from(document.querySelectorAll('#inventory-body .inv-section-header'));
    const header = headers.find((h) => h.textContent && h.textContent.includes(sectionName));
    if (!header) return null;
    const count = header.querySelector('.inv-section-count');
    return count ? count.textContent : null;
  }, name);
}

/** Flip the Preferences checkbox and let the modal close. */
async function setCategoryToggle(page, on) {
  await page.keyboard.press('Control+,');
  await expect(page.locator('#prefs-modal')).toBeVisible();
  // The search toggle lives on the General tab since the threshold tuner got
  // its own; navigation only, the assertions are untouched.
  await page.click('#prefs-tab-general');
  await page.locator('#pref-category-search').setChecked(on);
  await page.click('#prefs-save');
  await expect(page.locator('#prefs-modal')).toBeHidden();
}

async function openGrid(page, inventory) {
  await installRouteMocks(page, inventory);
  await page.setViewportSize({ width: 1920, height: 900 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
}

test.describe('a search term that names a category', () => {
  test.beforeEach(async ({ page }) => {
    await openGrid(page, MOCK_INVENTORY);
  });

  test('shows rows whose text never contains the term, by default', async ({ page }) => {
    // The bug in one line: no row in the fixture contains "diodes" anywhere in
    // the search haystack, so before this feature this search was empty.
    expect(DIODE_KEYS.length).toBeGreaterThan(0);

    await page.locator('#inv-search').fill('diodes');
    await expect
      .poll(async () => (await visibleKeys(page)).slice().sort())
      .toEqual(DIODE_KEYS);

    // And the header says what the user is looking at, rather than a stray count.
    await expect(sectionCount(page, 'Diodes')).resolves.toBe(`(${DIODE_KEYS.length})`);
  });

  test('a hit in one category does not light up the others', async ({ page }) => {
    await page.locator('#inv-search').fill('diodes');
    await expect.poll(async () => (await visibleKeys(page)).length)
      .toBe(DIODE_KEYS.length);
    // LEDs is a category too, and none of its rows say "diodes".
    await expect(sectionCount(page, 'LEDs')).resolves.toBeNull();
  });

  test('turning the toggle off drops back to the text search, with no reload', async ({ page }) => {
    const search = page.locator('#inv-search');
    await search.fill('diodes');
    await expect.poll(async () => (await visibleKeys(page)).length).toBe(DIODE_KEYS.length);

    // A marker on the live window: if the grid only changed because the page
    // reloaded, this is gone — and a reload would also lose the search term.
    await page.evaluate(() => { /** @type {any} */ (window).__no_reload = 'alive'; });

    await setCategoryToggle(page, false);

    await expect.poll(async () => (await visibleKeys(page)).length).toBe(0);
    await expect(search).toHaveValue('diodes');
    expect(await page.evaluate(() => /** @type {any} */ (window).__no_reload)).toBe('alive');

    // …and back on, the category returns. Proves the read is live rather than a
    // one-way latch, which is what a module-level cache would have been.
    await setCategoryToggle(page, true);
    await expect
      .poll(async () => (await visibleKeys(page)).slice().sort())
      .toEqual(DIODE_KEYS);
    expect(await page.evaluate(() => /** @type {any} */ (window).__no_reload)).toBe('alive');
  });

  test('a two-letter term does not swallow the grid', async ({ page }) => {
    // "ic" is a substring of nine IC section names AND of most of this fixture's
    // text, so the text search legitimately hits a lot. What the category rule
    // must not do is ADD to that: no category is NAMED "ic", so every row on
    // screen must be there because its own text contains the term.
    //
    // Asserting membership rather than comparing the grid before and after the
    // toggle is what makes this wait correctly: the unfiltered grid contains rows
    // that fail the predicate, so the poll cannot settle on the pre-search state
    // the way a "> 0" or "unchanged" check does (which is how this test first
    // passed for the wrong reason, on all 139 rows).
    const textHits = MOCK_INVENTORY.filter((r) => haystack(r).includes('ic'))
      .map(partKey)
      .sort();
    expect(textHits.length).toBeGreaterThan(0);
    expect(textHits.length).toBeLessThan(MOCK_INVENTORY.length);

    await page.locator('#inv-search').fill('ic');
    await expect
      .poll(async () => (await visibleKeys(page)).slice().sort())
      .toEqual(textHits);
  });
});

/* The hierarchy needs its own fixture, and that is a finding worth stating: the
   shipped inventory.json has NO "Parent > Child" sections at all — every
   Connectors row is flat — so run against it, "a parent shows its family" would
   compare the parent bucket with itself and pass while testing nothing. So this
   describe redistributes the fixture's Connectors rows across the real child keys
   from data/constants.json, which is what the live hierarchy looks like. */
test.describe('a parent category name', () => {
  const CHILDREN = ['Connectors > High Speed', 'Connectors > SMD', 'Connectors > Through Hole'];
  let n = 0;
  const NESTED = MOCK_INVENTORY.map((r) => {
    if (r.section !== 'Connectors') return r;
    n += 1;
    // Every fourth row stays directly under the parent, the rest go to a child,
    // so the parent bucket is a proper subset of the family.
    return { ...r, section: n % 4 === 0 ? 'Connectors' : CHILDREN[n % CHILDREN.length] };
  });
  const FAMILY = NESTED.filter((r) => (r.section || '').split(' > ')[0] === 'Connectors');
  const UNGROUPED = FAMILY.filter((r) => r.section === 'Connectors');
  // "SMD" would be the obvious child to pick and is the wrong one: 29 fixture
  // rows carry "SMD" in their PACKAGE field, so a text search for it lights up
  // half the grid and the assertion stops being about the category. "High Speed"
  // appears in no row's text at all, so anything rendered is rendered because of
  // the category rule and nothing else.
  const HIGH_SPEED = FAMILY.filter((r) => r.section === 'Connectors > High Speed');

  test.beforeEach(async ({ page }) => {
    // Preconditions, so a fixture that stops exercising the hierarchy fails here
    // rather than quietly turning this back into the flat case.
    expect(FAMILY.length).toBeGreaterThan(UNGROUPED.length);
    expect(HIGH_SPEED.length).toBeGreaterThan(0);
    await openGrid(page, NESTED);
  });

  test('names every child, because the child key carries it', async ({ page }) => {
    await page.locator('#inv-search').fill('connectors');
    await expect
      .poll(async () => (await visibleKeys(page)).slice().sort())
      .toEqual(FAMILY.map(partKey).sort());
  });

  test('a child name reaches down to that child, not up to the family', async ({ page }) => {
    await page.locator('#inv-search').fill('high speed');
    await expect
      .poll(async () => (await visibleKeys(page)).slice().sort())
      .toEqual(HIGH_SPEED.map(partKey).sort());
  });
});
