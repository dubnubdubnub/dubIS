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

import { App, getThreshold, setThreshold } from '../../js/store.js';

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
