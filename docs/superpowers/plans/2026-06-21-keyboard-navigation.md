# Keyboard Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire dubIS app usable without a mouse — every interactive element reachable, row buttons navigable as a 2D arrow grid, scroll regions keyboard-scrollable, modals focus-trapped, plus a full set of (partly configurable) keyboard shortcuts.

**Architecture:** A new `js/a11y/` utility layer (roving grid, scrollable regions, focus trap, key-activation, central shortcut dispatcher, help overlay) wired in via `app-init.js` and the existing EventBus re-render events. A single source of truth for shortcut prefs lives in `store.js` and is persisted to `preferences.json` under a `shortcuts` key. One global `:focus-visible` stylesheet provides the keyboard-only focus ring.

**Tech Stack:** Vanilla ES modules (no framework, no build step), vitest (jsdom) for unit tests, Playwright for E2E. Backend is Python (pywebview) but only `preferences.json` round-trips through it — no Python changes required.

## Global Constraints

- No build step — plain ES modules imported (transitively) from `js/app-init.js`. New CSS added as a `<link>` in `index.html`.
- Error policy: throw / `AppLog.warn`/`AppLog.error`, never silent catches.
- Test policy: never skip tests; add missing dev deps to `requirements-dev.txt`/`package.json`.
- **Guardrail:** `tests/js/e2e/resize-visibility.spec.mjs` and the sticky-button checks must keep passing untouched. Never weaken button-clipping tests.
- E2E: realistic interactions only — real `page.keyboard.press(...)` / `.click()`, never `dispatchEvent` or `force:true`.
- Default redo = **both** `Ctrl+Y` and `Ctrl+Shift+Z`. Default `enterSubmitsModals=true`, `vimNav=false`.
- Focus ring is `:focus-visible` only (keyboard), never on mouse click.
- This is a Windows app, but keep the existing `ctrlKey || metaKey` handling so shortcuts also work on Mac dev machines.
- New unit tests: `tests/js/*.test.js`. New E2E specs: `tests/js/e2e/*.spec.mjs` (land in the `functional` Playwright project by default).

---

## File Structure

**Create:**
- `js/a11y/roving-grid.js` — 2D roving-tabindex grid controller.
- `js/a11y/scrollable.js` — make a scroll container keyboard-scrollable.
- `js/a11y/focus-trap.js` — modal focus trap + restore.
- `js/a11y/activate-on-key.js` — Enter/Space activation for non-button elements.
- `js/a11y/shortcuts.js` — central keydown dispatcher + command registry.
- `js/a11y/shortcut-help.js` — `?`/`F1` help overlay.
- `js/a11y/keyboard-nav.js` — init: wires grids, scrollables, activation; re-applies on EventBus events.
- `css/a11y.css` — global `:focus-visible` ring.
- Tests: `tests/js/roving-grid.test.js`, `tests/js/scrollable.test.js`, `tests/js/shortcuts.test.js`, `tests/js/shortcut-prefs.test.js`.
- E2E: `tests/js/e2e/keyboard-nav.spec.mjs`, `tests/js/e2e/scroll-keyboard.spec.mjs`, `tests/js/e2e/modal-focus-trap.spec.mjs`, `tests/js/e2e/shortcuts.spec.mjs`, `tests/js/e2e/keyboard-prefs.spec.mjs`.

**Modify:**
- `js/store.js` — add `shortcuts` to prefs object, `getShortcutPrefs()`, `setShortcutPrefs()`, load/save handling.
- `js/ui-helpers.js` — extend `Modal()` with focus trap + `confirmId`/Enter-to-confirm.
- `js/app-init.js` — import `keyboard-nav.js` + `shortcuts.js`; migrate undo/redo into the dispatcher.
- `js/inventory/inventory-renderer.js` / `js/inventory/inv-render.js` — `role`/`tabindex` already-or-newly on section headers (via `activate-on-key`).
- `js/bom/bom-events.js` — extract `saveBomFile()` (save without close) for Ctrl+S.
- `js/preferences-modal.js` — add the Keyboard section UI.
- `index.html` — add `<link rel="stylesheet" href="css/a11y.css">`.

---

## Task 1: Shortcut preferences in the store (foundation)

Everything reads shortcut prefs from one place. This task adds the persisted prefs + accessor with defaults. Pure, no DOM.

**Files:**
- Modify: `js/store.js`
- Test: `tests/js/shortcut-prefs.test.js`

**Interfaces:**
- Produces:
  - `getShortcutPrefs(): { redo: 'both'|'ctrl-y'|'ctrl-shift-z', enterSubmitsModals: boolean, vimNav: boolean }` — always returns a fully-defaulted object.
  - `setShortcutPrefs(partial): void` — merges, persists via `savePreferences()`, fires `preferencesSignal.set(preferences)`.
  - `SHORTCUT_DEFAULTS` constant.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/shortcut-prefs.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { getShortcutPrefs, setShortcutPrefs, SHORTCUT_DEFAULTS } from '../../js/store.js';
import { api } from '../../js/api.js';

describe('shortcut prefs', () => {
  beforeEach(() => { api.mockClear(); setShortcutPrefs({ ...SHORTCUT_DEFAULTS }); });

  it('returns defaults when nothing set', () => {
    expect(getShortcutPrefs()).toEqual({ redo: 'both', enterSubmitsModals: true, vimNav: false });
  });

  it('merges partial updates and keeps other defaults', () => {
    setShortcutPrefs({ redo: 'ctrl-y' });
    expect(getShortcutPrefs()).toEqual({ redo: 'ctrl-y', enterSubmitsModals: true, vimNav: false });
  });

  it('persists via savePreferences (api save_preferences)', () => {
    setShortcutPrefs({ vimNav: true });
    expect(api).toHaveBeenCalledWith('save_preferences', expect.stringContaining('"vimNav":true'));
  });

  it('coerces unknown redo values back to default', () => {
    setShortcutPrefs({ redo: 'nonsense' });
    expect(getShortcutPrefs().redo).toBe('both');
  });
});
```

- [ ] **Step 2: Run test, verify it fails**

Run: `npx vitest run tests/js/shortcut-prefs.test.js`
Expected: FAIL — `getShortcutPrefs is not a function`.

- [ ] **Step 3: Implement in `js/store.js`**

Add the default constant near the top and extend the `preferences` object:

```js
export const SHORTCUT_DEFAULTS = Object.freeze({
  redo: 'both',               // 'both' | 'ctrl-y' | 'ctrl-shift-z'
  enterSubmitsModals: true,
  vimNav: false,
});
```

In the `let preferences = { ... }` initializer, add:

```js
  shortcuts: { ...SHORTCUT_DEFAULTS },
