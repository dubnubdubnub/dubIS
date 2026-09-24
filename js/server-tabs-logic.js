// @ts-check
/* server-tabs-logic.js — pure logic for the server quick-switcher.

   No DOM, no fetch, no store — js/server-tabs.js owns those. Everything here is
   a pure function, for the same reason js/servers-logic.js is: the interesting
   half of this feature is not the rendering, it is deciding what a half-answered
   roster is allowed to say out loud, and what a gesture means.

   ── The model: a tab is a VIEW INSTANCE, not a server ──────────────────────

   This started as a strip derived from the roster — one tab per server, plus a
   synthesized "All". That model cannot express the three things the feature is
   actually for, so tabs now have their own identity:

     - the same server can be open twice (that is the point of per-tab view
       state: bench filtered to capacitors in one tab, bench sorted by value in
       another), so a tab id can never be a source id;
     - a tab can show a *subset* of servers, not just one or all of them;
     - tabs are ordered by the user, so order is data, not a function of the
       roster's order.

   A tab therefore carries `sources` — one id for a plain tab, several for a
   group — and `All` stops being a concept. It is just a group whose source set
   happens to be every source there is, and `activeSourceValue` still serializes
   that to the hub's legacy `merged` token so the wire format did not have to
   fork. A group of exactly {bench, shop} serializes to `"bench,shop"`, which is
   the set syntax `X-Dubis-Source` accepts.

   ── What `GET /v1/sources` gives us ────────────────────────────────────────

     {
       "default": "local" | "<source id>" | "<id>,<id>" | "merged",
       "sources": [{ id, name, url, enabled, reachable, detail }, ...]
     }

   `default` is what a request carrying no `X-Dubis-Source` header is served
   from. It is a saved preference, NOT a live "current server" — the hub keeps
   no such state, because several windows are open on different servers on
   purpose (server/dispatch.py). The route also still emits `active` as an alias;
   this module reads `default` and falls back to it.

   `reachable` is deliberately tri-state (true / false / absent). The hub is what
   does the fetching now, so "reachable" finally means something honest — but
   only once the hub has actually probed. Before that it is absent, and an absent
   probe must read as *unknown*, never as `false`: a red dot is a claim that a
   server is down, and this module never lets the UI make that claim on no
   evidence. Same rule js/servers-logic.js's `probePlan` follows for the cases
   the page cannot probe at all.

   Local is not in that list and never is. It is the hub itself — the process
   serving this page — so it is synthesized here and can never be unreachable by
   construction: a hub that were down could not have answered the request that
   produced this roster.
*/

import { LOCAL_ID, normalizeServers, normalizeServerUrl, nameFromUrl } from './servers-logic.js';

export { LOCAL_ID };

/** The hub's legacy token for "every source at once". Still the wire value for a tab that covers them all. */
export const MERGED_ID = 'merged';

export const LOCAL_LABEL = 'Local';
export const ALL_LABEL = 'All';

/** Beyond this many members, a group's label counts the rest instead of naming them. */
const LABEL_NAME_LIMIT = 2;

/**
 * `has_token` / `auth` are carried straight through from `GET /v1/sources` in
 * the hub's own snake_case, deliberately un-renamed: they are the wire fields,
 * and a second spelling would be a second thing to keep in sync. `auth` is
 * "ok" | "required" | "rejected" | "unknown", and only the hub can answer it —
 * it is the hub's outbound client that authenticates to a source, never this
 * window. Nothing in THIS module reads either field; the tab strip's dots mean
 * reachability and nothing else. They are carried so `getSources()` can hand
 * them to the Preferences picker (js/server-list.js), which is where a
 * credential is shown and edited.
 *
 * `tunnel` is the hub's ssh tunnel behind an `ssh://` source (null for every
 * other source, and when the hub did not say): `{state, kind, error}` from
 * server/ssh_tunnel.py. The tab dot needs none of it — the hub already folds a
 * tunnel failure into `reachable`/`detail` — but the picker's `hubDot` uses
 * `kind` for its terse label.
 * @typedef {{id: string, name: string, url: string, enabled: boolean,
 *            reachable: (boolean|undefined), detail: string,
 *            has_token: boolean, auth: string,
 *            tunnel: ({state: string, kind: string, error: string}|null)}} Source
 */

/** @typedef {{state: 'active'|'live'|'down'|'partial'|'unknown', detail: string}} DotClaim */

