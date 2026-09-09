// @ts-check
/**
 * data-text-selectable.spec.mjs — the app-wide contract:
 *
 *   Every piece of text that is a DATA VALUE must be
 *     (a) selectable — no `user-select: none` anywhere up its ancestor chain,
 *     (b) actually selectable in practice — a real drag over it produces a
 *         selection, not an empty string, and
 *     (c) copyable by hover — the pointer resting on it surfaces a copy control.
 *   Only UI CHROME opts out, and every exemption is named and justified in
 *   tests/js/e2e/helpers/data-text.mjs.
 *
 * (a) and a programmatic version of (b) run over EVERY text-bearing element in
 * the rendered panels, so a newly-added unselectable cell fails here rather than
 * slipping through. The expensive real-pointer checks — a mouse drag, a 400ms
 * hover — run once per distinct KIND of cell, which covers every cell shape the
 * fixtures render without spending a minute per row.
 *
 * Failures name the offending selector and its text, because "some element
 * somewhere is unselectable" is not a bug report anyone can act on.
 *
 * Why this exists: `.part-id-lcsc` (the LCSC part number) computed
 * `user-select: text` and still could not be highlighted — its `inline-flex`
 * box put the text in an anonymous block that caret hit-testing missed, and the
 * leading distributor `<img>` turned any drag starting on it into a native image
 * drag. Check (a) alone passed throughout. (b) is the check that caught it.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaEmit } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import {
  partitionLeaves, oneOfEachKind, leafKind, harvestLeaves, widestLine, sharesRun,
} from './helpers/data-text.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

const CARTS_SEED = {
  carts: [{
    id: 'cart-1',
    name: 'My Cart',
    items: [{ ref: 'item-1', part_id: 'C496552', raw: null, qty: 5, target_distributor: 'lcsc' }],
  }],
  active_cart_id: 'cart-1',
};

/** A BOM whose part numbers exist in the fixture, so rows render matched. */
const BOM_CSV = [
  'Reference,Value,Footprint,Quantity,LCSC',
  'C1,100nF,0402,10,C429942',
  'R1,10k,0402,4,C496552',
].join('\n');

/** Harvest every text-bearing element under `rootSelector`. */
async function harvest(page, rootSelector) {
  return page.evaluate(harvestLeaves, rootSelector);
}

/**
 * Assert (a): nothing in the ancestor chain sets `user-select: none`. The
 * harvester reports the *computed* value, which already folds inheritance in.
 */
function expectSelectableStyle(dataLeaves, label) {
  const blocked = dataLeaves.filter(l => l.userSelect === 'none');
  expect(blocked.map(l => `${l.selector} — text ${JSON.stringify(l.text)}`),
    `${label}: these data values compute user-select:none, so they cannot be highlighted. `
    + 'Either make them selectable, or — if one is genuinely UI chrome — add it to '
    + 'CHROME_CLASSES in tests/js/e2e/helpers/data-text.mjs with a reason.')
    .toEqual([]);
}

/**
 * Assert (b) cheaply, for every data leaf: a Range over the element's own text
 * yields that text back. This catches a cell whose text is unreachable to the
 * selection API at all.
 */
async function expectProgrammaticallySelectable(page, dataLeaves, label) {
  const failures = await page.evaluate((selectors) => {
    const bad = [];
    const ownText = (el) => {
      let t = '';
      for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
      return t.trim();
    };
    // Re-find by index within the harvest order so the two walks line up.
    const roots = new Map();
    for (const { rootSelector, index, selector, text } of selectors) {
      if (!roots.has(rootSelector)) {
        const root = document.querySelector(rootSelector);
        const all = [];
        if (root) for (const el of root.querySelectorAll('*')) if (ownText(el)) all.push(el);
        roots.set(rootSelector, all);
      }
      const el = roots.get(rootSelector)[index];
      if (!el) { bad.push(`${selector}: element vanished between walks`); continue; }
      const sel = window.getSelection();
      sel.removeAllRanges();
      const range = document.createRange();
      range.selectNodeContents(el);
      sel.addRange(range);
      // Upper-cased on both sides: `text-transform` changes what the selection
      // API returns (the import panel's "Log" heading selects as "LOG"), and
      // that is a rendering detail, not an unselectable value.
      const got = sel.toString().replace(/\s+/g, ' ').trim().toUpperCase();
      const want = text.replace(/\s+/g, ' ').trim().toUpperCase();
      if (!got.includes(want)) {
        bad.push(`${selector}: selecting its contents gave ${JSON.stringify(got)}, expected to contain ${JSON.stringify(want)}`);
      }
      sel.removeAllRanges();
    }
    return bad;
  }, dataLeaves.map(l => ({
    rootSelector: l._root, index: l._index, selector: l.selector, text: l.text,
  })));

  expect(failures, `${label}: a Range over these data values did not return their text`)
    .toEqual([]);
}