```

In `loadPreferences()`, after the existing `inventory_view` block, add normalization:

```js
    if (stored.shortcuts && typeof stored.shortcuts === 'object') {
      preferences.shortcuts = normalizeShortcuts(stored.shortcuts);
    }
```

Add helpers + exported accessors at the end of the file:

```js
function normalizeShortcuts(s) {
  const redo = ['both', 'ctrl-y', 'ctrl-shift-z'].includes(s.redo) ? s.redo : SHORTCUT_DEFAULTS.redo;
  return {
    redo,
    enterSubmitsModals: typeof s.enterSubmitsModals === 'boolean' ? s.enterSubmitsModals : SHORTCUT_DEFAULTS.enterSubmitsModals,
    vimNav: typeof s.vimNav === 'boolean' ? s.vimNav : SHORTCUT_DEFAULTS.vimNav,
  };
}

export function getShortcutPrefs() {
  return normalizeShortcuts(preferences.shortcuts || {});
}

export function setShortcutPrefs(partial) {
  preferences.shortcuts = normalizeShortcuts({ ...getShortcutPrefs(), ...partial });
  savePreferences();
  preferencesSignal.set(preferences);
}
```

- [ ] **Step 4: Run test, verify it passes**

Run: `npx vitest run tests/js/shortcut-prefs.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
npx eslint js/store.js tests/js/shortcut-prefs.test.js
git add js/store.js tests/js/shortcut-prefs.test.js
git commit -m "feat(a11y): persisted shortcut preferences in store"
```

---

## Task 2: Roving grid controller

A 2D roving-tabindex grid. Pure logic (`computeTarget`) is unit-tested; DOM wiring is thin.

**Files:**
- Create: `js/a11y/roving-grid.js`
- Test: `tests/js/roving-grid.test.js`

**Interfaces:**
- Consumes: `getShortcutPrefs` from `js/store.js` (for `vimNav`).
- Produces:
  - `computeTarget(rows, r, c, key): { r, c } | null` — pure next-cell math. `rows` is an array of cell-counts per row; `key` ∈ `ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Home|End`. `↑/↓` preserve column index clamped to the target row's `count-1`; `←/→` stay in-row (no wrap, return null at edges); `Home/End` jump to col `0`/`count-1`.
  - `RovingGrid(container, { rowSelector, cellSelector, rowKey }): { refresh(): void, destroy(): void }`.

- [ ] **Step 1: Write the failing unit test (pure logic)**

```js
// tests/js/roving-grid.test.js
import { describe, it, expect } from 'vitest';
import { computeTarget } from '../../js/a11y/roving-grid.js';

const rows = [3, 1, 2]; // row0 has 3 cells, row1 has 1, row2 has 2

describe('computeTarget', () => {
  it('ArrowRight moves within a row', () => {
    expect(computeTarget(rows, 0, 0, 'ArrowRight')).toEqual({ r: 0, c: 1 });
  });
  it('ArrowRight at row end returns null (no wrap)', () => {
    expect(computeTarget(rows, 0, 2, 'ArrowRight')).toBeNull();
  });
  it('ArrowLeft at row start returns null', () => {
    expect(computeTarget(rows, 0, 0, 'ArrowLeft')).toBeNull();
  });
  it('ArrowDown preserves column, clamps to shorter row', () => {
    expect(computeTarget(rows, 0, 2, 'ArrowDown')).toEqual({ r: 1, c: 0 }); // row1 has 1 cell -> clamp
  });
  it('ArrowDown keeps column when target row is wide enough', () => {
    expect(computeTarget(rows, 0, 1, 'ArrowDown')).toEqual({ r: 1, c: 0 });
    expect(computeTarget(rows, 1, 0, 'ArrowDown')).toEqual({ r: 2, c: 0 });
  });
  it('ArrowUp from clamped position keeps column index intent', () => {
    expect(computeTarget(rows, 2, 1, 'ArrowUp')).toEqual({ r: 1, c: 0 });
  });
  it('ArrowDown at last row returns null', () => {
    expect(computeTarget(rows, 2, 0, 'ArrowDown')).toBeNull();
  });
  it('Home/End jump within row', () => {
    expect(computeTarget(rows, 0, 1, 'Home')).toEqual({ r: 0, c: 0 });
    expect(computeTarget(rows, 0, 1, 'End')).toEqual({ r: 0, c: 2 });
  });
});
```

- [ ] **Step 2: Run, verify fail**

Run: `npx vitest run tests/js/roving-grid.test.js`
Expected: FAIL — module/function not found.

- [ ] **Step 3: Implement `js/a11y/roving-grid.js`**

```js
/* js/a11y/roving-grid.js — 2D roving-tabindex grid for row-based button groups.
   Tab enters the grid as a single stop; Left/Right move within a row, Up/Down
   move to the same column index in the adjacent row (clamped). */
import { getShortcutPrefs } from '../store.js';

const VIM = { h: 'ArrowLeft', j: 'ArrowDown', k: 'ArrowUp', l: 'ArrowRight' };
const NAV_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);

/** Pure next-cell computation. `rows` = array of per-row cell counts. */
export function computeTarget(rows, r, c, key) {
  if (key === 'ArrowRight') return c + 1 < rows[r] ? { r, c: c + 1 } : null;
  if (key === 'ArrowLeft')  return c - 1 >= 0 ? { r, c: c - 1 } : null;
  if (key === 'Home') return { r, c: 0 };
  if (key === 'End')  return { r, c: rows[r] - 1 };
  if (key === 'ArrowDown') return r + 1 < rows.length ? { r: r + 1, c: Math.min(c, rows[r + 1] - 1) } : null;
  if (key === 'ArrowUp')   return r - 1 >= 0 ? { r: r - 1, c: Math.min(c, rows[r - 1] - 1) } : null;
  return null;
}

