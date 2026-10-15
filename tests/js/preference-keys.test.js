/* Guard: every preference key must be classified in js/preferences-logic.js.

   The sibling of tests/js/panel-collapse-events.test.js, which does this for the
   REOPEN_TRIGGERS table. The failure this exists to prevent is the quiet one:
   `preferencesSignal` now publishes for real, so a key added tomorrow either
   repaints the grid when it must not, or silently never repaints when it must —
   and both look fine to whoever added it, because nothing anywhere reads the
   table except this test.

   The key set is not hand-listed here. It is enumerated from the app:

   1. every key `loadPreferences()` copies out of the stored payload — that
      loader is the only thing that puts a value into the in-memory bag, so a key
      it does not read is a key that reads back as its default;
   2. every key of the initial `preferences` literal in js/store.js;
   3. every key any module assigns.

   (1) is the spine: the documented trap of that loader is that a key it does not
   read is erased from preferences.json by the next whole-bag save, so (1) is the
   complete set of keys that can exist at runtime, and (2)/(3) are checked against
   it rather than trusted. */
import { describe, it, expect, vi } from 'vitest';

// store.js is imported for SHORTCUT_DEFAULTS / BEHAVIOR_DEFAULTS, so it needs the
// same mocks tests/js/store.test.js gives it: constants.js does a top-level
// `await fetch`, and api.js reaches for the network at import time.
vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: ['Resistors', { name: 'Capacitors', children: ['MLCC'] }],
  FIELDNAMES: [],
}));
vi.mock('../../js/api.js', () => ({
  setSourceHeaderProvider: () => {},
  api: vi.fn().mockResolvedValue(undefined),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(), escHtml: (s) => s || '', Modal: vi.fn(), formatMoney: vi.fn(),
}));

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  REACTIONS,
  REACTION_OWNERS,
  PREFERENCE_REACTIONS,
  reactionFor,
  repaintsGrid,
  replansCart,
} from '../../js/preferences-logic.js';
import { SHORTCUT_DEFAULTS, BEHAVIOR_DEFAULTS } from '../../js/store.js';

const JS_DIR = fileURLToPath(new URL('../../js', import.meta.url));
const STORE_SRC = readFileSync(new URL('../../js/store.js', import.meta.url), 'utf8');

/** Every .js file under js/, with its path. */
function jsSources(dir = JS_DIR, acc = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) jsSources(full, acc);
    else if (name.endsWith('.js')) acc.push({ path: full, src: readFileSync(full, 'utf8') });
  }
  return acc;
}
const ALL_JS = jsSources();

/** The body of loadPreferences — the only writer of the in-memory bag's keys. */
function loaderBody() {
  const start = STORE_SRC.indexOf('export async function loadPreferences(');
  const end = STORE_SRC.indexOf('export async function savePreferences(', start + 1);
  if (start < 0 || end < 0) {
    throw new Error('loadPreferences/savePreferences not both found in js/store.js — update this enumeration');
  }
  return STORE_SRC.slice(start, end);
}

/** Keys the loader reads out of the stored payload, in either of its two forms. */
function loaderReadKeys() {
  const body = loaderBody();
  const keys = new Set();
  for (const m of body.matchAll(/\bstored\.([A-Za-z_$][\w$]*)/g)) keys.add(m[1]);
  for (const m of body.matchAll(/hasOwnProperty\.call\(stored,\s*'([^']+)'/g)) keys.add(m[1]);
  if (keys.size < 10) throw new Error(`Suspiciously few loader keys (${keys.size}) — the enumeration broke`);
  return keys;
}

/** Top-level keys of the `let preferences = { … }` literal. */
function initialLiteralKeys() {
  const start = STORE_SRC.indexOf('let preferences = {');
  const end = STORE_SRC.indexOf('\n};', start + 1);
  if (start < 0 || end < 0) throw new Error('the initial preferences literal is not findable — update this enumeration');
  const keys = new Set();
  for (const m of STORE_SRC.slice(start, end).matchAll(/^ {2}([A-Za-z_$][\w$]*):/gm)) keys.add(m[1]);
  return keys;
}

/** `preferences.<key> = …` / `store.preferences.<key>[x] = …` across all of js/. */
function writtenKeys() {
  const WRITE = /(?:^|[^.\w])(?:store\.)?preferences\.([A-Za-z_$][\w$]*)\s*(?:\[[^\]]*\])?\s*=(?!=)/g;
  /** @type {Map<string, string[]>} */
  const byKey = new Map();
  for (const { path, src } of ALL_JS) {
    for (const m of src.matchAll(WRITE)) {
      const rel = path.slice(JS_DIR.length + 1);
      if (!byKey.has(m[1])) byKey.set(m[1], []);
      byKey.get(m[1]).push(rel);
    }
  }
  return byKey;
}