/**
 * Park the pointer somewhere inert and wait for both hover affordances to close.
 *
 * Necessary between real-pointer checks: this app opens a popover, a tooltip or
 * a vendor flyout on hover, and any of them left standing sits *on top of* the
 * next drag target — the drag then selects the overlay's text and the result is
 * nonsense that looks like a pass or a mystery failure. (1, 1) lands on the
 * header's own padding, which bears no text of its own and no `data-*` part
 * number, so nothing re-opens there.
 */
async function parkPointer(page) {
  await page.mouse.move(1, 1);
  await page.evaluate(() => window.getSelection().removeAllRanges());
  await page.waitForFunction(() => {
    const hidden = (sel) => {
      const el = document.querySelector(sel);
      return !el || el.classList.contains('hidden');
    };
    return hidden('.text-popover') && hidden('.part-preview');
  }, null, { timeout: 4000 });
}

/**
 * Assert (b) for real: drag the mouse along the widest rendered line of the
 * element's own text and require the element's text back.
 *
 * Two deliberate choices:
 *  - the drag follows the TEXT's client rect, not the element's border box — a
 *    right-aligned cell, a padding-heavy `td`, or a 12px-wide wrapped column
 *    means a border-box drag crosses no glyphs at all;
 *  - the selection is read while the button is still DOWN. That is the moment
 *    the user sees the highlight, and it cannot be confounded by a click handler
 *    re-rendering the row on mouseup and wiping the selection.
 *
 * The assertion is on CONTENT, not merely on length: a drag that selects some
 * other element's text is a failure, not a pass.
 */
async function expectDragSelectable(page, leaf, label) {
  const line = widestLine(leaf);
  expect(line, `${label}: ${leaf.selector} renders text but has no text rect to drag along`)
    .not.toBeNull();
  if (!line) return;

  await parkPointer(page);
  const y = line.y + line.h / 2;
  /* Start and end 2px OUTSIDE the glyph run. Ending just inside it is enough for
     a long value but not for a 13px-wide "10": both ends resolve to the same
     caret offset and the drag yields nothing at all. Overshooting can pick up a
     neighbouring cell, which sharesRun tolerates — it only requires that this
     value's own characters are in there. */
  await page.mouse.move(line.x - 2, y);
  await page.mouse.down();
  await page.mouse.move(line.x + line.w + 2, y, { steps: 12 });
  const got = await page.evaluate(() => window.getSelection().toString().replace(/\s+/g, ' ').trim());
  await page.mouse.up();
  await page.evaluate(() => window.getSelection().removeAllRanges());

  const want = leaf.text.replace(/\s+/g, ' ').trim();
  expect(sharesRun(got, want),
    `${label}: dragging along ${leaf.selector} (text ${JSON.stringify(want)}) selected `
    + `${JSON.stringify(got)}, which shares no run of this value's characters — `
    + 'a user cannot highlight this value')
    .toBe(true);
}

/**
 * Assert (c): hovering the element surfaces a copy control — either the generic
 * `.text-popover-copy`, or, for a part-number cell, the part tooltip's
 * `.part-preview-copy-field` (the generic popover is suppressed there on purpose
 * so the two do not fight over the same hover).
 */
async function expectCopyAffordance(page, leaf, label) {
  await parkPointer(page);
  const line = widestLine(leaf) || { x: leaf.box.x, y: leaf.box.y, w: leaf.box.w, h: leaf.box.h };
  await page.mouse.move(line.x + line.w / 2, line.y + line.h / 2);

  const copy = page.locator('.text-popover:not(.hidden) .text-popover-copy, '
    + '.part-preview:not(.hidden) .part-preview-copy-field');
  await expect(copy.first(),
    `${label}: hovering ${leaf.selector} (text ${JSON.stringify(leaf.text)}) surfaced no copy `
    + 'control — neither .text-popover-copy nor the part tooltip\'s .part-preview-copy-field')
    .toBeVisible({ timeout: 4000 });
}