/**
 * A tab: a view instance, with its own identity, its own source set and its own
 * inventory view state.
 * @typedef {{id: string, sources: string[], name: string, view: (object|null)}} Tab
 */

/**
 * @typedef {{id: string, label: string, title: string, sources: string[],
 *            kind: 'single'|'group', active: boolean, selected: boolean,
 *            closable: boolean, dot: DotClaim}} TabView
 */

/**
 * One row of the `+` menu: a server (or the whole set) a new tab could be
 * opened on.
 * @typedef {{key: string, sources: string[], label: string, title: string,
 *            dot: DotClaim, current: boolean}} TabChoice
 */

// ══ Sources ════════════════════════════════════════════════════════════════

/**
 * Read a `GET /v1/sources` payload into a clean roster plus the active value.
 *
 * Tolerant on purpose, in both directions. The far more common runtime case is a
 * *partial* answer: a source the hub has not probed yet, or one whose probe is
 * still in flight. Every such gap has to degrade to "unknown", because the
 * alternative — a strip that refuses to render until every probe lands — is a
 * switcher you cannot use during the seconds you most want to.
 *
 * A bare array is accepted as well as the enveloped form, so a caller can feed
 * this a roster it already has (see `sourcesFromRoster`) without inventing an
 * envelope around it.
 *
 * @param {any} raw
 * @param {(msg: string) => void} [warn]
 * @returns {{sources: Array<Source>, defaultSource: string}}
 */
export function normalizeSources(raw, warn) {
  const log = typeof warn === 'function' ? warn : function () {};
  const envelope = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const list = Array.isArray(raw) ? raw : (Array.isArray(envelope.sources) ? envelope.sources : null);

  /** @type {Array<Source>} */
  const sources = [];
  if (list === null) {
    if (raw !== undefined && raw !== null) log('sources payload has no sources array — ignoring');
  } else {
    const seen = new Set();
    for (const entry of list) {
      if (!entry || typeof entry !== 'object') {
        log('ignoring non-object source entry');
        continue;
      }
      const id = typeof entry.id === 'string' ? entry.id.trim() : '';
      if (!id) {
        log('ignoring source entry with missing/invalid id');
        continue;
      }
      // Both ids name things this module synthesizes: LOCAL_ID is the hub
      // itself, MERGED_ID is the wire token for "all of them". A reported one
      // would render twice and be switchable in two contradictory ways — the
      // same reason normalizeServers reserves "local".
      if (id === LOCAL_ID || id === MERGED_ID) {
        log('ignoring source entry using the reserved id "' + id + '"');
        continue;
      }
      if (seen.has(id)) {
        log('ignoring source entry with duplicate id "' + id + '"');
        continue;
      }
      seen.add(id);
      const url = normalizeServerUrl(entry.url);
      const name = typeof entry.name === 'string' && entry.name.trim()
        ? entry.name.trim()
        : (nameFromUrl(url) || id);
      sources.push({
        id,
        name,
        url,
        // Absent means enabled: a source is in the roster because someone added
        // it, and the fan-out opt-OUT is the flag. Defaulting the other way
        // would make a hub that has not learned about `enabled` yet look like a
        // hub with no sources at all.
        enabled: entry.enabled === undefined ? true : !!entry.enabled,
        reachable: typeof entry.reachable === 'boolean' ? entry.reachable : undefined,
        detail: typeof entry.detail === 'string' ? entry.detail : '',
        // Both default to "we have not been told". A hub too old to report
        // them must not read as "definitely no token, definitely fine" — that
        // is the same false-confidence failure `reachable: undefined` avoids
        // one line up.
        has_token: entry.has_token === true,
        auth: typeof entry.auth === 'string' && entry.auth ? entry.auth : 'unknown',
        tunnel: normalizeTunnel(entry.tunnel),
      });
    }
  }

  // `default` is canonical; `active` is the older alias for the same value and
  // is read only so a hub that predates the rename still works.
  const raw0 = typeof envelope.default === 'string' ? envelope.default
    : (typeof envelope.active === 'string' ? envelope.active : '');
  return { sources, defaultSource: raw0.trim() };
}

/**
 * Derive a source roster from the client-side preferences roster, for when
 * `GET /v1/sources` has not answered (yet, or at all).
 *
 * Every entry comes back with `reachable: undefined`. The preferences roster is
 * a list of URLs somebody typed; it carries no probe result, and inventing one
 * here would put a green dot next to a server nothing has contacted.
 *
 * @param {Array<{id: string, name: string, url: string}>} servers the preferences roster
 * @returns {Array<Source>}
 */
