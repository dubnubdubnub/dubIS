/* Guard: publishing a preferences change must not depend on reference inequality.

   The bug, in one line: every writer mutated the one `preferences` object and
   then called `preferencesSignal.set(preferences)` with that same reference, so
   js/signals.js's `if (_value === v) return;` dropped every publish and the
   grid's only subscriber ran exactly once, at mount. Changing a threshold slider
   did nothing until a server-tab switch re-rendered by accident.

   These tests pin the fix from both ends: the publish reaches a subscriber, and
   the repaint stays gated on the classification table so that reaching a
   subscriber does not mean re-rendering the world. */
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

import {
  store, preferencesSignal, publishPreferences, rebasePreferences,
  setThreshold, setBehaviorPrefs, setShortcutPrefs, setPreferences,
  getBehaviorPrefs, getShortcutPrefs,
} from '../../js/store.js';
import { effect, signal } from '../../js/signals.js';
import { repaintsGrid, replansCart } from '../../js/preferences-logic.js';

/** Subscribe like js/inventory/inv-events.js does; return the log of publishes. */
function watch() {
  /** @type {Array<string[]>} */
  const seen = [];
  const dispose = effect(() => {
    const { changed } = preferencesSignal.get();
    seen.push(changed.slice());
  });
  // effect() runs fn immediately, so the first entry is the current publish,
  // exactly as the grid subscriber skips it with its _prefsEffectFirstRun flag.
  seen.shift();
  return { seen, dispose };
}

/** The grid subscriber's exact shape, minus the DOM. */
function watchGridRepaints() {
  /** @type {number} */
  let renders = 0;
  // Same first-run skip as js/inventory/inv-events.js: the effect runs on
  // creation, and the publish it reads there is whatever happened last, not one
  // this subscriber missed.
  let first = true;
  const dispose = effect(() => {
    const { changed } = preferencesSignal.get();
    if (first) { first = false; return; }
    if (!repaintsGrid(changed)) return;
    renders += 1;
  });
  return { renders: () => renders, dispose };
}

describe('preferencesSignal publishes', () => {
  beforeEach(() => {
    // Normalised on the way in, so a later setShortcutPrefs/setBehaviorPrefs
    // reports only the member the test moved rather than every default the
    // normalizer fills in behind an empty object.
    setPreferences({
      thresholds: {},
      behavior: getBehaviorPrefs(),
      shortcuts: getShortcutPrefs(),
      server_tabs: [],
      ui_zoom: undefined,
    });
    rebasePreferences();
  });

  it('notifies a subscriber when the bag was mutated in place', () => {
    // The regression itself. Before the fix this saw zero notifications: the
    // writer passed the identical object and signal() called it "no change".
    const { seen, dispose } = watch();
    setThreshold('Resistors', 42);
    expect(seen.length, 'a threshold change must reach a subscriber').toBe(1);
    expect(seen[0]).toContain('thresholds.Resistors');
    dispose();
  });

  it('reports the changed path, not just the changed bag', () => {
    const { seen, dispose } = watch();
    setBehaviorPrefs({ reelCeiling: 150 });
    expect(seen[0]).toEqual(['behavior.reelCeiling']);
    expect(replansCart(seen[0])).toBe(true);
    dispose();
  });

  it('publishes a plain in-place write made outside store.js', () => {
    // js/app-init.js, js/panel-collapse.js, js/bom/*, js/import/* write through
    // `store.preferences.<key> = …` and then call publishPreferences(); this is
    // the shape those call sites have.
    const { seen, dispose } = watch();
    store.preferences.ui_zoom = 1.25;
    expect(publishPreferences()).toEqual(['ui_zoom']);
    expect(seen.length).toBe(1);
    dispose();
  });

  it('publishes nothing when nothing moved', () => {
    // persistTabs() runs on every tab publish including startup hydration; a save
    // that changed no key must not reach a subscriber at all.
    const { seen, dispose } = watch();
    expect(publishPreferences()).toEqual([]);
    store.preferences.ui_zoom = store.preferences.ui_zoom;
    expect(publishPreferences()).toEqual([]);
    expect(seen).toEqual([]);
    dispose();
  });

  it('treats a hydration rebase as not-a-change', () => {
    // loadPreferences() fills the bag from disk and rebases: the startup appliers
    // already applied what it loaded, and the first real change must report only
    // what moved after it.
    store.preferences.thresholds = { Resistors: 5 };
    store.preferences.server_url = 'http://bench:8000';
    rebasePreferences();
    const { seen, dispose } = watch();
    setShortcutPrefs({ vimNav: true });
    expect(seen.length).toBe(1);
    expect(seen[0]).toEqual(['shortcuts.vimNav']);
    dispose();
  });

  it('publishes through setPreferences too', () => {
    const { seen, dispose } = watch();
    setPreferences({ lastBomDir: '/tmp' });
    expect(seen.length).toBe(1);
    expect(seen[0]).toEqual(['lastBomDir']);
    dispose();
  });

  it('publishes the live bag, so a subscriber reads current state', () => {
    setThreshold('Capacitors', 10);
    expect(preferencesSignal.peek().values).toBe(store.preferences);
    expect(store.preferences.thresholds.Capacitors).toBe(10);
  });
});

describe('the repaint gate', () => {
  beforeEach(() => {
    setPreferences({
      thresholds: {},
      behavior: getBehaviorPrefs(),
      shortcuts: getShortcutPrefs(),
      server_tabs: [],
      ui_zoom: undefined,
    });
    rebasePreferences();
  });

  it('re-renders the grid for a threshold and for nothing else', () => {
    const grid = watchGridRepaints();
    setThreshold('Resistors', 1);
    expect(grid.renders()).toBe(1);
    store.preferences.server_tabs = [{ id: 't1', sources: ['local'], name: '', view: null }];
    publishPreferences();
    store.preferences.ui_zoom = 1.5;
    publishPreferences();
    expect(grid.renders(), 'a tab-set or zoom save must not rebuild the grid').toBe(1);
    grid.dispose();
  });

  it('still notifies the subscriber for a key that does not repaint', () => {
    // Gating the RENDER must not gate the PUBLISH: a future subscriber that cares
    // about server_url has to be told, or it is the old bug with a filter on it.
    const { seen, dispose } = watch();
    store.preferences.server_url = 'http://fremont:8000';
    publishPreferences();
    expect(seen).toEqual([['server_url']]);
    dispose();
  });
});

describe('js/signals.js is not weakened for preferences', () => {
  it('signal() still ignores a re-set of the same reference', () => {
    // The fix belongs at the preferences writers. activeSourceSignal and
    // cartsSignal pass new values and depend on this guard to avoid re-rendering
    // on a no-op set; removing it here would make every signal storm.
    const s = signal({ a: 1 });
    let runs = 0;
    const dispose = effect(() => { s.get(); runs += 1; });
    expect(runs).toBe(1);
    const same = s.peek();
    same.a = 2;
    s.set(same);
    expect(runs, 'an in-place mutation must stay a no-op for signal()').toBe(1);
    s.set({ a: 3 });
    expect(runs).toBe(2);
    dispose();
  });
});
