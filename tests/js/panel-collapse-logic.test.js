import { describe, it, expect } from 'vitest';
import {
  REGIONS, REGION_IDS, normalizeCollapsed, toggleRegion, setRegion,
  panelsToReopen, REOPEN_TRIGGERS, regionsChanged,
} from '../../js/panel-collapse-logic.js';

const OPEN = { import: false, bom: false, console: false };

describe('REGIONS', () => {
  it('is exactly the three collapsible regions', () => {
    expect(REGION_IDS).toEqual(['import', 'bom', 'console']);
  });

  it('names real DOM ids for each region', () => {
    expect(REGIONS.map(r => r.panelId)).toEqual(['panel-import', 'panel-bom', 'console-log']);
  });

  it('gives every region a human label for the toggle tooltip', () => {
    for (const r of REGIONS) expect(r.label.length).toBeGreaterThan(0);
  });
});

describe('normalizeCollapsed', () => {
  it('defaults every region to open', () => {
    expect(normalizeCollapsed(undefined)).toEqual(OPEN);
    expect(normalizeCollapsed(null)).toEqual(OPEN);
    expect(normalizeCollapsed('nonsense')).toEqual(OPEN);
    expect(normalizeCollapsed([])).toEqual(OPEN);
    expect(normalizeCollapsed(42)).toEqual(OPEN);
  });

  it('keeps known regions and drops unknown keys', () => {
    expect(normalizeCollapsed({ import: true, bogus: true }))
      .toEqual({ import: true, bom: false, console: false });
  });

  it('coerces non-boolean stored values', () => {
    expect(normalizeCollapsed({ import: 1, bom: 'yes', console: 0 }))
      .toEqual({ import: true, bom: true, console: false });
  });
});

describe('toggleRegion / setRegion', () => {
  it('toggles one region without touching the others', () => {
    expect(toggleRegion(OPEN, 'bom')).toEqual({ import: false, bom: true, console: false });
  });

  it('is its own inverse', () => {
    expect(toggleRegion(toggleRegion(OPEN, 'bom'), 'bom')).toEqual(OPEN);
  });

  it('does not mutate the input state', () => {
    const before = { ...OPEN };
    toggleRegion(before, 'import');
    setRegion(before, 'bom', true);
    expect(before).toEqual(OPEN);
  });

  it('setRegion is idempotent', () => {
    const once = setRegion(OPEN, 'import', true);
    expect(setRegion(once, 'import', true)).toEqual(once);
  });

  it('throws on an unknown region id — a programming error', () => {
    expect(() => toggleRegion(OPEN, 'nope')).toThrow(RangeError);
    expect(() => setRegion(OPEN, 'nope', true)).toThrow(RangeError);
  });
});

describe('panelsToReopen', () => {
  it('reopens the left panel for PO and import activity', () => {
    expect(panelsToReopen('PO_CHANGED')).toEqual(['import']);
    expect(panelsToReopen('IMPORT_COMPLETED')).toEqual(['import']);
    expect(panelsToReopen('IMPORT_MAPPER_OPENED')).toEqual(['import']);
  });

  it('reopens the BOM panel for BOM activity', () => {
    expect(panelsToReopen('BOM_LOADED')).toEqual(['bom']);
    expect(panelsToReopen('BOM_CLEARED')).toEqual(['bom']);
    expect(panelsToReopen('BOM_DIRTY')).toEqual(['bom']);
    expect(panelsToReopen('CONFIRMED_CHANGED')).toEqual(['bom']);
    expect(panelsToReopen('LINKS_CHANGED')).toEqual(['bom']);
    expect(panelsToReopen('LINKING_MODE')).toEqual(['bom']);
  });

  it('reopens the console AND its containing panel on warn/error', () => {
    // #console-log lives inside #panel-import, so reopening it alone is invisible.
    expect(panelsToReopen('LOG_WARN').sort()).toEqual(['console', 'import']);
    expect(panelsToReopen('LOG_ERROR').sort()).toEqual(['console', 'import']);
  });

  it('reopens nothing for routine info logging', () => {
    // Otherwise a background log line would defeat the collapse entirely.
    expect(panelsToReopen('LOG_INFO')).toEqual([]);
    expect(REOPEN_TRIGGERS).not.toHaveProperty('LOG_INFO');
  });

  it('reopens the left panel for modes that need the PO picker', () => {
    expect(panelsToReopen('LABEL_MODE')).toEqual(['import']);
    expect(panelsToReopen('CART_ADD_MODE')).toEqual(['import']);
  });

  it('returns an empty list for an unknown trigger rather than throwing', () => {
    expect(panelsToReopen('SOMETHING_ELSE')).toEqual([]);
    expect(panelsToReopen('')).toEqual([]);
  });

  it('returns a copy, so a caller cannot mutate the table', () => {
    const got = panelsToReopen('PO_CHANGED');
    got.push('bom');
    expect(panelsToReopen('PO_CHANGED')).toEqual(['import']);
  });

  it('only ever names real regions', () => {
    for (const ids of Object.values(REOPEN_TRIGGERS)) {
      expect(ids.length).toBeGreaterThan(0);
      for (const id of ids) expect(REGION_IDS).toContain(id);
    }
  });
});

describe('regionsChanged', () => {
  it('is false when nothing moved, so no needless preference write happens', () => {
    expect(regionsChanged(OPEN, { ...OPEN })).toBe(false);
  });

  it('is true when any region moved', () => {
    expect(regionsChanged(OPEN, { ...OPEN, bom: true })).toBe(true);
    expect(regionsChanged({ ...OPEN, console: true }, OPEN)).toBe(true);
  });

  it('treats absent keys as open rather than as a change', () => {
    expect(regionsChanged(OPEN, /** @type {any} */ ({}))).toBe(false);
  });
});

describe('reopen semantics as a whole', () => {
  it('every trigger, applied to a fully-collapsed app, opens something', () => {
    const allClosed = { import: true, bom: true, console: true };
    for (const trigger of Object.keys(REOPEN_TRIGGERS)) {
      let next = allClosed;
      for (const id of panelsToReopen(trigger)) next = setRegion(next, id, false);
      expect(regionsChanged(allClosed, next), `${trigger} should open a region`).toBe(true);
    }
  });

  it('every trigger, applied to a fully-open app, changes nothing', () => {
    for (const trigger of Object.keys(REOPEN_TRIGGERS)) {
      let next = OPEN;
      for (const id of panelsToReopen(trigger)) next = setRegion(next, id, false);
      expect(regionsChanged(OPEN, next), `${trigger} should be a no-op when open`).toBe(false);
    }
  });
});
