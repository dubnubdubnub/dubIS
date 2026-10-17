// @ts-check
/* Model-based property test for the three-panel row (.panels).

   The bug class this exists to kill: some sequence of drags, collapses, window
   resizes, zoom changes and BOM loads leaves the row not *tiled* — a dead strip
   at the right, a gap between panels, a collapsed panel that still takes width,
   or a visible panel squeezed under its min-width while there was room for it.
   The reported instance was "drag a divider, load a BOM, hide Purchase Import →
   a big empty strip right of the BOM", and it survived every example spec
   because each of them collapsed a panel either without dragging first or
   without checking the space the survivors filled.

   fast-check generates random action sequences against one real page, and after
   EVERY step the invariant is asserted on real browser geometry — never on the
   app's own idea of the layout, which is what was wrong. On failure fast-check
   shrinks to a minimal sequence and prints it with its seed and path; replay it
   with:

     FC_SEED=<seed> FC_PATH=<path> SERVE_PORT=8791 \
       npx playwright test panel-layout-model --project functional

   Determinism follows tests/js/property/setup.mjs (see docs/property-testing.md):
   random locally, so many runs explore far more than one fixed seed; pinned
   under CI so a red job reproduces byte-for-byte and a green commit stays green.
   The run budget is its own env var, FC_E2E_NUM_RUNS, because FC_NUM_RUNS tunes
   the pure vitest suites and 100 runs of a browser are a minute, not
   milliseconds. FC_E2E_NUM_RUNS=300 is a good local soak after touching panel
   layout. */

import { test, expect } from '@playwright/test';
import fc from 'fast-check';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows, loadBomViaFileInput } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';
import { setZoom } from './helpers/no-clip.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));
const BOM_CSV = path.join(__dirname, 'fixtures', 'bom.csv');

const isCI = Boolean(process.env.CI);
const CI_SEED = 20261017;
// ~0.5s a run (a reload plus up to ten steps), so 40 runs is ~20s.
const NUM_RUNS = Number(process.env.FC_E2E_NUM_RUNS ?? 40);
const SEED = process.env.FC_SEED !== undefined
  ? Number(process.env.FC_SEED) : (isCI ? CI_SEED : undefined);

/* The app's documented minimum is 1200x700 — below it the header is allowed to
   hide controls, so the property is only claimed at or above it. */
const VIEWPORT = { minW: 1200, maxW: 1920, minH: 700, maxH: 1080 };
const START = { width: 1600, height: 900 };

/** Authored px of one .resize-handle-h (css/layout.css). */
const HANDLE_W = 5;
/** Sub-pixel rounding between rects; the bug this guards is hundreds of px. */
const TOL = 1;

const PANEL_IDS = ['panel-import', 'panel-inventory', 'panel-bom'];
const REGION_PANEL = { import: 'panel-import', bom: 'panel-bom' };

/** @typedef {{kind: 'drag', handle: 0|1, dx: number}
 *   | {kind: 'toggle', region: 'import'|'bom'}
 *   | {kind: 'roundtrip', region: 'import'|'bom'}
 *   | {kind: 'viewport', width: number, height: number}
 *   | {kind: 'zoom', percent: number}
 *   | {kind: 'loadBom'}} Action */

const region = fc.constantFrom(/** @type {const} */ ('import'), /** @type {const} */ ('bom'));

/**
 * The app's own tables, read from the served modules rather than imported here:
 * js/ is not an npm package, so Node loads its .js files as CommonJS and named
 * imports from a spec fail. Reading them keeps one source of truth anyway.
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<{ zoomSteps: number[], minWidths: number[] }>}
 */
function appTables(page) {
  return page.evaluate(async () => {
    const { ZOOM_STEPS } = await import('/js/ui-zoom-logic.js');
    const { PANEL_MIN_WIDTHS } = await import('/js/panel-layout-logic.js');
    return { zoomSteps: ZOOM_STEPS, minWidths: PANEL_MIN_WIDTHS };
  });
}

/**
 * @param {number[]} zoomSteps
 * @returns {fc.Arbitrary<Action>}
 */