export function sourcesFromRoster(servers) {
  return normalizeServers(servers).map((s) => ({
    id: s.id,
    name: s.name,
    url: s.url,
    enabled: true,
    reachable: undefined,
    detail: '',
    // Same reasoning as `reachable` above: the preferences roster is a list of
    // URLs somebody typed. It records no credential (see js/servers-logic.js on
    // why it must not) and no auth outcome.
    has_token: false,
    auth: 'unknown',
    tunnel: null,
  }));
}

/**
 * @param {any} raw
 * @returns {({state: string, kind: string, error: string}|null)}
 */
function normalizeTunnel(raw) {
  if (!raw || typeof raw !== 'object' || typeof raw.state !== 'string') return null;
  return {
    state: raw.state,
    kind: typeof raw.kind === 'string' ? raw.kind : '',
    error: typeof raw.error === 'string' ? raw.error : '',
  };
}

/**
 * Every source id a tab may name, Local first.
 *
 * Local is not in the roster and never is — it is the hub — so "all the sources
 * there are" has to be assembled rather than read.
 * @param {Array<Source>} sources
 * @returns {string[]}
 */
export function allSourceIds(sources) {
  return [LOCAL_ID, ...(sources || []).map((s) => s.id)];
}

/**
 * The source entries a tab names, in roster order, skipping ids that are gone.
 * @param {Tab} tab
 * @param {Array<Source>} sources
 * @returns {Array<Source>}
 */
export function tabMembers(tab, sources) {
  const want = new Set((tab && tab.sources) || []);
  /** @type {Array<Source>} */
  const out = [];
  if (want.has(LOCAL_ID)) {
    out.push({
      id: LOCAL_ID, name: LOCAL_LABEL, url: '', enabled: true,
      // The hub answered, so the hub is up. There is no honest way for this to
      // be anything else. Nor is there an auth hop to fail: this IS the process
      // serving the window, so it needs no credential and always passes.
      reachable: true, detail: 'always available — it serves this window',
      has_token: false, auth: 'ok', tunnel: null,
    });
  }
  for (const s of sources || []) if (want.has(s.id)) out.push(s);
  return out;
}

// ══ Dots ═══════════════════════════════════════════════════════════════════

/**
 * What one source's dot is allowed to claim.
 * @param {Source} source
 * @param {boolean} isActive
 * @returns {DotClaim}
 */
export function sourceDot(source, isActive) {
  const s = source || /** @type {Source} */ ({});
  // Active outranks reachability, and outranks it even when the last probe said
  // "down": we are reading this source's inventory right now, which is a
  // stronger and more recent piece of evidence than any probe result.
  if (isActive) return { state: 'active', detail: 'reading from this source now' };
  if (s.enabled === false) return { state: 'unknown', detail: 'not in the merged view' };
  if (s.reachable === true) return { state: 'live', detail: s.detail || 'reachable from the hub' };
  if (s.reachable === false) return { state: 'down', detail: s.detail || 'unreachable from the hub' };
  return { state: 'unknown', detail: 'not checked yet' };
}

/**
 * What a group's dot is allowed to claim, over exactly its own members.
 *
 * A group has no reachability of its own — it is not a server. What it does have
 * is a failure mode worth surfacing: the fan-out degrades rather than fails when
 * a source is down (server/fanout.py), so a group can quietly be showing you
 * less than it promises. `partial` is that state, and it exists so a missing
 * part reads as "shop is down" instead of "we lost your inventory".
 *
 * This is also where the old `All` tab's dot came from — `All` is now just a
 * group over every source, so it gets this for free rather than by a second
 * code path that could disagree with it.
 *
 * @param {Array<Source>} members the group's OWN members, not the whole roster
 * @param {boolean} isActive
 * @returns {DotClaim}
 */
export function mergedDot(members, isActive) {
  const enabled = (members || []).filter((s) => s.enabled !== false);
  const down = enabled.filter((s) => s.reachable === false);
  if (down.length) {
    const names = down.map((s) => s.name).join(', ');
    return {
      state: 'partial',
      detail: down.length === 1
        ? names + ' is unreachable — this view is incomplete'
        : names + ' are unreachable — this view is incomplete',
    };
  }
  if (isActive) return { state: 'active', detail: 'reading from these sources now' };
  if (enabled.length && enabled.every((s) => s.reachable === true)) {
    return { state: 'live', detail: 'every source in this group is reachable' };
  }
  return { state: 'unknown', detail: 'not checked yet' };
}

