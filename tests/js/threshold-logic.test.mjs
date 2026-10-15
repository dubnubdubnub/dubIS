// The stock-value threshold resolution order (js/threshold-logic.js), pinned
// against the SAME legacy table
// tests/python/domain/test_threshold_defaults.py reads. The grid and the server
// used to answer two different numbers for one category — $50 here, 0 in
// `dubis low-stock` — and one fixture on both sides is what stops that coming
// back.
//
// The fixture's DERIVED half (categories in neither table) pins the family
// derivation on both sides too: the tables themselves are generated from
// domain/thresholds.py, and this file is what pins the MATCHING — longest
// prefix wins, base-by-decision respected, no-opinion falls to the base.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  BASE_BY_DECISION,
  BASE_THRESHOLD,
  DEFAULT_THRESHOLDS,
  FAMILY_THRESHOLDS,
  PRESET_BUNDLES,
  PRESET_INFO,
  PRESETS,
  PRESET_SHIPPED,
  deriveThreshold,
  parentOf,
  presetBundle,
  resolveThreshold,
  storedPresetId,
} from '../../js/threshold-logic.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const FULL = JSON.parse(
  readFileSync(path.join(here, '..', 'fixtures', 'thresholds', 'legacy-defaults.json'), 'utf8'),
);

// The entries appended for derivation — the SAME list
// tests/python/domain/test_threshold_defaults.py keeps. The legacy half is a
// historical record and editing one of its rows to make a derivation pass is
// rewriting the pin.
const DERIVED_CASES = {
  'ICs - Clock and Timing': 100,
  'ICs - Clock and Timing > Programmable Oscillators': 100,
  'ICs - Power / Voltage Regulators > DC-DC': 100,
  'Passives - Varistors': 20,
  'Passives - Inductors > Chokes': 20,
  'Discrete Semiconductors > IGBTs': 20,
  'Connectors > RF': 20,
  'Development Boards, Kits, Programmers': 50,
  'Mechanical & Hardware > Standoffs': 50,
  'Something Brand New': 50,
};

const LEGACY = Object.fromEntries(
  Object.entries(FULL).filter(([section]) => !(section in DERIVED_CASES)),
);

describe('parentOf', () => {
  it('has no parent for a top-level section', () => {
    expect(parentOf('Diodes')).toBe('');
  });

  it('splits a compound section on " > "', () => {
    expect(parentOf('Passives - Resistors > Chip Resistors')).toBe('Passives - Resistors');
  });

  it('is not fooled by the " - " inside a section name', () => {
    expect(parentOf('ICs - Power / Voltage Regulators')).toBe('');
  });
});

describe('resolveThreshold — the order', () => {
  it('answers the base when nothing is configured and no family applies', () => {
    expect(resolveThreshold('Other', {})).toBe(BASE_THRESHOLD);
    expect(resolveThreshold('Other', null)).toBe(BASE_THRESHOLD);
    expect(resolveThreshold('Other', undefined)).toBe(BASE_THRESHOLD);
  });

  it('prefers the user override over the shipped default', () => {
    expect(DEFAULT_THRESHOLDS['Diodes']).toBe(10);
    expect(resolveThreshold('Diodes', { Diodes: 7 })).toBe(7);
  });

  it('inherits a parent override for a sub-category', () => {
    expect(resolveThreshold('Diodes > Zener', { Diodes: 7 })).toBe(7);
  });

  it('inherits a parent default for a sub-category', () => {
    expect(resolveThreshold('Passives - Capacitors > MLCC', {})).toBe(20);
  });

  it('lets a parent override outrank a child default', () => {
    // The user spoke about the family; the shipped table must not overrule
    // something they set deliberately one level up.
    expect(resolveThreshold('Passives - Capacitors > MLCC', { 'Passives - Capacitors': 3 })).toBe(3);
  });

  it('lets an exact child override outrank a parent override', () => {
    const overrides = { Diodes: 3, 'Diodes > Zener': 90 };
    expect(resolveThreshold('Diodes > Zener', overrides)).toBe(90);
  });

  it('treats $0 as a value, not an absence', () => {
    // A slider dragged to 0 means "never green"; falling through to the default
    // here would silently undo the person who set it.
    expect(resolveThreshold('Diodes', { Diodes: 0 })).toBe(0);
  });

  it('reads a numeric string from a preferences file as a number', () => {
    expect(resolveThreshold('Diodes', { Diodes: '12' })).toBe(12);
  });
});

describe('deriveThreshold — the family tables', () => {
  it('derives the IC family for a category nobody tuned', () => {
    expect(deriveThreshold('ICs - Clock and Timing')).toBe(100);
    expect(deriveThreshold('ICs - Power / Voltage Regulators > DC-DC')).toBe(100);
  });

  it('derives the passives family', () => {
    expect(deriveThreshold('Passives - Varistors')).toBe(20);
    // The base-by-decision of the PARENT is an opinion about that row, not
    // about categories nobody has seen yet — the child still derives.
    expect(deriveThreshold('Passives - Inductors > Chokes')).toBe(20);
  });

  it('derives the connector/discrete family', () => {
    expect(deriveThreshold('Discrete Semiconductors > IGBTs')).toBe(20);
    expect(deriveThreshold('Connectors > RF')).toBe(20);
  });

  it('answers null — no opinion, not zero — outside every family', () => {
    expect(deriveThreshold('Something Brand New')).toBeNull();
  });

  it('respects the base-by-decision list over a family prefix', () => {
    // "Passives - Inductors" starts with a passives family prefix and the
    // hand-tuned era left it at the base ON PURPOSE. The legacy fixture pins
    // the number; this pins the mechanism.
    expect(BASE_BY_DECISION.has('Passives - Inductors')).toBe(true);
    expect(deriveThreshold('Passives - Inductors')).toBeNull();
    expect(resolveThreshold('Passives - Inductors', {})).toBe(BASE_THRESHOLD);
  });

  it('lets a decision only speak where the tables are silent', () => {
    expect(resolveThreshold('Switches', { Switches: 12 })).toBe(12);
    expect(resolveThreshold('Connectors', {})).toBe(20); // the default, not the family
  });

  it('is consulted only after every table misses', () => {
    // "ICs - Sensors" is a legacy base-by-decision name; an override still wins.
    expect(resolveThreshold('ICs - Sensors', { 'ICs - Sensors': 200 })).toBe(200);
  });
});