const actionArb = (zoomSteps) => fc.oneof(
  // Drags and toggles are weighted up: the bug lives in their interleaving.
  { weight: 4, arbitrary: fc.record({
    kind: fc.constant(/** @type {const} */ ('drag')),
    handle: fc.constantFrom(/** @type {const} */ (0), /** @type {const} */ (1)),
    dx: fc.integer({ min: -500, max: 500 }),
  }) },
  { weight: 4, arbitrary: fc.record({
    kind: fc.constant(/** @type {const} */ ('toggle')), region }) },
  { weight: 1, arbitrary: fc.record({
    kind: fc.constant(/** @type {const} */ ('roundtrip')), region }) },
  { weight: 1, arbitrary: fc.record({
    kind: fc.constant(/** @type {const} */ ('viewport')),
    width: fc.integer({ min: VIEWPORT.minW, max: VIEWPORT.maxW }),
    height: fc.integer({ min: VIEWPORT.minH, max: VIEWPORT.maxH }),
  }) },
  { weight: 1, arbitrary: fc.record({
    kind: fc.constant(/** @type {const} */ ('zoom')),
    percent: fc.constantFrom(...zoomSteps.map(z => Math.round(z * 100))),
  }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant(/** @type {const} */ ('loadBom')) }) },
);

/** Two frames: one for the change to apply, one for dependent layout to settle. */
const settle = (page) => page.evaluate(() => new Promise(r => requestAnimationFrame(
  () => requestAnimationFrame(() => r(undefined)))));

/**
 * Everything the invariant needs, read from the live DOM in one round trip.
 * Rect fields are post-zoom px (all one space, so they compare with each other);
 * `authored` widths are divided back by the zoom so they compare with the
 * authored min-widths.
 * @param {import('@playwright/test').Page} page
 */
function readGeometry(page) {
  return page.evaluate(() => {
    const zoom = Number(getComputedStyle(document.documentElement)
      .getPropertyValue('--ui-zoom').trim()) || 1;
    const row = /** @type {HTMLElement} */ (document.querySelector('.panels'));
    const rr = row.getBoundingClientRect();
    // In-flow children only: #flyout-container is absolutely positioned and
    // takes no part in the row.
    const pieces = [...row.children]
      .filter(el => el.matches('.panel, .resize-handle-h'))
      .map(el => {
        const r = el.getBoundingClientRect();
        return {
          id: el.id || 'resize-handle-h',
          left: r.left, right: r.right, width: r.width,
          collapsed: el.classList.contains('panel-collapsed'),
          inlineFlex: /** @type {HTMLElement} */ (el).style.flex,
        };
      });
    const toggles = {
      import: document.getElementById('panel-toggle-import')?.getAttribute('aria-expanded'),
      bom: document.getElementById('panel-toggle-bom')?.getAttribute('aria-expanded'),
    };
    return { zoom, row: { left: rr.left, right: rr.right, width: rr.width }, pieces, toggles };
  });
}

/** @param {Awaited<ReturnType<typeof readGeometry>>} g */
const panelWidths = (g) => Object.fromEntries(
  g.pieces.filter(p => PANEL_IDS.includes(p.id)).map(p => [p.id, p.width]));

/**
 * The invariant. Holds after every step of every sequence.
 * @param {Awaited<ReturnType<typeof readGeometry>>} g
 * @param {number[]} minWidths  authored px, [import, inventory, bom]
 * @param {string} where  the action log, so a failure names the step
 */