const LOADER_KEYS = loaderReadKeys();
const LITERAL_KEYS = initialLiteralKeys();
const WRITTEN = writtenKeys();
const TABLE_KEYS = Object.keys(PREFERENCE_REACTIONS);
const TOP_LEVEL = new Set(TABLE_KEYS.map((k) => k.split('.')[0]));

describe('preference key enumeration', () => {
  it('the initial literal is read back by loadPreferences (the erasure trap)', () => {
    // savePreferences() posts the WHOLE in-memory object and the loader copies
    // known keys only, so a literal key the loader skips reads back as its
    // default and is erased from disk by the next unrelated save.
    for (const key of LITERAL_KEYS) {
      expect(LOADER_KEYS, `preferences.${key} is in the literal but loadPreferences never reads it back`)
        .toContain(key);
    }
  });

  it('every key any module writes is read back by loadPreferences', () => {
    // The same trap seen from the write side, and the one that actually bites:
    // a writer has no way to notice its key is dropped on the next launch.
    // (lastOcrTemplate sat in exactly this state — written, read by the import
    // panel, never carried through the loader.)
    for (const [key, files] of WRITTEN) {
      expect(LOADER_KEYS, `preferences.${key} is written by ${files.join(', ')} but loadPreferences never reads it back`)
        .toContain(key);
    }
  });

  it('every module that writes a preference publishes the write', () => {
    // Publishing is what makes a change visible; a writer that only saves has
    // reintroduced the bug this branch fixed, one file over.
    for (const { path, src } of ALL_JS) {
      if (path.endsWith(`${join('js', 'store.js')}`) || path.endsWith('preferences-logic.js')) continue;
      if (!/(?:^|[^.\w])(?:store\.)?preferences\.[A-Za-z_$][\w$]*\s*(?:\[[^\]]*\])?\s*=(?!=)/.test(src)) continue;
      const rel = path.slice(JS_DIR.length + 1);
      expect(src, `${rel} writes a preference but never calls publishPreferences()`).toContain('publishPreferences(');
    }
  });
});

