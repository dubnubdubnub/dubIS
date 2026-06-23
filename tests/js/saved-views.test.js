// @vitest-environment jsdom
/**
 * saved-views.test.js — TDD tests for js/inventory/saved-views.js
 *
 * Tests:
 *   - captureView snapshots the right fields from a mock state
 *   - applyView restores them (incl. activeDistributors Set + search input value)
 *   - save/list/delete/rename round-trip through a mocked store.preferences + savePreferences
 *   - malformed saved_views on load is ignored gracefully
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: ['Resistors', { name: 'Capacitors', children: ['MLCC'] }],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn().mockResolvedValue(undefined),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
  Modal: vi.fn(),
}));

const mockSavePreferences = vi.fn().mockResolvedValue(undefined);

vi.mock('../../js/store.js', () => {
  // Build a minimal preferences object with saved_views
  const prefs = {
    thresholds: {},
    inventory_view: {},
    shortcuts: { redo: 'both', enterSubmitsModals: true, vimNav: false },
    saved_views: [],
  };

  const store = { preferences: prefs };

  return {
    store,
    savePreferences: (...a) => mockSavePreferences(...a),
    // other exports used transitively
    loadPreferences: vi.fn(),
    onInventoryUpdated: vi.fn(),
    loadInventory: vi.fn(),
    getShortcutPrefs: vi.fn(() => ({ redo: 'both', enterSubmitsModals: true, vimNav: false })),
    setInventory: vi.fn(),
    preferencesSignal: { get: vi.fn(), set: vi.fn() },
    SHORTCUT_DEFAULTS: { redo: 'both', enterSubmitsModals: true, vimNav: false },
    saveInventoryView: vi.fn(),
    EventBus: { on: vi.fn(), emit: vi.fn() },
    Events: {},
  };
});

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Build a minimal inventory state object for testing.
 * @param {object} [overrides]
 */
function makeState(overrides = {}) {
  const searchInput = document.createElement('input');
  searchInput.value = overrides.searchValue || '';

  return {
    searchInput,
    activeDistributors: new Set(overrides.activeDistributors || []),
    groupLevel: overrides.groupLevel !== undefined ? overrides.groupLevel : 0,
    sortColumn: overrides.sortColumn !== undefined ? overrides.sortColumn : null,
    sortScope: overrides.sortScope !== undefined ? overrides.sortScope : null,
    vendorGroupScope: overrides.vendorGroupScope !== undefined ? overrides.vendorGroupScope : null,
  };
}

// ── Import under test ────────────────────────────────────────────────────────

import {
  captureView,
  applyView,
  saveView,
  listViews,
  deleteView,
  renameView,
} from '../../js/inventory/saved-views.js';

import { store } from '../../js/store.js';

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  mockSavePreferences.mockClear();
  // Reset saved_views before each test
  store.preferences.saved_views = [];
});

// ── captureView ───────────────────────────────────────────────────────────────

describe('captureView', () => {
  it('captures searchInput.value', () => {
    const state = makeState({ searchValue: 'resistor' });
    const view = captureView(state);
    expect(view.searchTerm).toBe('resistor');
  });

  it('captures activeDistributors as array', () => {
    const state = makeState({ activeDistributors: ['lcsc', 'digikey'] });
    const view = captureView(state);
    expect(view.distributors).toEqual(expect.arrayContaining(['lcsc', 'digikey']));
    expect(view.distributors).toHaveLength(2);
  });

  it('captures empty activeDistributors as empty array', () => {
    const state = makeState({ activeDistributors: [] });
    const view = captureView(state);
    expect(view.distributors).toEqual([]);
  });

  it('captures groupLevel', () => {
    const state = makeState({ groupLevel: 2 });
    const view = captureView(state);
    expect(view.groupLevel).toBe(2);
  });

  it('captures sortColumn and sortScope', () => {
    const state = makeState({ sortColumn: 'mpn', sortScope: 'global' });
    const view = captureView(state);
    expect(view.sortColumn).toBe('mpn');
    expect(view.sortScope).toBe('global');
  });

  it('captures vendorGroupScope', () => {
    const state = makeState({ vendorGroupScope: 'section' });
    const view = captureView(state);
    expect(view.vendorGroupScope).toBe('section');
  });

  it('captures null sort fields', () => {
    const state = makeState({ sortColumn: null, sortScope: null });
    const view = captureView(state);
    expect(view.sortColumn).toBeNull();
    expect(view.sortScope).toBeNull();
  });

  it('sets predicate to null', () => {
    const state = makeState();
    const view = captureView(state);
    expect(view.predicate).toBeNull();
  });

  it('does not mutate the state', () => {
    const state = makeState({ activeDistributors: ['lcsc'] });
    captureView(state);
    expect([...state.activeDistributors]).toEqual(['lcsc']);
  });
});

