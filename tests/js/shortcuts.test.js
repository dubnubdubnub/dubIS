import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [
    'Resistors',
    { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
    'Inductors',
  ],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { matchesRedo } from '../../js/a11y/shortcuts.js';

const ev = (o) => ({ ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, key: '', ...o });

describe('matchesRedo', () => {
  it('ctrl-shift-z matches Ctrl+Shift+Z only', () => {
    expect(matchesRedo(ev({ ctrlKey: true, shiftKey: true, key: 'Z' }), 'ctrl-shift-z')).toBe(true);
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'ctrl-shift-z')).toBe(false);
  });
  it('ctrl-y matches Ctrl+Y only', () => {
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'ctrl-y')).toBe(true);
    expect(matchesRedo(ev({ ctrlKey: true, shiftKey: true, key: 'Z' }), 'ctrl-y')).toBe(false);
    expect(matchesRedo(ev({ ctrlKey: true, shiftKey: true, key: 'Y' }), 'ctrl-y')).toBe(false);
  });
  it('both matches either', () => {
    expect(matchesRedo(ev({ ctrlKey: true, key: 'y' }), 'both')).toBe(true);
    expect(matchesRedo(ev({ metaKey: true, shiftKey: true, key: 'Z' }), 'both')).toBe(true);
  });
  it('alt disqualifies', () => {
    expect(matchesRedo(ev({ ctrlKey: true, altKey: true, key: 'y' }), 'both')).toBe(false);
  });
});