/**
 * What a tab's dot may claim — one source or many, one entry point.
 * @param {Tab} tab
 * @param {Array<Source>} sources
 * @param {boolean} isActive
 * @returns {DotClaim}
 */
export function tabDot(tab, sources, isActive) {
  const members = tabMembers(tab, sources);
  if (members.length === 1) return sourceDot(members[0], isActive);
  return mergedDot(members, isActive);
}

// ══ Tabs ═══════════════════════════════════════════════════════════════════

/** @param {Tab} tab @returns {'single'|'group'} */
export function tabKind(tab) {
  return tab && tab.sources && tab.sources.length > 1 ? 'group' : 'single';
}

/**
 * Does this tab name every source there is?
 *
 * This is what "All" means now. It is asked rather than stored so that the
 * answer stays true as the roster changes — a stored `isAll: true` would go on
 * claiming to be everything the moment a new server was added.
 * @param {Tab} tab
 * @param {string[]} sourceIds
 * @returns {boolean}
 */
export function coversAll(tab, sourceIds) {
  const ids = sourceIds || [];
  const have = new Set((tab && tab.sources) || []);
  return ids.length > 1 && ids.every((id) => have.has(id)) && have.size === ids.length;
}

/**
 * A tab's visible label.
 * @param {Tab} tab
 * @param {Array<Source>} sources
 * @returns {string}
 */
export function tabLabel(tab, sources) {
  if (tab && typeof tab.name === 'string' && tab.name.trim()) return tab.name.trim();
  const members = tabMembers(tab, sources);
  if (!members.length) return 'Empty';
  if (members.length === 1) return members[0].name;
  if (coversAll(tab, allSourceIds(sources))) return ALL_LABEL;
  const named = members.slice(0, LABEL_NAME_LIMIT).map((m) => m.name).join(' + ');
  // A group of six servers must not widen the strip by six names — the row is
  // fixed-height and horizontally scrolled, so every px of width is a px the
  // user has to scroll past to reach the tab after it.
  return members.length > LABEL_NAME_LIMIT ? named + ' +' + (members.length - LABEL_NAME_LIMIT) : named;
}

/**
 * The tooltip: what this tab is actually reading from.
 * @param {Tab} tab
 * @param {Array<Source>} sources
 * @returns {string}
 */
export function tabTitle(tab, sources) {
  const members = tabMembers(tab, sources);
  if (!members.length) return 'No sources — this tab shows nothing';
  if (members.length === 1) {
    return members[0].id === LOCAL_ID ? 'This machine’s own dubIS server' : (members[0].url || members[0].name);
  }
  return 'Merged across ' + members.map((m) => m.name).join(', ');
}

/**
 * The value to PUT to `/v1/sources/active` for this tab.
 *
 * Three cases, and the middle one is the whole reason `All` could be retired
 * without a wire change:
 *   - one source        → its id, exactly as before;
 *   - every source      → `merged`, the hub's own token for "all of them", which
 *                         stays correct as the roster grows and means the hub
 *                         does not have to be told the roster it already owns;
 *   - any other subset  → the comma-joined set, which is the syntax
 *                         `X-Dubis-Source` takes.
 * @param {Tab} tab
 * @param {string[]} sourceIds every source id there is
 * @returns {string}
 */
export function activeSourceValue(tab, sourceIds) {
  const list = (tab && tab.sources) || [];
  if (!list.length) return LOCAL_ID;
  if (list.length === 1) return list[0];
  if (coversAll(tab, sourceIds)) return MERGED_ID;
  return list.join(',');
}

/**
 * Read a persisted `server_tabs` payload into clean tabs.
 *
 * Drops what it cannot repair rather than crashing the load — preferences.json
 * is a file a user may reasonably hand-edit — and, critically, repairs tabs
 * against the CURRENT roster: a tab naming a server that has since been removed
 * loses that source, and a tab left with no sources at all is dropped. The
 * alternative is a tab that switches the hub to a source that does not exist.
 *
 * @param {any} raw
 * @param {string[]} sourceIds every source id there is
 * @param {(msg: string) => void} [warn]
 * @returns {Array<Tab>}
 */