// ── applyView ─────────────────────────────────────────────────────────────────

describe('applyView', () => {
  it('restores searchInput.value', () => {
    const state = makeState({ searchValue: '' });
    applyView({ searchTerm: 'capacitor', distributors: [], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null }, state);
    expect(state.searchInput.value).toBe('capacitor');
  });

  it('restores activeDistributors as a Set', () => {
    const state = makeState({ activeDistributors: [] });
    applyView({ searchTerm: '', distributors: ['lcsc', 'mouser'], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null }, state);
    expect(state.activeDistributors instanceof Set).toBe(true);
    expect(state.activeDistributors.has('lcsc')).toBe(true);
    expect(state.activeDistributors.has('mouser')).toBe(true);
    expect(state.activeDistributors.size).toBe(2);
  });

  it('clears previous distributors when restoring', () => {
    const state = makeState({ activeDistributors: ['digikey', 'pololu'] });
    applyView({ searchTerm: '', distributors: ['lcsc'], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null }, state);
    expect(state.activeDistributors.has('digikey')).toBe(false);
    expect(state.activeDistributors.has('lcsc')).toBe(true);
    expect(state.activeDistributors.size).toBe(1);
  });

  it('restores groupLevel', () => {
    const state = makeState({ groupLevel: 0 });
    applyView({ searchTerm: '', distributors: [], groupLevel: 2, sortColumn: null, sortScope: null, vendorGroupScope: null }, state);
    expect(state.groupLevel).toBe(2);
  });

  it('restores sortColumn and sortScope', () => {
    const state = makeState({ sortColumn: null, sortScope: null });
    applyView({ searchTerm: '', distributors: [], groupLevel: 0, sortColumn: 'qty', sortScope: 'subsection', vendorGroupScope: null }, state);
    expect(state.sortColumn).toBe('qty');
    expect(state.sortScope).toBe('subsection');
  });

  it('restores vendorGroupScope', () => {
    const state = makeState({ vendorGroupScope: null });
    applyView({ searchTerm: '', distributors: [], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: 'global' }, state);
    expect(state.vendorGroupScope).toBe('global');
  });

  it('restores null fields correctly', () => {
    const state = makeState({ sortColumn: 'mpn', sortScope: 'global', vendorGroupScope: 'section' });
    applyView({ searchTerm: '', distributors: [], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null }, state);
    expect(state.sortColumn).toBeNull();
    expect(state.sortScope).toBeNull();
    expect(state.vendorGroupScope).toBeNull();
  });

  it('round-trips: captureView then applyView restores exact state', () => {
    const original = makeState({
      searchValue: 'caps',
      activeDistributors: ['lcsc', 'digikey'],
      groupLevel: 1,
      sortColumn: 'value',
      sortScope: 'section',
      vendorGroupScope: null,
    });

    const captured = captureView(original);

    const restored = makeState({
      searchValue: 'old',
      activeDistributors: ['mouser'],
      groupLevel: 2,
      sortColumn: 'mpn',
      sortScope: 'global',
      vendorGroupScope: 'global',
    });

    applyView(captured, restored);

    expect(restored.searchInput.value).toBe('caps');
    expect([...restored.activeDistributors].sort()).toEqual(['digikey', 'lcsc']);
    expect(restored.groupLevel).toBe(1);
    expect(restored.sortColumn).toBe('value');
    expect(restored.sortScope).toBe('section');
    expect(restored.vendorGroupScope).toBeNull();
  });
});

// ── saveView / listViews ──────────────────────────────────────────────────────

