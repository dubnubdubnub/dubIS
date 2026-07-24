// Powerful CI guard for a whole CLASS of bug: a user action that mutates
// persisted BOM state (manual links / confirmed matches) but forgets to mark
// the BOM dirty. When that happens, api._bom_dirty stays False and closing the
// app silently drops the change with no "Save & Close" prompt (see #372, and
// the undo/redo follow-ups).
//
// The registry test below forces EVERY mutating method on store.links to be
// classified as either "must mark dirty" (and then behaviourally verified to do
// so) or "exempt" (load / reset / query / linking-mode). Add a new store.links
// method and this test fails until you classify it — you cannot silently ship a
// persisted-state mutation that skips the dirty flag.
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(), escHtml: vi.fn(s => s || ''), Modal: vi.fn(),
}));
vi.mock('../../js/constants.js', () => ({ SECTION_ORDER: [], FIELDNAMES: [] }));
vi.mock('../../js/api.js', () => ({
  api: vi.fn().mockResolvedValue(undefined),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));

import { store } from '../../js/store.js';
import { api } from '../../js/api.js';

// Each entry invokes the method in a way that genuinely changes persisted state.
const MUST_MARK_DIRTY = {
  addManualLink: () => store.links.addManualLink('C1', 'I1'),
  confirmMatch: () => store.links.confirmMatch('B1', 'I1'),
  unconfirmMatch: () => { store.links.confirmMatch('B1', 'I1'); api.mockClear(); store.links.unconfirmMatch('B1'); },
  restoreLinks: () => store.links.restoreLinks({
    manualLinks: [{ bomKey: 'B', invPartKey: 'I' }], confirmedMatches: [],
  }),
};

// Methods that legitimately must NOT mark dirty: initial load, explicit
// clear/reset, pure queries, and transient linking-mode UI state.
const EXEMPT = [
  'loadFromSaved',        // loading a saved BOM -> freshly clean
  'clearAll',             // explicit discard / BOM-cleared reset
  'hasLinks',             // query
  'setLinkingMode',       // transient UI state, not persisted
  'setReverseLinkingMode',
];

const methodNames = () =>
  Object.keys(store.links).filter(k => typeof store.links[k] === 'function');

describe('BOM-dirty invariant on store.links', () => {
  beforeEach(() => { store.links.clearAll(); api.mockClear(); });

  it('every store.links method is classified (must-mark-dirty OR exempt)', () => {
    const classified = new Set([...Object.keys(MUST_MARK_DIRTY), ...EXEMPT]);
    const unclassified = methodNames().filter(m => !classified.has(m));
    expect(unclassified, `Unclassified store.links method(s): ${unclassified.join(', ')}.\n` +
      'A new link/confirm mutation must call markBomDirty() (add it to MUST_MARK_DIRTY) ' +
      'or be justified as EXEMPT — otherwise closing after it silently drops the change.',
    ).toEqual([]);
  });

  for (const [name, invoke] of Object.entries(MUST_MARK_DIRTY)) {
    it(`store.links.${name} marks the BOM dirty`, () => {
      invoke();
      expect(api).toHaveBeenCalledWith('set_bom_dirty', true);
    });
  }

  it('exempt loaders/reset do NOT mark dirty (a freshly-loaded BOM is clean)', () => {
    store.links.loadFromSaved({ manualLinks: [{ bomKey: 'B', invPartKey: 'I' }], confirmedMatches: [] });
    store.links.clearAll();
    expect(api).not.toHaveBeenCalledWith('set_bom_dirty', true);
  });
});