export function normalizeTabs(raw, sourceIds, warn) {
  const log = typeof warn === 'function' ? warn : function () {};
  const known = new Set(sourceIds || [LOCAL_ID]);
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) {
    log('server_tabs is not an array — ignoring');
    return [];
  }
  /** @type {Array<Tab>} */
  const out = [];
  const seen = new Set();
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') {
      log('ignoring non-object tab entry');
      continue;
    }
    const id = typeof entry.id === 'string' ? entry.id.trim() : '';
    if (!id) {
      log('ignoring tab entry with missing/invalid id');
      continue;
    }
    if (seen.has(id)) {
      log('ignoring tab entry with duplicate id "' + id + '"');
      continue;
    }
    const rawSources = Array.isArray(entry.sources) ? entry.sources : [];
    /** @type {string[]} */
    const sources = [];
    for (const s of rawSources) {
      const sid = typeof s === 'string' ? s.trim() : '';
      if (!sid || sources.includes(sid)) continue;
      if (!known.has(sid)) {
        log('tab "' + id + '" names source "' + sid + '", which is no longer configured — dropping it');
        continue;
      }
      sources.push(sid);
    }
    if (!sources.length) {
      log('ignoring tab "' + id + '" — none of its sources still exist');
      continue;
    }
    seen.add(id);
    out.push({
      id,
      sources,
      name: typeof entry.name === 'string' ? entry.name.trim() : '',
      // Raw pass-through: the snapshot's shape is owned by captureView/applyView
      // in js/inventory/saved-views.js, and applyView already defaults every
      // field it reads. Validating it a second time here would be a second
      // opinion about a shape this module does not own.
      view: entry.view && typeof entry.view === 'object' ? entry.view : null,
    });
  }
  return out;
}

/**
 * The tab set to start from when nothing has been persisted.
 *
 * Deliberately NOT "one empty tab". Before tabs had identity the strip was
 * derived from the roster — Local, each server, then All — so seeding exactly
 * that means an existing user's strip looks identical after the upgrade and
 * only changes when they change it. A first-time user with no servers gets one
 * Local tab, which is also what they had.
 *
 * @param {string[]} sourceIds
 * @param {() => string} idFactory
 * @returns {Array<Tab>}
 */
export function seedTabs(sourceIds, idFactory) {
  const ids = (sourceIds || []).length ? sourceIds : [LOCAL_ID];
  /** @type {Array<Tab>} */
  const tabs = ids.map((id) => ({ id: idFactory(), sources: [id], name: '', view: null }));
  if (ids.length > 1) {
    tabs.push({ id: idFactory(), sources: [...ids], name: '', view: null });
  }
  return tabs;
}

/**
 * Bring an existing tab set back in line with a changed roster.
 *
 * Two rules, and the second is the one that is easy to get wrong:
 *   - a source that is gone is removed from every tab, and a tab left empty is
 *     closed (never the last one — the strip always has somewhere to be);
 *   - a tab that covered every source BEFORE the change keeps covering every
 *     source after it. Otherwise adding a server silently demotes "All" to
 *     "all except the one you just added", which is a lie told by omission and
 *     exactly the kind of thing nobody notices until a part goes missing.
 *
 * @param {Array<Tab>} tabs
 * @param {string[]} beforeIds the source ids as they were
 * @param {string[]} afterIds the source ids as they now are
 * @returns {Array<Tab>}
 */
export function reconcileTabs(tabs, beforeIds, afterIds) {
  const after = new Set(afterIds || []);
  /** @type {Array<Tab>} */
  const out = [];
  for (const tab of tabs || []) {
    const wasAll = coversAll(tab, beforeIds || []);
    const sources = wasAll ? [...after] : tab.sources.filter((s) => after.has(s));
    if (!sources.length) continue;
    out.push({ ...tab, sources });
  }
  if (!out.length && (tabs || []).length) {
    // Everything was dropped. Keep the strip alive on Local rather than handing
    // back an empty set the caller would have to special-case.
    return [{ ...tabs[0], sources: [LOCAL_ID] }];
  }
  return out;
}

/**
 * Coerce a persisted active tab id to one that names a real tab.
 *
 * Falls back to the first tab rather than to "nothing selected". A strip with no
 * active tab is not a neutral state — it tells the user their data is coming
 * from somewhere unnamed.
 * @param {Array<Tab>} tabs
 * @param {any} raw
 * @returns {string}
 */
