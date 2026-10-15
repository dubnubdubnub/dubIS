// The per-source threshold profile cache (js/threshold-profiles.js) and the
// row-colour seam it feeds (js/store.js's `getThreshold`).
//
// A threshold is a PER-SERVER fact: every dubIS server keeps its own snapshot
// history and answers `GET /v1/meta` with the active one. Before this module the
// grid coloured rows from the table baked into THIS build, so a window showing a
// remote server painted that server's parts with local numbers — the same part
// low on one screen and fine on the tab beside it. These tests are the contract
// that closed it:
//
//   1. two sources with different active profiles colour the SAME section
//      differently, and each answer is that source's own;
//   2. a source whose /v1/meta fails is marked stale and keeps its last-known
//      profile — it does NOT quietly resolve through the build-time table;
//   3. merged and grouped tabs resolve against the hub's profile;
//   4. the cache is keyed BY SOURCE, not by recency — a late answer for the
//      source you LEFT cannot recolour the source you moved to.
//
// `profileState()` is asserted alongside the resolutions because "we fell
// through to the shipped table" has to be a thing the UI can say, not a thing
// that silently happens.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// constants.js has top-level fetch that crashes vitest; mock it first.
vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
  LABEL_EXPORT_CFG: {},
}));

// api.js has network side effects; stub it before importing the store. The
// profile cache imports SOURCE_HEADER rather than restating the wire name, so
// the mock has to carry it — a missing one would send `undefined: <source>` and
// every test below would still pass while the real client sent no header at all.
vi.mock('../../js/api.js', () => ({
  setSourceHeaderProvider: () => {},
  SOURCE_HEADER: 'X-Dubis-Source',
  api: vi.fn(async () => ({})),
  apiEnvelope: vi.fn(async () => ({ inventory: [], sources: [] })),
  apiOn: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn(), clear: vi.fn() },
}));

vi.mock('../../js/sse.js', () => ({ onEvent: vi.fn() }));

/** The shipped table's Diodes entry — the number the assertions below name as
 *  the one that must NOT show up. */
const SHIPPED_DIODES = 10;

/** Three servers, three different answers for `Diodes`. The hub's is a number
 *  no shipped table would ever produce, which is what makes "did it fall back
 *  to the build-time table?" answerable from a value alone. */
const HUB = {
  active_threshold_profile: { Diodes: 4242 },
  default_thresholds: { Diodes: SHIPPED_DIODES, 'Chip Resistors': 20 },
  base_threshold: 50,
  threshold_snapshots: { count: 2, active_id: 1 },
};
const SHOP = {
  active_threshold_profile: { Diodes: 900, 'Chip Resistors': 11 },
  default_thresholds: { Diodes: SHIPPED_DIODES, 'Chip Resistors': 20 },
  base_threshold: 50,
  threshold_snapshots: { count: 3, active_id: 2 },
};
const BENCH = {
  active_threshold_profile: { Diodes: 2 },
  default_thresholds: { Diodes: SHIPPED_DIODES, 'Chip Resistors': 20 },
  base_threshold: 50,
  threshold_snapshots: { count: 1, active_id: 0 },
};

/** @type {Map<string, any>} what each source id answers /v1/meta with */
let responders;
/** @type {Array<{url: string, source: string}>} */
let calls;

function installFetch() {
  responders = new Map();
  calls = [];
  globalThis.fetch = vi.fn(async (url, init) => {
    const source = (init && init.headers && init.headers['X-Dubis-Source']) || '';
    calls.push({ url: String(url), source });
    if (!responders.has(source)) {
      return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) };
    }
    const responder = responders.get(source);
    if (responder instanceof Error) throw responder;
    if (responder && responder.__status) {
      return {
        ok: false,
        status: responder.__status,
        statusText: 'Internal Server Error',
        json: async () => responder.__body || {},
      };
    }
    return { ok: true, status: 200, statusText: 'OK', json: async () => responder };
  });
}

/** Source ids that have been asked, in order. */
function asked() { return calls.map((c) => c.source); }