function assertTiled(g, minWidths, where) {
  const ctx = `${where}\ngeometry: ${JSON.stringify(g)}`;
  expect(g.pieces.map(p => p.id), `row order\n${ctx}`).toEqual([
    'panel-import', 'resize-handle-h', 'panel-inventory', 'resize-handle-h', 'panel-bom',
  ]);

  // Collapse state is told three ways; they must agree.
  expect(g.toggles.import, `import toggle vs class\n${ctx}`)
    .toBe(String(!g.pieces[0].collapsed));
  expect(g.toggles.bom, `bom toggle vs class\n${ctx}`)
    .toBe(String(!g.pieces[4].collapsed));
  expect(g.pieces[2].collapsed, `the inventory grid is never collapsible\n${ctx}`).toBe(false);

  for (const p of g.pieces) {
    if (p.collapsed) {
      expect(p.width, `collapsed ${p.id} must be zero width\n${ctx}`).toBeLessThanOrEqual(0.5);
    }
    if (p.id === 'resize-handle-h') {
      // flex-shrink: 0 — a handle squeezed narrower stops being grabbable.
      expect(Math.abs(p.width - HANDLE_W * g.zoom),
        `handle must keep its ${HANDLE_W}px\n${ctx}`).toBeLessThanOrEqual(TOL);
    }
  }

  // Tiling: abut the row's left edge, abut each other, no gap anywhere.
  expect(Math.abs(g.pieces[0].left - g.row.left),
    `first piece must start at the row's left edge\n${ctx}`).toBeLessThanOrEqual(TOL);
  for (let i = 1; i < g.pieces.length; i++) {
    expect(Math.abs(g.pieces[i].left - g.pieces[i - 1].right),
      `gap/overlap before ${g.pieces[i].id} (#${i})\n${ctx}`).toBeLessThanOrEqual(TOL);
  }
  const last = g.pieces[g.pieces.length - 1];
  const deadStrip = g.row.right - last.right;
  expect(deadStrip, `dead space at the right of the row\n${ctx}`).toBeLessThanOrEqual(TOL);

  // Whenever the row is wide enough to honour every visible panel's minimum, it
  // must: nothing overflows and nothing is squeezed. Narrower than that (200%
  // zoom on a 1200px window is a 600px authored row) the row may overflow to the
  // right — but the no-gap checks above still hold.
  const authoredRow = g.row.width / g.zoom;
  const visible = PANEL_IDS
    .map((id, i) => ({ id, min: minWidths[i], piece: g.pieces[i * 2] }))
    .filter(v => !v.piece.collapsed);
  const needed = visible.reduce((s, v) => s + v.min, 0) + 2 * HANDLE_W;
  if (authoredRow >= needed + TOL) {
    expect(-deadStrip, `row overflows although it fits every min-width\n${ctx}`)
      .toBeLessThanOrEqual(TOL);
    for (const v of visible) {
      expect(v.piece.width / g.zoom, `${v.id} squeezed under its min-width\n${ctx}`)
        .toBeGreaterThanOrEqual(v.min - TOL);
    }
  }
}

/**
 * @param {import('@playwright/test').Page} page
 * @param {0|1} handle
 * @param {number} dx  post-zoom px, i.e. what the user's pointer travels
 */
async function drag(page, handle, dx) {
  const box = await page.locator('.resize-handle-h').nth(handle).boundingBox();
  const vw = /** @type {{width: number}} */ (page.viewportSize()).width;
  if (!box) throw new Error(`resize handle ${handle} has no box`);
  const x = box.x + box.width / 2;
  // Mid-height: the toggle pill sits at the top of the handle and swallows
  // pointerdown on purpose (clicking it must not start a drag).
  const y = box.y + box.height / 2;
  if (x < 0 || x > vw) return { performed: false, started: false };
  await page.mouse.move(x, y);
  await page.mouse.down();
  const started = await page.locator('.resize-handle-h').nth(handle)
    .evaluate(el => el.classList.contains('active'));
  await page.mouse.move(Math.min(vw - 2, Math.max(1, x + dx)), y, { steps: 4 });
  await page.mouse.up();
  return { performed: true, started };
}

/**
 * Apply one action and check what that action specifically promises; the
 * general invariant is checked by the caller after every step.
 * @param {import('@playwright/test').Page} page
 * @param {Action} a
 * @param {{ bomLoaded: boolean }} st
 * @param {string} where
 */
