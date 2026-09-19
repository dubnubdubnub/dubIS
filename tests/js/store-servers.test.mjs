import { describe, it, expect, vi, beforeEach } from 'vitest';

// constants.js has top-level fetch that crashes vitest; mock it first.
vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
  LABEL_EXPORT_CFG: {},
}));

// api.js has network side effects; stub it before importing the store.
vi.mock('../../js/api.js', () => ({
  setSourceHeaderProvider: () => {},
  api: vi.fn(async () => ({})),
  // store.js fetches inventory through apiEnvelope(), which keeps the
  // per-source status a merged `GET /v1/parts` carries beside `inventory`.
  // Every switch ends in a debounced refresh, so this has to exist even though
  // nothing here asserts on the rows.
  apiEnvelope: vi.fn(async () => ({ inventory: [], sources: [] })),
  apiOn: vi.fn(async () => ({})),
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
    // Reset, not just clear: a test that installs its own mockImplementation
    // would otherwise leak it into every test after it in this file.
    api.mockReset();
    api.mockImplementation(async () => ({}));
    store = await import('../../js/store.js');
    // The app always loads preferences before anything can switch; hydrating
    // here gives every test the same starting point (one Local tab) instead of
    // the pre-hydration state, in which this window deliberately names no
    // source at all so the hub falls back to its saved default.
    store.hydrateSourcesFromPreferences();
  });

  it('starts empty with local selected', () => {
    expect(store.getServers()).toEqual([]);
    expect(store.getServerUrl()).toBe('');
  });

  it('adds a server without selecting it, and persists the roster', async () => {
    // Adding is browsing; selecting switches the inventory you are looking at.
    // They are deliberately separate gestures — putting a server on the menu
    // must not move you onto it.
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

  it('switches by moving this window, not by asking the hub to move', async () => {
    // The hub keeps no "current server" (server/dispatch.py): every /v1 request
    // names its own source with X-Dubis-Source, so moving the signal IS the
    // switch. `PUT /v1/sources/active` only saves the header-less default, for
    // tools/dubis-cli and the next launch.
    const { api } = await import('../../js/api.js');
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    const r = await store.switchActiveSource(entry.id);

    expect(r).toMatchObject({ ok: true, source: entry.id });
    expect(api.mock.calls).toContainEqual(['set_active_source', entry.id]);
    expect(store.getActiveSource()).toBe(entry.id);
    // server_url still moves with it: remote_mode.py, app_restart.py and
    // tools/dubis-cli all read that key and know nothing about tabs.
    expect(store.getServerUrl()).toBe('https://dubis.example.ts.net');
    const saved = await lastSaved();
    expect(saved.server_url).toBe('https://dubis.example.ts.net');
    expect(saved.active_source).toBe(entry.id);
  });

  it('stays switched even when saving the default fails', async () => {
    // The window has demonstrably switched — its next request already carries
    // the new X-Dubis-Source — so refusing to believe it would leave the strip
    // pointing at a tab whose data is already on screen. Only the saved default
    // is lost, and that is a warning, not a failed switch.
    const { api, AppLog } = await import('../../js/api.js');
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    api.mockImplementation(async (method) => (method === 'set_active_source' ? undefined : {}));

    const r = await store.switchActiveSource(entry.id);
    expect(r.ok).toBe(true);
    expect(store.getActiveSource()).toBe(entry.id);
    await Promise.resolve();
    expect(AppLog.warn).toHaveBeenCalledWith(expect.stringMatching(/default/i));
  });

  it('sends the whole source set as one header value for a group', async () => {
    const { api } = await import('../../js/api.js');
    const a = store.addServer('A', 'https://a.example').entry;
    const b = store.addServer('B', 'https://b.example').entry;
    // Adding a server does NOT open a tab — tabs do not appear by themselves,
    // any more than browser tabs do. Opening one per server is what the
    // Preferences `Use` button (and the strip's +) does.
    await store.switchActiveSource(a.id);
    await store.switchActiveSource(b.id);
    const tabs = store.getTabs();
    const ids = [a.id, b.id].map((sid) => tabs.find((t) => t.sources[0] === sid).id);
    const r = await store.groupTabsById(ids);

    expect(r.ok).toBe(true);
    expect(store.getActiveSource()).toBe(a.id + ',' + b.id);
    expect(api.mock.calls).toContainEqual(['set_active_source', a.id + ',' + b.id]);
    // A group has no single owning server, so the key that answers "which ONE
    // server" keeps whatever it last meant rather than being blanked — blanking
    // it would silently re-point tools/dubis-cli and the next launch at local.
    expect(store.getServerUrl()).toBe('https://b.example');
  });

  it('sends "merged" — not the enumerated set — for a tab covering everything', async () => {
    // The hub's own word for "all of them", which stays correct as the roster
    // grows. It is why retiring the All tab needed no wire change.
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    await store.switchActiveSource(entry.id);
    const tabs = store.getTabs();
    const r = await store.groupTabsById(tabs.map((t) => t.id));
    expect(store.getTabs()).toHaveLength(1);

    expect(r.ok).toBe(true);
    expect(store.getActiveSource()).toBe('merged');
    // "merged" has no URL. Clearing server_url would silently re-point the CLI
    // and the next launch at local, which is not what the user asked for.
    expect(store.getServerUrl()).toBe('https://dubis.example.ts.net');
    expect((await lastSaved()).active_source).toBe('merged');
  });

  it('switching to the local id clears server_url', async () => {
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    await store.switchActiveSource(entry.id);
    expect(await store.switchActiveSource('local')).toMatchObject({ ok: true });
    expect(store.getServerUrl()).toBe('');
    expect(store.getActiveSource()).toBe('local');
  });

  it('reuses the tab already showing a server instead of re-pointing the one in front', async () => {
    // A tab's saved view belongs to the server it was opened for; re-pointing
    // would silently change what that tab means.
    const { entry } = store.addServer('Cluster', 'https://dubis.example.ts.net');
    await store.switchActiveSource(entry.id);
    const after = store.getTabs().length;
    await store.switchActiveSource('local');
    await store.switchActiveSource(entry.id);
    expect(store.getTabs()).toHaveLength(after);
  });

  it('refuses to switch to an id that is not in the roster', async () => {
    const { api } = await import('../../js/api.js');
    const r = await store.switchActiveSource('nope');
    expect(r.ok).toBe(false);
    expect(store.getServerUrl()).toBe('');
    expect(store.getActiveSource()).toBe('local');
    // And never tells the hub to adopt it.
    expect(api.mock.calls.filter((c) => c[0] === 'set_active_source')).toEqual([]);
  });

  it('re-pointing the selected server moves the selection with it', async () => {
    // Fixing a typo in the URL of the server you are using must not leave the
    // app pointed at the address you just corrected away from.
    const { entry } = store.addServer('Cluster', 'https://typo.example');
    await store.switchActiveSource(entry.id);
    store.updateServer(entry.id, { url: 'https://fixed.example' });
    expect(store.getServerUrl()).toBe('https://fixed.example');
  });

  it('re-pointing an unselected server leaves the selection alone', async () => {
    const a = store.addServer('A', 'https://a.example').entry;
    const b = store.addServer('B', 'https://b.example').entry;
    await store.switchActiveSource(a.id);
    store.updateServer(b.id, { url: 'https://b2.example' });
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('renaming the selected server does not disturb the selection', async () => {
    const { entry } = store.addServer('Cluster', 'https://a.example');
    await store.switchActiveSource(entry.id);
    store.updateServer(entry.id, { name: 'Home' });
    expect(store.getServerUrl()).toBe('https://a.example');
    expect(store.getServers()[0].name).toBe('Home');
  });

  it('rejecting an update leaves both roster and selection untouched', async () => {
    const { entry } = store.addServer('Cluster', 'https://a.example');
    await store.switchActiveSource(entry.id);
    expect(store.updateServer(entry.id, { url: 'not-a-url' }).ok).toBe(false);
    expect(store.getServers()[0].url).toBe('https://a.example');
    expect(store.getServerUrl()).toBe('https://a.example');
  });

  it('removing the selected server falls back to local', async () => {
    // Otherwise the app would keep connecting to an entry the user deleted,
    // and the picker would show it back as an unlisted row.
    const { entry } = store.addServer('Cluster', 'https://a.example');
    await store.switchActiveSource(entry.id);
    expect(store.removeServer(entry.id)).toEqual({ removed: true, deselected: true });
    expect(store.getServers()).toEqual([]);
    expect(store.getServerUrl()).toBe('');
  });

  it('removing an unselected server keeps the selection', async () => {
    const a = store.addServer('A', 'https://a.example').entry;
    const b = store.addServer('B', 'https://b.example').entry;
    await store.switchActiveSource(a.id);
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

  it('carries active_source through the same way, and resolves it against the roster', async () => {
    // Exactly the same trap as `servers` above, one key over. A preference the
    // loader does not read looks like it saved and comes back as the default,
    // in both directions and with no error — which for this key would mean the
    // tab strip silently reverting to Local on every launch.
    const { api } = await import('../../js/api.js');
    api.mockImplementation(async (method) => {
      if (method === 'load_preferences') {
        return {
          active_source: 'a',
          servers: [{ id: 'a', name: 'A', url: 'https://a.example' }],
        };
      }
      return {};
    });
    await store.loadPreferences();
    expect(store.getActiveSource()).toBe('a');
    store.setBehaviorPrefs({ autoCopySelection: true });
    expect((await lastSaved()).active_source).toBe('a');
  });

  it('drops a persisted active_source naming a server that is gone', async () => {
    const { api } = await import('../../js/api.js');
    api.mockImplementation(async (method) => {
      if (method === 'load_preferences') return { active_source: 'deleted', servers: [] };
      return {};
    });
    await store.loadPreferences();
    expect(store.getActiveSource()).toBe('local');
  });

  it('falls back to server_url when nothing recorded an active_source', async () => {
    // A DUBIS_URL launch, or a hand-edited preferences.json.
    const { api } = await import('../../js/api.js');
    api.mockImplementation(async (method) => {
      if (method === 'load_preferences') {
        return {
          server_url: 'https://a.example',
          servers: [{ id: 'a', name: 'A', url: 'https://a.example' }],
        };
      }
      return {};
    });
    await store.loadPreferences();
    expect(store.getActiveSource()).toBe('a');
  });
});