/**
 * A `/v1/meta` whose answers the test releases by hand, so two sources can
 * answer OUT OF ORDER. Every eager-fetch test awaits each tab switch, which
 * means the last response to land is always the ACTIVE source's — and a single
 * global profile copy overwritten on each fetch gives the identical answer. The
 * deferred fetch is what makes "the source you LEFT answered AFTER the source
 * you moved to" expressible, and that ordering is the only thing which tells a
 * per-source cache apart from one global copy.
 * @returns {Map<string, {resolve: (body: any) => void}>} source id -> its gate
 */
function installDeferredFetch() {
  /** @type {Map<string, {resolve: (body: any) => void}>} */
  const gates = new Map();
  globalThis.fetch = vi.fn((_url, init) => {
    const source = (init && init.headers && init.headers['X-Dubis-Source']) || '';
    /** @type {(body: any) => void} */
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    gates.set(source, { resolve: release });
    return held;
  });
  return gates;
}

/** Let one source's held `/v1/meta` answer. Throws rather than silently
 *  succeeding if that source was never asked — a test that answered nothing
 *  would assert nothing. */
function answerDeferred(gates, source, body) {
  const gate = gates.get(source);
  if (!gate) {
    throw new Error(`no /v1/meta request was made for ${source} `
      + `(asked: ${[...gates.keys()].join(', ') || 'nothing'})`);
  }
  gate.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => body });
}

/** Drain the fire-and-forget refresh a tab switch kicks off. The fake fetch is
 *  pure microtasks (no real timer inside), so yielding repeatedly is enough —
 *  the fake CLOCK only has to hold store.js's 250ms inventory debounce still. */
async function settle() {
  for (let i = 0; i < 16; i += 1) await Promise.resolve();
}

/**
 * The store with preferences hydrated and these servers on the roster.
 * `Shop`/`Bench` get SHOP/BENCH as their /v1/meta answer automatically; the hub
 * always answers HUB.
 * @param {Record<string, string>} roster name -> url
 */
async function bootStore(roster) {
  // The hub answers BEFORE hydration: hydrating fires the first profile refresh,
  // and a source with no responder yet would cache an `unavailable` entry that
  // the coalescing guard then hands to every later caller in the same test.
  responders.set('local', HUB);
  const store = await import('../../js/store.js');
  store.hydrateSourcesFromPreferences();
  await settle();
  const ids = {};
  for (const [name, url] of Object.entries(roster)) {
    ids[name] = store.addServer(name, url).entry.id;
    responders.set(ids[name], name === 'Shop' ? SHOP : BENCH);
  }
  await settle();
  return { store, ids };
}