export function RovingGrid(container, { rowSelector, cellSelector, rowKey }) {
  let lastKey = null; // remembers focused row key across re-render

  function grid() {
    const rowEls = Array.from(container.querySelectorAll(rowSelector));
    return rowEls
      .map((row) => ({ row, cells: Array.from(row.querySelectorAll(cellSelector)) }))
      .filter((g) => g.cells.length > 0);
  }

  function setRover(cell) {
    container.querySelectorAll(cellSelector).forEach((el) => { el.tabIndex = -1; });
    if (cell) cell.tabIndex = 0;
  }

  function locate(cell) {
    const g = grid();
    for (let r = 0; r < g.length; r++) {
      const c = g[r].cells.indexOf(cell);
      if (c !== -1) return { g, r, c };
    }
    return null;
  }

  function onKeydown(e) {
    let key = e.key;
    if (getShortcutPrefs().vimNav && VIM[key]) key = VIM[key];
    if (!NAV_KEYS.has(key)) return;
    const cell = e.target.closest(cellSelector);
    if (!cell || !container.contains(cell)) return;
    const loc = locate(cell);
    if (!loc) return;
    const counts = loc.g.map((x) => x.cells.length);
    const t = computeTarget(counts, loc.r, loc.c, key);
    if (!t) return;
    e.preventDefault();
    const target = loc.g[t.r].cells[t.c];
    setRover(target);
    target.focus();
    target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function onFocusin(e) {
    const cell = e.target.closest(cellSelector);
    if (!cell || !container.contains(cell)) return;
    setRover(cell);
    if (rowKey) {
      const row = cell.closest(rowSelector);
      if (row) lastKey = row.getAttribute(rowKey);
    }
  }

  function refresh() {
    const g = grid();
    if (!g.length) return;
    // Re-establish a single tab stop, preferring the previously focused row.
    let target = g[0].cells[0];
    if (lastKey && rowKey) {
      const match = g.find((x) => x.row.getAttribute(rowKey) === lastKey);
      if (match) target = match.cells[0];
    }
    setRover(target);
  }

  container.addEventListener('keydown', onKeydown);
  container.addEventListener('focusin', onFocusin);
  refresh();

  return {
    refresh,
    destroy() {
      container.removeEventListener('keydown', onKeydown);
      container.removeEventListener('focusin', onFocusin);
    },
  };
}
```

- [ ] **Step 4: Run, verify pass**

Run: `npx vitest run tests/js/roving-grid.test.js`
Expected: PASS (8 tests).

- [ ] **Step 5: Lint + commit**

```bash
npx eslint js/a11y/roving-grid.js tests/js/roving-grid.test.js
git add js/a11y/roving-grid.js tests/js/roving-grid.test.js
git commit -m "feat(a11y): roving-tabindex grid controller"
```

---

## Task 3: Scrollable regions

**Files:**
- Create: `js/a11y/scrollable.js`
- Test: `tests/js/scrollable.test.js`

**Interfaces:**
- Consumes: `getShortcutPrefs` (vimNav) from `js/store.js`.
- Produces:
  - `scrollDelta(key, clientHeight): number | null` — pure: `ArrowDown`→`+40`, `ArrowUp`→`-40`, `PageDown`→`+round(clientHeight*0.9)`, `PageUp`→`-round(clientHeight*0.9)`, `Home`→`-Infinity`, `End`→`+Infinity`, else `null`.
  - `makeScrollable(el): void` — idempotent; adds `tabindex="0"`, `role="region"`, `data-kbd-scroll`, keydown handler. Only acts when `el` is the event target (not a child control).

- [ ] **Step 1: Failing test**

```js
// tests/js/scrollable.test.js
import { describe, it, expect } from 'vitest';
import { scrollDelta } from '../../js/a11y/scrollable.js';

describe('scrollDelta', () => {
  it('arrows scroll by a line', () => {
    expect(scrollDelta('ArrowDown', 500)).toBe(40);
    expect(scrollDelta('ArrowUp', 500)).toBe(-40);
  });
  it('page keys scroll ~90% of client height', () => {
    expect(scrollDelta('PageDown', 500)).toBe(450);
    expect(scrollDelta('PageUp', 500)).toBe(-450);
  });
  it('Home/End jump to extremes', () => {
    expect(scrollDelta('Home', 500)).toBe(-Infinity);
    expect(scrollDelta('End', 500)).toBe(Infinity);
  });
  it('ignores other keys', () => {
    expect(scrollDelta('Enter', 500)).toBeNull();
  });
});
```

- [ ] **Step 2: Run, verify fail.** `npx vitest run tests/js/scrollable.test.js` → FAIL.

- [ ] **Step 3: Implement `js/a11y/scrollable.js`**

```js
/* js/a11y/scrollable.js — make an overflow container keyboard-scrollable. */
import { getShortcutPrefs } from '../store.js';

const VIM = { h: 'ArrowLeft', j: 'ArrowDown', k: 'ArrowUp', l: 'ArrowRight' };

export function scrollDelta(key, clientHeight) {
  const page = Math.round(clientHeight * 0.9);
  switch (key) {
    case 'ArrowDown': return 40;
    case 'ArrowUp': return -40;
    case 'PageDown': return page;
    case 'PageUp': return -page;
    case 'Home': return -Infinity;
    case 'End': return Infinity;
    default: return null;
  }
}

export function makeScrollable(el) {
  if (!el || el.dataset.kbdScroll === '1') return;
  el.dataset.kbdScroll = '1';
  if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
  if (!el.hasAttribute('role')) el.setAttribute('role', 'region');

  el.addEventListener('keydown', (e) => {
    // Only act when the region itself is focused, not a child control/grid cell.
    if (e.target !== el) return;
    let key = e.key;
    if (getShortcutPrefs().vimNav && VIM[key]) key = VIM[key];
    const d = scrollDelta(key, el.clientHeight);
    if (d === null) return;
    e.preventDefault();
    if (d === -Infinity) el.scrollTop = 0;
    else if (d === Infinity) el.scrollTop = el.scrollHeight;
    else el.scrollTop += d;
  });
}
```

- [ ] **Step 4: Run, verify pass.** → PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
npx eslint js/a11y/scrollable.js tests/js/scrollable.test.js
git add js/a11y/scrollable.js tests/js/scrollable.test.js
git commit -m "feat(a11y): keyboard-scrollable regions"
```

---

## Task 4: Focus trap + key-activation helpers

Small DOM helpers with no pure-math core; verified in the E2E specs of later tasks. Keep them tiny and obviously correct.

**Files:**
- Create: `js/a11y/focus-trap.js`, `js/a11y/activate-on-key.js`

**Interfaces:**
- Produces:
  - `trap(modalEl): void`, `release(): void` (single active trap; `release` restores focus to the previously focused element).
  - `activateOnKey(el): void` — idempotent; adds `tabindex=0`, `role="button"` (if not a `<button>`), Enter/Space → `el.click()`.

- [ ] **Step 1: Implement `js/a11y/focus-trap.js`**

```js
/* js/a11y/focus-trap.js — confine Tab within a modal and restore focus on close. */
const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',');

let active = null;       // { el, prev, handler }

function visibleFocusables(el) {
  return Array.from(el.querySelectorAll(FOCUSABLE))
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
}

export function trap(modalEl) {
  release();
  const prev = document.activeElement;
  const handler = (e) => {
    if (e.key !== 'Tab') return;
    const items = visibleFocusables(modalEl);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    const cur = document.activeElement;
    if (e.shiftKey && (cur === first || !modalEl.contains(cur))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (cur === last || !modalEl.contains(cur))) { e.preventDefault(); first.focus(); }
  };
  modalEl.addEventListener('keydown', handler);
  active = { el: modalEl, prev, handler };

  // Initial focus: [autofocus] -> first focusable -> the modal itself.
  const initial = modalEl.querySelector('[autofocus]') || visibleFocusables(modalEl)[0] || modalEl;
  if (initial === modalEl && !modalEl.hasAttribute('tabindex')) modalEl.tabIndex = -1;
  initial.focus();
}

export function release() {
  if (!active) return;
  active.el.removeEventListener('keydown', active.handler);
  const { prev } = active;
  active = null;
  if (prev && typeof prev.focus === 'function') prev.focus();
}
```

- [ ] **Step 2: Implement `js/a11y/activate-on-key.js`**

```js
/* js/a11y/activate-on-key.js — make a clickable non-button keyboard-activatable. */
export function activateOnKey(el) {
  if (!el || el.dataset.kbdActivate === '1') return;
  el.dataset.kbdActivate = '1';
  if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
  if (el.tagName !== 'BUTTON' && !el.hasAttribute('role')) el.setAttribute('role', 'button');
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      el.click();
    }
  });
}
```

- [ ] **Step 3: Lint + commit**

```bash
npx eslint js/a11y/focus-trap.js js/a11y/activate-on-key.js
git add js/a11y/focus-trap.js js/a11y/activate-on-key.js
git commit -m "feat(a11y): focus-trap and key-activation helpers"
```

---

## Task 5: Global focus-visible ring

**Files:**
- Create: `css/a11y.css`
- Modify: `index.html` (add `<link>` after the other stylesheets, before the module script)

- [ ] **Step 1: Create `css/a11y.css`**

```css
/* a11y.css — keyboard-only focus ring. Loaded last so it wins over outline:none. */
:focus-visible {
  outline: 2px solid var(--color-blue, #58a6ff);
  outline-offset: 2px;
  border-radius: 2px;
}
/* Inputs already show a blue border on :focus; keep that and add the ring on
   keyboard focus only. Mouse :focus (no -visible) shows no ring. */
button:focus:not(:focus-visible),
[role="button"]:focus:not(:focus-visible),
[data-kbd-scroll]:focus:not(:focus-visible) {
  outline: none;
}
```

- [ ] **Step 2: Add the link in `index.html`** (after `css/components/scan-grouping.css`, line ~80):

```html
<link rel="stylesheet" href="css/a11y.css">
```

- [ ] **Step 3: Verify load** — `npx eslint js/` (no-op for CSS) then open quick sanity: `node scripts/serve-static.mjs . 3123 &` is not required; the E2E spec in Task 11 (`focus-ring`) is the real check. For now confirm the file is referenced:

Run: `grep -n a11y.css index.html`
Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add css/a11y.css index.html
git commit -m "feat(a11y): global focus-visible ring stylesheet"
```

---

## Task 6: Wire grids, scroll regions, and activation (keyboard-nav init)

**Files:**
- Create: `js/a11y/keyboard-nav.js`
- Modify: `js/app-init.js` (import + call `initKeyboardNav()`)
- Test (E2E): `tests/js/e2e/keyboard-nav.spec.mjs`, `tests/js/e2e/scroll-keyboard.spec.mjs`

**Interfaces:**
- Consumes: `RovingGrid` (Task 2), `makeScrollable` (Task 3), `activateOnKey` (Task 4), `EventBus`/`Events`.
- Produces: `initKeyboardNav(): void`.

Cell selector for inventory rows includes row content per spec (MPN, badges, qty/warn, group/near-miss badges, action buttons). Use a single selector string.

- [ ] **Step 1: Write the failing E2E test (inventory grid)**

```js
// tests/js/e2e/keyboard-nav.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Inventory roving grid', () => {
  test('arrow keys move focus within and across rows', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Focus the first grid cell directly (single tab stop established by refresh()).
    const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
    await firstCell.focus();
    await expect(firstCell).toBeFocused();

    // ArrowRight stays in the same row, moves to the next focusable cell.
    const r0 = await firstCell.evaluate((el) => el.closest('.inv-part-row')?.dataset.partId);
    await page.keyboard.press('ArrowRight');
    const afterRight = await page.evaluate(() => ({
      pid: document.activeElement?.closest('.inv-part-row')?.dataset.partId,
    }));
    expect(afterRight.pid).toBe(r0);

    // ArrowDown moves to a different row.
    await page.keyboard.press('ArrowDown');
    const afterDown = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
    expect(afterDown).not.toBe(r0);
  });

  test('only one tab stop exists in the inventory grid', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    const count = await page.locator('#inventory-body [tabindex="0"]').count();
    expect(count).toBe(1);
  });
});
```

- [ ] **Step 2: Run, verify fail.** `npx playwright test keyboard-nav --project=functional` → FAIL (multiple/zero tab stops; no roving).

- [ ] **Step 3: Implement `js/a11y/keyboard-nav.js`**

```js
/* js/a11y/keyboard-nav.js — wire roving grids, scrollable regions, and key
   activation across the app; re-apply after re-renders. */