describe('saveView', () => {
  it('saves a view with a name and an id', async () => {
    const state = makeState({ searchValue: 'res' });
    const view = captureView(state);
    await saveView('My View', view);

    const views = listViews();
    expect(views).toHaveLength(1);
    expect(views[0].name).toBe('My View');
    expect(typeof views[0].id).toBe('string');
    expect(views[0].id.length).toBeGreaterThan(0);
  });

  it('saves searchTerm in the stored view', async () => {
    const state = makeState({ searchValue: 'inductor' });
    await saveView('Inductors', captureView(state));

    const views = listViews();
    expect(views[0].searchTerm).toBe('inductor');
  });

  it('saves distributors in the stored view', async () => {
    const state = makeState({ activeDistributors: ['lcsc'] });
    await saveView('LCSC Only', captureView(state));

    const views = listViews();
    expect(views[0].distributors).toEqual(['lcsc']);
  });

  it('calls savePreferences after saving', async () => {
    const state = makeState();
    await saveView('Test', captureView(state));
    expect(mockSavePreferences).toHaveBeenCalledTimes(1);
  });

  it('accumulates multiple saved views', async () => {
    await saveView('View A', captureView(makeState({ searchValue: 'a' })));
    await saveView('View B', captureView(makeState({ searchValue: 'b' })));

    const views = listViews();
    expect(views).toHaveLength(2);
    expect(views.map(v => v.name)).toEqual(expect.arrayContaining(['View A', 'View B']));
  });

  it('each view has a unique id', async () => {
    await saveView('View 1', captureView(makeState()));
    await saveView('View 2', captureView(makeState()));

    const views = listViews();
    const ids = views.map(v => v.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

// ── listViews ─────────────────────────────────────────────────────────────────

describe('listViews', () => {
  it('returns empty array when no views saved', () => {
    expect(listViews()).toEqual([]);
  });

  it('returns the views array from store.preferences', async () => {
    await saveView('One', captureView(makeState()));
    const views = listViews();
    expect(Array.isArray(views)).toBe(true);
    expect(views).toHaveLength(1);
  });
});

// ── deleteView ────────────────────────────────────────────────────────────────

describe('deleteView', () => {
  it('removes the view with the given id', async () => {
    await saveView('To Delete', captureView(makeState()));
    const id = listViews()[0].id;

    await deleteView(id);
    expect(listViews()).toHaveLength(0);
  });

  it('calls savePreferences after deleting', async () => {
    await saveView('Gone', captureView(makeState()));
    mockSavePreferences.mockClear();

    const id = listViews()[0].id;
    await deleteView(id);
    expect(mockSavePreferences).toHaveBeenCalledTimes(1);
  });

  it('leaves other views intact when deleting one', async () => {
    await saveView('Keep A', captureView(makeState()));
    await saveView('Delete Me', captureView(makeState()));
    await saveView('Keep B', captureView(makeState()));

    const toDelete = listViews().find(v => v.name === 'Delete Me');
    await deleteView(toDelete.id);

    const remaining = listViews().map(v => v.name);
    expect(remaining).toContain('Keep A');
    expect(remaining).toContain('Keep B');
    expect(remaining).not.toContain('Delete Me');
  });

  it('is a no-op for a non-existent id', async () => {
    await saveView('Existing', captureView(makeState()));
    mockSavePreferences.mockClear();

    await deleteView('nonexistent-id');
    expect(listViews()).toHaveLength(1);
    expect(mockSavePreferences).not.toHaveBeenCalled();
  });
});

// ── renameView ────────────────────────────────────────────────────────────────

describe('renameView', () => {
  it('updates the name of the view with the given id', async () => {
    await saveView('Original', captureView(makeState()));
    const id = listViews()[0].id;

    await renameView(id, 'Renamed');
    expect(listViews()[0].name).toBe('Renamed');
  });

  it('calls savePreferences after renaming', async () => {
    await saveView('Name', captureView(makeState()));
    const id = listViews()[0].id;
    mockSavePreferences.mockClear();

    await renameView(id, 'New Name');
    expect(mockSavePreferences).toHaveBeenCalledTimes(1);
  });

  it('preserves the view data when renaming', async () => {
    const state = makeState({ searchValue: 'caps', activeDistributors: ['lcsc'], groupLevel: 1 });
    await saveView('Before', captureView(state));
    const id = listViews()[0].id;

    await renameView(id, 'After');
    const view = listViews()[0];
    expect(view.name).toBe('After');
    expect(view.searchTerm).toBe('caps');
    expect(view.distributors).toEqual(['lcsc']);
    expect(view.groupLevel).toBe(1);
  });

  it('is a no-op for a non-existent id', async () => {
    await saveView('Untouched', captureView(makeState()));
    mockSavePreferences.mockClear();

    await renameView('nonexistent-id', 'New');
    expect(listViews()[0].name).toBe('Untouched');
    expect(mockSavePreferences).not.toHaveBeenCalled();
  });
});

// ── Malformed saved_views load graceful degradation ───────────────────────────

describe('malformed saved_views handling', () => {
  it('listViews returns empty array when saved_views is not an array', () => {
    store.preferences.saved_views = 'not-an-array';
    expect(listViews()).toEqual([]);
  });

  it('listViews returns empty array when saved_views is null', () => {
    store.preferences.saved_views = null;
    expect(listViews()).toEqual([]);
  });

  it('listViews filters out entries missing id', () => {
    store.preferences.saved_views = [
      { name: 'Good', id: 'abc', searchTerm: '', distributors: [], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null, predicate: null },
      { name: 'Bad — no id', searchTerm: '' },
    ];
    const views = listViews();
    expect(views).toHaveLength(1);
    expect(views[0].name).toBe('Good');
  });

  it('listViews filters out entries missing name', () => {
    store.preferences.saved_views = [
      { id: 'abc', searchTerm: '', distributors: [], groupLevel: 0, sortColumn: null, sortScope: null, vendorGroupScope: null, predicate: null },
    ];
    expect(listViews()).toHaveLength(0);
  });
});
