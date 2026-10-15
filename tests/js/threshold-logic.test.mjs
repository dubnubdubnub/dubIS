// The stock-value threshold resolution order (js/threshold-logic.js), pinned
// against the SAME legacy table
// tests/python/domain/test_threshold_defaults.py reads. The grid and the server
// used to answer two different numbers for one category — $50 here, 0 in
// `dubis low-stock` — and one fixture on both sides is what stops that coming
// back.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  BASE_THRESHOLD,
  DEFAULT_THRESHOLDS,
  parentOf,
  resolveThreshold,
} from '../../js/threshold-logic.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const LEGACY = JSON.parse(
  readFileSync(path.join(here, '..', 'fixtures', 'thresholds', 'legacy-defaults.json'), 'utf8'),
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
  it('answers the base when nothing is configured', () => {
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

describe('the legacy per-category numbers are preserved exactly', () => {
  for (const [section, value] of Object.entries(LEGACY)) {
    it(`${section} -> $${value}`, () => {
      expect(resolveThreshold(section, {})).toBe(value);
    });
  }

  it('leaves the one category the hand-set table never covered at the base', () => {
    expect(resolveThreshold('Development Boards, Kits, Programmers', {})).toBe(BASE_THRESHOLD);
  });
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
});