import { RovingGrid } from './roving-grid.js';
import { makeScrollable } from './scrollable.js';
import { activateOnKey } from './activate-on-key.js';
import { EventBus, Events } from '../event-bus.js';

const INV_CELLS = [
  '.part-mpn', '.no-dist-warn', '.price-warn-btn', '.generic-group-badge',
  '.near-miss-badge', '.adj-btn', '.link-btn',
].join(',');

const BOM_CELLS = [
  '.swap-btn', '.adj-btn', '.confirm-btn', '.link-btn', '.row-delete',
].join(',');

const SCROLL_SELECTORS = [
  '.panel-body', '.prefs-sliders', '.vendors-list', '.vendors-detail-col',
  '.bom-table-wrap', '.flyout-members', '.refs-scroll', '.console-entries',
  '.label-export-preview', '.label-po-list', '.import-preview',
  '.scan-grouping-list', '.ocr-grid-pane',
];

const ACTIVATE_SELECTORS = [
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
  '.label-po-row',
];

let invGrid = null;
let bomGrid = null;

function applyScrollables(root = document) {
  SCROLL_SELECTORS.forEach((sel) => root.querySelectorAll(sel).forEach(makeScrollable));
}
function applyActivation(root = document) {
  ACTIVATE_SELECTORS.forEach((sel) => root.querySelectorAll(sel).forEach(activateOnKey));
}

