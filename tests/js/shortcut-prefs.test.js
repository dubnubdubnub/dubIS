import { describe, it, expect, vi, beforeEach } from 'vitest';

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

import { getShortcutPrefs, setShortcutPrefs, SHORTCUT_DEFAULTS } from '../../js/store.js';
import { api } from '../../js/api.js';

describe('shortcut prefs', () => {
  beforeEach(() => { api.mockClear(); setShortcutPrefs({ ...SHORTCUT_DEFAULTS }); });

  it('returns defaults when nothing set', () => {
    expect(getShortcutPrefs()).toEqual({ redo: 'both', enterSubmitsModals: true, vimNav: false });
  });

  it('merges partial updates and keeps other defaults', () => {
    setShortcutPrefs({ redo: 'ctrl-y' });
    expect(getShortcutPrefs()).toEqual({ redo: 'ctrl-y', enterSubmitsModals: true, vimNav: false });
  });

  it('persists via savePreferences (api save_preferences)', () => {
    setShortcutPrefs({ vimNav: true });
    expect(api).toHaveBeenCalledWith('save_preferences', expect.stringContaining('"vimNav":true'));
  });

  it('coerces unknown redo values back to default', () => {
    setShortcutPrefs({ redo: 'nonsense' });
    expect(getShortcutPrefs().redo).toBe('both');
  });
});