describe('presets — the cart semantics, applied to thresholds', () => {
  it('ships the sentinel first and a bundle for every other id', () => {
    expect(PRESETS[0]).toBe(PRESET_SHIPPED);
    expect(Object.keys(PRESET_BUNDLES).sort()).toEqual(
      PRESETS.filter((id) => id !== PRESET_SHIPPED).sort(),
    );
    for (const id of PRESETS) {
      expect(PRESET_INFO[id].name, id).toBeTruthy();
      expect(PRESET_INFO[id].description, id).toBeTruthy();
    }
  });

  it('throws for an unrecognised CALLER id — the plan_cart asymmetry', () => {
    expect(() => presetBundle('no-such-preset')).toThrow(/unknown threshold preset/);
    expect(presetBundle(PRESET_SHIPPED)).toEqual({});
  });

  it('falls back for an unrecognised STORED id — the _effective_preset asymmetry', () => {
    expect(storedPresetId('no-such-preset')).toBe(PRESET_SHIPPED);
    expect(storedPresetId('')).toBe(PRESET_SHIPPED);
    expect(storedPresetId(null)).toBe(PRESET_SHIPPED);
    expect(storedPresetId(41)).toBe(PRESET_SHIPPED);
    expect(storedPresetId(' ic-heavy ')).toBe('ic-heavy');
  });

  it('sits between the override and the shipped default', () => {
    const bundle = { Diodes: 90 };
    expect(resolveThreshold('Diodes', {}, { preset: bundle })).toBe(90);
    expect(resolveThreshold('Diodes', { Diodes: 7 }, { preset: bundle })).toBe(7);
    expect(resolveThreshold('Diodes', {})).toBe(10); // no preset: nothing moves
  });

  it('inherits a parent bundle entry for a child', () => {
    expect(resolveThreshold('Diodes > Zener', {}, { preset: { Diodes: 90 } })).toBe(90);
  });

  it('moves the tuned IC names without touching derivation for new ones', () => {
    const bundle = presetBundle('ic-heavy');
    expect(resolveThreshold('ICs - Microcontrollers', {})).toBe(BASE_THRESHOLD);
    expect(resolveThreshold('ICs - Microcontrollers', {}, { preset: bundle })).toBe(200);
    // A NEW IC category derives the family value with or without the preset —
    // bundles name categories, derivation extrapolates to unseen ones.
    expect(resolveThreshold('ICs - Clock and Timing', {}, { preset: bundle })).toBe(100);
  });

  it('is frozen, so a panel cannot edit the shipped bundles at runtime', () => {
    expect(Object.isFrozen(PRESET_BUNDLES)).toBe(true);
    expect(Object.isFrozen(PRESET_BUNDLES['ic-heavy'])).toBe(true);
  });
});

describe('the legacy per-category numbers are preserved exactly', () => {
  for (const [section, value] of Object.entries(LEGACY)) {
    it(`${section} -> $${value}`, () => {
      expect(resolveThreshold(section, {})).toBe(value);
    });
  }

  it('keeps the one category the hand-set table never covered at the base BY DECISION', () => {
    expect(BASE_BY_DECISION.has('Development Boards, Kits, Programmers')).toBe(true);
    expect(resolveThreshold('Development Boards, Kits, Programmers', {})).toBe(BASE_THRESHOLD);
  });
});

describe('the derived per-category numbers are pinned on both sides', () => {
  for (const [section, value] of Object.entries(DERIVED_CASES)) {
    it(`${section} -> $${value}`, () => {
      expect(resolveThreshold(section, {})).toBe(value);
    });
  }
});

describe('the generated default table', () => {
  it('ships the categories that differ from the base', () => {
    expect(DEFAULT_THRESHOLDS).toEqual({
      'Connectors': 20,
      'Crystals & Oscillators': 10,
      'Diodes': 10,
      'Passives - Capacitors': 20,
      'Passives - Resistors': 4,
    });
  });

  it('never repeats the base — that is what the base is for', () => {
    for (const [section, value] of Object.entries(DEFAULT_THRESHOLDS)) {
      expect(value, section).not.toBe(BASE_THRESHOLD);
    }
  });

  it('is frozen, so a panel cannot edit the shipped table at runtime', () => {
    expect(Object.isFrozen(DEFAULT_THRESHOLDS)).toBe(true);
  });

  it('carries the family table and the decision list the Python side owns', () => {
    // These arrive GENERATED from domain/thresholds.py; the fixture pins the
    // answers, this pins that the tables arrived at all.
    expect(FAMILY_THRESHOLDS.length).toBeGreaterThan(0);
    expect(BASE_BY_DECISION.has('Switches')).toBe(true);
  });
});