export function initKeyboardNav() {
  const invBody = document.getElementById('inventory-body');
  const bomBody = document.getElementById('bom-body');

  if (invBody) {
    invGrid = RovingGrid(invBody, {
      rowSelector: '.inv-part-row', cellSelector: INV_CELLS, rowKey: 'data-part-id',
    });
  }

  function refreshInventory() {
    if (invGrid) invGrid.refresh();
    applyScrollables();
    applyActivation();
  }
  function refreshBom() {
    if (!bomGrid && bomBody && bomBody.querySelector('.inv-part-row, tbody')) {
      const gridRoot = bomBody.querySelector('#bom-tbody') || bomBody;
      bomGrid = RovingGrid(gridRoot, {
        rowSelector: 'tr, .inv-part-row', cellSelector: BOM_CELLS, rowKey: 'data-bom-key',
      });
    } else if (bomGrid) {
      bomGrid.refresh();
    }
    applyScrollables();
  }

  EventBus.on(Events.INVENTORY_LOADED, refreshInventory);
  EventBus.on(Events.INVENTORY_UPDATED, refreshInventory);
  EventBus.on(Events.BOM_LOADED, refreshBom);
  EventBus.on(Events.BOM_CLEARED, refreshBom);

  // First pass for anything already in the DOM.
  applyScrollables();
  applyActivation();
}
```

- [ ] **Step 4: Import + call in `js/app-init.js`** — add near the other imports:

```js
import { initKeyboardNav } from './a11y/keyboard-nav.js';
```

and call it inside the init function after the inventory body exists (right before `await whenPywebviewReady();`):

```js
  initKeyboardNav();
```

- [ ] **Step 5: Run, verify pass.** `npx playwright test keyboard-nav --project=functional` → PASS.

- [ ] **Step 6: Write + run the scroll-keyboard E2E**

```js
// tests/js/e2e/scroll-keyboard.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('focused panel body scrolls with PageDown and Home/End', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  const body = page.locator('#inventory-body');
  await body.evaluate((el) => el.focus());
  const before = await body.evaluate((el) => el.scrollTop);
  await page.keyboard.press('PageDown');
  const after = await body.evaluate((el) => el.scrollTop);
  expect(after).toBeGreaterThan(before);

  await page.keyboard.press('End');
  const atEnd = await body.evaluate((el) => el.scrollTop);
  await page.keyboard.press('Home');
  const atHome = await body.evaluate((el) => el.scrollTop);
  expect(atEnd).toBeGreaterThan(atHome);
  expect(atHome).toBe(0);
});
```

Run: `npx playwright test scroll-keyboard --project=functional`
Expected: PASS. (If the panel doesn't overflow at this viewport, the fixture has enough rows; if flaky, lower viewport height.)

- [ ] **Step 7: Commit**

```bash
git add js/a11y/keyboard-nav.js js/app-init.js tests/js/e2e/keyboard-nav.spec.mjs tests/js/e2e/scroll-keyboard.spec.mjs
git commit -m "feat(a11y): wire roving grids, scroll regions, key activation"
```

---

## Task 7: Modal focus trap + Enter-to-confirm

**Files:**
- Modify: `js/ui-helpers.js` (`Modal()`), `js/inventory-modals.js` (migrate hand-rolled Enter handlers)
- Test (E2E): `tests/js/e2e/modal-focus-trap.spec.mjs`

**Interfaces:**
- Consumes: `trap`, `release` from `js/a11y/focus-trap.js`; `getShortcutPrefs` from `js/store.js`.
- Produces: `Modal(id, { onClose, cancelId, confirmId })` — on open: `trap(el)`; on close: `release()`. When `confirmId` set and `getShortcutPrefs().enterSubmitsModals`, a plain Enter (not from `textarea` or `#adj-note`) clicks `confirmId`.

- [ ] **Step 1: Write the failing E2E**

```js
// tests/js/e2e/modal-focus-trap.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('preferences modal traps focus and restores it on Escape', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  const prefsBtn = page.locator('#prefs-btn');
  await prefsBtn.focus();
  await prefsBtn.click();
  await expect(page.locator('#prefs-modal')).toBeVisible();

  // Focus is inside the modal.
  const insideAtOpen = await page.evaluate(() => !!document.getElementById('prefs-modal')?.contains(document.activeElement));
  expect(insideAtOpen).toBe(true);

  // Tab several times — focus never leaves the modal.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    const inside = await page.evaluate(() => !!document.getElementById('prefs-modal')?.contains(document.activeElement));
    expect(inside).toBe(true);
  }

  // Escape closes and restores focus to the trigger.
  await page.keyboard.press('Escape');
  await expect(page.locator('#prefs-modal')).toBeHidden();
  await expect(prefsBtn).toBeFocused();
});
```

- [ ] **Step 2: Run, verify fail.** → FAIL (focus escapes / not restored).

- [ ] **Step 3: Update `Modal()` in `js/ui-helpers.js`**

```js
import { trap, release } from './a11y/focus-trap.js';
import { getShortcutPrefs } from './store.js';

export function Modal(id, { onClose, cancelId, confirmId } = {}) {
  const el = document.getElementById(id);
  function open()  { el.classList.remove("hidden"); trap(el); }
  function close() { el.classList.add("hidden"); release(); if (onClose) onClose(); }
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  if (cancelId) document.getElementById(cancelId).addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (el.classList.contains("hidden")) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key === "Enter" && confirmId && getShortcutPrefs().enterSubmitsModals) {
      const t = e.target;
      if (t instanceof Element && t.closest('textarea, #adj-note')) return;
      e.preventDefault();
      const btn = document.getElementById(confirmId);
      if (btn && !btn.disabled) btn.click();
    }
  });
  return { el, open, close };
}
```

- [ ] **Step 4: Pass `confirmId` where each modal is constructed.** Update the seven `Modal(...)` call sites to add `confirmId`:
  - `prefs-modal` → `confirmId: 'prefs-save'`
  - `consume-modal` → `confirmId: 'consume-confirm'`
  - `price-modal` → `confirmId: 'price-apply'`
  - `adjust-modal` → `confirmId: 'adj-apply'`
  - `label-export-modal` → `confirmId: 'label-export-do'`
  - `vendors-modal` → (no single confirm; leave unset)
  - `close-modal` → `confirmId: 'close-save'`

  Then **remove** the now-redundant hand-rolled Enter handlers in `js/inventory-modals.js` (the `keydown`/Enter blocks for adjust ~line 204 and price ~line 263) so behavior isn't doubled. Verify by search:

  Run: `grep -n "Enter" js/inventory-modals.js`
  Expected: no remaining manual Enter-to-apply handlers (the factory now owns it).