async function apply(page, a, st, where) {
  const before = await readGeometry(page);
  switch (a.kind) {
    case 'drag': {
      const res = await drag(page, a.handle, a.dx);
      await settle(page);
      if (!res.performed) return;
      const after = await readGeometry(page);
      const left = before.pieces[a.handle * 2];
      const right = before.pieces[a.handle * 2 + 2];
      const besideCollapsed = left.collapsed || right.collapsed;
      // A handle beside a collapsed panel has nothing on one side to trade width
      // with, so it does not drag at all (js/resize-panels.js). Every panel stays
      // exactly where it was — in particular the collapsed one is never handed
      // width, nor recorded at its 0px.
      expect(res.started, `drag on handle ${a.handle} started (collapsed neighbour: `
        + `${besideCollapsed})\n${where}`).toBe(!besideCollapsed);
      const b = panelWidths(before), c = panelWidths(after);
      if (besideCollapsed) {
        for (const id of PANEL_IDS) {
          expect(Math.abs(c[id] - b[id]),
            `a no-op drag moved ${id}\n${where}`).toBeLessThanOrEqual(TOL);
        }
        return;
      }
      // A real drag trades width between its two neighbours only: the pair's
      // total and the third panel are untouched. (Not *how much* moved — that
      // would re-derive clampDrag, which tests/js/panel-layout-logic.test.js pins.)
      const pair = [PANEL_IDS[a.handle], PANEL_IDS[a.handle + 1]];
      const third = PANEL_IDS.find(id => !pair.includes(id));
      expect(Math.abs((c[pair[0]] + c[pair[1]]) - (b[pair[0]] + b[pair[1]])),
        `drag on handle ${a.handle} changed the pair's total\n${where}`).toBeLessThanOrEqual(TOL);
      expect(Math.abs(c[third] - b[third]),
        `drag on handle ${a.handle} moved ${third}\n${where}`).toBeLessThanOrEqual(TOL);
      return;
    }
    case 'toggle': {
      await page.click(`#panel-toggle-${a.region}`);
      await settle(page);
      const after = await readGeometry(page);
      const i = PANEL_IDS.indexOf(REGION_PANEL[a.region]) * 2;
      expect(after.pieces[i].collapsed, `toggling ${a.region} flips it\n${where}`)
        .toBe(!before.pieces[i].collapsed);
      return;
    }
    case 'roundtrip': {
      // Metamorphic: collapse-then-reopen (or reopen-then-collapse) is the
      // identity on the whole row. This is the "a collapse round-trip must not
      // cost the user their dragged width" promise, for any reachable state.
      await page.click(`#panel-toggle-${a.region}`);
      await settle(page);
      await page.click(`#panel-toggle-${a.region}`);
      await settle(page);
      const b = panelWidths(before), c = panelWidths(await readGeometry(page));
      for (const id of PANEL_IDS) {
        expect(Math.abs(c[id] - b[id]),
          `${a.region} round-trip changed ${id}: ${b[id]} → ${c[id]}\n${where}`)
          .toBeLessThanOrEqual(TOL);
      }
      return;
    }
    case 'viewport':
      await page.setViewportSize({ width: a.width, height: a.height });
      await settle(page);
      return;
    case 'zoom':
      await setZoom(page, a.percent);
      return;
    case 'loadBom':
      // Once per run: reloading the same file would let waitForSelector return
      // on the previous render's rows.
      if (st.bomLoaded) return;
      await loadBomViaFileInput(page, BOM_CSV);
      st.bomLoaded = true;
      await settle(page);
      return;
    default:
      throw new Error(`unknown action ${JSON.stringify(a)}`);
  }
}

test.describe('Panel layout — model-based property', () => {
  test('any sequence of drags, collapses, resizes, zooms and BOM loads keeps the row tiled',
    async ({ page }) => {
      // Shrinking replays sequences against a real browser, so leave it room.
      test.setTimeout(240_000);
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize(START);
      await page.goto('/index.html');
      await waitForInventoryRows(page);

      const { zoomSteps, minWidths } = await appTables(page);
      const action = actionArb(zoomSteps);

      let first = true;
      await fc.assert(
        fc.asyncProperty(fc.array(action, { minLength: 1, maxLength: 10 }), async (actions) => {
          // One page for the whole property: a reload resets every module's state
          // (preferences are served statically, so nothing persists between runs)
          // and is far cheaper than a fresh context.
          if (!first) {
            await page.setViewportSize(START);
            await page.reload();
            await waitForInventoryRows(page);
          }
          first = false;
          const st = { bomLoaded: false };
          assertTiled(await readGeometry(page), minWidths, 'at boot');
          for (let i = 0; i < actions.length; i++) {
            const where = `after step ${i}: ${JSON.stringify(actions.slice(0, i + 1))}`;
            await apply(page, actions[i], st, where);
            assertTiled(await readGeometry(page), minWidths, where);
          }
        }),
        {
          numRuns: NUM_RUNS,
          ...(SEED === undefined ? {} : { seed: SEED }),
          ...(process.env.FC_PATH ? { path: process.env.FC_PATH } : {}),
        },
      );
    });
});

test.describe('Panel layout — reported regression', () => {
  test('drag a divider, load a BOM, hide Purchase Import → the BOM fills the row',
    async ({ page }) => {
      await installRouteMocks(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await loadBomViaFileInput(page, BOM_CSV);

      const res = await drag(page, 1, -40);
      expect(res.started, 'the drag must really have happened').toBe(true);
      await page.click('#panel-toggle-import');
      await settle(page);

      const g = await readGeometry(page);
      const { minWidths } = await appTables(page);
      assertTiled(g, minWidths, 'drag inventory|bom by -40px, then hide import, BOM loaded');
      const w = panelWidths(g);
      expect(w['panel-inventory'] + w['panel-bom'] + 2 * HANDLE_W,
        'inventory + BOM + handles must span the whole row').toBeCloseTo(g.row.width, 0);
    });
});
