import { describe, it, expect, vi, beforeEach } from 'vitest';

// constants.js has top-level fetch that crashes vitest; mock it first.
vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
  LABEL_EXPORT_CFG: {},
}));

// api.js has network side effects; stub it before importing the store.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));

describe('behavior preferences slice', () => {
  let store;
  beforeEach(async () => {
    vi.resetModules();
    store = await import('../../js/store.js');
  });

  it('defaults autoCopySelection to false', () => {
    expect(store.getBehaviorPrefs().autoCopySelection).toBe(false);
  });

  it('exposes exactly the known behavior keys', () => {
    expect(Object.keys(store.getBehaviorPrefs()).sort())
      .toEqual(['autoCopySelection', 'reelCeiling']);
  });

  it('setBehaviorPrefs updates the value and persists', async () => {
    const { api } = await import('../../js/api.js');
    store.setBehaviorPrefs({ autoCopySelection: true });
    expect(store.getBehaviorPrefs().autoCopySelection).toBe(true);
    expect(api).toHaveBeenCalledWith('save_preferences', expect.any(String));
  });

  it('coerces non-boolean input to boolean', () => {
    store.setBehaviorPrefs({ autoCopySelection: 'yes' });
    expect(store.getBehaviorPrefs().autoCopySelection).toBe(true);
  });

  // ── reel ceiling ──
  // A default rule the user can change. The whitelist in normalizeBehavior is
  // applied on read AND write, so a preference missing from it looks saved and
  // silently returns the default — these pin that it is wired both ways.

  it('defaults the reel ceiling to 80', () => {
    expect(store.getBehaviorPrefs().reelCeiling).toBe(80);
  });

  it('persists a changed reel ceiling', () => {
    store.setBehaviorPrefs({ reelCeiling: 150 });
    expect(store.getBehaviorPrefs().reelCeiling).toBe(150);
  });

  it('keeps the two behavior preferences independent', () => {
    store.setBehaviorPrefs({ reelCeiling: 25 });
    store.setBehaviorPrefs({ autoCopySelection: true });
    expect(store.getBehaviorPrefs()).toEqual({ autoCopySelection: true, reelCeiling: 25 });
  });

  it('falls back to the default rather than accepting a ceiling of zero', () => {
    // 0 is not "a ceiling of zero" — it would reject every reel there is.
    for (const bad of [0, -5, 'lots', null, undefined, NaN]) {
      store.setBehaviorPrefs({ reelCeiling: bad });
      expect(store.getBehaviorPrefs().reelCeiling).toBe(80);
    }
  });

  it('accepts a fractional ceiling without rounding it', () => {
    store.setBehaviorPrefs({ reelCeiling: 79.5 });
    expect(store.getBehaviorPrefs().reelCeiling).toBe(79.5);
  });

  it('drops an unknown behavior key rather than persisting it', () => {
    store.setBehaviorPrefs({ somethingElse: true });
    expect(store.getBehaviorPrefs()).not.toHaveProperty('somethingElse');
  });

  // ── server_url ──
  // savePreferences() posts the WHOLE in-memory object while the loader copies
  // known keys only, so a key the loader forgets is erased from
  // preferences.json by the next save of any unrelated preference. remote_mode
  // .resolve_remote_base_url reads server_url from that file.

  it('defaults to local mode', () => {
    expect(store.getServerUrl()).toBe('');
  });

  it('round-trips a URL', () => {
    store.setServerUrl('https://dubis-server.example.ts.net');
    expect(store.getServerUrl()).toBe('https://dubis-server.example.ts.net');
  });

  it('survives a save of an unrelated preference', async () => {
    store.setServerUrl('https://dubis-server.example.ts.net');
    store.setBehaviorPrefs({ reelCeiling: 150 });
    expect(store.getServerUrl()).toBe('https://dubis-server.example.ts.net');
  });

  it('survives being loaded back from stored preferences', async () => {
    const { api } = await import('../../js/api.js');
    api.mockResolvedValueOnce({ server_url: 'https://from-disk.example.ts.net' });
    await store.loadPreferences();
    expect(store.getServerUrl()).toBe('https://from-disk.example.ts.net');
  });

  it('strips a trailing slash so comparisons against an origin match', () => {
    store.setServerUrl('https://dubis-server.example.ts.net/');
    expect(store.getServerUrl()).toBe('https://dubis-server.example.ts.net');
  });

  it('rejects a URL with no scheme rather than storing it', () => {
    // The webview would resolve it relative to the app's own origin and quietly
    // talk to the LOCAL server — a wrong answer that looks like a working one.
    expect(store.setServerUrl('dubis-server.example.ts.net')).toBe('');
    expect(store.getServerUrl()).toBe('');
  });

  it('clears back to local mode on empty input', () => {
    store.setServerUrl('https://dubis-server.example.ts.net');
    expect(store.setServerUrl('   ')).toBe('');
    expect(store.getServerUrl()).toBe('');
  });

  // ── reader_mode / reader_url ──
  // Same carry-through hazard as server_url, with a worse failure: "local"
  // downloads multi-GB weights, so a mode silently reverting to "off" on the
  // next unrelated save would strand a completed install.

  it('defaults reader_mode to off so nothing downloads unasked', () => {
    expect(store.getReaderMode()).toBe('off');
  });

  it('round-trips all four reader modes', () => {
    for (const mode of store.READER_MODES) {
      expect(store.setReaderMode(mode)).toBe(mode);
      expect(store.getReaderMode()).toBe(mode);
    }
    expect(store.READER_MODES).toEqual(['off', 'local', 'remote', 'auto']);
  });

  it('keeps the reader mode across a save of an unrelated preference', () => {
    store.setReaderMode('auto');
    store.setBehaviorPrefs({ reelCeiling: 150 });
    expect(store.getReaderMode()).toBe('auto');
  });

  it('loads the reader mode and url back from stored preferences', async () => {
    const { api } = await import('../../js/api.js');
    api.mockResolvedValueOnce({ reader_mode: 'remote', reader_url: 'http://y740.ts.net:8080' });
    await store.loadPreferences();
    expect(store.getReaderMode()).toBe('remote');
    expect(store.getReaderUrl()).toBe('http://y740.ts.net:8080');
  });

  it('repairs an unknown stored reader mode to off rather than posting it back', async () => {
    // The server rejects an unknown mode with a 400 (domain/api_preferences.py),
    // so a stale value must be repaired here or every later save would fail.
    const { api } = await import('../../js/api.js');
    api.mockResolvedValueOnce({ reader_mode: 'locl' });
    await store.loadPreferences();
    expect(store.getReaderMode()).toBe('off');
  });

  it('defaults reader_url to fleet discovery', () => {
    expect(store.getReaderUrl()).toBe('');
  });

  it('round-trips a reader url and strips its trailing slash', () => {
    expect(store.setReaderUrl('http://y740.ts.net:8080/')).toBe('http://y740.ts.net:8080');
    expect(store.getReaderUrl()).toBe('http://y740.ts.net:8080');
  });

  it('rejects a reader url with no scheme or no host', () => {
    // Without a scheme the browser resolves it against dubIS's own origin,
    // quietly pointing the reader at dubIS instead of the GPU node.
    expect(store.setReaderUrl('y740.ts.net:8080')).toBe('');
    expect(store.setReaderUrl('http://')).toBe('');
    expect(store.getReaderUrl()).toBe('');
  });

  it('clears the reader url back to fleet discovery on empty input', () => {
    store.setReaderUrl('http://y740.ts.net:8080');
    expect(store.setReaderUrl('  ')).toBe('');
    expect(store.getReaderUrl()).toBe('');
  });
});