- [ ] **Step 5: Run, verify pass.** `npx playwright test modal-focus-trap --project=functional` → PASS.

- [ ] **Step 6: Regression — adjust/price still apply on Enter.** Run the live specs that cover them:

Run: `npx playwright test adjust-modal --project=live`
Expected: PASS (Enter still applies via the factory).

- [ ] **Step 7: Commit**

```bash
git add js/ui-helpers.js js/inventory-modals.js js/app-init.js tests/js/e2e/modal-focus-trap.spec.mjs
git commit -m "feat(a11y): modal focus trap + uniform Enter-to-confirm"
```

---

## Task 8: Central shortcut dispatcher

**Files:**
- Create: `js/a11y/shortcuts.js`
- Modify: `js/app-init.js` (remove the inline undo/redo keydown block; call `initShortcuts(...)`), `js/bom/bom-events.js` (extract `saveBomFile()`)
- Test: `tests/js/shortcuts.test.js` (unit, binding match), `tests/js/e2e/shortcuts.spec.mjs`

**Interfaces:**
- Consumes: `getShortcutPrefs`; `UndoRedo`; `EventBus`/`Events`; `showToast`.
- Produces:
  - `matchesRedo(e, redoPref): boolean` — pure. `ctrl-shift-z`: Ctrl/Meta+Shift+Z. `ctrl-y`: Ctrl/Meta+Y. `both`: either.
  - `initShortcuts({ undo, redo, save, openPreferences, focusPanel, exitMode, showHelp }): void` — registers one document keydown listener dispatching to the provided command callbacks.

- [ ] **Step 1: Failing unit test**

```js
// tests/js/shortcuts.test.js
import { describe, it, expect } from 'vitest';
import { matchesRedo } from '../../js/a11y/shortcuts.js';

const ev = (o) => ({ ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, key: '', ...o });

describe('matchesRedo', () => {
  it('ctrl-shift-z matches Ctrl+Shift+Z only', () => {
    expect(matchesRedo(ev({ ctrlKey: true, shiftKey: true, key: 'Z' }), 'ctrl-shift-z')).toBe(true);
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'ctrl-shift-z')).toBe(false);
  });
  it('ctrl-y matches Ctrl+Y only', () => {
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'ctrl-y')).toBe(true);
    expect(matchesRedo(ev({ ctrlKey: true, shiftKey: true, key: 'Z' }), 'ctrl-y')).toBe(false);
  });
  it('both matches either', () => {
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'both')).toBe(true);
    expect(matchesRedo(ev({ metaKey: true, shiftKey: true, key: 'Z' }), 'both')).toBe(true);
  });
  it('alt disqualifies', () => {
    expect(matchesRedo(ev({ ctrlKey: true, altKey: true, key: 'y' }), 'both')).toBe(false);
  });
});
```

- [ ] **Step 2: Run, verify fail.** → FAIL.

- [ ] **Step 3: Implement `js/a11y/shortcuts.js`**

```js
/* js/a11y/shortcuts.js — central keyboard-shortcut dispatcher. */
import { getShortcutPrefs } from '../store.js';

const mod = (e) => (e.ctrlKey || e.metaKey) && !e.altKey;

export function matchesRedo(e, redoPref) {
  if (!mod(e)) return false;
  const y = e.key === 'y' || e.key === 'Y';
  const shiftZ = e.shiftKey && (e.key === 'z' || e.key === 'Z');
  if (redoPref === 'ctrl-y') return y && !e.shiftKey;
  if (redoPref === 'ctrl-shift-z') return shiftZ;
  return (y && !e.shiftKey) || shiftZ; // both
}

function isUndo(e) { return mod(e) && !e.shiftKey && (e.key === 'z' || e.key === 'Z'); }

function typingTarget(e) {
  const t = e.target;
  return t instanceof Element && t.closest('input, textarea, select, [contenteditable="true"]');
}

export function initShortcuts(cmd) {
  document.addEventListener('keydown', (e) => {
    const prefs = getShortcutPrefs();

    // Global (work even while typing): Undo/Redo/Save/Preferences.
    if (isUndo(e)) { e.preventDefault(); cmd.undo(); return; }
    if (matchesRedo(e, prefs.redo)) { e.preventDefault(); cmd.redo(); return; }
    if (mod(e) && (e.key === 's' || e.key === 'S')) { e.preventDefault(); cmd.save(); return; }
    if (mod(e) && e.key === ',') { e.preventDefault(); cmd.openPreferences(); return; }
    if (mod(e) && (e.key === '1' || e.key === '2' || e.key === '3')) {
      e.preventDefault(); cmd.focusPanel(Number(e.key)); return;
    }

    // Context-sensitive: skip while typing.
    if (typingTarget(e)) return;
    if (e.key === 'Escape') { cmd.exitMode(); return; }     // does not preventDefault; modal Escape still runs
    if (e.key === '?' || e.key === 'F1') { e.preventDefault(); cmd.showHelp(); return; }
  });
}
```

- [ ] **Step 4: Run, verify pass.** → PASS (4 tests).

- [ ] **Step 5: Extract `saveBomFile()` in `js/bom/bom-events.js`** — refactor the `SAVE_AND_CLOSE` handler so the save logic is reusable without closing:

```js
export async function saveBomFile() {
  if (!state.bomHeaders.length || !state.bomRawRows.length) {
    showToast('No BOM to save');
    return false;
  }
  const csvText = generateCSV(state.bomHeaders, state.bomRawRows);
  const linksJson = store.links.hasLinks() ? JSON.stringify({
    manualLinks: store.links.manualLinks,
    confirmedMatches: store.links.confirmedMatches,
  }) : null;
  const result = await api('save_file_dialog', csvText, state.lastFileName || 'bom.csv', store.preferences.lastBomDir || null, linksJson);
  if (result && result.path) {
    state.bomDirty = false; setBomDirty(false); api('set_bom_dirty', false);
    store.preferences.lastBomFile = result.path; await savePreferences();
    return true;
  }
  return false;
}
```

Then make the `SAVE_AND_CLOSE` handler call it:

```js
  EventBus.on(Events.SAVE_AND_CLOSE, async () => {
    await saveBomFile();
    api('confirm_close');
  });
```