/** Boot the app with inventory, a matched BOM and a seeded cart. */
async function boot(page) {
  await installRouteMocks(page, MOCK_INVENTORY, { carts: CARTS_SEED });
  await page.setViewportSize({ width: 1600, height: 950 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);
  await page.waitForTimeout(300);
}

/**
 * Harvest one root and tag each leaf with where it came from, so the
 * programmatic re-walk can find it again.
 */
async function leavesOf(page, rootSelector) {
  const raw = await harvest(page, rootSelector);
  return raw.map((l, i) => ({ ...l, _root: rootSelector, _index: i }));
}

/**
 * The subset a real pointer can actually reach: on screen, wide enough to drag
 * along, and not underneath another element.
 *
 * `occluded` is the load-bearing one. The matched-BOM table's action column is
 * `position: sticky` and floats over the qty cells by design (that is what
 * sticky-buttons.spec.mjs protects), so no pointer can select the text beneath
 * it. That is an overlay question, not a selection-policy one — the CSS check
 * and the Range check still cover those cells, and each caller asserts the
 * remaining sample is non-empty so this filter can never empty the test out.
 */
function pointerReachable(leaves, { top = 100, bottom = 820 } = {}) {
  return leaves.filter(l => !l.occluded && l.lines.length
    && l.box.y > top && l.box.y < bottom && l.box.w > 8);
}

test.describe('every data value is selectable and offers a copy affordance', () => {

  test('the harvester finds a substantial amount of text (it is not scanning nothing)', async ({ page }) => {
    await boot(page);
    const leaves = await leavesOf(page, '#panel-inventory');
    expect(leaves.length,
      'the inventory panel should render hundreds of text-bearing elements; a tiny number '
      + 'means this spec is asserting almost nothing').toBeGreaterThan(100);
    const { data } = partitionLeaves(leaves);
    expect(data.length, 'most inventory text is data, not chrome').toBeGreaterThan(50);
  });

  test('inventory rows: PN, MPN, description, qty and price cells', async ({ page }) => {
    await boot(page);
    const leaves = await leavesOf(page, '#panel-inventory');
    const { data } = partitionLeaves(leaves);

    expectSelectableStyle(data, 'inventory panel');
    await expectProgrammaticallySelectable(page, data, 'inventory panel');

    // The specific cells the user named must be in the enforced set, not
    // silently classified as chrome.
    const kinds = new Set(data.map(leafKind));
    for (const required of ['part-id-lcsc', 'part-mpn', 'part-qty', 'part-value']) {
      expect([...kinds].some(k => k.split('.').includes(required)),
        `${required} must be classified as data (kinds seen: ${[...kinds].join(', ')})`).toBe(true);
    }
  });

  test('inventory rows: a real drag selects every kind of value cell', async ({ page }) => {
    await boot(page);
    const leaves = await leavesOf(page, '#panel-inventory');
    const { data } = partitionLeaves(leaves);
    // Only cells inside a row: the panel header's text is chrome, and off-screen
    // rows have no usable box to drag over.
    const rowCells = pointerReachable(
      data.filter(l => l.chainClasses.includes('inv-part-row')));
    const sample = oneOfEachKind(rowCells);
    expect(sample.length, 'expected several kinds of on-screen inventory row cell to drag over')
      .toBeGreaterThan(3);

    for (const leaf of sample) {
      await expectDragSelectable(page, leaf, 'inventory row');
    }
  });

  test('inventory rows: hovering every kind of value cell surfaces a copy control', async ({ page }) => {
    await boot(page);
    const leaves = await leavesOf(page, '#panel-inventory');
    const { data } = partitionLeaves(leaves);
    const rowCells = pointerReachable(
      data.filter(l => l.chainClasses.includes('inv-part-row')), { bottom: 700 });
    const sample = oneOfEachKind(rowCells);
    expect(sample.length, 'expected several kinds of on-screen inventory row cell to hover')
      .toBeGreaterThan(3);

    for (const leaf of sample) {
      await expectCopyAffordance(page, leaf, 'inventory row');
    }
  });

  test('matched BOM table', async ({ page }) => {
    await boot(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForSelector('#inventory-body .table-wrap tbody tr', { timeout: 10_000 });

    const leaves = await leavesOf(page, '#inventory-body .table-wrap');
    const { data } = partitionLeaves(leaves);
    expect(data.length, 'the matched BOM table should contribute data cells').toBeGreaterThan(3);

    expectSelectableStyle(data, 'matched BOM table');
    await expectProgrammaticallySelectable(page, data, 'matched BOM table');

    const sample = oneOfEachKind(pointerReachable(data));
    expect(sample.length, 'expected reachable matched-BOM cells to drag over')
      .toBeGreaterThan(2);
    for (const leaf of sample) {
      await expectDragSelectable(page, leaf, 'matched BOM table');
    }
  });

  test('BOM staging panel', async ({ page }) => {
    await boot(page);
    await loadBomViaEmit(page, BOM_CSV);
    const leaves = await leavesOf(page, '#panel-bom');
    const { data } = partitionLeaves(leaves);
    expectSelectableStyle(data, 'BOM panel');
    await expectProgrammaticallySelectable(page, data, 'BOM panel');
  });

  test('purchase import panel', async ({ page }) => {
    await boot(page);
    const leaves = await leavesOf(page, '#panel-import');
    const { data } = partitionLeaves(leaves);
    expectSelectableStyle(data, 'import panel');
    await expectProgrammaticallySelectable(page, data, 'import panel');
  });

  test('cart modal', async ({ page }) => {
    await boot(page);
    await page.click('#cart-btn');
    await expect(page.locator('.cart-modal')).toBeVisible();
    await page.waitForTimeout(300);

    const leaves = await leavesOf(page, '.cart-modal');
    const { data } = partitionLeaves(leaves);
    expect(data.length, 'the seeded cart line should contribute data cells').toBeGreaterThan(2);

    expectSelectableStyle(data, 'cart modal');
    await expectProgrammaticallySelectable(page, data, 'cart modal');

    const sample = oneOfEachKind(pointerReachable(data, { top: 0, bottom: 950 }));
    expect(sample.length, 'expected reachable cart-modal cells to drag over')
      .toBeGreaterThan(0);
    for (const leaf of sample) {
      await expectDragSelectable(page, leaf, 'cart modal');
    }
  });

  test('the part number the user reported: a full drag across it selects it whole', async ({ page }) => {
    // The headline regression, asserted directly rather than only through the
    // classifier: drag from the very left edge (on top of the distributor icon)
    // to the right edge and require the entire part number back.
    await boot(page);
    const pn = page.locator('.inv-part-row:visible .part-id-lcsc:visible').first();
    const text = (await pn.innerText()).trim();
    expect(text.length).toBeGreaterThan(2);
    const box = await pn.boundingBox();
    expect(box).not.toBeNull();
    if (!box) return;

    const y = box.y + box.height / 2;
    await page.evaluate(() => window.getSelection().removeAllRanges());
    await page.mouse.move(box.x + 1, y);          // starts ON the vendor icon
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - 1, y, { steps: 15 });
    await page.mouse.up();

    const got = await page.evaluate(() => window.getSelection().toString().trim());
    expect(got,
      'dragging the whole part-number cell must select the whole part number — '
      + 'starting the drag on the vendor icon must not turn it into an image drag, '
      + 'and the cell must not be a flex container (see css/components/tooltip.css)')
      .toBe(text);
  });

  test('UI chrome still opts out of selection', async ({ page }) => {
    // The inversion must not have become "everything is selectable": the
    // click-to-collapse headers and the sort-header row stay unselectable, so a
    // drag over them does not paint a selection instead of operating a control.
    await boot(page);
    const chromeState = await page.evaluate(() => {
      const out = {};
      for (const sel of ['.inv-col-header', '.inv-section-header', '.inv-parent-header',
        '.inv-subsection-header']) {
        const el = document.querySelector(sel);
        out[sel] = el ? getComputedStyle(el).userSelect : 'ABSENT';
      }
      return out;
    });
    for (const [sel, value] of Object.entries(chromeState)) {
      if (value === 'ABSENT') continue;   // not rendered by this fixture
      expect(value, `${sel} is a control and must stay user-select:none`).toBe('none');
    }
    expect(Object.values(chromeState).some(v => v === 'none'),
      'at least one chrome selector should have been present to check').toBe(true);
  });
});
