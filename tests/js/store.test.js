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
  store, getThreshold, setThreshold, snapshotLinks,
  setInventory, setBomResults, setBomDirty, setBomMeta, setPreferences,
  addManualLink, confirmMatch,
  setLinkingMode, clearLinks,
} from '../../js/store.js';
import { EventBus, Events } from '../../js/event-bus.js';

describe('store.links', () => {
  beforeEach(() => {
    store.links.clearAll();
  });

  describe('addManualLink', () => {
    it('adds a manual link entry', () => {
      store.links.addManualLink('C123', 'INV456');
      expect(store.links.manualLinks).toEqual([{ bomKey: 'C123', invPartKey: 'INV456' }]);
    });

    it('accumulates multiple links', () => {
      store.links.addManualLink('C1', 'I1');
      store.links.addManualLink('C2', 'I2');
      expect(store.links.manualLinks).toHaveLength(2);
    });
  });

  describe('confirmMatch / unconfirmMatch', () => {
    it('adds a confirmed match', () => {
      store.links.confirmMatch('BK1', 'IPK1');
      expect(store.links.confirmedMatches).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK1' }]);
    });

    it('replaces existing confirmation for same bomKey', () => {
      store.links.confirmMatch('BK1', 'IPK1');
      store.links.confirmMatch('BK1', 'IPK2');
      expect(store.links.confirmedMatches).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK2' }]);
    });

    it('removes confirmation with unconfirmMatch', () => {
      store.links.confirmMatch('BK1', 'IPK1');
      store.links.unconfirmMatch('BK1');
      expect(store.links.confirmedMatches).toEqual([]);
    });
  });

  describe('setLinkingMode', () => {
    it('enables linking mode with invItem', () => {
      store.links.setLinkingMode(true, { id: 'item1' });
      expect(store.links.linkingMode).toBe(true);
      expect(store.links.linkingInvItem).toEqual({ id: 'item1' });
      expect(store.links.linkingBomRow).toBeNull();
    });

    it('disabling clears invItem', () => {
      store.links.setLinkingMode(true, { id: 'item1' });
      store.links.setLinkingMode(false);
      expect(store.links.linkingMode).toBe(false);
      expect(store.links.linkingInvItem).toBeNull();
    });
  });

  describe('setReverseLinkingMode', () => {
    it('enables reverse linking with bomRow', () => {
      store.links.setReverseLinkingMode(true, { key: 'row1' });
      expect(store.links.linkingMode).toBe(true);
      expect(store.links.linkingBomRow).toEqual({ key: 'row1' });
      expect(store.links.linkingInvItem).toBeNull();
    });
  });

  describe('loadFromSaved', () => {
    it('loads array format (legacy)', () => {
      store.links.loadFromSaved([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(store.links.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(store.links.confirmedMatches).toEqual([]);
    });

    it('loads object format with both arrays', () => {
      store.links.loadFromSaved({
        manualLinks: [{ bomKey: 'a', invPartKey: 'b' }],
        confirmedMatches: [{ bomKey: 'c', invPartKey: 'd' }],
      });
      expect(store.links.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
      expect(store.links.confirmedMatches).toEqual([{ bomKey: 'c', invPartKey: 'd' }]);
    });

    it('handles null/undefined input', () => {
      store.links.loadFromSaved(null);
      expect(store.links.manualLinks).toEqual([]);
      expect(store.links.confirmedMatches).toEqual([]);
    });

    it('resets linking mode', () => {
      store.links.setLinkingMode(true, { id: 'x' });
      store.links.loadFromSaved([]);
      expect(store.links.linkingMode).toBe(false);
      expect(store.links.linkingInvItem).toBeNull();
    });
  });

  describe('hasLinks', () => {
    it('returns false when empty', () => {
      expect(store.links.hasLinks()).toBe(false);
    });

    it('returns true with manual links', () => {
      store.links.addManualLink('a', 'b');
      expect(store.links.hasLinks()).toBe(true);
    });

    it('returns true with confirmed matches', () => {
      store.links.confirmMatch('a', 'b');
      expect(store.links.hasLinks()).toBe(true);
    });
  });

  describe('clearAll', () => {
    it('resets all link state', () => {
      store.links.addManualLink('a', 'b');
      store.links.confirmMatch('c', 'd');
      store.links.setLinkingMode(true, { id: 'x' });
      store.links.clearAll();
      expect(store.links.manualLinks).toEqual([]);
      expect(store.links.confirmedMatches).toEqual([]);
      expect(store.links.linkingMode).toBe(false);
      expect(store.links.linkingInvItem).toBeNull();
      expect(store.links.linkingBomRow).toBeNull();
    });
  });
});

describe('SECTION_ORDER parsing', () => {
  it('builds SECTION_HIERARCHY from mixed SECTION_ORDER', () => {
    expect(store.SECTION_HIERARCHY).toEqual([
      { name: 'Resistors', children: null },
      { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
      { name: 'Inductors', children: null },
    ]);
  });

  it('builds FLAT_SECTIONS with compound names', () => {
    expect(store.FLAT_SECTIONS).toEqual([
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
    store.preferences.thresholds = { Resistors: 100, Capacitors: 200 };
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
    store.preferences.thresholds = {};
  });

  it('sets threshold value', () => {
    setThreshold('Resistors', 75);
    expect(store.preferences.thresholds.Resistors).toBe(75);
  });
});

// ── New store/setter API tests ────────────────────────────

describe('store (read-only getters)', () => {
  beforeEach(() => {
    setInventory([]);
    setBomResults(null);
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

  it('store.SECTION_HIERARCHY is defined', () => {
    expect(store.SECTION_HIERARCHY).toEqual([
      { name: 'Resistors', children: null },
      { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
      { name: 'Inductors', children: null },
    ]);
  });

  it('store.FLAT_SECTIONS is defined', () => {
    expect(store.FLAT_SECTIONS).toEqual([
      'Resistors',
      'Capacitors',
      'Capacitors > MLCC',
      'Capacitors > Electrolytic',
      'Inductors',
    ]);
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
  });
});

describe('store object', () => {
  beforeEach(() => {
    setInventory([]);
    setBomResults(null);
    setPreferences({ thresholds: {} });
    clearLinks();
  });

  it('store.bomResults reflects setBomResults()', () => {
    const results = [{ bom: {}, inv: null }];
    setBomResults(results);
    expect(store.bomResults).toBe(results);
  });

  it('store.links.addManualLink(bk, ipk) works', () => {
    store.links.addManualLink('BK1', 'IPK1');
    expect(store.links.manualLinks).toEqual([{ bomKey: 'BK1', invPartKey: 'IPK1' }]);
  });

  it('store.links.manualLinks returns the array', () => {
    addManualLink('X', 'Y');
    expect(store.links.manualLinks).toEqual([{ bomKey: 'X', invPartKey: 'Y' }]);
  });

  it('store.links.manualLinks setter works (undo/redo path)', () => {
    addManualLink('A', 'B');
    store.links.manualLinks = [{ bomKey: 'C', invPartKey: 'D' }];
    expect(store.links.manualLinks).toEqual([{ bomKey: 'C', invPartKey: 'D' }]);
  });

  it('store.links.confirmedMatches setter works (undo/redo path)', () => {
    store.links.confirmedMatches = [{ bomKey: 'E', invPartKey: 'F' }];
    expect(store.links.confirmedMatches).toEqual([{ bomKey: 'E', invPartKey: 'F' }]);
  });

  it('store.inventory reads correctly', () => {
    const items = [{ lcsc: 'C1', qty: 5 }];
    setInventory(items);
    expect(store.inventory).toBe(items);
  });

  it('store.preferences.thresholds is accessible and mutable', () => {
    store.preferences.thresholds = { Resistors: 42 };
    expect(store.preferences.thresholds.Resistors).toBe(42);
  });

  it('store.preferences direct property mutation works', () => {
    store.preferences.lastBomDir = '/some/path';
    expect(store.preferences.lastBomDir).toBe('/some/path');
  });
});