(Add `showToast` to the existing import from `ui-helpers.js` if not present.)

- [ ] **Step 6: Wire the dispatcher in `js/app-init.js`** — remove the existing inline `document.addEventListener("keydown", ...)` undo/redo block and replace with:

```js
import { initShortcuts } from './a11y/shortcuts.js';
import { saveBomFile } from './bom/bom-events.js';
// ...
  initShortcuts({
    undo: async () => { if (UndoRedo.canUndo()) { await UndoRedo.undo(); syncUndoRedoButtons(); } },
    redo: async () => { if (UndoRedo.canRedo()) { await UndoRedo.redo(); syncUndoRedoButtons(); } },
    save: () => saveBomFile(),
    openPreferences: () => prefsModalOpen(),  // see note
    focusPanel: (n) => {
      const id = n === 1 ? 'import-body' : n === 2 ? 'inventory-body' : 'bom-body';
      const panel = document.getElementById(id);
      const first = panel?.querySelector('[tabindex="0"]') || panel;
      if (first) first.focus();
    },
    exitMode: () => EventBus.emit(Events.LINKING_MODE, { active: false }),  // see note
    showHelp: () => {},  // replaced in Task 9
  });
```

**Notes for the implementer:**
- `prefsModalOpen()`: the prefs modal is owned by `preferences-modal.js`. Export an `openPreferences()` from there (wrapping `prefsModal.open()`) and import it here; if the existing `#prefs-btn` click handler already calls a function, reuse it.
- `exitMode`: reuse the existing Escape-exits-linking handler. If exiting label mode needs a call, click `#label-done-btn` when `#label-toolbar` is visible: `const t = document.getElementById('label-toolbar'); if (t && !t.classList.contains('hidden')) document.getElementById('label-done-btn').click();` — fold both (label + linking) into `exitMode`.

- [ ] **Step 7: Write + run the shortcuts E2E**

```js
// tests/js/e2e/shortcuts.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test.describe('Global shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('Ctrl+, opens preferences', async ({ page }) => {
    await page.keyboard.press('Control+,');
    await expect(page.locator('#prefs-modal')).toBeVisible();
  });

  test('Ctrl+2 focuses the inventory panel', async ({ page }) => {
    await page.keyboard.press('Control+2');
    const inside = await page.evaluate(() => !!document.getElementById('inventory-body')?.contains(document.activeElement) || document.activeElement?.id === 'inventory-body');
    expect(inside).toBe(true);
  });

  test('Ctrl+1/2/3 move focus between panels', async ({ page }) => {
    await page.keyboard.press('Control+1');
    const p1 = await page.evaluate(() => document.activeElement?.closest('.panel')?.id);
    await page.keyboard.press('Control+3');
    const p3 = await page.evaluate(() => document.activeElement?.closest('.panel')?.id);
    expect(p1).toBe('panel-import');
    expect(p3).toBe('panel-bom');
  });
});
```

Run: `npx playwright test shortcuts --project=functional`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add js/a11y/shortcuts.js js/app-init.js js/bom/bom-events.js js/preferences-modal.js tests/js/shortcuts.test.js tests/js/e2e/shortcuts.spec.mjs
git commit -m "feat(a11y): central shortcut dispatcher (undo/redo/save/prefs/panel-jump)"
```

---

## Task 9: Shortcut-help overlay

**Files:**
- Create: `js/a11y/shortcut-help.js`
- Modify: `index.html` (add a `#help-modal` overlay), `js/app-init.js` (wire `showHelp` to it)
- Test (E2E): folded into `tests/js/e2e/shortcuts.spec.mjs`

**Interfaces:**
- Consumes: `Modal` from `ui-helpers.js`; `getShortcutPrefs`.
- Produces: `initShortcutHelp(): { open(): void }`.

- [ ] **Step 1: Add the modal markup in `index.html`** (next to the other modals):

```html
<div class="modal-overlay hidden" id="help-modal">
  <div class="modal">
    <div class="modal-title">Keyboard Shortcuts</div>
    <div class="prefs-sliders" id="help-body"></div>
    <div class="modal-actions">
      <button class="btn-lg btn btn-cancel" id="help-close">Close</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Implement `js/a11y/shortcut-help.js`**

```js
/* js/a11y/shortcut-help.js — '?' / F1 overlay listing keyboard shortcuts. */
import { Modal, escHtml } from '../ui-helpers.js';
import { getShortcutPrefs } from '../store.js';

const ROWS = (redo) => [
  ['Ctrl+F', 'Focus search'],
  ['Ctrl+S', 'Save BOM'],
  ['Ctrl+Z', 'Undo'],
  [redo === 'ctrl-y' ? 'Ctrl+Y' : redo === 'ctrl-shift-z' ? 'Ctrl+Shift+Z' : 'Ctrl+Y or Ctrl+Shift+Z', 'Redo'],
  ['Ctrl+,', 'Preferences'],
  ['Ctrl+1 / 2 / 3', 'Focus Import / Inventory / BOM panel'],
  ['Arrows', 'Move between row buttons / scroll a focused region'],
  ['Enter', 'Confirm the open dialog'],
  ['Esc', 'Close dialog / exit linking or label mode'],
  ['? or F1', 'This help'],
];

export function initShortcutHelp() {
  const modal = Modal('help-modal', { cancelId: 'help-close' });
  function open() {
    const redo = getShortcutPrefs().redo;
    document.getElementById('help-body').innerHTML = ROWS(redo)
      .map(([k, d]) => `<div class="prefs-row"><label class="prefs-label">${escHtml(d)}</label><kbd>${escHtml(k)}</kbd></div>`)
      .join('');
    modal.open();
  }
  return { open };
}
```

- [ ] **Step 3: Wire in `js/app-init.js`** — replace the `showHelp: () => {}` stub:

```js
import { initShortcutHelp } from './a11y/shortcut-help.js';
// ... inside init, before initShortcuts:
  const help = initShortcutHelp();
// ... in the initShortcuts config:
    showHelp: () => help.open(),
```

- [ ] **Step 4: Add an E2E case to `shortcuts.spec.mjs`**

```js
  test('? opens the shortcut help overlay', async ({ page }) => {
    await page.keyboard.press('?');
    await expect(page.locator('#help-modal')).toBeVisible();
    await expect(page.locator('#help-body')).toContainText('Redo');
    await page.keyboard.press('Escape');
    await expect(page.locator('#help-modal')).toBeHidden();
  });