export function resolveActiveTabId(tabs, raw) {
  const list = tabs || [];
  if (!list.length) return '';
  const id = typeof raw === 'string' ? raw.trim() : '';
  return list.some((t) => t.id === id) ? id : list[0].id;
}

/**
 * The tab id that should be active for a bare source id.
 *
 * Used by the Preferences roster, which still picks a *server*, not a tab. It
 * activates an existing plain tab for that server rather than re-pointing
 * whatever tab happens to be in front: re-pointing would silently change what a
 * tab means, and the tab's saved view belongs to the server it was opened for.
 * Returns "" when no such tab exists, which the caller answers by opening one.
 * @param {Array<Tab>} tabs
 * @param {string} sourceId
 * @returns {string}
 */
export function tabForSource(tabs, sourceId) {
  const match = (tabs || []).find((t) => t.sources.length === 1 && t.sources[0] === sourceId);
  return match ? match.id : '';
}

/**
 * The tab whose source set serializes to `value`, if one does.
 *
 * Used exactly once: to land a brand-new window on the hub's persisted default
 * (a `DUBIS_URL` launch, say) instead of on whichever tab happened to be seeded
 * first. After that the window owns its own active tab and the hub's default is
 * irrelevant to it — which is the whole point of sending `X-Dubis-Source` on
 * every request.
 * @param {Array<Tab>} tabs
 * @param {string[]} sourceIds
 * @param {string} value an `X-Dubis-Source` value
 * @returns {string} tab id, or "" if nothing matches
 */
export function tabForSourceValue(tabs, sourceIds, value) {
  const want = String(value || '').trim();
  if (!want) return '';
  const match = (tabs || []).find((t) => activeSourceValue(t, sourceIds) === want);
  return match ? match.id : '';
}

// ══ Operations ═════════════════════════════════════════════════════════════

/**
 * Add a tab, immediately after `afterId` (browser behaviour: a new tab opens
 * next to the one you opened it from, not at the far end of a scrolled strip
 * where you would have to go looking for it).
 * @param {Array<Tab>} tabs
 * @param {Tab} tab
 * @param {string} [afterId]
 * @returns {Array<Tab>}
 */
export function addTab(tabs, tab, afterId) {
  const list = (tabs || []).slice();
  const idx = list.findIndex((t) => t.id === afterId);
  if (idx === -1) list.push(tab);
  else list.splice(idx + 1, 0, tab);
  return list;
}

/**
 * Close a tab.
 *
 * The last tab cannot be closed: the inventory grid is always showing
 * *something*, so there is always a tab describing what that something is.
 * Closing the active tab activates its right-hand neighbour, falling back to the
 * left — the browser rule, and the one that keeps a rapid close-close-close from
 * jumping the user across the strip.
 * @param {Array<Tab>} tabs
 * @param {string} activeTabId
 * @param {string} id
 * @returns {{ok: boolean, reason?: string, tabs?: Array<Tab>, activeTabId?: string}}
 */
export function closeTab(tabs, activeTabId, id) {
  const list = tabs || [];
  const idx = list.findIndex((t) => t.id === id);
  if (idx === -1) return { ok: false, reason: 'That tab is already closed' };
  if (list.length <= 1) return { ok: false, reason: 'The last tab cannot be closed' };
  const out = list.slice();
  out.splice(idx, 1);
  const active = activeTabId === id
    ? (out[Math.min(idx, out.length - 1)].id)
    : activeTabId;
  return { ok: true, tabs: out, activeTabId: active };
}

/**
 * Move a tab so it sits immediately before `beforeId`, or last when that is "".
 *
 * Expressed as "before which tab" rather than "at which index" because that is
 * what a drop actually knows — the element under the pointer — and converting to
 * an index at the call site is where off-by-one bugs live.
 * @param {Array<Tab>} tabs
 * @param {string} id
 * @param {string} beforeId "" to append
 * @returns {Array<Tab>}
 */
export function moveTab(tabs, id, beforeId) {
  const list = (tabs || []).slice();
  const from = list.findIndex((t) => t.id === id);
  if (from === -1 || id === beforeId) return list;
  const [moved] = list.splice(from, 1);
  const to = beforeId ? list.findIndex((t) => t.id === beforeId) : -1;
  if (to === -1) list.push(moved);
  else list.splice(to, 0, moved);
  return list;
}

