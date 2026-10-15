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
   it rather than trusted.

   The enumeration is parser-based (tests/js/preference-key-scan.mjs), not
   regex-based. The regexes were syntax-coupled: destructuring the loader's
   payload, `preferences["foo"] = …` bracket writes, and
   `Object.assign(preferences, …)` all evaded them — and `js/col-resize.js`
   was doing a live sixth variant (a bag-returning alias plus a computed-key
   bracket write) when this guard was hardened. The scan now RAISES on any bag
   reference it cannot account for, and the planted-key tests below prove each
   formerly-evasive form now fails the guard. */
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
  parseModule, scanLoaderReadKeys, scanInitialLiteralKeys, scanWrittenKeys,
} from './preference-key-scan.mjs';
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

/** js/store.js → "store.js", js/bom/bom-events.js → "bom/bom-events.js". */
function relOf(path) { return path.slice(JS_DIR.length + 1); }

const SOURCES = ALL_JS.map(({ path, src }) => ({ path: relOf(path), src }));

const LOADER_KEYS = (() => {
  const keys = scanLoaderReadKeys(STORE_SRC, 'js/store.js');
  if (keys.size < 10) throw new Error(`Suspiciously few loader keys (${keys.size}) — the enumeration broke`);
  return keys;
})();
const LITERAL_KEYS = scanInitialLiteralKeys(parseModule(STORE_SRC, 'js/store.js'), 'js/store.js');
const WRITTEN = scanWrittenKeys(SOURCES);
const WRITING_FILES = new Set([...WRITTEN.values()].flat());
const TABLE_KEYS = Object.keys(PREFERENCE_REACTIONS);

/** The classification guard's predicate, as a value the planted-key tests can
 * assert against without failing this file: which keys does the enumeration
 * find that the table has no row for? Empty means the guard passes. */