describe('PREFERENCE_REACTIONS classification', () => {
  it('every preference key the app can hold is classified', () => {
    for (const key of LOADER_KEYS) {
      expect(TABLE_KEYS, `preferences.${key} has no entry in PREFERENCE_REACTIONS`).toContain(key);
    }
  });

  it('classifies every shortcut and behavior member individually', () => {
    // shortcuts/behavior are whitelisted objects (normalizeShortcuts /
    // normalizeBehavior drop anything unknown), so their members are knowable
    // exactly — which is what makes "add a member, get a red test" possible here
    // rather than falling back to the parent's answer.
    for (const key of Object.keys(SHORTCUT_DEFAULTS)) {
      expect(TABLE_KEYS, `shortcuts.${key} has no entry`).toContain(`shortcuts.${key}`);
    }
    for (const key of Object.keys(BEHAVIOR_DEFAULTS)) {
      expect(TABLE_KEYS, `behavior.${key} has no entry`).toContain(`behavior.${key}`);
    }
  });

  it('has no stale row', () => {
    for (const row of TABLE_KEYS) {
      const [root, member] = row.split('.');
      expect(LOADER_KEYS, `${row} names a preference key that no longer exists`).toContain(root);
      if (member !== undefined) {
        const real = new Set([...Object.keys(SHORTCUT_DEFAULTS), ...Object.keys(BEHAVIOR_DEFAULTS)]);
        expect(real, `${row} names a member that is not in SHORTCUT_DEFAULTS or BEHAVIOR_DEFAULTS`).toContain(member);
      }
    }
  });

  it('uses only the declared reaction vocabulary', () => {
    for (const [row, reaction] of Object.entries(PREFERENCE_REACTIONS)) {
      expect(REACTIONS, `${row} claims "${reaction}", which is not a declared reaction`).toContain(reaction);
      expect(typeof REACTION_OWNERS[reaction], `${row} claims "${reaction}" with no owner`).toBe('string');
    }
  });

  it('names subscribers that are actually wired', () => {
    // A reaction whose owner does not subscribe is the same lie in the other
    // direction: the table says the grid repaints, and nothing does.
    const invEvents = readFileSync(new URL('../../js/inventory/inv-events.js', import.meta.url), 'utf8');
    const planStore = readFileSync(new URL('../../js/cart/cart-plan-store.js', import.meta.url), 'utf8');
    expect(invEvents).toContain('repaintsGrid(');
    expect(invEvents).toMatch(/if \(!repaintsGrid\(changed\)\) return;\s*\n\s*render\(\);/);
    expect(planStore).toContain('replansCart(');
    expect(planStore).toContain('schedulePlanRefresh()');
  });
});

describe('the classification itself', () => {
  it('repaints for the keys baked into row HTML', () => {
    expect(reactionFor('thresholds')).toBe('repaints');
    // Sections are data-driven (SECTION_ORDER), so a change arrives as a path
    // under thresholds and must resolve through it.
    expect(repaintsGrid(['thresholds.Capacitors > MLCC'])).toBe(true);
    // The category-search toggle decides WHICH rows are in the grid and is read
    // at render time, so it is the second member of this class — and the one
    // whose failure the user would read as "the toggle does nothing".
    expect(reactionFor('behavior.categorySearch')).toBe('repaints');
    expect(repaintsGrid(['behavior.categorySearch'])).toBe(true);
    // It is not an argument to the cart planner, so claiming a re-plan here
    // would be a claim about a fetch that does not happen.
    expect(replansCart(['behavior.categorySearch'])).toBe(false);
  });

  it('re-plans the cart for the reel ceiling, and does not repaint the grid', () => {
    expect(reactionFor('behavior.reelCeiling')).toBe('replans');
    expect(replansCart(['behavior.reelCeiling'])).toBe(true);
    // Truthfulness clause: the grid shows no cart recommendation, so claiming a
    // repaint here would be a claim about a render that does not happen.
    expect(repaintsGrid(['behavior.reelCeiling'])).toBe(false);
  });

  it('never repaints for a key whose actor already applied it', () => {
    // The whole point of gating: a tab switch, a zoom step or a remembered
    // dialog path must not cost a full grid rebuild.
    for (const key of ['server_url', 'server_tabs', 'active_tab', 'active_source',
      'servers', 'ui_zoom', 'panels_collapsed', 'column_widths', 'inventory_view',
      'saved_views', 'lastBomDir', 'lastBomFile', 'lastImportDir', 'lastOcrTemplate']) {
      expect(repaintsGrid([key]), `${key} must not repaint the grid`).toBe(false);
      expect(replansCart([key]), `${key} must not re-plan the cart`).toBe(false);
    }
  });

  it('refuses to guess about a key nobody classified', () => {
    expect(() => reactionFor('someNewKey')).toThrow(RangeError);
    expect(() => reactionFor('someNewKey')).toThrow(/no entry in PREFERENCE_REACTIONS/);
    // …but a data-driven child of a classified parent is never "unknown".
    expect(() => reactionFor('thresholds.Something Nobody Added')).not.toThrow();
  });
});
