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
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../js/sse.js', () => ({ onEvent: vi.fn() }));

/** The preferences object as it was last written to disk. */
async function lastSaved() {
  const { api } = await import('../../js/api.js');
  const calls = api.mock.calls.filter((c) => c[0] === 'save_preferences');
  if (!calls.length) return null;
  return JSON.parse(calls[calls.length - 1][1]);
}

describe('server roster slice', () => {
  let store;
  beforeEach(async () => {
    vi.resetModules();
    const { api } = await import('../../js/api.js');
    api.mockClear();
    store = await import('../../js/store.js');
  });

  it('starts empty with local selected', () => {
    expect(store.getServers()).toEqual([]);
    expect(store.getServerUrl()).toBe('');
  });

  it('adds a server without selecting it, and persists the roster', async () => {
    // Adding is browsing; selecting is a commitment that costs a restart. They
    // are deliberately separate gestures.
    const r = store.addServer('Cluster', 'https://dubis.example.ts.net/');
    expect(r.ok).toBe(true);
    expect(r.entry).toMatchObject({ name: 'Cluster', url: 'https://dubis.example.ts.net' });
    expect(store.getServerUrl()).toBe('');

    const saved = await lastSaved();
    expect(saved.servers).toHaveLength(1);
    expect(saved.servers[0].url).toBe('https://dubis.example.ts.net');
  });

  it('gives every added server a distinct id', () => {
    store.addServer('One', 'https://one.example');
    store.addServer('Two', 'https://two.example');
    const ids = store.getServers().map((s) => s.id);
    expect(new Set(ids).size).toBe(2);
  });

  it('rejects a bad or duplicate URL without changing the roster', () => {
    store.addServer('Cluster', 'https://dubis.example.ts.net');
    expect(store.addServer('Bad', 'dubis.example.ts.net').ok).toBe(false);
    expect(store.addServer('Dupe', 'https://dubis.example.ts.net/').ok).toBe(false);
    expect(store.getServers()).toHaveLength(1);
  });

  it('returns copies, so a caller cannot mutate the roster in place', () => {
    store.addServer('Cluster', 'https://dubis.example.ts.net');
    store.getServers()[0].name = 'clobbered';
    expect(store.getServers()[0].name).toBe('Cluster');
  });

  it('selectServer writes the chosen URL into server_url', async () => {
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    expect(store.selectServer(entry.id)).toEqual({ ok: true, url: 'https://dubis.example.ts.net' });
    expect(store.getServerUrl()).toBe('https://dubis.example.ts.net');
    expect((await lastSaved()).server_url).toBe('https://dubis.example.ts.net');
  });

  it('selecting the local id clears server_url', () => {
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    store.selectServer(entry.id);
    expect(store.selectServer('local')).toEqual({ ok: true, url: '' });
    expect(store.getServerUrl()).toBe('');
  });

  it('refuses to select an id that is not in the roster', () => {
    const r = store.selectServer('nope');
    expect(r.ok).toBe(false);
    expect(store.getServerUrl()).toBe('');
  });

  it('re-pointing the selected server moves the selection with it', () => {
    // Fixing a typo in the URL of the server you are using must not leave the
    // app pointed at the address you just corrected away from.
    const { entry } = store.addServer('Cluster', 'https://typo.example');
    store.selectServer(entry.id);
    store.updateServer(entry.id, { url: 'https://fixed.example' });
    expect(store.getServerUrl()).toBe('https://fixed.example');
  });

  it('re-pointing an unselected server leaves the selection alone', () => {
    const a = store.addServer('A', 'https://a.example').entry;
    const b = store.addServer('B', 'https://b.example').entry;
    store.selectServer(a.id);
    store.updateServer(b.id, { url: 'https://b2.example' });
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('renaming the selected server does not disturb the selection', () => {
    const { entry } = store.addServer('Cluster', 'https://a.example');
    store.selectServer(entry.id);
    store.updateServer(entry.id, { name: 'Home' });
    expect(store.getServerUrl()).toBe('https://a.example');
    expect(store.getServers()[0].name).toBe('Home');
  });

  it('rejecting an update leaves both roster and selection untouched', () => {
    const { entry } = store.addServer('Cluster', 'https://a.example');
    store.selectServer(entry.id);
    expect(store.updateServer(entry.id, { url: 'not-a-url' }).ok).toBe(false);
    expect(store.getServers()[0].url).toBe('https://a.example');
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('removing the selected server falls back to local', () => {
    // Otherwise the app would keep connecting to an entry the user deleted,
    // and the picker would show it back as an unlisted row.
    const { entry } = store.addServer('Cluster', 'https://a.example');
    store.selectServer(entry.id);
    expect(store.removeServer(entry.id)).toEqual({ removed: true, deselected: true });
    expect(store.getServers()).toEqual([]);
    expect(store.getServerUrl()).toBe('');
  });

  it('removing an unselected server keeps the selection', () => {
    const a = store.addServer('A', 'https://a.example').entry;
    const b = store.addServer('B', 'https://b.example').entry;
    store.selectServer(a.id);
    expect(store.removeServer(b.id)).toEqual({ removed: true, deselected: false });
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('removing an unknown id is a no-op', () => {
    store.addServer('A', 'https://a.example');
    expect(store.removeServer('nope')).toEqual({ removed: false, deselected: false });
    expect(store.getServers()).toHaveLength(1);
  });

  it('loads a persisted roster and drops malformed entries', async () => {
    const { api } = await import('../../js/api.js');
    api.mockImplementation(async (method) => {
      if (method === 'load_preferences') {
        return {
          server_url: 'https://a.example',
          servers: [
            { id: 'a', name: 'A', url: 'https://a.example' },
            { id: 'b', url: 'https://b.example/' },
            { id: 'c', name: 'bad', url: 'b.example' },
          ],
        };
      }
      return {};
    });
    await store.loadPreferences();
    expect(store.getServers()).toEqual([
      { id: 'a', name: 'A', url: 'https://a.example' },
      { id: 'b', name: 'b.example', url: 'https://b.example' },
    ]);
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('carries the roster through a save of an unrelated preference', async () => {
    // savePreferences posts the WHOLE in-memory object and loadPreferences
    // copies known keys only, so a key missing from the loader is erased by
    // the next save of anything else. This pins that servers is not that key.
    const { api } = await import('../../js/api.js');
    api.mockImplementation(async (method) => {
      if (method === 'load_preferences') {
        return { servers: [{ id: 'a', name: 'A', url: 'https://a.example' }] };
      }
      return {};
    });
    await store.loadPreferences();
    store.setBehaviorPrefs({ autoCopySelection: true });
    const saved = await lastSaved();
    expect(saved.servers).toEqual([{ id: 'a', name: 'A', url: 'https://a.example' }]);
  });
});
