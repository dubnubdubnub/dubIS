import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
  Modal: vi.fn(),
}));

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [
    'Resistors',
    { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
    'Inductors',
  ],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn().mockResolvedValue(undefined),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));

import {
  App, store, getThreshold, setThreshold, snapshotLinks,
  setInventory, setBomResults, addManualLink, confirmMatch,
  setLinkingMode, clearLinks,
} from '../../js/store.js';
import { EventBus, Events } from '../../js/event-bus.js';

describe('App.links', () => {
  beforeEach(() => {
    App.links.clearAll();
  });

  describe('addManualLink', () => {
    it('adds a manual link entry', () => {
      App.links.addManualLink('C123', 'INV456');
      expect(App.links.manualLinks).toEqual([{ bomKey: 'C123', invPartKey: 'INV456' }]);
    });

    it('accumulates multiple links', () => {
      App.links.addManualLink('C1', 'I1');
      App.links.addManualLink('C2', 'I2');
      expect(App.links.manualLinks).toHaveLength(2);
    });
  });

  describe('confirmMatch / unconfirmMatch', () => {
    it('adds a confirmed match', () => {
      App.links.confirmMatch('BK1', 'IPK1');
      expect(App.links.confirmedMatches).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK1' }]);
    });

    it('replaces existing confirmation for same bomKey', () => {
      App.links.confirmMatch('BK1', 'IPK1');
      App.links.confirmMatch('BK1', 'IPK2');
      expect(App.links.confirmedMatches).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK2' }]);
    });

    it('removes confirmation with unconfirmMatch', () => {
      App.links.confirmMatch('BK1', 'IPK1');
      App.links.unconfirmMatch('BK1');
      expect(App.links.confirmedMatches).toEqual([]);
    });
  });

  describe('setLinkingMode', () => {
    it('enables linking mode with invItem', () => {
      App.links.setLinkingMode(true, { id: 'item1' });
      expect(App.links.linkingMode).toBe(true);
      expect(App.links.linkingInvItem).toEqual({ id: 'item1' });
      expect(App.links.linkingBomRow).toBeNull();
    });

    it('disabling clears invItem', () => {
      App.links.setLinkingMode(true, { id: 'item1' });
      App.links.setLinkingMode(false);
      expect(App.links.linkingMode).toBe(false);
      expect(App.links.linkingInvItem).toBeNull();
    });
  });

  describe('setReverseLinkingMode', () => {
    it('enables reverse linking with bomRow', () => {
      App.links.setReverseLinkingMode(true, { key: 'row1' });
      expect(App.links.linkingMode).toBe(true);
      expect(App.links.linkingBomRow).toEqual({ key: 'row1' });
      expect(App.links.linkingInvItem).toBeNull();
    });
  });

  describe('loadFromSaved', () => {
    it('loads array format (legacy)', () => {
      App.links.loadFromSaved([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(App.links.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(App.links.confirmedMatches).toEqual([]);
    });

    it('loads object format with both arrays', () => {
      App.links.loadFromSaved({
        manualLinks: [{ bomKey: 'a', invPartKey: 'b' }],
        confirmedMatches: [{ bomKey: 'c', invPartKey: 'd' }],
      });
      expect(App.links.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(App.links.confirmedMatches).toEqual([{ bomKey: 'c', invPartKey: 'd' }]);
    });

    it('handles null/undefined input', () => {
      App.links.loadFromSaved(null);
      expect(App.links.manualLinks).toEqual([]);
      expect(App.links.confirmedMatches).toEqual([]);
    });

    it('resets linking mode', () => {
      App.links.setLinkingMode(true, { id: 'x' });
      App.links.loadFromSaved([]);
      expect(App.links.linkingMode).toBe(false);
      expect(App.links.linkingInvItem).toBeNull();
    });
  });

  describe('hasLinks', () => {
    it('returns false when empty', () => {
      expect(App.links.hasLinks()).toBe(false);
    });

    it('returns true with manual links', () => {
      App.links.addManualLink('a', 'b');
      expect(App.links.hasLinks()).toBe(true);
    });

    it('returns true with confirmed matches', () => {
      App.links.confirmMatch('a', 'b');
      expect(App.links.hasLinks()).toBe(true);
    });
  });

  describe('clearAll', () => {
    it('resets all link state', () => {
      App.links.addManualLink('a', 'b');
      App.links.confirmMatch('c', 'd');
      App.links.setLinkingMode(true, { id: 'x' });
      App.links.clearAll();
      expect(App.links.manualLinks).toEqual([]);
      expect(App.links.confirmedMatches).toEqual([]);
      expect(App.links.linkingMode).toBe(false);
      expect(App.links.linkingInvItem).toBeNull();
      expect(App.links.linkingBomRow).toBeNull();
    });
  });
});

describe('SECTION_ORDER parsing', () => {
  it('builds SECTION_HIERARCHY from mixed SECTION_ORDER', () => {
    expect(App.SECTION_HIERARCHY).toEqual([
      { name: 'Resistors', children: null },
      { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
      { name: 'Inductors', children: null },
    ]);
  });

  it('builds FLAT_SECTIONS with compound names', () => {
    expect(App.FLAT_SECTIONS).toEqual([
      'Resistors',
      'Capacitors',
      'Capacitors > MLCC',
      'Capacitors > Electrolytic',
      'Inductors',
    ]);
  });
});

describe('getThreshold', () => {
  beforeEach(() => {
    App.preferences.thresholds = { Resistors: 100, Capacitors: 200 };
  });

  it('returns threshold for direct section', () => {
    expect(getThreshold('Resistors')).toBe(100);
  });

  it('falls back to parent threshold for compound section', () => {
    expect(getThreshold('Capacitors > MLCC')).toBe(200);
  });

  it('returns default 50 for unknown section', () => {
    expect(getThreshold('Unknown')).toBe(50);
  });
});

describe('setThreshold', () => {
  beforeEach(() => {
    App.preferences.thresholds = {};
  });

  it('sets threshold value', () => {
    setThreshold('Resistors', 75);
    expect(App.preferences.thresholds.Resistors).toBe(75);
  });
});

// ── New store/setter API tests ────────────────────────────

describe('store (read-only getters)', () => {
  beforeEach(() => {
    App.inventory = [];
    App.bomResults = null;
    clearLinks();
  });

  it('store.inventory returns what setInventory() set', () => {
    const items = [{ lcsc: 'C1', qty: 10 }];
    setInventory(items);
    expect(store.inventory).toBe(items);
  });

  it('store.bomResults returns what setBomResults() set', () => {
    const results = [{ bom: {}, inv: null }];
    setBomResults(results);
    expect(store.bomResults).toBe(results);
  });

  it('store.SECTION_HIERARCHY matches App.SECTION_HIERARCHY', () => {
    expect(store.SECTION_HIERARCHY).toEqual(App.SECTION_HIERARCHY);
  });

  it('store.FLAT_SECTIONS matches App.FLAT_SECTIONS', () => {
    expect(store.FLAT_SECTIONS).toEqual(App.FLAT_SECTIONS);
  });
});

describe('event emissions from setters', () => {
  beforeEach(() => {
    clearLinks();
    vi.restoreAllMocks();
  });

  it('addManualLink() emits LINKS_CHANGED', () => {
    const spy = vi.fn();
    EventBus.on(Events.LINKS_CHANGED, spy);
    addManualLink('BK1', 'IPK1');
    expect(spy).toHaveBeenCalledTimes(1);
    EventBus.off(Events.LINKS_CHANGED, spy);
  });

  it('confirmMatch() emits CONFIRMED_CHANGED', () => {
    const spy = vi.fn();
    EventBus.on(Events.CONFIRMED_CHANGED, spy);
    confirmMatch('BK1', 'IPK1');
    expect(spy).toHaveBeenCalledTimes(1);
    EventBus.off(Events.CONFIRMED_CHANGED, spy);
  });

  it('setLinkingMode() emits LINKING_MODE with correct payload', () => {
    const spy = vi.fn();
    EventBus.on(Events.LINKING_MODE, spy);
    const invItem = { lcsc: 'C999' };
    setLinkingMode(true, invItem);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ active: true, invItem });
    EventBus.off(Events.LINKING_MODE, spy);
  });
});

describe('snapshotLinks', () => {
  beforeEach(() => {
    clearLinks();
  });

  it('returns a deep copy (mutating it does not affect store)', () => {
    addManualLink('BK1', 'IPK1');
    const snap = snapshotLinks();
    snap.manualLinks.push({ bomKey: 'extra', invPartKey: 'extra' });
    expect(store.links.manualLinks).toHaveLength(1);
    expect(App.links.manualLinks).toHaveLength(1);
  });
});

describe('App proxy backward compatibility', () => {
  beforeEach(() => {
    App.inventory = [];
    App.bomResults = null;
    App.preferences = { thresholds: {} };
    clearLinks();
  });

  it('App.bomResults = x updates store.bomResults', () => {
    const results = [{ bom: {}, inv: null }];
    App.bomResults = results;
    expect(store.bomResults).toBe(results);
  });

  it('App.links.addManualLink(bk, ipk) works', () => {
    App.links.addManualLink('BK1', 'IPK1');
    expect(App.links.manualLinks).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK1' }]);
    expect(store.links.manualLinks).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK1' }]);
  });

  it('App.links.manualLinks returns the array', () => {
    addManualLink('X', 'Y');
    expect(App.links.manualLinks).toEqual([{ bomKey: 'X', invPartKey: 'Y' }]);
  });

  it('App.links.manualLinks setter works (undo/redo path)', () => {
    addManualLink('A', 'B');
    App.links.manualLinks = [{ bomKey: 'C', invPartKey: 'D' }];
    expect(App.links.manualLinks).toEqual([{ bomKey: 'C', invPartKey: 'D' }]);
    expect(store.links.manualLinks).toEqual([{ bomKey: 'C', invPartKey: 'D' }]);
  });

  it('App.links.confirmedMatches setter works (undo/redo path)', () => {
    App.links.confirmedMatches = [{ bomKey: 'E', invPartKey: 'F' }];
    expect(App.links.confirmedMatches).toEqual([{ bomKey: 'E', invPartKey: 'F' }]);
  });

  it('App.inventory reads correctly', () => {
    const items = [{ lcsc: 'C1', qty: 5 }];
    setInventory(items);
    expect(App.inventory).toBe(items);
  });

  it('App.inventory setter updates store', () => {
    const items = [{ lcsc: 'C2', qty: 3 }];
    App.inventory = items;
    expect(store.inventory).toBe(items);
  });

  it('App.preferences.thresholds is accessible and mutable', () => {
    App.preferences.thresholds = { Resistors: 42 };
    expect(App.preferences.thresholds.Resistors).toBe(42);
    expect(store.preferences.thresholds.Resistors).toBe(42);
  });

  it('App.preferences direct property assignment works (e.g. lastBomDir)', () => {
    App.preferences.lastBomDir = '/some/path';
    expect(App.preferences.lastBomDir).toBe('/some/path');
    expect(store.preferences.lastBomDir).toBe('/some/path');
  });
});