/**
 * Merge several tabs into one group tab.
 *
 * The group takes the union of their sources and the position of the leftmost of
 * them, and the tabs it replaces go away — grouping is a rearrangement, not a
 * duplication, so leaving the originals behind would double the strip every
 * time. Its view state is the leftmost tab's, because that is the one whose
 * filters the user was looking at when they decided these belonged together.
 *
 * Refuses a group of one: that is not a merge, it is a no-op with a new id.
 * @param {Array<Tab>} tabs
 * @param {string[]} ids
 * @param {string} newId
 * @returns {{ok: boolean, reason?: string, tabs?: Array<Tab>, activeTabId?: string}}
 */
export function groupTabs(tabs, ids, newId) {
  const list = tabs || [];
  const wanted = list.filter((t) => (ids || []).includes(t.id));
  if (wanted.length < 2) return { ok: false, reason: 'Select two or more tabs to group them' };
  /** @type {string[]} */
  const sources = [];
  for (const t of wanted) for (const s of t.sources) if (!sources.includes(s)) sources.push(s);
  const at = list.findIndex((t) => t.id === wanted[0].id);
  const group = { id: newId, sources, name: '', view: wanted[0].view };
  const out = list.filter((t) => !(ids || []).includes(t.id));
  out.splice(at, 0, group);
  return { ok: true, tabs: out, activeTabId: newId };
}

/**
 * What a modified click does to the multi-selection.
 *
 * Mirrors a file manager, which is what everyone already has in their fingers:
 * plain click replaces the selection, ctrl/cmd toggles one, shift extends from
 * the anchor. The anchor only moves on a plain or toggling click, never on a
 * shift-click, so shift-clicking twice re-extends from the same origin instead
 * of walking the selection across the strip.
 *
 * @param {string[]} orderedIds tab ids in strip order
 * @param {string[]} selected currently selected ids
 * @param {string} anchor the id ranges extend from
 * @param {string} id the clicked tab
 * @param {{ctrl?: boolean, shift?: boolean}} mods
 * @returns {{selected: string[], anchor: string}}
 */
export function selectionAfterClick(orderedIds, selected, anchor, id, mods) {
  const order = orderedIds || [];
  const m = mods || {};
  if (m.shift && anchor && order.includes(anchor) && order.includes(id)) {
    const a = order.indexOf(anchor);
    const b = order.indexOf(id);
    const [lo, hi] = a <= b ? [a, b] : [b, a];
    return { selected: order.slice(lo, hi + 1), anchor };
  }
  if (m.ctrl) {
    const cur = new Set(selected || []);
    if (cur.has(id)) cur.delete(id);
    else cur.add(id);
    return { selected: order.filter((x) => cur.has(x)), anchor: id };
  }
  return { selected: [id], anchor: id };
}

/**
 * What clicking a tab means.
 *
 * `changed: false` is a success, not a rejection — clicking the tab you are
 * already on is a no-op, and firing a switch (and therefore an inventory
 * refetch) for it would make the strip feel like a reload button.
 *
 * A tab whose dot says `down` is deliberately still switchable. The hub's probe
 * is a snapshot taken up to a poll-interval ago; refusing the click would mean a
 * server that came back thirty seconds ago is unreachable through the UI until
 * the strip agrees it is back. A switch that fails says so loudly, which is the
 * better failure.
 * @param {Array<Tab>} tabs
 * @param {string} activeTabId
 * @param {string} id
 * @returns {{ok: boolean, changed: boolean, tabId?: string, reason?: string}}
 */
export function switchIntent(tabs, activeTabId, id) {
  const tab = (tabs || []).find((t) => t.id === id);
  if (!tab) return { ok: false, changed: false, reason: 'That tab is no longer open' };
  if (tab.id === activeTabId) return { ok: true, changed: false, tabId: tab.id };
  return { ok: true, changed: true, tabId: tab.id };
}

/**
 * Whether the strip is worth showing at all.
 *
 * One tab and nowhere else to go is not a switcher. A user with no servers
 * configured would get a permanent row that says "Local" and does nothing — and,
 * because that row sits above the panels, it would cost them its height forever
 * in exchange for no capability. Hidden is the honest default; adding a server
 * in Preferences is what makes it appear, and from then on `+` keeps it there.
 * @param {Array<Tab>} tabs
 * @param {string[]} sourceIds
 * @returns {boolean}
 */
export function shouldShowTabs(tabs, sourceIds) {
  return (tabs || []).length > 1 || (sourceIds || []).length > 1;
}