describe('threshold profile cache', () => {
  beforeEach(async () => {
    // Fake timers for the whole file: every tab switch schedules js/store.js's
    // 250ms debounced inventory refresh, and a real-clock one left pending
    // fires inside a later test — a flake that only shows up under load.
    vi.useFakeTimers();
    vi.resetModules();
    installFetch();
    const { AppLog } = await import('../../js/api.js');
    AppLog.warn.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    delete globalThis.fetch;
  });

  describe('which source answers', () => {
    it('names a single server itself, and the hub for merged or grouped', async () => {
      const { profileKeyFor } = await import('../../js/threshold-profiles.js');
      const { LOCAL_ID, MERGED_ID } = await import('../../js/server-tabs-logic.js');

      expect(profileKeyFor('local')).toBe(LOCAL_ID);
      expect(profileKeyFor('shop')).toBe('shop');
      // A merged view is the SUM of several servers' stock and has no threshold
      // profile of its own — the hub is what answers it (server/dispatch.py's
      // outcome table: a read of anything but /v1/parts is served locally).
      expect(profileKeyFor(MERGED_ID)).toBe(LOCAL_ID);
      expect(profileKeyFor('shop,bench')).toBe(LOCAL_ID);
      expect(profileKeyFor(' local ')).toBe(LOCAL_ID);
      // Pre-hydration the window names no source. Guessing the hub there would
      // colour a remote window's first paint with the hub's numbers.
      expect(profileKeyFor('')).toBe('');
      expect(profileKeyFor(undefined)).toBe('');
    });
  });

  describe('resolution', () => {
    it('hands resolveThreshold the source’s own profile, defaults and base', async () => {
      const { refreshProfile, resolveOptionsFor, profileState } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await refreshProfile('shop');

      expect(profileState('shop')).toBe('fresh');
      expect(resolveOptionsFor('shop')).toEqual({
        server: SHOP.active_threshold_profile,
        defaults: SHOP.default_thresholds,
        base: SHOP.base_threshold,
      });
    });

    it('omits a base the server did not answer, rather than sending 0', async () => {
      // resolveThreshold() reads only `undefined` as "no answer"; Number(null)
      // is 0, which would silently make every section low — the same bug that
      // once made `dubis low-stock` flag every part.
      const { refreshProfile, resolveOptionsFor } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', { ...SHOP, base_threshold: null });
      await refreshProfile('shop');
      expect('base' in resolveOptionsFor('shop')).toBe(false);
    });

    it('says so, and resolves with nothing, for a source it has never asked', async () => {
      const { resolveOptionsFor, profileState, profileError } =
        await import('../../js/threshold-profiles.js');
      // The empty options object is the ONLY way the shipped table is reached
      // any more — and the state names it, so no caller can claim 'fresh'.
      expect(resolveOptionsFor('never-asked')).toEqual({});
      expect(profileState('never-asked')).toBe('unknown');
      expect(profileError('never-asked')).toBe('');
    });

    it('marks a source that failed its FIRST fetch unavailable, and logs it', async () => {
      const { refreshProfile, resolveOptionsFor, profileState, profileError } =
        await import('../../js/threshold-profiles.js');
      const { AppLog } = await import('../../js/api.js');
      responders.set('shop', new Error('Connection refused'));

      await refreshProfile('shop');

      expect(profileState('shop')).toBe('unavailable');
      expect(profileError('shop')).toContain('Connection refused');
      expect(resolveOptionsFor('shop')).toEqual({});
      // Loud, not silent: the shipped table is in use and the console says why.
      expect(AppLog.warn.mock.calls.flat().join(' ')).toContain('shop');
    });
  });

  describe('staleness', () => {
    it('keeps the last-known profile when a later fetch fails', async () => {
      const { refreshProfile, resolveOptionsFor, profileState, profileError } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await refreshProfile('shop');
      expect(resolveOptionsFor('shop').server.Diodes).toBe(900);

      responders.set('shop', new Error('socket hang up'));
      await refreshProfile('shop');

      expect(profileState('shop')).toBe('stale');
      expect(profileError('shop')).toContain('socket hang up');
      // The whole point: a blip must not read as "that server reset its
      // thresholds", and must not drop to the shipped table either.
      expect(resolveOptionsFor('shop').server.Diodes).toBe(900);
      expect(resolveOptionsFor('shop').server.Diodes).not.toBe(SHIPPED_DIODES);
    });

    it('returns to fresh, with the new values, once the source answers again', async () => {
      const { refreshProfile, resolveOptionsFor, profileState, profileError } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await refreshProfile('shop');
      responders.set('shop', new Error('down'));
      await refreshProfile('shop');
      responders.set('shop', { ...SHOP, active_threshold_profile: { Diodes: 77 } });
      await refreshProfile('shop');

      expect(profileState('shop')).toBe('fresh');
      expect(profileError('shop')).toBe('');
      expect(resolveOptionsFor('shop').server.Diodes).toBe(77);
    });

    it('reads a server error body as the reason', async () => {
      const { refreshProfile, profileError, profileState } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await refreshProfile('shop');
      responders.set('shop', { __status: 500, __body: { error: 'threshold store is locked' } });
      await refreshProfile('shop');
      expect(profileState('shop')).toBe('stale');
      expect(profileError('shop')).toBe('threshold store is locked');
    });

    it('treats a server with no snapshot fields as no server layer', async () => {
      // An older dubIS answers /v1/meta without `active_threshold_profile`.
      // That is "this server has no opinion", not "every threshold is zero" —
      // and its OWN snapshot 0 / base still travel.
      const { refreshProfile, resolveOptionsFor, profileState } =
        await import('../../js/threshold-profiles.js');
      responders.set('old', { default_thresholds: { Diodes: 33 }, base_threshold: 7 });
      await refreshProfile('old');
      expect(profileState('old')).toBe('fresh');
      expect('server' in resolveOptionsFor('old')).toBe(false);
      expect(resolveOptionsFor('old')).toEqual({ defaults: { Diodes: 33 }, base: 7 });
    });
  });

  describe('the grid’s own seam', () => {
    /** The two-server roster every test here switches between. */
    const ROSTER = { Shop: 'https://shop.example', Bench: 'https://bench.example' };

    it('colours one section differently on two servers', async () => {
      const { store, ids } = await bootStore(ROSTER);
      store.setPreferences({ thresholds: {} });

      await store.switchActiveSource(ids.Shop);
      await settle();
      expect(store.getThreshold('Diodes')).toBe(900);

      await store.switchActiveSource(ids.Bench);
      await settle();
      // Same part, same section, same build — the only thing that moved is
      // which server is answering.
      expect(store.getThreshold('Diodes')).toBe(2);
      // And neither of them is the table this build shipped with.
      expect(store.getThreshold('Diodes')).not.toBe(SHIPPED_DIODES);
    });

    it('is keyed by source, not by recency: a late answer for the source you LEFT cannot recolour the one you moved to', async () => {
      // THE regression this module exists to prevent, and the only test here
      // that can tell a per-source cache from ONE GLOBAL COPY. Every other test
      // awaits each tab switch, so the last `/v1/meta` to complete is always
      // the ACTIVE source's — and a global copy overwritten on each fetch
      // answers identically. (Verified by mutation: replacing the cache key
      // with a constant leaves all of them green.) What separates the two
      // designs is ORDER: the source you moved AWAY FROM answering LAST.
      const gates = installDeferredFetch();
      const store = await import('../../js/store.js');
      store.hydrateSourcesFromPreferences();
      await settle();
      answerDeferred(gates, 'local', HUB);
      await settle();

      const shop = store.addServer('Shop', 'https://shop.example').entry.id;
      const bench = store.addServer('Bench', 'https://bench.example').entry.id;
      store.setPreferences({ thresholds: {} });

      // Move to Shop. Its /v1/meta is asked and stays in flight.
      await store.switchActiveSource(shop);
      await settle();
      expect(gates.has(shop)).toBe(true);

      // Move on to Bench BEFORE Shop answers — now Shop's response is late.
      await store.switchActiveSource(bench);
      await settle();
      expect(gates.has(bench)).toBe(true);

      // Bench answers first, so the grid colours with Bench.
      answerDeferred(gates, bench, BENCH);
      await settle();
      expect(store.getThreshold('Diodes')).toBe(2);

      // Shop answers LATE, for a source this window no longer shows. It belongs
      // in Shop's entry and nowhere else.
      answerDeferred(gates, shop, SHOP);
      await settle();

      const { profileState, profileFor } = await import('../../js/threshold-profiles.js');
      // A single global copy would have been overwritten here and now say 900.
      expect(profileState(store.getActiveSource())).toBe('fresh');
      expect(store.getThreshold('Diodes')).toBe(2);
      // And the late answer is not thrown away either — it is filed under Shop,
      // where the tab that shows Shop reads it.
      expect(profileFor(shop).state).toBe('fresh');
      expect(profileFor(shop).profile.active.Diodes).toBe(900);
      expect(profileFor(bench).profile.active.Diodes).toBe(2);
    });

    it('agrees with resolveThreshold() run against that source’s profile', async () => {
      const { store, ids } = await bootStore(ROSTER);
      const mod = await import('../../js/threshold-profiles.js');
      const { presetBundle, resolveThreshold, storedPresetId } =
        await import('../../js/threshold-logic.js');
      await store.switchActiveSource(ids.Bench);
      await settle();

      // Differential, not a restated constant: whatever the shared order says
      // for the ACTIVE source's own profile is what the grid must show.
      const prefs = store.preferencesSignal.peek().values;
      const expected = resolveThreshold('Diodes', prefs.thresholds, {
        preset: presetBundle(storedPresetId(prefs.threshold_preset)),
        ...mod.resolveOptionsFor(store.getActiveSource()),
      });
      expect(store.getThreshold('Diodes')).toBe(expected);
      expect(expected).toBe(2);

      // And the SAME expression against the source this window is NOT showing
      // must DISAGREE. Without this the test only recomputes the number the
      // getter just produced — a tautology that passes with one profile, two
      // profiles or none. Two profiles, two answers, and the grid holds the
      // active one: that is the whole claim.
      await mod.refreshProfile(ids.Shop);
      const shopAnswer = resolveThreshold('Diodes', prefs.thresholds, {
        preset: presetBundle(storedPresetId(prefs.threshold_preset)),
        ...mod.resolveOptionsFor(ids.Shop),
      });
      expect(shopAnswer).toBe(900);
      expect(shopAnswer).not.toBe(store.getThreshold('Diodes'));
    });

    it('keeps the layer order: override beats preset beats server', async () => {
      const { store, ids } = await bootStore(ROSTER);
      store.setPreferences({ thresholds: {} });
      await store.switchActiveSource(ids.Shop);
      await settle();

      // The shipped preset is an empty bundle, so the server answers — and it
      // answers 900, not the 10 this build shipped with.
      store.setPreferences({ threshold_preset: 'shipped' });
      expect(store.getThreshold('Diodes')).toBe(900);
      // `tight` names Diodes, and the chosen preset still sits ABOVE the server
      // (js/threshold-logic.js's order — the server layer did not move it).
      store.setPreferences({ threshold_preset: 'tight' });
      expect(store.getThreshold('Diodes')).toBe(20);
      // And the sparse client override beats everything. That layer is
      // preferences.thresholds, unchanged by this work.
      store.setPreferences({ thresholds: { Diodes: 3 } });
      expect(store.getThreshold('Diodes')).toBe(3);
    });

    it('follows the tab: switching sources re-fetches and re-colours', async () => {
      const { store, ids } = await bootStore(ROSTER);

      await store.switchActiveSource(ids.Shop);
      await settle();
      expect(asked()).toContain(ids.Shop);
      expect(store.getThreshold('Diodes')).toBe(900);

      await store.switchActiveSource(ids.Bench);
      await settle();
      expect(asked()).toContain(ids.Bench);
      expect(store.getThreshold('Diodes')).toBe(2);
    });

    it('asks the HUB for a merged tab, not either member', async () => {
      const { store, ids } = await bootStore(ROSTER);
      await store.switchActiveSource(ids.Shop);
      await settle();
      expect(store.getThreshold('Diodes')).toBe(900);

      // Both remotes + local = every source = the merged selector.
      await store.openTab(['local', ids.Shop, ids.Bench]);
      await settle();
      expect(store.getActiveSource()).toBe('merged');
      // Not 900 (the tab we left) and not 2 (a member): the hub answers a
      // merged read, so the hub's profile is the honest colour.
      expect(store.getThreshold('Diodes')).toBe(4242);
      expect(asked()).toContain('local');
    });

    it('asks the HUB for a grouped tab too', async () => {
      const { store, ids } = await bootStore(ROSTER);
      // A group that is NOT every source serializes to "<shop>,<bench>" rather
      // than `merged`, so it needs its own case: a comma is as much "no single
      // server" as the merged token is.
      await store.openTab([ids.Shop, ids.Bench]);
      await settle();
      expect(store.getActiveSource()).toBe(`${ids.Shop},${ids.Bench}`);
      expect(store.getThreshold('Diodes')).toBe(4242);
    });

    it('keeps colouring with the last-known profile when the source goes down', async () => {
      // The requirement in grid terms: a failed refresh must not repaint the
      // rows with the table this build shipped.
      const { store, ids } = await bootStore(ROSTER);
      const { refreshProfile, profileState, profileError } =
        await import('../../js/threshold-profiles.js');
      await store.switchActiveSource(ids.Shop);
      await settle();
      expect(store.getThreshold('Diodes')).toBe(900);

      responders.set(ids.Shop, new Error('connection reset'));
      await refreshProfile(store.getActiveSource());

      expect(profileState(store.getActiveSource())).toBe('stale');
      expect(profileError(store.getActiveSource())).toContain('connection reset');
      expect(store.getThreshold('Diodes')).toBe(900);
      expect(store.getThreshold('Diodes')).not.toBe(SHIPPED_DIODES);
    });

    it('reaches the shipped table only where the state admits it', async () => {
      // The one remaining path to the build-time table: a source that has never
      // answered at all. It resolves through snapshot 0 / derivation / base as
      // the shared fixture defines — and `profileState()` says 'unavailable',
      // which is what makes it a stated fallback rather than a silent one.
      const { store, ids } = await bootStore(ROSTER);
      const { profileState, resolveOptionsFor } =
        await import('../../js/threshold-profiles.js');
      // Never answers at all — so there is no last-known profile to keep.
      responders.delete(ids.Bench);
      await store.switchActiveSource(ids.Bench);
      await settle();

      expect(profileState(store.getActiveSource())).toBe('unavailable');
      expect(resolveOptionsFor(store.getActiveSource())).toEqual({});
      expect(store.getThreshold('Diodes')).toBe(SHIPPED_DIODES);
    });

    it('asks nothing while the window does not know its source', async () => {
      // No hydrateSourcesFromPreferences() here on purpose: pre-hydration
      // `getActiveSource()` is '' and the hub would answer a header-less
      // request from its PERSISTED default — another window's setting.
      const store = await import('../../js/store.js');
      expect(store.getActiveSource()).toBe('');
      await settle();
      expect(calls).toHaveLength(0);
      const { profileState } = await import('../../js/threshold-profiles.js');
      expect(profileState('')).toBe('unknown');
    });
  });

  describe('the repaint signal', () => {
    it('publishes the source whose entry moved', async () => {
      const { refreshProfile, thresholdProfileSignal } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      responders.set('bench', BENCH);

      expect(thresholdProfileSignal.peek().sourceId).toBe('');

      await refreshProfile('shop');
      expect(thresholdProfileSignal.peek().sourceId).toBe('shop');
      await refreshProfile('bench');
      expect(thresholdProfileSignal.peek().sourceId).toBe('bench');
    });

    it('publishes on a stale->fresh recovery, so the marker can clear', async () => {
      const { refreshProfile, thresholdProfileSignal } =
        await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await refreshProfile('shop');
      responders.set('shop', new Error('down'));
      await refreshProfile('shop');
      const stale = thresholdProfileSignal.peek().revision;
      responders.set('shop', SHOP);
      await refreshProfile('shop');
      expect(thresholdProfileSignal.peek().revision).toBeGreaterThan(stale);
    });

    it('never rejects, so a paint path cannot be taken down by a dead source', async () => {
      const { refreshProfile } = await import('../../js/threshold-profiles.js');
      responders.set('shop', new Error('down'));
      await expect(refreshProfile('shop')).resolves.toMatchObject({ state: 'unavailable' });
    });

    it('coalesces concurrent refreshes of one source into one request', async () => {
      const { refreshProfile } = await import('../../js/threshold-profiles.js');
      responders.set('shop', SHOP);
      await Promise.all([refreshProfile('shop'), refreshProfile('shop'), refreshProfile('shop')]);
      expect(asked().filter((s) => s === 'shop')).toHaveLength(1);
    });

    it('asks nothing when there is no source to ask', async () => {
      const { refreshProfile } = await import('../../js/threshold-profiles.js');
      await expect(refreshProfile('')).resolves.toBeNull();
      expect(calls).toHaveLength(0);
    });
  });
});