function unclassified(loaderKeys) {
  return [...loaderKeys].filter((k) => !TABLE_KEYS.includes(k));
}

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
    // reintroduced the bug this branch fixed, one file over. "Writes" is the
    // parser's answer — the same one the erasure guard above uses — so a
    // bracket write or an Object.assign counts here too, not just `x.k =`.
    for (const { path, src } of ALL_JS) {
      const rel = relOf(path);
      if (rel === 'store.js' || rel === 'preferences-logic.js') continue;
      if (!WRITING_FILES.has(rel)) continue;
      expect(src, `${rel} writes a preference but never calls publishPreferences()`).toContain('publishPreferences(');
    }
  });

  it('the enumeration itself is alive (sanity floors)', () => {
    // A scan that silently finds nothing would pass every assertion above.
    // These floors are far below today's counts (18/11/18) and far above zero:
    // an enumeration that broke must trip this before it could trip nothing.
    expect(LOADER_KEYS.size).toBeGreaterThanOrEqual(10);
    expect(LITERAL_KEYS.size).toBeGreaterThanOrEqual(10);
    expect(WRITTEN.size).toBeGreaterThanOrEqual(10);
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

describe('planted-key guard: the forms the regex scans evaded', () => {
  // Each test PLANTS a key written in one formerly-evasive style, proves the
  // scan now sees it, and proves the guard fails on it — an unclassified key
  // reaching reactionFor() throws, and a written key the loader never carries
  // is the erasure trap. This is how the last guard was validated (a
  // `lastOcrTemplate`-shape bug walked in through a form the scan could read);
  // a hardening without planted keys is an unproven hardening.

  it('plants a destructured read in loadPreferences and the guard fails', () => {
    const planted = 'plantedDestructuredKey';
    const keys = scanLoaderReadKeys(`
      export async function loadPreferences() {
        const stored = await api("load_preferences");
        const { ${planted} } = stored;
        if (stored.thresholds) preferences.thresholds = stored.thresholds;
      }`, 'planted-loader.js');
    expect(keys, 'destructuring the payload must count as reading that key').toContain(planted);
    // The guard then fails on the planted key, two ways:
    expect(unclassified(keys), 'a destructured key must reach classification').toContain(planted);
    expect(() => reactionFor(planted)).toThrow(RangeError);
    // And it is the erasure trap exactly: written nowhere in the real loader,
    // so the whole-bag save would erase it.
    expect(LOADER_KEYS, 'the planted key must not exist in the real loader').not.toContain(planted);
  });

  it('plants preferences["plantedBracketKey"] = … and the write guard fails it', () => {
    const planted = 'plantedBracketKey';
    const written = scanWrittenKeys([{
      path: 'js/store.js',
      src: `let preferences = { thresholds: {} };\npreferences["${planted}"] = 1;\n`,
    }]);
    expect([...written.keys()], 'a bracket-literal write must count as a write').toContain(planted);
    // The guard then fails: the written key is not carried by the real loader
    // (the erasure test above would go red on exactly this predicate), and it
    // has no classification row.
    expect(LOADER_KEYS).not.toContain(planted);
    expect(() => reactionFor(planted)).toThrow(RangeError);
  });

  it('plants Object.assign(store.preferences, { plantedAssignKey }) and the write guard fails it', () => {
    const planted = 'plantedAssignKey';
    const written = scanWrittenKeys([{
      path: 'js/planted-writer.js',
      src: `import { store } from './store.js';\nObject.assign(store.preferences, { ${planted}: 1 });\n`,
    }]);
    expect([...written.keys()], 'Object.assign into the bag must count as a write').toContain(planted);
    expect(LOADER_KEYS).not.toContain(planted);
    expect(() => reactionFor(planted)).toThrow(RangeError);
    // The writer must also be caught by the publish rule, since it is now in
    // the writing-file set the publish test iterates.
    expect(WRITING_FILES, 'the planted writer must not be in the real tree').not.toContain('planted-writer.js');
  });

  // ── and the loud half: a construct the scan cannot account for is a FAILURE,
  // never a silent pass.

  it('refuses to scan a computed bracket write it cannot name', () => {
    expect(() => scanWrittenKeys([{
      path: 'js/store.js',
      src: `let preferences = { thresholds: {} };\nconst k = pick();\npreferences[k] = 1;\n`,
    }])).toThrow(/COMPUTED key/);
  });

  it('refuses to scan Object.assign(bag, <non-literal>)', () => {
    expect(() => scanWrittenKeys([{
      path: 'js/planted-writer.js',
      src: `import { store } from './store.js';\nObject.assign(store.preferences, patch);\n`,
    }])).toThrow(/not an object literal/);
  });

  it('refuses a spread inside Object.assign(bag, …)', () => {
    expect(() => scanWrittenKeys([{
      path: 'js/planted-writer.js',
      src: `import { store } from './store.js';\nObject.assign(store.preferences, { ...patch, ok: 1 });\n`,
    }])).toThrow(/spreads into the preferences bag/);
  });

  it('refuses a rest-element destructure of the stored payload', () => {
    expect(() => scanLoaderReadKeys(`
      export async function loadPreferences() {
        const stored = await api("load_preferences");
        const { thresholds, ...everythingElse } = stored;
      }`, 'planted-loader.js')).toThrow(/rest element/);
  });

  it('refuses an alias of the bag — writes through it would be invisible', () => {
    expect(() => scanWrittenKeys([{
      path: 'js/store.js',
      src: `let preferences = { thresholds: {} };\nconst bag = preferences;\nbag.sneaky = 1;\n`,
    }])).toThrow(/aliases the preferences bag/);
  });

  it('refuses a function that returns the bag (the live col-resize.js shape)', () => {
    // js/col-resize.js used to do exactly this and write through the result
    // with a computed key; the scan now refuses the escape route itself.
    expect(() => scanWrittenKeys([{
      path: 'js/planted-writer.js',
      src: `import { store } from './store.js';\nfunction prefsBag() { return store.preferences; }\nprefsBag().sneaky = 1;\n`,
    }])).toThrow(/returns the preferences bag/);
  });

  it('refuses a file that shadows the name `preferences` with its own binding', () => {
    expect(() => scanWrittenKeys([{
      path: 'js/planted-writer.js',
      src: `const preferences = { notTheBag: true };\npreferences.notTheBag = false;\n`,
    }])).toThrow(/binds the name `preferences`/);
  });

  it('refuses a source it cannot parse, rather than scanning around it', () => {
    expect(() => scanWrittenKeys([{ path: 'js/planted-writer.js', src: 'preferences.x = (' }])).toThrow(/cannot parse/);
  });
});