```

Run: `npx playwright test shortcuts --project=functional`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/a11y/shortcut-help.js index.html js/app-init.js tests/js/e2e/shortcuts.spec.mjs
git commit -m "feat(a11y): keyboard shortcut help overlay (? / F1)"
```

---

## Task 10: Preferences "Keyboard" section

**Files:**
- Modify: `index.html` (Keyboard block in `#prefs-modal`), `js/preferences-modal.js` (render/load/save the controls)
- Test (E2E): `tests/js/e2e/keyboard-prefs.spec.mjs`

**Interfaces:**
- Consumes: `getShortcutPrefs`, `setShortcutPrefs` from `js/store.js`.

- [ ] **Step 1: Add markup in `index.html`** inside `#prefs-modal`, after the sliders divider:

```html
<div class="modal-divider"></div>
<div class="modal-subtitle" style="margin-bottom:4px"><strong>Keyboard</strong></div>
<div class="modal-form" style="flex-direction:column;align-items:flex-start;gap:8px">
  <label style="display:flex;gap:8px;align-items:center">Redo binding
    <select id="pref-redo">
      <option value="both">Ctrl+Y and Ctrl+Shift+Z</option>
      <option value="ctrl-shift-z">Ctrl+Shift+Z only</option>
      <option value="ctrl-y">Ctrl+Y only</option>
    </select>
  </label>
  <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pref-enter-submit"> Enter submits modals</label>
  <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pref-vim-nav"> Vim-style hjkl navigation</label>
</div>
```

- [ ] **Step 2: Write the failing E2E**

```js
// tests/js/e2e/keyboard-prefs.spec.mjs
// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

test('redo pref restricts the binding live', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await page.locator('#pref-redo').selectOption('ctrl-y');
  await expect(page.locator('#pref-redo')).toHaveValue('ctrl-y');

  // With redo restricted to Ctrl+Y, Ctrl+Shift+Z must no longer redo. Verify the
  // select change took effect by reloading the modal: close + reopen reads it back.
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+,');
  await expect(page.locator('#pref-redo')).toHaveValue('ctrl-y');
});

test('vim nav toggle moves the grid with j/k', async ({ page }) => {
  await addMockSetup(page, MOCK_INVENTORY);
  await page.goto('/index.html');
  await waitForInventoryRows(page);

  await page.keyboard.press('Control+,');
  await page.locator('#pref-vim-nav').check();
  await page.keyboard.press('Escape');

  const firstCell = page.locator('#inventory-body [tabindex="0"]').first();
  await firstCell.focus();
  const r0 = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
  await page.keyboard.press('j');
  const r1 = await page.evaluate(() => document.activeElement?.closest('.inv-part-row')?.dataset.partId);
  expect(r1).not.toBe(r0);
});
```

- [ ] **Step 3: Run, verify fail.** → FAIL (controls not wired).

- [ ] **Step 4: Wire controls in `js/preferences-modal.js`** — import the accessors and populate/save on the existing open + save flow:

```js
import { getShortcutPrefs, setShortcutPrefs } from './store.js';

function syncKeyboardPrefs() {
  const p = getShortcutPrefs();
  document.getElementById('pref-redo').value = p.redo;
  document.getElementById('pref-enter-submit').checked = p.enterSubmitsModals;
  document.getElementById('pref-vim-nav').checked = p.vimNav;
}

function wireKeyboardPrefs() {
  document.getElementById('pref-redo').addEventListener('change', (e) => setShortcutPrefs({ redo: e.target.value }));
  document.getElementById('pref-enter-submit').addEventListener('change', (e) => setShortcutPrefs({ enterSubmitsModals: e.target.checked }));
  document.getElementById('pref-vim-nav').addEventListener('change', (e) => setShortcutPrefs({ vimNav: e.target.checked }));
}
```

Call `wireKeyboardPrefs()` once at module init, and `syncKeyboardPrefs()` inside the prefs-modal `open()` path (wherever sliders are currently (re)built). Changes apply immediately (live), so no extra Save wiring is needed; the Save button still persists everything.

- [ ] **Step 5: Run, verify pass.** `npx playwright test keyboard-prefs --project=functional` → PASS.

- [ ] **Step 6: Commit**

```bash
git add index.html js/preferences-modal.js tests/js/e2e/keyboard-prefs.spec.mjs
git commit -m "feat(a11y): Preferences keyboard section (redo binding, Enter-submit, vim nav)"
```

---

## Task 11: Reconverge — full verification

**Files:** none new (fix-ups only).

- [ ] **Step 1: Lint + type check**

Run: `npx eslint js/ && npx tsc --noEmit`
Expected: clean. Fix any issues in the touched files.

- [ ] **Step 2: Unit tests**

Run: `npx vitest run`
Expected: all PASS (including the four new suites).

- [ ] **Step 3: E2E — functional + quality projects**

Run: `npx playwright test --project=functional --project=quality`
Expected: all PASS, including the new specs **and** the untouched `resize-visibility.spec.mjs` / sticky-button checks (guardrail).

- [ ] **Step 4: E2E — live project (modal Enter regressions)**

Run: `npx playwright test --project=live`
Expected: PASS (adjust/price/consume still apply via Enter; undo-redo spec still passes).

- [ ] **Step 5: Accessibility regression**

Run: `npx playwright test accessibility --project=quality`
Expected: no new axe violations introduced by added roles/tabindex.

- [ ] **Step 6: Final commit (if any fix-ups)**

```bash
git add -A
git commit -m "test(a11y): reconverge — full lint/type/unit/e2e green"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** Tasks 1–10 map 1:1 to the spec's execution list; Task 11 is the reconverge step. Row-cell grid (incl. MPN/badges) → Task 6 `INV_CELLS`. Configurable redo/enter/vim → Tasks 1, 7, 8, 10.
- **Type consistency:** `getShortcutPrefs()` shape is identical everywhere (`{ redo, enterSubmitsModals, vimNav }`). `computeTarget(rows, r, c, key)` and `scrollDelta(key, clientHeight)` signatures are stable across their tests and call sites.
- **Known soft spots to watch during execution:**
  1. BOM grid root selector (`#bom-tbody` vs `.inv-part-row`) — confirm against the live BOM DOM before finalizing `BOM_CELLS`/`rowSelector` in Task 6; adjust `rowKey` to whatever the BOM rows actually carry.
  2. `openPreferences`/`exitMode` wiring in Task 8 depends on existing functions in `preferences-modal.js`/`inv-events.js` — export/reuse rather than duplicate.
  3. The `scroll-keyboard` test needs the inventory body to actually overflow; if the fixture is short, reduce viewport height.