/**
 * Where a drop lands: the id of the tab the dragged one should sit before, or ""
 * for "after the last one".
 *
 * `pointerX` and `rect` MUST come from the same coordinate space. Under
 * `html { zoom: z }` an `event.clientX` and a `getBoundingClientRect()` are both
 * *post-zoom*, so comparing them is correct at any zoom — the trap is mixing one
 * of them with an authored-px value such as `offsetWidth`, or writing a
 * rect-derived number into a style. This function does neither: it compares two
 * post-zoom numbers and returns an id. Nothing downstream turns a measurement
 * into a position, which is why the drag needs no conversion helpers at all.
 *
 * @param {number} pointerX
 * @param {{left: number, width: number}} rect the hovered tab's rect
 * @param {string} hoveredId
 * @param {string} nextId the id after the hovered tab, "" if it is last
 * @returns {string}
 */
export function dropTarget(pointerX, rect, hoveredId, nextId) {
  const mid = rect.left + rect.width / 2;
  return pointerX < mid ? hoveredId : nextId;
}

// ══ Rendering ══════════════════════════════════════════════════════════════

/**
 * The view models to render, in tab order.
 * @param {Array<Tab>} tabs
 * @param {Array<Source>} sources
 * @param {string} activeTabId
 * @param {string[]} [selected]
 * @returns {Array<TabView>}
 */
export function renderTabs(tabs, sources, activeTabId, selected) {
  const list = tabs || [];
  const sel = new Set(selected || []);
  const active = resolveActiveTabId(list, activeTabId);
  return list.map((tab) => ({
    id: tab.id,
    label: tabLabel(tab, sources),
    title: tabTitle(tab, sources),
    sources: tab.sources.slice(),
    kind: tabKind(tab),
    active: tab.id === active,
    selected: sel.has(tab.id),
    // The last tab is the one the grid is describing; there is no state in
    // which the strip is empty.
    closable: list.length > 1,
    dot: tabDot(tab, sources, tab.id === active),
  }));
}

/**
 * What `+` may open a tab on: every source, then the whole set as one group.
 *
 * `+` used to take no argument at all — it opened another view of whatever the
 * active tab showed. That is a useful gesture and it is still the top of this
 * list, but it was the ONLY one, so the strip could not open a tab on a server
 * that had no tab: the roster was reachable only through Preferences, three
 * clicks away, for the single most common thing a tab strip is for.
 *
 * Every row opens a NEW tab, including one naming the server already in front —
 * that is the whole point of a tab id not being a source id, and it is why a row
 * is never disabled. `current` marks which row is the active tab's own source so
 * the menu can say so; it does not take the row away.
 *
 * The dots deliberately claim reachability, never `active`: this menu is a list
 * of places to go, and painting one of them green-because-you-are-there would
 * use the strip's "reading from this now" vocabulary for a row that is not a tab.
 *
 * @param {Array<Source>} sources
 * @param {Tab} [activeTab] the tab in front, for the `current` mark
 * @returns {Array<TabChoice>}
 */
export function newTabChoices(sources, activeTab) {
  const ids = allSourceIds(sources);
  const active = new Set((activeTab && activeTab.sources) || []);
  /** @type {Array<TabChoice>} */
  const out = ids.map((id) => {
    const tab = { id: '', sources: [id], name: '', view: null };
    const member = tabMembers(tab, sources)[0];
    return {
      key: id,
      sources: [id],
      label: tabLabel(tab, sources),
      title: tabTitle(tab, sources),
      dot: sourceDot(member, false),
      current: active.size === 1 && active.has(id),
    };
  });
  if (ids.length > 1) {
    const all = { id: '', sources: ids.slice(), name: '', view: null };
    out.push({
      key: MERGED_ID,
      sources: ids.slice(),
      label: ALL_LABEL,
      title: tabTitle(all, sources),
      dot: mergedDot(tabMembers(all, sources), false),
      current: coversAll(activeTab, ids),
    });
  }
  return out;
}

/**
 * The sentence to put in a toast after a switch lands.
 * @param {Array<Tab>} tabs
 * @param {Array<Source>} sources
 * @param {string} id
 * @returns {string}
 */
export function switchedMessage(tabs, sources, id) {
  const tab = (tabs || []).find((t) => t.id === id);
  if (!tab) return 'Switched tab';
  return 'Now showing ' + tabLabel(tab, sources);
}
